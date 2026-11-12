"""Validate the tract housing-need score (see docs/need_validation_plan.md for the pre-set criteria).

    python -m pipelines.need.build_tract_need      # build first
    python -m pipelines.need.validate_need         # then validate (downloads ACS 2019-2023 once)

Four parts, matching the plan:
1. Verification: independent spot-check of ACS values (Census Reporter API), HUD tract placement,
   coverage. The tool-vs-pipeline check runs in the browser against the scores written to
   data/processed/need/validation/python_scores.json.
2. Convergent validity: CDC/ATSDR SVI 2022, Housing Choice Vouchers, 2026 QCTs.
3. Criterion validity: Eviction Lab filings per 100 renter households, 2023-2025.
4. Uncertainty: data error (MOE resampling), 162 modeling-choice combinations, a combined Monte
   Carlo, and the ACS 2019-2023 vs 2020-2024 comparison.

Inputs it needs that the build doesn't fetch:
- data/raw/svi/SVI_2022_Georgia.csv (https://svi.cdc.gov/Documents/Data/2022/csv/states/Georgia.csv)
- data/raw/eviction/atlanta_tract_filings_2023_2025.json: tract sums of Eviction Lab's Atlanta
  monthly file (https://evictionlab.org/eviction-tracking/get-the-data/). The site blocks
  scripted downloads, so the file was summed in a browser; see the "source" field in the JSON.
  Not committed: Eviction Lab's data comes through a download form with its own terms, so tract-level
  eviction values stay local (.gitignore) and only summary results are committed.

Outputs in data/processed/need/validation/, and the results are added to need_site/data.js
(meta.validation, plus per-tract "ux" combined-uncertainty values) for the Methodology tab.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.stats import norm
from sklearn.metrics import roc_auc_score

from pipelines.need.build_tract_need import (
    APP_DATA, ARC_COUNTIES, GROUP_WEIGHTS, GROUPS, HEADERS, METRICS, OUT_CSV, RAW, ROOT,
    STUDENT_SHARE_FLAG, TRACTS_CSV, add_supply_gap, build_metrics, fetch_acs, fetch_hud, fetch_tract_shapes,
    point_to_tract, reliability, ACS_TABLES)

VAL_DIR = ROOT / "data" / "processed" / "need" / "validation"
SVI_CSV = RAW / "svi" / "SVI_2022_Georgia.csv"
EVICTION_JSON = RAW / "eviction" / "atlanta_tract_filings_2023_2025.json"
KEYS = [k for k, *_ in METRICS]
DIRS = np.array([d for _, _, _, d, *_ in METRICS], dtype=float)
GKEYS = [g for g, _ in GROUPS]
GROUP_OF = np.array([GKEYS.index(g) for _, g, *_ in METRICS])
GW0 = np.array([GROUP_WEIGHTS[g] for g in GKEYS], dtype=float)
PROPORTIONS = {k for k, _, _, _, fmt, _ in METRICS if fmt == "pct"} - {"rent_growth"}
EPS = 0.01
rng = np.random.default_rng(20261022)


SVI_URL = "https://svi.cdc.gov/Documents/Data/2022/csv/states/Georgia.csv"


def ensure_svi() -> None:
    """Download the CDC/ATSDR SVI 2022 Georgia file once."""
    if not SVI_CSV.exists():
        SVI_CSV.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(SVI_URL, headers=HEADERS, timeout=120)
        r.raise_for_status()
        SVI_CSV.write_bytes(r.content)


# ---------------------------------------------------------------------------------------------
# A general scorer, so modeling choices can be swapped
# ---------------------------------------------------------------------------------------------
def unreliable_flags(m: pd.DataFrame, cutoff: float) -> pd.DataFrame:
    """Same rule as the build, with the CV cutoff as a parameter (median income uses cutoff - 0.1)."""
    out = {}
    for k in KEYS:
        est, moe = m[k], m[k + "_moe"]
        cv = moe / 1.645 / est.abs().where(est.abs() > 0)
        if k == "median_hh_income":
            out[k] = est.isna() | cv.isna() | (cv > cutoff - 0.1)
        else:
            abs_ok = (moe <= 0.05) & (est >= moe)
            out[k] = est.isna() | cv.isna() | ((cv > cutoff) & ~abs_ok)
    return pd.DataFrame(out)


def normalize(V: pd.DataFrame, flags: pd.DataFrame, rows: pd.Series, method: str) -> pd.DataFrame:
    """Need-oriented values among `rows` (higher = more need), NaN where unreliable."""
    X = V[KEYS].where(~flags[KEYS] & rows.to_numpy()[:, None]) * DIRS
    if method == "pct":
        return X.rank(pct=True)
    if method == "z":
        return (X - X.mean()) / X.std()
    lo, hi = X.min(), X.max()
    return (X - lo) / (hi - lo).where(hi > lo)


def aggregate(X: pd.DataFrame, mw: np.ndarray, gw: np.ndarray, how: str, method: str) -> pd.Series:
    """Weighted arithmetic or geometric mean within groups, then across groups."""
    A = X.notna().to_numpy()
    V = X.to_numpy()
    if how == "geo":
        if method == "z":
            V = norm.cdf(V)                       # z-scores to (0, 1) so logs are defined
        V = np.log(np.clip(V, 0, None) + EPS)
    V = np.where(A, V, 0.0)
    num = np.zeros(len(X))
    den = np.zeros(len(X))
    for g in range(len(GKEYS)):
        cols = GROUP_OF == g
        w = mw[cols]
        gd = (A[:, cols] * w).sum(axis=1)
        gn = (V[:, cols] * w).sum(axis=1)
        has = gd > 0
        num += np.where(has, gw[g] * gn / np.where(has, gd, 1), 0)
        den += np.where(has, gw[g], 0)
    s = num / np.where(den > 0, den, np.nan)
    return pd.Series(np.exp(s) if how == "geo" else s, index=X.index)


def score(V, flags, ref, mw, gw=GW0, method="pct", how="arith", out=None) -> pd.Series:
    """Score tracts in `out` (default `ref`), normalizing each metric among the `ref` tracts.
    The tool normalizes among all tracts with 500+ residents (student-heavy ones included) and
    ranks only the non-student ones, so that's the baseline."""
    out = ref if out is None else out
    return aggregate(normalize(V, flags, ref, method), mw, gw, how, method).where(out)


