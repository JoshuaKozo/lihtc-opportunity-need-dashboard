"""Finalize the Need weights for the combined tool: research ranges, calibrated to later outcomes.

    python -m pipelines.combined.calibrate_need          # or: make combined-calibrate

Category weights use the Opportunity team's method (pipelines/opportunity/calibrate_weights.py), applied to
Need with the Need team's backtest design (pipelines/need/backtest_frameworks.py):

1. Research and federal need definitions set a starting weight and an allowed range for each category
   (RANGES below). The starting point is the Need team's backtested group weights.
2. The category scores are rebuilt from data that ends in 2021 (ACS 2017-2021, CHAS 2017-2021), and later
   outcomes move each weight inside its range: a regression of each outcome on the category scores with
   non-negative coefficients, averaged over the outcomes. One outcome per kind of hardship, each counted once:
   eviction filings 2023-2025 (eviction), the loss of low-income renters 2021-2024 (displacement), and CDC
   PLACES housing insecurity (adults' own reports of struggling to pay or fearing losing their home). The two
   CDC measures correlate 0.99, so only one votes, as the Need team's regression check recommends.
3. Final weight = half the data's suggestion + half the starting point, kept inside the ranges.
4. Every candidate weighting is graded on counties left out of the fit, on the fitted outcomes and on
   outcomes the fit never sees: eviction filings January-May 2026 (held out by the Need team), CDC PLACES
   utility shutoff threats (close to housing insecurity, so a weak check), and the CDC/ATSDR Social
   Vulnerability Index. Metro-wide
   differences from the starting point get 95% intervals from the Need team's spatial block bootstrap.

Weights inside each category: the Need team's factor analysis (each metric's share of the category's first
principal component, squared loadings, OECD/JRC), recomputed on the current data over the tool's tracts.

Needs the ACS 2017-2021 tables (cached in data/raw/acs/ by pipelines.need.build_tract_need.fetch_acs), the
CHAS 2017-2021 tract layer (data/raw/hud_chas_tract/), and the Need team's outcome table
(data/processed/need/validation/backtest_tract_scores.csv, local only: it holds Eviction Lab data).

Writes data/processed/combined/need_calibration.csv and need_weights.json, and prints the weights to put in
NEED in metric_set.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression

from pipelines.combined.metric_set import NEED
from pipelines.need.build_tract_need import (ACS_TABLES, ARC_COUNTIES, GROUP_WEIGHTS, add_supply_gap, build_metrics,
                                             fetch_acs, reliability)

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "combined"
CHAS_2021 = ROOT / "data" / "raw" / "hud_chas_tract" / "hud_chas_tract.csv"   # HUD's CHAS 2017-2021 tract layer
OUTCOMES = PROCESSED / "need" / "validation" / "backtest_tract_scores.csv"
CATS = [c for c, *_ in NEED]
BLEND = 0.5

# category -> (low, start, high). The start is the Need team's backtested group weights, summed into these
# categories (market 10 + quality 5; vulnerable 10 + work 10). The ranges, and why:
RANGES = {
    # Income is the gate in every federal need definition: HUD's worst case needs count renters under 50% of
    # area income; LIHTC serves households under 60% (or 80% with income averaging); CHAS reports by these
    # bands. Capped at 35% so the score stays a measure of housing need rather than of income alone.
    "income": (0.20, 0.25, 0.35),
    # HUD's Worst Case Housing Needs reports to Congress find severe rent burden the primary driver of worst
    # case needs; the Need team's HUD-based variant gave burden 40%.
    "rent_burden": (0.20, 0.25, 0.40),
    # Worst case needs counts only renters without housing assistance; NLIHC's The Gap reports about 27
    # affordable and available homes per 100 extremely low-income renters in metro Atlanta. Both of the Need
    # team's frameworks gave the supply gap 15%.
    "supply": (0.10, 0.15, 0.20),
    # Severely inadequate housing is a small share of worst case needs; renter share and rent pressure are
    # context for where need concentrates.
    "market": (0.05, 0.15, 0.15),
    # Job loss predicts eviction even after missed rent (Desmond & Gershenson 2017); the CDC/ATSDR SVI counts
    # unemployment, no vehicle and limited English. The Need team's HUD-based variant gave these 5%.
    "barriers": (0.05, 0.20, 0.20),
}
START = {c: RANGES[c][1] for c in CATS}
# one fitted outcome per kind of hardship: eviction, displacement, and hardship people report
FIT = {"evictions_2023_2025": "Eviction filings, 2023-2025", "low_income_renter_loss": "Low-income renters leaving, 2021-2024",
       "housing_insecurity": "Housing insecurity (CDC PLACES)"}
HELD_OUT = {"evictions_2026": "Eviction filings, Jan-May 2026", "utility_shutoff": "Utility shutoff threat (CDC PLACES)",
            "cdc_svi": "CDC Social Vulnerability Index"}
ALTERNATIVES = {
    "Need team (starting point)": START,
    "equal": {c: 0.2 for c in CATS},
    "HUD worst-case needs (Need team's variant)": {"income": .25, "rent_burden": .40, "supply": .15, "market": .15, "barriers": .05},
    "income only": {c: (1.0 if c == "income" else 1e-9) for c in CATS},
}


def within_weights(t: pd.DataFrame) -> dict:
    """The Need team's factor analysis inside each category, on the current data."""
    out = {}
    for c, label, _, _, ms in NEED:
        keys, dirs = [k for k, *_ in ms], {k: d for k, _, _, d, _ in ms}
        if len(keys) == 1:
            out[c] = {"label": label, "metrics": {keys[0]: {"weight": 100, "loading": 1.0, "flag": None}}}
            continue
        X = pd.DataFrame({k: (t[k] * dirs[k]).rank(pct=True) for k in keys}).fillna(0.5)
        Z = (X - X.mean()) / X.std()
        pca = PCA().fit(Z)
        load = pca.components_[0] * np.sqrt(pca.explained_variance_[0])
        load = load * np.sign(load.sum())
        strong = np.where(load >= 0.2, load ** 2, 0.0)
        out[c] = {"label": label, "pc1_share": round(float(pca.explained_variance_ratio_[0]), 3),
                  "metrics": {k: {"weight": max(1, round(float(s / strong.sum()) * 100)), "loading": round(float(l), 2),
                                  "flag": "weak link" if 0 <= l < 0.2 else ("against the category" if l < 0 else None)}
                              for k, l, s in zip(keys, load, strong)}}
    return out


