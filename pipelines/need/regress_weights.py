"""Regress later outcomes on the seven group scores to check the tool's group weights.

    python -m pipelines.need.backtest_frameworks   # writes backtest_tract_scores.csv (outcomes)
    python -m pipelines.need.regress_weights

Predictors: the seven group scores built from 2017-2021 data (as in the backtests), so every outcome
comes after the data. For the regression every value is used, including ones the tool would skip as
unreliable, so no tract drops out for a single noisy metric; noise then weakens a group's coefficient
rather than removing tracts. Predictors and outcomes are converted to ranks and standardized, so a
coefficient reads as "standard deviations of outcome rank per standard deviation of group rank".

For each outcome:
- OLS coefficients with standard errors clustered on 60 spatial clusters of neighboring tracts
  (neighbors aren't independent), and variance inflation factors (groups overlap, income most).
- Shapley R-squared decomposition (each group's average added R-squared over all 128 orders of entry),
  normalized to shares. Unlike coefficients, shares are never negative and add to 100%, so they can be
  set beside the tool's weights. 95% intervals from a spatial block bootstrap (300 draws).

Outcomes: eviction filings 2023-2025 and January-May 2026, CDC PLACES housing insecurity and utility
shutoff threat (modeled partly from income, so they favor the income group), low-income renter loss,
and the combined outcome with the two CDC measures counted once.

Also checks how much the rounded group weights matter: every weight is moved by up to +/-2.5 points
500 times, and the ranking is compared with the default (current 2020-2024 data).

Writes data/processed/need/validation/regress_weights.json and adds the results to need_site/data.js
(meta.regression) for the Methodology tab.
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.cluster import KMeans
from statsmodels.stats.outliers_influence import variance_inflation_factor

from pipelines.need.build_tract_need import APP_DATA, GROUPS, OUT_CSV
from pipelines.need.validate_need import KEYS, VAL_DIR, aggregate, normalize
from pipelines.need.weight_tests import ORIGINAL, composite, group_scores, vintage

GK = [g for g, _ in GROUPS]
LABELS = dict(GROUPS)
OUTCOMES = {"evictions_2023_2025": "Evictions 2023-25", "evictions_2026": "Evictions 2026",
            "housing_insecurity": "Housing insecurity", "utility_shutoff": "Utility shutoff threat",
            "low_income_renter_loss": "Loss of low-income renters", "combined_dedup": "Combined outcome"}
BOOT = 300
rng = np.random.default_rng(20261101)


def r2(X: np.ndarray, y: np.ndarray) -> float:
    if X.shape[1] == 0:
        return 0.0
    A = np.c_[np.ones(len(X)), X]
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    return 1 - resid.var() / y.var()


def shapley_shares(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Each predictor's average marginal R-squared over all subsets (exact Shapley values)."""
    k = X.shape[1]
    cache = {}
    for size in range(k + 1):
        for sub in itertools.combinations(range(k), size):
            cache[sub] = r2(X[:, list(sub)], y)
    from math import factorial
    phi = np.zeros(k)
    for j in range(k):
        for sub, v in cache.items():
            if j in sub:
                continue
            with_j = tuple(sorted(sub + (j,)))
            w = factorial(len(sub)) * factorial(k - len(sub) - 1) / factorial(k)
            phi[j] += w * (cache[with_j] - v)
    return phi


def zr(s: pd.Series) -> pd.Series:
    r = s.rank()
    return (r - r.mean()) / r.std()


