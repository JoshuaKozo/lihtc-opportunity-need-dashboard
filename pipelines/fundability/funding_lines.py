"""Funding lines from DCA's 9% lists: roughly what total score it took to be funded, by round and pool.

    python pipelines/fundability/funding_lines.py

For each round and geographic pool (statewide applications, from applications_9pct.parquet):
  line           highest DCA score among applications turned down for score or threshold reasons;
                 a score above it would have been funded in the general competition
  funded_median  median DCA score of funded applications
  funded_min     lowest funded score (often a set-aside winner, so below the line)
Also, for funded ARC applications scored by SiteLine, the "deal points" they earned: DCA score minus
SiteLine's location points (point-in-time), i.e. everything that wasn't location.

Only the 2024 and 2025 rounds use the 2024-2025 QAP (scores ran about 10 points higher than in
2023), so the website compares against those. Writes fundability_site/data/funding_lines.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
CURRENT_QAP_ROUNDS = (2024, 2025)


def main() -> None:
    apps = pd.read_parquet(PROC / "applications_9pct.parquet")
    apps = apps[apps["pool"].isin(["Atlanta Metro", "Other Metro", "Rural"]) & apps["dca_score"].gt(0)]
    lines = []
    for (yr, pool), g in apps[apps["round_year"] >= 2019].groupby(["round_year", "pool"]):
        funded = g[g["outcome"] == "selected"]["dca_score"]
        lost = g[(g["outcome"] == "not_selected") & (g["nonselect_reason"] == "score_or_threshold")]["dca_score"]
        lines.append({"round": int(yr), "pool": pool, "applications": int(len(g)), "funded": int(len(funded)),
                      "line": float(lost.max()) if len(lost) else None,
                      "funded_median": float(funded.median()) if len(funded) else None,
                      "funded_min": float(funded.min()) if len(funded) else None})

    scored = pd.read_parquet(PROC / "applications_9pct_scored.parquet")
    s = scored[scored["round_year"].isin(CURRENT_QAP_ROUNDS) & (scored["outcome"] == "selected")
               & scored["sl_total_pit"].notna() & scored["dca_score"].gt(0)].copy()
    s["deal_points"] = s["dca_score"] - s["sl_total_pit"]
    deal = {"rounds": list(CURRENT_QAP_ROUNDS), "funded_arc_applications": int(len(s)),
            "median": float(s["deal_points"].median()), "p25": float(s["deal_points"].quantile(.25)),
            "p75": float(s["deal_points"].quantile(.75)),
            "median_location": float(s["sl_total_pit"].median()), "median_total": float(s["dca_score"].median())}

    out = {"current_qap_rounds": list(CURRENT_QAP_ROUNDS), "lines": lines, "deal_points_of_funded_arc": deal,
           "note": "line = highest DCA score turned down for score/threshold reasons in that round and pool. "
                   "Set-aside winners (nonprofit, preservation, USDA) can be funded below it."}
    (ROOT / "fundability_site" / "data" / "funding_lines.json").write_text(json.dumps(out, indent=1))
    cur = pd.DataFrame([l for l in lines if l["round"] in CURRENT_QAP_ROUNDS])
    print(cur.to_string(index=False))
    print("deal points of funded ARC applications, 2024-2025:", {k: round(v, 1) if isinstance(v, float) else v for k, v in deal.items()})


if __name__ == "__main__":
    main()