def need_2021(current: pd.DataFrame) -> pd.DataFrame:
    """The tool's Need metrics rebuilt from data that ends in 2021, as the Need team's backtest does."""
    prefixes = tuple(f"1400000US13{c}" for c in ARC_COUNTIES)
    acs21 = {t: fetch_acs(t, 2021, prefixes, offline=True) for t in ACS_TABLES}
    m21, _ = build_metrics(acs21, acs21["B25064"])
    m21.index = m21.index.str[-11:]
    m21 = m21.reindex(current.index).copy()
    m21["rent_growth"] = np.nan                     # no earlier rent on 2020 tracts
    m21["assisted_units"] = current["assisted_units"]
    add_supply_gap(m21)
    reliability(m21)
    # CHAS 2017-2021 from HUD's tract layer (no margins of error there, so the Need team's rule: 25+ low-income renters)
    ch = pd.read_csv(CHAS_2021, dtype={"GEOID": str}).set_index("GEOID").reindex(current.index)
    li = ch[["T8_EST69", "T8_EST82", "T8_EST95"]].sum(axis=1)
    m21["chas_li_renter_burden"] = (ch[["T8_LE30_CB_R", "T8_GT30_LE50_CB_R", "T8_GT50_LE80_CB_R"]].sum(axis=1)
                                    / ch["T8_EST68"].where(ch["T8_EST68"] > 0)).clip(upper=1)
    m21["chas_li_renter_burden_unreliable"] = m21["chas_li_renter_burden"].isna() | (li < 25)
    return m21