def main() -> None:
    m = pd.read_csv(OUT_CSV, dtype={"tract_geoid": str}).set_index("tract_geoid")
    app = json.loads(APP_DATA.read_text()[len("window.NEED_DATA = "):-2])
    mw = np.array([x["weight"] for x in app["metrics"]], dtype=float)
    bt = pd.read_csv(VAL_DIR / "backtest_tract_scores.csv", dtype={"tract_geoid": str}).set_index("tract_geoid")

    m21 = vintage(m, 2021)
    scored = m21["population"].fillna(0) >= 500
    ranked = scored & (m21["student_share"] < 0.25)
    no_flags = pd.DataFrame(False, index=m21.index, columns=KEYS)       # use every value
    X = normalize(m21, no_flags, scored, "pct")
    G = pd.DataFrame({g: aggregate(X, mw, np.eye(len(GK))[i], "arith", "pct") for i, g in enumerate(GK)},
                     index=m21.index).where(ranked, axis=0)
    xy = np.c_[m["lat"], m["lon"] * np.cos(np.radians(33.75))]
    cluster = pd.Series(KMeans(60, n_init=10, random_state=0).fit_predict(xy), index=m.index)

    tool_share = ORIGINAL / ORIGINAL.sum()
    res = {"tool_weights": {g: round(float(w), 3) for g, w in zip(GK, tool_share)}, "outcomes": {}}
    for out, label in OUTCOMES.items():
        y = bt[f"out_{out}"]
        if out.startswith("evictions"):
            y = y.where(bt["renters"] >= 50)
        d = G.join(y.rename("y")).dropna()
        Z = d[GK].apply(zr)
        yz = zr(d["y"])
        cl = cluster.reindex(d.index).to_numpy()

        ols = sm.OLS(yz, sm.add_constant(Z)).fit(cov_type="cluster", cov_kwds={"groups": cl})
        vif = [variance_inflation_factor(sm.add_constant(Z).to_numpy(), i + 1) for i in range(len(GK))]
        phi = shapley_shares(Z.to_numpy(), yz.to_numpy())
        share = phi / phi.sum()

        groups_ix = [np.flatnonzero(cl == c) for c in np.unique(cl)]
        boots = np.empty((BOOT, len(GK)))
        Zn, yn = Z.to_numpy(), yz.to_numpy()
        for b in range(BOOT):
            ix = np.concatenate([groups_ix[i] for i in rng.integers(0, len(groups_ix), len(groups_ix))])
            p = shapley_shares(Zn[ix], yn[ix])
            boots[b] = p / p.sum()
        lo, hi = np.percentile(boots, [2.5, 97.5], axis=0)

        res["outcomes"][out] = {
            "label": label, "tracts": len(d), "r2": round(float(ols.rsquared), 3),
            "groups": {g: {"beta": round(float(ols.params[g]), 3), "se": round(float(ols.bse[g]), 3),
                           "p": float(ols.pvalues[g]), "vif": round(float(vif[i]), 2),
                           "shapley_share": round(float(share[i]), 3),
                           "share_ci": [round(float(lo[i]), 3), round(float(hi[i]), 3)],
                           "tool_weight": round(float(tool_share[i]), 3),
                           "tool_weight_in_ci": bool(lo[i] <= tool_share[i] <= hi[i])}
                       for i, g in enumerate(GK)}}
        print(f"\n== {label}: {len(d)} tracts, R2 {ols.rsquared:.3f}")
        print(f"{'group':12s} {'beta':>7s} {'p':>8s} {'VIF':>5s} {'share':>6s} {'95% CI':>14s} {'tool':>5s}")
        for i, g in enumerate(GK):
            e = res["outcomes"][out]["groups"][g]
            print(f"{g:12s} {e['beta']:7.3f} {e['p']:8.4f} {e['vif']:5.1f} {e['shapley_share']:6.2f} "
                  f"[{e['share_ci'][0]:.2f}, {e['share_ci'][1]:.2f}] {e['tool_weight']:5.2f}")

    # average implied shares, with and without the CDC outcomes that are modeled from income
    for name, outs in [("all_outcomes", list(OUTCOMES)),
                       ("without_cdc_modeled", ["evictions_2023_2025", "evictions_2026", "low_income_renter_loss"])]:
        avg = np.mean([[res["outcomes"][o]["groups"][g]["shapley_share"] for g in GK] for o in outs], axis=0)
        res[f"implied_{name}"] = {g: round(float(a), 3) for g, a in zip(GK, avg)}
        print(f"\nimplied shares ({name}):", res[f"implied_{name}"])
    print("tool weights:", res["tool_weights"])

    # are the round numbers doing any work? jitter every group weight by up to +/-2.5 points
    for c in ["low_population", "student_tract"]:
        m[c] = m[c].astype(str).eq("True")
    sc24 = ~m["low_population"] & m["population"].notna()
    G24 = group_scores(m, mw, sc24).where(sc24 & ~m["student_tract"], axis=0)
    base = composite(G24, ORIGINAL)
    n_top = int(round(0.1 * base.notna().sum()))
    top = base.rank(ascending=False) <= n_top
    rhos, keep = [], []
    for _ in range(500):
        alt = composite(G24, ORIGINAL + rng.uniform(-2.5, 2.5, len(GK)))
        rhos.append(base.corr(alt, method="spearman"))
        keep.append(int(((alt.rank(ascending=False) <= n_top) & top).sum()))
    implied = np.array([res["implied_without_cdc_modeled"][g] for g in GK]) * 100
    alt = composite(G24, implied)
    res["rounding"] = {"draws": 500, "jitter_points": 2.5, "rho_median": round(float(np.median(rhos)), 4),
                       "rho_min": round(float(np.min(rhos)), 4), "top10": n_top,
                       "top10_kept_median": int(np.median(keep)), "top10_kept_min": int(np.min(keep)),
                       "implied_weights_rho": round(float(base.corr(alt, method="spearman")), 3),
                       "implied_weights_top10_kept": int(((alt.rank(ascending=False) <= n_top) & top).sum())}
    print("rounding:", res["rounding"])
    (VAL_DIR / "regress_weights.json").write_text(json.dumps(res, indent=2))
    app["meta"]["regression"] = res
    APP_DATA.write_text("window.NEED_DATA = " + json.dumps(app, separators=(",", ":")) + ";\n")


if __name__ == "__main__":
    main()
