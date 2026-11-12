"""Score the 11-county ARC with SiteLine on a 0.25-mile grid and roll the cells up to census tracts.

    python pipelines/fundability/score_grid_tracts.py [--siteline ../SitelineLIHTCStartUp-main] [--cell-mi 0.25]

SiteLine is used exactly as provided (no changes to its rules or data). Outputs, in data/processed/fundability/:
  siteline_grid_arc.parquet     one row per grid cell: location, tract, every section score
  siteline_tract_scores.csv     one row per tract: best / median / share-of-cells-above-median per
                                section and for the total (see column names below)
Takes about two minutes. Uses the point-in-time awards only if you ask SiteLine for a round year.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "fundability"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--siteline", default=str(ROOT.parent / "SitelineLIHTCStartUp-main"))
    ap.add_argument("--cell-mi", type=float, default=0.25)
    args = ap.parse_args()
    sys.path.insert(0, args.siteline)
    import siteline                                               # noqa: E402

    grid = siteline.score_grid("arc", cell_mi=args.cell_mi, progress=False)
    OUT.mkdir(parents=True, exist_ok=True)
    grid.to_parquet(OUT / "siteline_grid_arc.parquet")
    print(f"grid: {len(grid):,} cells; columns: {list(grid.columns)}")

    score_cols = [c for c in grid.columns if c.startswith("score_")] + ["total_score"]
    g = grid[grid["tract_geoid"].notna()]
    agg = {c: ["max", "median"] for c in score_cols}
    t = g.groupby("tract_geoid").agg(agg)
    t.columns = [f"{c}_{stat}" for c, stat in t.columns]
    t["n_cells"] = g.groupby("tract_geoid").size()
    # share of the tract's cells scoring at least 30/46 (a rough "viable site" cutoff, to revisit)
    t["share_cells_ge30"] = g.assign(ok=g["total_score"] >= 30).groupby("tract_geoid")["ok"].mean()
    t.reset_index().rename(columns={"tract_geoid": "geoid"}).to_csv(OUT / "siteline_tract_scores.csv",
                                                                     index=False)
    print(f"tracts: {len(t):,}")


if __name__ == "__main__":
    main()