def spearman(a: pd.Series, b: pd.Series) -> float:
    both = pd.concat([a, b], axis=1).dropna()
    return float(both.iloc[:, 0].corr(both.iloc[:, 1], method="spearman")), len(both)


# ---------------------------------------------------------------------------------------------
def main() -> None:
    VAL_DIR.mkdir(parents=True, exist_ok=True)
    m = pd.read_csv(OUT_CSV, dtype={"tract_geoid": str}).set_index("tract_geoid")
    for c in ["low_population", "student_tract", "qct_2026"]:
        m[c] = m[c].astype(str).eq("True")
    app = json.loads(APP_DATA.read_text()[len("window.NEED_DATA = "):-2])
    mw_fa = np.array([x["weight"] for x in app["metrics"]], dtype=float)
    mw_ex = np.array([x["weight_expert"] for x in app["metrics"]], dtype=float)
    scored = ~m["low_population"] & m["population"].notna()
    ranked = scored & ~m["student_tract"]
    # baseline flags exactly as the build stored them (recomputing from the CSV's 5-significant-figure
    # values can flip borderline cases); other cutoffs are recomputed with unreliable_flags()
    flags0 = pd.DataFrame({k: m[k + "_unreliable"].astype(str).eq("True") for k in KEYS})
    base = score(m, flags0, scored, mw_fa, out=ranked)
    n = int(ranked.sum())
    top_n = int(round(0.1 * n))
    res = {"n_ranked": n, "top10": top_n}
    base.rename("score").to_frame().dropna().round(6).to_json(VAL_DIR / "python_scores.json")

    print(f"ranked tracts: {n}")

    # ======================= 1. verification ==================================================
    v = {}
    ids = list(rng.choice(m.index[ranked], 10, replace=False))
    url = "https://api.censusreporter.org/1.0/data/show/acs2024_5yr"
    cr = requests.get(url, params={"table_ids": "B01003,B25003,B19013,C17002",
                                   "geo_ids": ",".join("14000US" + g for g in ids)},
                      headers=HEADERS, timeout=60).json()
    acs_cache = {t: pd.read_csv(RAW / "acs" / f"acsdt5y2024-{t.lower()}_arc.dat", sep="|",
                                dtype={"GEO_ID": str}).set_index("GEO_ID")
                 for t in ["B01003", "B25003", "B19013", "C17002"]}
    rows, exact = [], 0
    for g in ids:
        d = cr["data"]["14000US" + g]
        ours = {t: acs_cache[t].loc["1400000US" + g] for t in acs_cache}
        pairs = [("B01003001", ours["B01003"]["B01003_E001"]), ("B25003001", ours["B25003"]["B25003_E001"]),
                 ("B25003003", ours["B25003"]["B25003_E003"]), ("B19013001", ours["B19013"]["B19013_E001"]),
                 ("C17002001", ours["C17002"]["C17002_E001"])]
        for code, our in pairs:
            theirs = d[code[:6]]["estimate"][code]
            ok = (theirs is None and our < 0) or (theirs is not None and float(theirs) == float(our))
            exact += ok
            rows.append({"tract": g, "column": code, "ours": our, "census_reporter": theirs, "match": ok})
        # derived metrics from the independent values
        c = d["C17002"]["estimate"]
        pov = (c["C17002002"] + c["C17002003"]) / c["C17002001"]
        rs = d["B25003"]["estimate"]["B25003003"] / d["B25003"]["estimate"]["B25003001"]
        rows.append({"tract": g, "column": "poverty_rate", "ours": round(m.loc[g, "poverty_rate"], 5),
                     "census_reporter": round(pov, 5), "match": abs(pov - m.loc[g, "poverty_rate"]) < 0.001})
        rows.append({"tract": g, "column": "renter_share", "ours": round(m.loc[g, "renter_share"], 5),
                     "census_reporter": round(rs, 5), "match": abs(rs - m.loc[g, "renter_share"]) < 0.001})
    spot = pd.DataFrame(rows)
    spot.to_csv(VAL_DIR / "spot_check_census_reporter.csv", index=False)
    v["spot_check"] = {"tracts": len(ids), "values": len(spot), "matched": int(spot["match"].sum()),
                       "release": cr["release"]["name"], "pass": bool(spot["match"].all())}
    print("spot check:", v["spot_check"])

    shapes = fetch_tract_shapes(offline=True)
    hud = fetch_hud(offline=True)
    tract_set = set(m.index)
    agree = total = 0
    for key in ["lihtc", "public_housing", "hud_multifamily"]:
        feats = [f for f in hud[key] if f.get("geometry")]
        ours = point_to_tract([tuple(f["geometry"]["coordinates"][:2]) for f in feats], shapes)
        for f, o in zip(feats, ours):
            p = f["properties"]
            if not (p.get("CNTY2KX") and p.get("TRACT2KX")):
                continue
            theirs = "13" + str(p["CNTY2KX"])[-3:].zfill(3) + str(p["TRACT2KX"]).replace(".", "").zfill(6)
            if o is None or theirs not in tract_set:
                continue
            total += 1
            agree += theirs == o
    v["hud_placement"] = {"compared": total, "agree": agree, "share": round(agree / total, 3),
                          "pass": agree / total >= 0.95}
    print("HUD placement:", v["hud_placement"])
    have_shape = {f["properties"]["GEOID"] for f in shapes["features"]}
    v["coverage"] = {"tracts": len(m), "with_acs": int(m["population"].notna().sum()),
                     "with_boundary": len(tract_set & have_shape),
                     "pass": bool(m["population"].notna().all() and tract_set <= have_shape)}
    print("coverage:", v["coverage"])
    tool_check = VAL_DIR / "tool_check.json"      # written from the browser check (see module docstring)
    if tool_check.exists():
        v["tool_matches_pipeline"] = json.loads(tool_check.read_text())
    res["verification"] = v

    # ======================= 2. convergent validity ===========================================
    ensure_svi()
    svi = pd.read_csv(SVI_CSV, dtype={"FIPS": str}).set_index("FIPS")
    svi = svi[["RPL_THEMES", "RPL_THEME1", "RPL_THEME2", "RPL_THEME3", "RPL_THEME4"]].where(lambda x: x >= 0)
    svi = svi.reindex(m.index)
    c = {}
    for col, label in [("RPL_THEMES", "svi_overall"), ("RPL_THEME1", "svi_socioeconomic"),
                       ("RPL_THEME4", "svi_housing_transport")]:
        rho, k = spearman(base, svi[col].where(ranked))
        c[label] = {"rho": round(rho, 4), "n": k, "_raw": rho}
    for label in ["svi_overall", "svi_socioeconomic"]:
        c[label]["pass"] = c[label].pop("_raw") >= 0.70
    c["svi_housing_transport"].pop("_raw")
    vouch = m["vouchers"].fillna(5)                      # suppressed counts are 1-10: use the midpoint
    vrate = 100 * vouch / m["renter_households"].where(m["renter_households"] >= 50)
    rho, k = spearman(base, vrate.where(ranked))
    c["vouchers"] = {"rho": round(rho, 4), "n": k, "pass": rho >= 0.40}
    # diagnostic: the same test without the affordable-supply group, which scores tracts with lots of
    # assisted housing (where many voucher holders live) as lower need
    no_supply = GW0.copy()
    no_supply[GKEYS.index("supply")] = 0
    c["vouchers"]["rho_without_supply_group"] = round(spearman(score(m, flags0, scored, mw_fa, no_supply, out=ranked),
                                                               vrate.where(ranked))[0], 4)
    r = ranked & base.notna()
    auc = roc_auc_score(m.loc[r, "qct_2026"], base[r])
    c["qct_auc"] = {"auc": round(float(auc), 3), "n": int(r.sum()), "pass": auc >= 0.80}
    ours_pct = base.rank(pct=True)
    svi_pct = svi["RPL_THEMES"].where(ranked).rank(pct=True)
    gap = (ours_pct - svi_pct).dropna()
    dis = gap.abs().sort_values(ascending=False).head(15).index
    pd.DataFrame({"county": m.loc[dis, "county"], "need_pctl": (ours_pct[dis] * 100).round(),
                  "svi_pctl": (svi_pct[dis] * 100).round(), "renter_share": m.loc[dis, "renter_share"].round(2),
                  "median_hh_income": m.loc[dis, "median_hh_income"], "poverty_rate": m.loc[dis, "poverty_rate"].round(3)}
                 ).to_csv(VAL_DIR / "svi_disagreements.csv")
    c["disagreements"] = {"higher_than_svi": int((gap.loc[dis] > 0).sum()), "lower_than_svi": int((gap.loc[dis] < 0).sum()),
                          "renter_share_median": round(float(m.loc[dis, "renter_share"].median()), 2),
                          "renter_share_all": round(float(m.loc[ranked, "renter_share"].median()), 2)}
    print("convergent:", c)
    res["convergent"] = c

    # ======================= 3. criterion validity (evictions) ================================
    ev = json.loads(EVICTION_JSON.read_text())
    fil = pd.Series({g: v_[0] for g, v_ in ev["data"].items()}, dtype=float).reindex(m.index)
    covered = ranked & fil.notna() & (m["renter_households"] >= 50) & base.notna()
    rate = (fil / 3) / m["renter_households"] * 100                   # filings per 100 renter hh per year
    e = {"tracts": int(covered.sum()), "counties": sorted(m.loc[covered, "county"].unique().tolist())}
    rho, _ = spearman(base[covered], rate[covered])
    pooled = fil[covered].sum() / 3 / m.loc[covered, "renter_households"].sum() * 100
    topq = covered & (base >= base[covered].quantile(0.9))
    top_rate = fil[topq].sum() / 3 / m.loc[topq, "renter_households"].sum() * 100
    e["rho"] = round(rho, 4)
    e["avg_rate"] = round(float(pooled), 1)
    e["top10_rate"] = round(float(top_rate), 1)
    e["lift"] = round(float(top_rate / pooled), 2)
    e["by_county"] = {cty: round(spearman(base[covered & (m["county"] == cty)], rate[covered & (m["county"] == cty)])[0], 4)
                      for cty in e["counties"]}
    e["pass_rho"] = rho >= 0.40
    e["pass_lift"] = e["lift"] >= 2.0
    e["pass_county"] = all(x >= 0.30 for x in e["by_county"].values())
    svi_rho, _ = spearman(svi["RPL_THEMES"][covered], rate[covered])
    s_top = covered & (svi["RPL_THEMES"] >= svi["RPL_THEMES"][covered].quantile(0.9))
    e["svi_rho"] = round(svi_rho, 4)
    e["svi_lift"] = round(float(fil[s_top].sum() / 3 / m.loc[s_top, "renter_households"].sum() * 100 / pooled), 2)
    # which groups track evictions (context for weights)
    X = normalize(m, flags0, scored, "pct")
    X.loc[~ranked] = np.nan
    e["by_group"] = {}
    for gi, g in enumerate(GKEYS):
        oh = np.zeros(len(GKEYS))
        oh[gi] = 1
        gs = aggregate(X, mw_fa, oh, "arith", "pct")
        e["by_group"][g] = round(spearman(gs[covered], rate[covered])[0], 4)
    print("evictions:", e)
    res["criterion"] = e

    # ======================= 4. uncertainty ====================================================
    u = {}
    base_rank = base.rank(ascending=False)

    def resample(mm: pd.DataFrame) -> pd.DataFrame:
        out = mm.copy()
        for k in KEYS:
            sd = mm[k + "_moe"] / 1.645
            draw = mm[k] + rng.standard_normal(len(mm)) * sd.fillna(0)
            if k in PROPORTIONS:
                draw = draw.clip(0, 1)
            elif k == "median_hh_income":
                draw = draw.clip(lower=0)
            out[k] = draw
        return out

    # 4a data error
    D = 200
    ranks = np.empty((D, len(m)))
    for d in range(D):
        ranks[d] = score(resample(m), flags0, scored, mw_fa, out=ranked).rank(ascending=False).to_numpy()
    R = pd.DataFrame(ranks, columns=m.index).loc[:, ranked]
    width = R.quantile(0.95) - R.quantile(0.05)
    top_share = (R <= top_n).mean()
    base_top = base_rank[ranked] <= top_n
    u["data_error"] = {"draws": D, "median_interval_width": int(width.median()),
                       "p90_interval_width": int(width.quantile(0.9)),
                       "baseline_top10_kept_half": int((top_share[base_top] >= 0.5).sum()),
                       "pass": width.median() <= 200}
    print("data error:", u["data_error"])

    # 4b modeling choices (full factorial)
    combos, rhos = [], []
    for method, how, cut, stu, wname in itertools.product(["pct", "z", "minmax"], ["arith", "geo"],
                                                         [0.30, 0.40, 0.50], [0.20, 0.25, 0.30],
                                                         ["fa", "expert", "equal"]):
        rows_c = scored & (m["student_share"] < stu)
        mw = {"fa": mw_fa, "expert": mw_ex, "equal": np.full(len(KEYS), 50.0)}[wname]
        s = score(m, flags0 if cut == 0.40 else unreliable_flags(m, cut), scored, mw, method=method, how=how, out=rows_c)
        rho, k = spearman(base, s)
        combos.append({"normalization": method, "aggregation": how, "cv_cutoff": cut,
                       "student_threshold": stu, "weights": wname, "rho": round(rho, 4), "n_common": k})
    mc = pd.DataFrame(combos)
    mc.to_csv(VAL_DIR / "modeling_choices.csv", index=False)
    worst = mc.sort_values("rho").iloc[0]
    u["modeling_choices"] = {"combinations": len(mc), "min_rho": float(mc["rho"].min()),
                             "median_rho": float(mc["rho"].median()),
                             "worst": {k: (v_ if not isinstance(v_, np.generic) else v_.item())
                                       for k, v_ in worst.items()},
                             "by_choice": {col: mc.groupby(col)["rho"].mean().round(3).to_dict()
                                           for col in ["normalization", "aggregation", "cv_cutoff",
                                                       "student_threshold", "weights"]},
                             "pass": bool(mc["rho"].min() >= 0.85)}
    print("modeling choices:", u["modeling_choices"])

    # 4c combined Monte Carlo (student threshold fixed so every draw ranks the same tracts)
    C = 500
    ranks = np.empty((C, len(m)))
    flag_by_cut = {0.30: unreliable_flags(m, 0.30), 0.40: flags0, 0.50: unreliable_flags(m, 0.50)}
    for d in range(C):
        method = rng.choice(["pct", "z", "minmax"])
        how = rng.choice(["arith", "geo"])
        cut = float(rng.choice([0.30, 0.40, 0.50]))
        mw = {"fa": mw_fa, "expert": mw_ex, "equal": np.full(len(KEYS), 50.0)}[rng.choice(["fa", "expert", "equal"])]
        mw = mw * rng.uniform(0.5, 1.5, len(KEYS))
        gw = GW0 * rng.uniform(0.5, 1.5, len(GKEYS))
        ranks[d] = score(resample(m), flag_by_cut[cut], scored, mw, gw, method, how, out=ranked).rank(ascending=False).to_numpy()
    R = pd.DataFrame(ranks, columns=m.index).loc[:, ranked]
    ux = pd.DataFrame({"top10_share": (R <= top_n).mean(), "rank_p05": R.quantile(0.05),
                       "rank_median": R.median(), "rank_p95": R.quantile(0.95)})
    ux.round(3).to_csv(VAL_DIR / "tract_uncertainty.csv")
    u["combined"] = {"draws": C, "robust_top10": int((ux["top10_share"] >= 0.9).sum()),
                     "likely_top10": int(((ux["top10_share"] >= 0.5) & (ux["top10_share"] < 0.9)).sum()),
                     "median_interval_width": int((ux["rank_p95"] - ux["rank_p05"]).median())}
    print("combined:", u["combined"])

    # 4d data year: ACS 2019-2023 with the same method
    prefixes = tuple(f"1400000US13{cty}" for cty in ARC_COUNTIES)
    acs23 = {t: fetch_acs(t, 2023, prefixes, offline=False) for t in ACS_TABLES}
    m23, _ = build_metrics(acs23, fetch_acs("B25064", 2021, prefixes, offline=True))
    m23 = m23.reindex(m.index)
    m23 = m23.copy()
    m23["assisted_units"] = m["assisted_units"]
    add_supply_gap(m23)
    reliability(m23)
    s23 = score(m23, unreliable_flags(m23, 0.40), scored & m23["population"].notna(), mw_fa, out=ranked)
    rho, k = spearman(base, s23)
    r23 = s23.rank(ascending=False)
    u["data_year"] = {"rho": round(rho, 4), "n": k, "pass": rho >= 0.90,
                      "top10_overlap": int(((base_rank <= top_n) & (r23 <= top_n)).sum()),
                      "median_rank_change": int((base_rank - r23).abs().median())}
    print("data year:", u["data_year"])
    res["uncertainty"] = u

    # ======================= write ===========================================================
    res = json.loads(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    (VAL_DIR / "validation_results.json").write_text(json.dumps(res, indent=2))
    app["meta"]["validation"] = res
    for g in app["tracts"]:
        app["tracts"][g]["ux"] = (None if g not in ux.index or ux.loc[g].isna().any() else
                                  [round(float(ux.loc[g, "top10_share"]), 3), int(ux.loc[g, "rank_p05"]),
                                   int(ux.loc[g, "rank_median"]), int(ux.loc[g, "rank_p95"])])
    APP_DATA.write_text("window.NEED_DATA = " + json.dumps(app, separators=(",", ":")) + ";\n")
    print(f"wrote {VAL_DIR.relative_to(ROOT)}/ and updated {APP_DATA.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