def category_scores(m: pd.DataFrame, within: dict) -> pd.DataFrame:
    """Each category's weighted mean of its metrics' need percentiles (unreliable values skipped)."""
    out = {}
    for c, _, _, _, ms in NEED:
        num = pd.Series(0.0, index=m.index)
        den = pd.Series(0.0, index=m.index)
        for k, _, _, d, _ in ms:
            w = within[c]["metrics"][k]["weight"]
            flag = m[f"{k}_unreliable"].map(lambda x: True if pd.isna(x) else bool(x))
            p = (m[k].where(~flag) * d).rank(pct=True) * 100
            num += p.fillna(0) * w
            den += p.notna() * w
        out[c] = num / den.where(den > 0)
    return pd.DataFrame(out)


def composite(S: pd.DataFrame, w: dict) -> pd.Series:
    X = S[CATS].fillna(S[CATS].mean())
    return (X * np.array([w[c] for c in CATS])).sum(axis=1) / sum(w.values())


def data_weights(df: pd.DataFrame) -> dict:
    shares = []
    for y in FIT:
        d = df.dropna(subset=[y])
        X = d[CATS].fillna(d[CATS].mean()) / 100
        coef = LinearRegression(positive=True).fit(X, d[y]).coef_
        shares.append(coef / coef.sum() if coef.sum() > 0 else np.full(len(CATS), 1 / len(CATS)))
    avg = np.mean(shares, axis=0)
    return dict(zip(CATS, avg / avg.sum()))


def project(w: dict) -> dict:
    lo, hi = {c: RANGES[c][0] for c in CATS}, {c: RANGES[c][2] for c in CATS}
    w = {c: min(max(w[c], lo[c]), hi[c]) for c in CATS}
    for _ in range(50):
        gap = 1 - sum(w.values())
        if abs(gap) < 1e-9:
            break
        free = [c for c in CATS if (gap > 0 and w[c] < hi[c]) or (gap < 0 and w[c] > lo[c])]
        for c in free:
            w[c] = min(max(w[c] + gap / len(free), lo[c]), hi[c])
    return w


def blended(df: pd.DataFrame, blend: float = BLEND) -> dict:
    dw = data_weights(df)
    return project({c: blend * dw[c] + (1 - blend) * START[c] for c in CATS})


def held_out_counties(df: pd.DataFrame, weights_for) -> dict:
    """Mean within-county rank correlation, each county scored with weights fit on the others."""
    res = {}
    for y in {**FIT, **HELD_OUT}:
        rs, ns = [], []
        for county, test in df.groupby("county"):
            test = test.dropna(subset=[y])
            if len(test) < 20:
                continue
            c = composite(test, weights_for(df[df["county"] != county]))
            rs.append(c.rank().corr(test[y].rank()))
            ns.append(len(test))
        res[y] = round(float(np.average(rs, weights=ns)), 3)
    return res


def bootstrap_vs_start(df: pd.DataFrame, w: dict, draws: int = 2000) -> dict:
    """Metro-wide rank correlation of each outcome with the score (2021 data), and its difference from the
    starting point, with 95% intervals from the Need team's spatial block bootstrap (60 clusters)."""
    xy = np.c_[df["lat"], df["lon"] * np.cos(np.radians(33.75))]
    clusters = KMeans(60, n_init=10, random_state=0).fit_predict(xy)
    rng = np.random.default_rng(20261101)
    out = {}
    for y in {**FIT, **HELD_OUT}:
        ok = df[y].notna().to_numpy()
        a, b, yy, cl = composite(df, w).to_numpy()[ok], composite(df, START).to_numpy()[ok], df[y].to_numpy()[ok], clusters[ok]
        groups = [np.flatnonzero(cl == g) for g in np.unique(cl)]
        rk = lambda u, v: float(np.corrcoef(pd.Series(u).rank(), pd.Series(v).rank())[0, 1])
        diffs, rhos = np.empty(draws), np.empty(draws)
        for i in range(draws):
            idx = np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))])
            rhos[i] = rk(a[idx], yy[idx])
            diffs[i] = rhos[i] - rk(b[idx], yy[idx])
        out[y] = {"rho": round(rk(a, yy), 3), "ci": [round(float(x), 3) for x in np.percentile(rhos, [2.5, 97.5])],
                  "diff_vs_start": round(rk(a, yy) - rk(b, yy), 3), "diff_ci": [round(float(x), 3) for x in np.percentile(diffs, [2.5, 97.5])]}
    return out


