"""Does swapping the Need score's low-income renter burden from the ACS to HUD CHAS make it better or worse?

    python -m pipelines.combined.test_chas_swap          # or: make combined-test-chas

The combined tool replaced the Need team's "renters under $35k paying 30%+" (ACS) with "renters under 80%
of area income paying 30%+" (HUD CHAS), because the CHAS version passes the reliability test in more tracts.
The Need team's backtests found CHAS-based versions of the whole score less accurate, but those replaced
several metrics at once. This tests the one swap the combined tool makes, with the Need team's outcomes
and significance method (pipelines/need/significance_frameworks.py: 60 spatial clusters, 2,000 block
bootstrap draws, the same seed).

Versions of the combined Need score, identical except for the first rent-burden metric:
  acs    renters under $35k paying 30%+ (ACS B25106), the Need team's metric
  chas   renters under 80% of area income paying 30%+ (CHAS Table 8), the swap
  none   neither: rent burden from all-renter burden alone (reference)

Outcomes (from data/processed/need/validation/backtest_tract_scores.csv): eviction filings per 100 renter
households, 2023-2025 and January-May 2026 (held out); CDC PLACES housing insecurity and utility shutoff
threats; the CDC Social Vulnerability Index. Eviction rates use tracts with 50+ renter households.

That table holds tract-level Eviction Lab data, so it isn't in git: get Eviction Lab's Atlanta file (see
pipelines/need/validate_need.py) and run `python -m pipelines.need.backtest_frameworks` to write it. The
committed results (chas_swap_test.csv/.json) are summaries only.

Writes data/processed/combined/chas_swap_test.csv and .json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from pipelines.combined.metric_set import NEED

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "combined"
N_CLUSTERS, BOOT = 60, 2000
OUTCOMES = {"evictions_2023_2025": "Eviction filings, 2023-2025", "evictions_2026": "Eviction filings, Jan-May 2026 (held out)",
            "housing_insecurity": "Housing insecurity (CDC PLACES)", "utility_shutoff": "Utility shutoff threat (CDC PLACES)",
            "cdc_svi": "CDC Social Vulnerability Index"}
VARIANTS = {"acs": "low_income_renter_burden", "chas": "chas_li_renter_burden", "none": None}


def rank_pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100


def need_score(t: pd.DataFrame, swap: str | None) -> pd.Series:
    """The combined tool's Need score (build_combined.py), with the first rent-burden metric set to `swap`."""
    num = pd.Series(0.0, index=t.index)
    den = pd.Series(0.0, index=t.index)
    for c, _, cw, _, ms in NEED:
        cnum = pd.Series(0.0, index=t.index)
        cden = pd.Series(0.0, index=t.index)
        for k, _, _, direction, w in ms:
            if k == "chas_li_renter_burden":
                if swap is None:
                    continue
                k = swap
            v = t[k].where(~t[f"{k}_unreliable"].map(lambda x: True if pd.isna(x) else bool(x)))
            p = rank_pct(v * direction)
            cnum += p.fillna(0) * w
            cden += p.notna() * w
        cat = cnum / cden.where(cden > 0)
        num += cat.fillna(0) * cw
        den += cat.notna() * cw
    return rank_pct(num / den.where(den > 0))


def rank_corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy())[0, 1])


def main() -> None:
    need = pd.read_csv(PROCESSED / "need" / "tract_need_metrics.csv", dtype={"tract_geoid": str})
    chas = pd.read_csv(OUT / "chas_renter_burden.csv", dtype={"tract_geoid": str})
    outcomes = PROCESSED / "need" / "validation" / "backtest_tract_scores.csv"
    if not outcomes.exists():
        raise SystemExit("Needs backtest_tract_scores.csv (local only: it holds Eviction Lab data). Get Eviction Lab's "
                         "Atlanta file, then run python -m pipelines.need.backtest_frameworks.")
    out = pd.read_csv(outcomes, dtype={"tract_geoid": str})
    out = out.rename(columns={"score_cdc_svi": "out_cdc_svi"})
    t = need.merge(chas, on="tract_geoid", how="left")
    t = t[(t["population"] > 0) & ~t["student_tract"].astype(bool)].set_index("tract_geoid")
    scores = pd.DataFrame({v: need_score(t, k) for v, k in VARIANTS.items()})
    d0 = scores.join(out.set_index("tract_geoid")[[f"out_{o}" for o in OUTCOMES] + ["renters", "lat", "lon"]], how="inner")
    xy = np.c_[d0["lat"], d0["lon"] * np.cos(np.radians(33.75))]
    d0["cluster"] = KMeans(N_CLUSTERS, n_init=10, random_state=0).fit_predict(xy)
    rng = np.random.default_rng(20261101)

    rows = []
    for o, label in OUTCOMES.items():
        d = d0.dropna(subset=[f"out_{o}"])
        if o.startswith("evictions"):
            d = d[d["renters"] >= 50]
        y = d[f"out_{o}"].to_numpy()
        S = {v: d[v].to_numpy() for v in VARIANTS}
        groups = [np.flatnonzero(d["cluster"].to_numpy() == c) for c in np.unique(d["cluster"])]
        boot = {v: np.empty(BOOT) for v in VARIANTS}
        for b in range(BOOT):
            idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
            for v in VARIANTS:
                boot[v][b] = rank_corr(S[v][idx], y[idx])
        base = rank_corr(S["acs"], y)
        for v in VARIANTS:
            r = rank_corr(S[v], y)
            lo, hi = np.percentile(boot[v], [2.5, 97.5])
            row = {"outcome": o, "label": label, "version": v, "tracts": len(d), "rho": round(r, 3),
                   "ci_lo": round(float(lo), 3), "ci_hi": round(float(hi), 3)}
            if v != "acs":
                dlo, dhi = np.percentile(boot[v] - boot["acs"], [2.5, 97.5])
                row.update({"diff_vs_acs": round(r - base, 3), "diff_ci_lo": round(float(dlo), 3),
                            "diff_ci_hi": round(float(dhi), 3), "diff_significant": bool(dlo > 0 or dhi < 0)})
            rows.append(row)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "chas_swap_test.csv", index=False)
    usable = {v: round(float(t[k + "_unreliable"].map(lambda x: True if pd.isna(x) else bool(x)).eq(False).mean()), 3)
              for v, k in VARIANTS.items() if k}
    summary = {"tracts": int(len(t)), "usable_share": usable,
               "rank_corr_acs_vs_chas": round(float(scores["acs"].corr(scores["chas"], method="spearman")), 3),
               "results": rows}
    (OUT / "chas_swap_test.json").write_text(json.dumps(summary, indent=2))
    pd.set_option("display.width", 220)
    print(f"{len(t)} tracts; usable: {usable}; ACS vs CHAS version rank corr {summary['rank_corr_acs_vs_chas']}")
    print(res.drop(columns=["label"]).to_string(index=False))


if __name__ == "__main__":
    main()
