"""Are the backtest results statistically significant, once neighboring tracts are accounted for?

    python -m pipelines.need.backtest_frameworks      # first: writes backtest_tract_scores.csv
    python -m pipelines.need.significance_frameworks

Neighboring tracts are alike, so treating 1,000+ tracts as independent makes p-values far too small.
This uses a spatial block bootstrap: tracts are grouped into 60 contiguous clusters (k-means on their
coordinates) and whole clusters are resampled, 2,000 times. For each framework and outcome it reports
the rank correlation with its 95% interval and bootstrap p-value, the eviction lift with its interval,
whether the interval clears the targets in docs/need_validation_plan.md, and the difference from the
original weights.

Writes data/processed/need/validation/significance_frameworks.json and .csv.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.cluster import KMeans

from pipelines.need.validate_need import VAL_DIR

N_CLUSTERS, BOOT = 60, 2000
FRAMEWORKS = ["original", "hud", "hud_chas"]
BENCHMARKS = ["equal", "income_only", "cdc_svi"]
# outcome -> (minimum, strong) correlation targets from docs/need_validation_plan.md
TARGETS = {"evictions_2023_2025": (0.30, 0.40), "evictions_2026": (0.30, 0.40),
           "housing_insecurity": (0.50, 0.70), "utility_shutoff": (0.50, 0.70),
           "low_income_renter_loss": (0.30, 0.40), "combined_dedup": (None, None)}
LIFT_TARGETS = (1.5, 2.0)
rng = np.random.default_rng(20261101)


def rank_corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy())[0, 1])


def lift(score, num, den) -> float:
    top = score >= np.quantile(score, 0.9)
    return float((num[top].sum() / den[top].sum()) / (num.sum() / den.sum()))


def main() -> None:
    t = pd.read_csv(VAL_DIR / "backtest_tract_scores.csv", dtype={"tract_geoid": str}).set_index("tract_geoid")
    t = t[t["ranked"].astype(str).eq("True")]
    xy = np.c_[t["lat"], t["lon"] * np.cos(np.radians(33.75))]
    t["cluster"] = KMeans(N_CLUSTERS, n_init=10, random_state=0).fit_predict(xy)

    rows = []
    for out, (tmin, tstrong) in TARGETS.items():
        cols = [f"score_{f}" for f in FRAMEWORKS + BENCHMARKS] + [f"out_{out}"]
        d = t.dropna(subset=cols)
        if out.startswith("evictions"):
            d = d[d["renters"] >= 50]
        clusters = d["cluster"].to_numpy()
        groups = [np.flatnonzero(clusters == c) for c in np.unique(clusters)]
        y = d[f"out_{out}"].to_numpy()
        S = {f: d[f"score_{f}"].to_numpy() for f in FRAMEWORKS + BENCHMARKS}
        ev = out.startswith("evictions")
        num = d["ev25_per_year" if out == "evictions_2023_2025" else "ev26_per_year"].to_numpy() if ev else None
        den = d["renters"].to_numpy() if ev else None

        boot_r = {f: np.empty(BOOT) for f in S}
        boot_l = {f: np.empty(BOOT) for f in S}
        for b in range(BOOT):
            idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
            for f in S:
                boot_r[f][b] = rank_corr(S[f][idx], y[idx])
                if ev:
                    boot_l[f][b] = lift(S[f][idx], num[idx], den[idx])
        for f in S:
            rho, p_naive = spearmanr(S[f], y)
            br = boot_r[f]
            p_boot = min(1.0, 2 * min((br <= 0).mean(), (br >= 0).mean()))
            lo, hi = np.percentile(br, [2.5, 97.5])
            r = {"outcome": out, "framework": f, "tracts": len(d), "clusters": len(groups),
                 "rho": round(float(rho), 3), "ci_lo": round(float(lo), 3), "ci_hi": round(float(hi), 3),
                 "p_naive": float(p_naive), "p_spatial": p_boot if p_boot > 0 else f"< {1 / BOOT}",
                 "significant": bool(lo > 0)}
            if tmin is not None:
                r["meets_min"] = bool(lo >= tmin)
                r["meets_strong"] = bool(lo >= tstrong)
            if ev:
                L = lift(S[f], num, den)
                llo, lhi = np.percentile(boot_l[f], [2.5, 97.5])
                r.update({"lift": round(L, 2), "lift_lo": round(float(llo), 2), "lift_hi": round(float(lhi), 2),
                          "lift_meets_min": bool(llo >= LIFT_TARGETS[0])})
            if f != "original":
                dd = br - boot_r["original"]
                r["diff_vs_original"] = round(float(rho - spearmanr(S["original"], y)[0]), 3)
                r["diff_ci"] = [round(float(x), 3) for x in np.percentile(dd, [2.5, 97.5])]
                r["diff_significant"] = bool(r["diff_ci"][0] > 0 or r["diff_ci"][1] < 0)
            rows.append(r)

    out = pd.DataFrame(rows)
    out.to_csv(VAL_DIR / "significance_frameworks.csv", index=False)
    (VAL_DIR / "significance_frameworks.json").write_text(json.dumps(rows, indent=2, default=str))
    pd.set_option("display.width", 250)
    show = ["outcome", "framework", "tracts", "rho", "ci_lo", "ci_hi", "p_spatial", "significant",
            "meets_min", "meets_strong", "lift", "lift_lo", "lift_hi", "diff_vs_original", "diff_ci"]
    print(out[[c for c in show if c in out]].to_string(index=False))


if __name__ == "__main__":
    main()