def main() -> None:
    need = pd.read_csv(PROCESSED / "need" / "tract_need_metrics.csv", dtype={"tract_geoid": str})
    chas = pd.read_csv(OUT / "chas_renter_burden.csv", dtype={"tract_geoid": str})
    cur = need.merge(chas, on="tract_geoid", how="left")
    cur = cur[(cur["population"] > 0) & ~cur["student_tract"].astype(bool)].set_index("tract_geoid")
    within = within_weights(cur)

    if not OUTCOMES.exists():
        raise SystemExit("Needs backtest_tract_scores.csv (local only: it holds Eviction Lab data). Get Eviction Lab's "
                         "Atlanta file, then run python -m pipelines.need.backtest_frameworks.")
    o = pd.read_csv(OUTCOMES, dtype={"tract_geoid": str}).set_index("tract_geoid").rename(columns={"score_cdc_svi": "out_cdc_svi"})
    S = category_scores(need_2021(cur), within)
    df = S.join(cur[["county", "lat", "lon"]]).join(o[[f"out_{y}" for y in {**FIT, **HELD_OUT}] + ["renters"]])
    df.columns = [c.removeprefix("out_") for c in df.columns]
    for y in ("evictions_2023_2025", "evictions_2026"):
        df.loc[df["renters"] < 50, y] = np.nan          # the Need team's rule for stable eviction rates

    data = data_weights(df)
    final = {c: round(v, 3) for c, v in blended(df).items()}
    sets = {**{k: (lambda d, w=w: w) for k, w in ALTERNATIVES.items()},
            "data only": data_weights, "final (half data, half start, kept in ranges)": blended}
    rows = []
    for name, f in sets.items():
        w = f(df)
        rows.append({"weighting": name, **{f"w_{c}": round(w[c], 3) for c in CATS},
                     **{f"heldout_county_{y}": v for y, v in held_out_counties(df, f).items()}})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "need_calibration.csv", index=False)

    pct = {c: 100 * final[c] for c in CATS}   # whole percents that sum to 100 (largest remainder)
    whole = {c: int(np.floor(v)) for c, v in pct.items()}
    for c in sorted(CATS, key=lambda c: pct[c] - whole[c], reverse=True)[:100 - sum(whole.values())]:
        whole[c] += 1
    summary = {
        "method": "research ranges, calibrated to later outcomes, one per kind of hardship (Opportunity team's method, Need team's backtest design)",
        "tracts": int(len(df)), "fit_outcomes": FIT, "held_out_outcomes": HELD_OUT,
        "ranges": {c: {"low": RANGES[c][0], "start": RANGES[c][1], "high": RANGES[c][2]} for c in CATS},
        "group_weights_from_need_team": GROUP_WEIGHTS,
        "data_weights": {c: round(v, 3) for c, v in data.items()},
        "final_weights": final, "final_percent": whole,
        "weights_by_blend": {f"{b:.2f}": {c: round(v, 3) for c, v in blended(df, b).items()} for b in (0.25, 0.5, 0.75)},
        "held_out_counties": {r["weighting"]: {k.removeprefix("heldout_county_"): v for k, v in r.items() if k.startswith("heldout_")} for r in rows},
        "metro_vs_start": bootstrap_vs_start(df, final),
        "categories": {c: {**within[c], "weight": whole[c]} for c in CATS},
    }
    (OUT / "need_weights.json").write_text(json.dumps(summary, indent=2))
    pd.set_option("display.width", 250)
    print(f"{len(df)} tracts. Data-suggested weights:", summary["data_weights"])
    print(res.to_string(index=False))
    print("\nBy blend:", json.dumps(summary["weights_by_blend"]))
    print("Metro-wide vs starting point:", json.dumps(summary["metro_vs_start"], indent=1))
    print("\nFinal category weights for NEED in metric_set.py (percent):", whole)
    for c in CATS:
        print(f"  {within[c]['label']:<34} " + ", ".join(f"{k} {m['weight']}" for k, m in within[c]["metrics"].items()))


if __name__ == "__main__":
    main()
