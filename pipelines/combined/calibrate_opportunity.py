"""Finalize the Opportunity category weights for the combined tool, with the Opportunity team's method.

    python -m pipelines.combined.calibrate_opportunity          # or: make combined-calibrate

The method is pipelines/opportunity/calibrate_weights.py (docs/opportunity_weights.md), reused as is:
1. Published research sets a starting weight and an allowed range for each category (LITERATURE there).
2. Two tract outcomes move each weight inside its range: the upward mobility of children from low-income
   families (Opportunity Atlas) and life expectancy at birth, 2010-15 (CDC USALEEP). Each outcome is
   regressed on the category scores with non-negative coefficients; mobility is left out of the mobility
   model, since it contains that outcome.
3. Final weight = half the data's suggestion + half the research starting point, kept inside the ranges.
4. Every candidate weighting is graded on counties left out of the fit.

What changes here is only the input: the category scores are rebuilt from the combined tool's final
metric set (pipelines/combined/metric_set.py: adults without health insurance dropped, optional metrics
unweighted) over its 1,225 scored tracts (student-heavy tracts left out). The script also reports how the
weights move with the data/research blend and how stable the ranking is to the weights.

Writes data/processed/combined/opportunity_weight_calibration.csv and opportunity_weights.json, and prints
the weights to put in OPPORTUNITY in metric_set.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipelines.combined.metric_set import OPPORTUNITY
from pipelines.opportunity.build_opportunity import RAW, to_2020
from pipelines.opportunity.calibrate_weights import (DOMAINS, LITERATURE, OUTCOMES, TEST_OUTCOMES, adult_employment,
                                                     composite, data_weights, project, within_county_validity)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "combined"
# the Opportunity team's latest calibrated weights (data/processed/opportunity/weight_calibration.csv, final row)
PUBLISHED = {"mobility": 0.345, "schools": 0.271, "jobs": 0.106, "transit": 0.05, "health": 0.107, "environment": 0.121}


def load() -> pd.DataFrame:
    t = pd.read_csv(OUT / "combined_tract.csv", dtype={"tract_geoid": str}).rename(columns={"tract_geoid": "tract"})
    for c, _, _, _, ms in OPPORTUNITY:   # category score = weighted mean of the metrics' percentiles
        w = {k: wt for k, _, _, _, wt in ms if wt > 0}
        P = t[[f"pct_{k}" for k in w]]
        num = sum(P[f"pct_{k}"].fillna(0) * v for k, v in w.items())
        den = sum(P[f"pct_{k}"].notna() * v for k, v in w.items())
        t[f"score_{c}"] = num / den.where(den > 0)
    le = pd.read_csv(RAW / "usaleep_ga_2010_15.csv", dtype={"Tract ID": str})
    le = le.rename(columns={"Tract ID": "tract_2010", "e(0)": "life_exp"})[["tract_2010", "life_exp"]]
    t = t.merge(to_2020(le, "tract_2010"), on="tract", how="left")
    # the Opportunity team's test-only outcome: education-adjusted employment of adults without a degree
    t = t.merge(adult_employment(set(t["tract"])), on="tract", how="left")
    return t.rename(columns={"raw_oa_kfr_p25": "mobility_outcome"})


def blended(df: pd.DataFrame, blend: float) -> dict[str, float]:
    prior = {d: LITERATURE[d][1] for d in DOMAINS}
    data = data_weights(df)
    return project({d: blend * data[d] + (1 - blend) * prior[d] for d in DOMAINS})


def stability(df: pd.DataFrame, w: dict, draws: int = 500, seed: int = 0) -> dict:
    """How the ranking moves when every weight is scaled by a random factor in [0.5, 1.5]."""
    rng = np.random.default_rng(seed)
    base = composite(df, w).rank(pct=True)
    top = base >= 0.9
    rhos, stay = [], np.zeros(len(df))
    for _ in range(draws):
        r = composite(df, {d: v * rng.uniform(0.5, 1.5) for d, v in w.items()}).rank(pct=True)
        rhos.append(base.corr(r, method="spearman"))
        stay += (r >= 0.9).to_numpy()
    stay /= draws
    return {"median_rank_corr": round(float(np.median(rhos)), 3),
            "top10_kept_90pct": int((stay[top.to_numpy()] >= 0.9).sum()), "top10": int(top.sum())}


def main() -> None:
    df = load()
    prior = {d: LITERATURE[d][1] for d in DOMAINS}
    data = data_weights(df)
    final = {d: round(v, 3) for d, v in blended(df, 0.5).items()}
    sets = {
        "published (Opportunity team: 1,245 tracts, uninsured included)": lambda tr: PUBLISHED,
        "equal": lambda tr: {d: 1 / 6 for d in DOMAINS},
        "research starting point": lambda tr: prior,
        "data only": lambda tr: data_weights(tr),
        "final (half data, half research, kept in ranges)": lambda tr: blended(tr, 0.5),
    }
    rows = []
    for name, f in sets.items():
        v = within_county_validity(df, f)
        w = f(df)
        rows.append({"weighting": name, **{f"w_{d}": round(w[d], 3) for d in DOMAINS},
                     "validity_mobility": round(v["mobility_outcome"], 3), "validity_life_exp": round(v["life_exp"], 3),
                     "validity_adult_emp (test only)": round(v["adult_emp_adj"], 3)})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "opportunity_weight_calibration.csv", index=False)

    blends = {f"{b:.2f}": {d: round(v, 3) for d, v in blended(df, b).items()} for b in (0.25, 0.5, 0.75)}
    pct = {d: 100 * final[d] for d in DOMAINS}   # whole percents that sum to 100 (largest remainder)
    whole = {d: int(np.floor(v)) for d, v in pct.items()}
    for d in sorted(DOMAINS, key=lambda d: pct[d] - whole[d], reverse=True)[:100 - sum(whole.values())]:
        whole[d] += 1
    summary = {
        "tracts": int(len(df)), "life_expectancy_available": int(df["life_exp"].notna().sum()),
        "ranges": {d: {"low": LITERATURE[d][0], "start": LITERATURE[d][1], "high": LITERATURE[d][2]} for d in DOMAINS},
        "data_weights": {d: round(v, 3) for d, v in data.items()},
        "final_weights": final, "final_percent": whole,
        "weights_by_blend": blends,
        "validity_final": {"mobility": float(res.iloc[-1]["validity_mobility"]), "life_expectancy": float(res.iloc[-1]["validity_life_exp"]),
                           "adult_employment_test_only": float(res.iloc[-1]["validity_adult_emp (test only)"])},
        "stability_final": stability(df, final),
        "rank_corr_with_published": round(float(composite(df, final).corr(composite(df, PUBLISHED), method="spearman")), 3),
    }
    (OUT / "opportunity_weights.json").write_text(json.dumps(summary, indent=2))
    print(f"{len(df)} tracts; life expectancy for {summary['life_expectancy_available']}")
    print("Data-suggested weights:", summary["data_weights"])
    print(res.to_string(index=False))
    print("\nBy blend:", json.dumps(blends))
    print("Stability:", summary["stability_final"], "| rank corr with published:", summary["rank_corr_with_published"])
    print("\nFinal weights for OPPORTUNITY in metric_set.py (percent):", whole)


if __name__ == "__main__":
    main()
