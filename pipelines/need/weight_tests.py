"""Credibility tests for the need-score weights, then a backtest of test-informed weights.

    python -m pipelines.need.build_tract_need
    python -m pipelines.need.backtest_frameworks     # writes backtest_tract_scores.csv (outcomes)
    python -m pipelines.need.weight_tests

Part A, four tests (group scores use the tool's factor-analysis metric weights):
1. Incremental value: drop one group at a time from the original weights and see whether prediction
   of 2023-2025 eviction filings gets worse (the group adds value) or better (it adds noise). Also the
   partial rank correlation of each group with evictions, controlling for the income group.
2. Nominal vs effective weights: each group's share of the composite's variance (its squared
   correlation with the composite, normalized; Paruolo, Saisana & Saltelli 2013), compared with the
   weight it's given. Uses the tool's current 2020-2024 data.
3. Discriminant validity and fairness. Discriminant: the score's correlation with things that aren't
   need (density, land area, population, distance to downtown) should stay well below its correlation
   with the CDC SVI. Fairness: data coverage, and how well the score tracks housing insecurity and
   evictions, by county, by majority-minority status (SVI 2022 EP_MINRTY) and by density tercile.
4. Best achievable: non-negative group weights fit to 2023-2025 evictions (rank scale, free intercept),
   tested by leaving one county out at a time.

Part B, backtest. Weight sets: original, HUD, calibrated (test 4 fit on all five counties), blended
(half calibrated, half original), and pruned (original minus any group whose removal significantly
improves eviction prediction in test 1). The only outcome used to choose weights is 2023-2025
evictions; the evaluation outcomes are the CDC SVI 2022, CDC PLACES housing insecurity, January-May
2026 evictions and rank stability across ACS years (2019-2023 vs 2020-2024, and 2017-2021 vs
2020-2024). Scores are built from 2017-2021 data for the outcome tests. Intervals come from a
spatial block bootstrap (60 clusters of neighboring tracts, 1,000 draws). Targets are those adopted
in docs/need_validation_plan.md on November 1, 2026.

Writes data/processed/need/validation/weight_tests.json.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear
from sklearn.cluster import KMeans

from pipelines.need.build_tract_need import (ACS_TABLES, APP_DATA, ARC_COUNTIES, GROUPS, OUT_CSV, add_supply_gap,
                                        build_metrics, fetch_acs, reliability)
from pipelines.need.validate_need import KEYS, SVI_CSV, VAL_DIR, aggregate, normalize, unreliable_flags

GK = [g for g, _ in GROUPS]
ORIGINAL = np.array([25, 25, 10, 10, 10, 5, 15], dtype=float)
HUD = np.array([25, 40, 5, 5, 0, 10, 15], dtype=float)
BOOT = 1000
rng = np.random.default_rng(20261101)
DOWNTOWN = (33.749, -84.388)


def rk(x) -> pd.Series:
    return pd.Series(x).rank(pct=True)


def rho(a, b) -> float:
    d = pd.concat([pd.Series(a), pd.Series(b)], axis=1).dropna()
    return float(d.iloc[:, 0].corr(d.iloc[:, 1], method="spearman"))


def group_scores(mm: pd.DataFrame, mw: np.ndarray, scored: pd.Series) -> pd.DataFrame:
    """The 7 group scores (0-1) per tract, normalizing each metric among `scored` tracts."""
    flags = unreliable_flags(mm, 0.40)
    X = normalize(mm, flags, scored, "pct")
    out = {}
    for i, g in enumerate(GK):
        oh = np.zeros(len(GK))
        oh[i] = 1
        out[g] = aggregate(X, mw, oh, "arith", "pct")
    return pd.DataFrame(out, index=mm.index)


def composite(G: pd.DataFrame, w: np.ndarray) -> pd.Series:
    """Weighted mean of available group scores; groups with no data are skipped and weights rescaled."""
    A = G.notna().to_numpy()
    V = np.where(A, G.to_numpy(), 0.0)
    num = (V * w).sum(axis=1)
    den = (A * w).sum(axis=1)
    return pd.Series(np.where(den > 0, num / np.where(den > 0, den, 1), np.nan), index=G.index)


def vintage(m: pd.DataFrame, year: int) -> pd.DataFrame:
    prefixes = tuple(f"1400000US13{c}" for c in ARC_COUNTIES)
    acs = {t: fetch_acs(t, year, prefixes, offline=True) for t in ACS_TABLES}
    mm, _ = build_metrics(acs, fetch_acs("B25064", 2021, prefixes, offline=True))
    mm = mm.reindex(m.index).copy()
    if year == 2021:
        mm["rent_growth"] = np.nan
    mm["assisted_units"] = m["assisted_units"]
    add_supply_gap(mm)
    reliability(mm)
    return mm


class Boot:
    """Spatial block bootstrap over clusters of neighboring tracts."""

    def __init__(self, ids: pd.Index, cluster: pd.Series):
        c = cluster.reindex(ids).to_numpy()
        self.groups = [np.flatnonzero(c == k) for k in np.unique(c)]
        self.draws = [np.concatenate([self.groups[i] for i in rng.integers(0, len(self.groups), len(self.groups))])
                      for _ in range(BOOT)]

    def ci(self, stat) -> tuple[float, float]:
        vals = np.array([stat(ix) for ix in self.draws])
        return float(np.nanpercentile(vals, 2.5)), float(np.nanpercentile(vals, 97.5))


def main() -> None:
    m = pd.read_csv(OUT_CSV, dtype={"tract_geoid": str}).set_index("tract_geoid")
    for c in ["low_population", "student_tract"]:
        m[c] = m[c].astype(str).eq("True")
    app = json.loads(APP_DATA.read_text()[len("window.NEED_DATA = "):-2])
    mw = np.array([x["weight"] for x in app["metrics"]], dtype=float)
    bt = pd.read_csv(VAL_DIR / "backtest_tract_scores.csv", dtype={"tract_geoid": str}).set_index("tract_geoid")
    svi = pd.read_csv(SVI_CSV, dtype={"FIPS": str}).set_index("FIPS")
    minority = svi["EP_MINRTY"].where(svi["EP_MINRTY"] >= 0).reindex(m.index)
    xy = np.c_[m["lat"], m["lon"] * np.cos(np.radians(33.75))]
    cluster = pd.Series(KMeans(60, n_init=10, random_state=0).fit_predict(xy), index=m.index)
    res = {}

    # ---- data vintages ----------------------------------------------------------------------
    m21, m23 = vintage(m, 2021), vintage(m, 2023)
    sc21 = (m21["population"].fillna(0) >= 500)
    rk21 = sc21 & (m21["student_share"] < 0.25)
    sc24 = ~m["low_population"] & m["population"].notna()
    rk24 = sc24 & ~m["student_tract"]
    G21 = group_scores(m21, mw, sc21).where(rk21, axis=0)
    G23 = group_scores(m23, mw, sc24 & m23["population"].notna()).where(rk24, axis=0)
    G24 = group_scores(m, mw, sc24).where(rk24, axis=0)

    ev = bt["out_evictions_2023_2025"].where(bt["renters"] >= 50)
    ev26 = bt["out_evictions_2026"].where(bt["renters"] >= 50)
    hous = bt["out_housing_insecurity"]
    svi_score = bt["score_cdc_svi"]

    # =============================== Part A =================================================
    # ---- test 1: incremental value -----------------------------------------------------------
    t1 = {}
    d1 = G21.join(ev.rename("y")).dropna(subset=["y"])
    d1 = d1[composite(d1[GK], ORIGINAL).notna()]
    b1 = Boot(d1.index, cluster)
    base = composite(d1[GK], ORIGINAL)
    for i, g in enumerate(GK):
        w = ORIGINAL.copy()
        w[i] = 0
        drop = composite(d1[GK], w)
        diff = rho(drop, d1["y"]) - rho(base, d1["y"])
        lo, hi = b1.ci(lambda ix: rho(drop.iloc[ix].values, d1["y"].iloc[ix].values)
                       - rho(base.iloc[ix].values, d1["y"].iloc[ix].values))
        # partial rank correlation with evictions, controlling for the income group
        partial = None
        if g != "income":
            dd = d1[[g, "income", "y"]].dropna()
            r_gy, r_gi, r_iy = rho(dd[g], dd["y"]), rho(dd[g], dd["income"]), rho(dd["income"], dd["y"])
            partial = (r_gy - r_gi * r_iy) / np.sqrt((1 - r_gi ** 2) * (1 - r_iy ** 2))
        t1[g] = {"rho_without": round(rho(drop, d1["y"]), 3), "change_when_dropped": round(diff, 3),
                 "ci": [round(lo, 3), round(hi, 3)],
                 "verdict": "adds value" if hi < 0 else ("adds noise" if lo > 0 else "no clear effect"),
                 "partial_rho_given_income": None if partial is None else round(float(partial), 3)}
    res["test1_incremental"] = {"outcome": "evictions 2023-2025", "tracts": len(d1),
                                "rho_full": round(rho(base, d1["y"]), 3), "groups": t1}
    print("TEST 1", json.dumps(res["test1_incremental"], indent=1))

    # ---- test 2: nominal vs effective weights (current data) ---------------------------------
    t2 = {}
    for name, w in [("original", ORIGINAL), ("hud", HUD)]:
        comp = composite(G24, w)
        S = np.array([G24[g].corr(comp) ** 2 if w[i] > 0 else 0.0 for i, g in enumerate(GK)])
        eff = S / S.sum()
        t2[name] = {g: {"nominal": round(float(w[i] / w.sum()), 3), "effective": round(float(eff[i]), 3),
                        "ratio": None if w[i] == 0 else round(float(eff[i] / (w[i] / w.sum())), 2)}
                    for i, g in enumerate(GK)}
    res["test2_effective_weights"] = t2
    print("TEST 2", json.dumps(t2, indent=1))

    # ---- test 3: discriminant validity and fairness --------------------------------------------
    comp24 = {"original": composite(G24, ORIGINAL), "hud": composite(G24, HUD)}
    dist = np.hypot(m["lat"] - DOWNTOWN[0], (m["lon"] - DOWNTOWN[1]) * np.cos(np.radians(33.75))) * 69.0
    other = {"population_density": m["population"] / m["land_sqmi"], "land_area": m["land_sqmi"],
             "population": m["population"], "miles_to_downtown": dist}
    svi24 = svi["RPL_THEMES"].where(svi["RPL_THEMES"] >= 0).reindex(m.index)
    t3d = {}
    for name, c in comp24.items():
        conv = rho(c, svi24)
        t3d[name] = {"convergent_svi": round(conv, 3),
                     **{k: round(rho(c, v), 3) for k, v in other.items()}}
        t3d[name]["pass"] = all(abs(t3d[name][k]) < conv for k in other)
    # fairness: coverage and accuracy by group (2021-based scores vs later outcomes; coverage on current data)
    flags24 = unreliable_flags(m, 0.40)
    gof = np.array([GK.index(x["group"]) for x in app["metrics"]])
    avail = (~flags24[KEYS]).to_numpy() * mw
    tot = np.array([mw[gof == i].sum() for i in range(len(GK))])
    cover = pd.Series(sum((HUD[i] / HUD.sum()) * avail[:, gof == i].sum(axis=1) / tot[i] for i in range(len(GK))
                          if tot[i] > 0), index=m.index)
    dens = other["population_density"]
    segs = {"county": m["county"],
            "majority_minority": np.where(minority >= 50, "majority-minority", np.where(minority.notna(), "majority-white", None)),
            "density": pd.qcut(dens.rank(method="first"), 3, labels=["low density", "mid density", "high density"])}
    s21 = composite(G21, ORIGINAL)
    t3f = {}
    for seg, lab in segs.items():
        lab = pd.Series(lab, index=m.index)
        rows = {}
        for v in sorted(lab.dropna().unique(), key=str):
            ix = (lab == v) & rk24
            ixb = (lab == v) & rk21
            top_out = hous >= hous[rk21].quantile(0.8)
            top_score = s21 >= s21[rk21].quantile(0.8)
            caught = (top_out & top_score & ixb).sum() / max(1, (top_out & ixb).sum())
            rows[str(v)] = {"tracts": int(ix.sum()), "data_coverage": round(float(cover[ix].mean()), 3),
                            "rho_housing_insecurity": round(rho(s21[ixb], hous[ixb]), 3),
                            "rho_evictions": round(rho(s21[ixb], ev[ixb]), 3) if ev[ixb].notna().sum() > 30 else None,
                            "share_of_high_hardship_tracts_flagged": round(float(caught), 3)}
        t3f[seg] = rows
    res["test3_discriminant"] = t3d
    res["test3_fairness"] = t3f
    print("TEST 3 discriminant", json.dumps(t3d, indent=1))
    print("TEST 3 fairness", json.dumps(t3f, indent=1))

    # ---- test 4: best achievable weights ------------------------------------------------------
    d4 = G21.join(ev.rename("y")).join(m["county"]).dropna()
    X = d4[GK].rank(pct=True).to_numpy()
    y = d4["y"].rank(pct=True).to_numpy()

    def fit(Xf, yf):
        A = np.c_[Xf, np.ones(len(Xf))]
        r = lsq_linear(A, yf, bounds=(np.r_[np.zeros(len(GK)), -np.inf], np.r_[np.full(len(GK), np.inf), np.inf]))
        w = r.x[:len(GK)]
        return 100 * w / w.sum()

    w_cal = fit(X, y)
    folds = []
    for cty in sorted(d4["county"].unique()):
        tr, te = d4["county"] != cty, d4["county"] == cty
        w = fit(X[tr.to_numpy()], y[tr.to_numpy()])
        folds.append({"county": cty, "weights": [round(float(x), 1) for x in w],
                      "rho_fitted": round(rho(composite(d4.loc[te, GK], w), d4.loc[te, "y"]), 3),
                      "rho_original": round(rho(composite(d4.loc[te, GK], ORIGINAL), d4.loc[te, "y"]), 3),
                      "rho_hud": round(rho(composite(d4.loc[te, GK], HUD), d4.loc[te, "y"]), 3)})
    res["test4_best_achievable"] = {
        "tracts": len(d4), "weights_all_counties": {g: round(float(x), 1) for g, x in zip(GK, w_cal)},
        "leave_one_county_out": folds,
        "mean_rho_fitted": round(float(np.mean([f["rho_fitted"] for f in folds])), 3),
        "mean_rho_original": round(float(np.mean([f["rho_original"] for f in folds])), 3),
        "mean_rho_hud": round(float(np.mean([f["rho_hud"] for f in folds])), 3)}
    print("TEST 4", json.dumps(res["test4_best_achievable"], indent=1))

    # =============================== Part B =================================================
    pruned = ORIGINAL.copy()
    for i, g in enumerate(GK):
        if t1[g]["verdict"] == "adds noise":
            pruned[i] = 0
    sets = {"original": ORIGINAL, "hud": HUD, "calibrated": w_cal,
            "blended": 0.5 * w_cal / w_cal.sum() * 100 + 0.5 * ORIGINAL / ORIGINAL.sum() * 100, "pruned": pruned}
    res["weight_sets"] = {k: {g: round(float(x), 1) for g, x in zip(GK, v / v.sum() * 100)} for k, v in sets.items()}
    income_only = np.array([1, 0, 0, 0, 0, 0, 0], dtype=float)

    evals = {"svi": svi_score, "housing_insecurity": hous, "evictions_2026": ev26}
    backtest = {}
    for oname, y in evals.items():
        d = G21.join(y.rename("y")).dropna(subset=["y"])
        d = d[composite(d[GK], ORIGINAL).notna()]
        b = Boot(d.index, cluster)
        comps = {k: composite(d[GK], v) for k, v in sets.items()}
        comps["income_only"] = composite(d[GK], income_only)
        out = {}
        for k, c in comps.items():
            r = rho(c, d["y"])
            lo, hi = b.ci(lambda ix: rho(c.iloc[ix].values, d["y"].iloc[ix].values))
            e = {"rho": round(r, 3), "ci": [round(lo, 3), round(hi, 3)]}
            if k != "original":
                dlo, dhi = b.ci(lambda ix: rho(c.iloc[ix].values, d["y"].iloc[ix].values)
                                - rho(comps["original"].iloc[ix].values, d["y"].iloc[ix].values))
                e["diff_vs_original"] = round(r - rho(comps["original"], d["y"]), 3)
                e["diff_ci"] = [round(dlo, 3), round(dhi, 3)]
            out[k] = e
        backtest[oname] = {"tracts": len(d), "results": out}
    stab = {}
    for k, v in sets.items():
        a, c, e = composite(G23, v), composite(G24, v), composite(G21, v)
        both = pd.concat([a, c], axis=1).dropna()
        b = Boot(both.index, cluster)
        r23 = rho(both.iloc[:, 0], both.iloc[:, 1])
        lo, hi = b.ci(lambda ix: rho(both.iloc[ix, 0].values, both.iloc[ix, 1].values))
        stab[k] = {"rho_2023_vs_2024": round(r23, 3), "ci": [round(lo, 3), round(hi, 3)],
                   "rho_2021_vs_2024": round(rho(e, c), 3)}
    backtest["stability"] = stab
    res["backtest"] = backtest
    print("WEIGHT SETS", json.dumps(res["weight_sets"], indent=1))
    print("BACKTEST", json.dumps(backtest, indent=1))
    res = json.loads(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    (VAL_DIR / "weight_tests.json").write_text(json.dumps(res, indent=2))
    # hand the results to the tool's Methodology tab ("How the weights changed")
    sig = VAL_DIR / "significance_frameworks.json"
    app["meta"]["weights_review"] = {"weight_tests": res,
                                     "significance": json.loads(sig.read_text()) if sig.exists() else None}
    APP_DATA.write_text("window.NEED_DATA = " + json.dumps(app, separators=(",", ":")) + ";\n")


if __name__ == "__main__":
    main()
