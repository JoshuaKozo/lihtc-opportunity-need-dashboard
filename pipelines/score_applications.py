"""Score every ARC application's location with SiteLine, as of its own round (feeds Fundability).

    python -m pipelines.score_applications

Input:  data/processed/applications_9pct.parquet (from parse_applications.py)
Output: data/processed/applications_9pct_scored.parquet: the input plus `sl_*` columns

Two adjustments make SiteLine usable on past applications:

1. Point-in-time §XVIII. SiteLine counts every award in lihtc_awards.csv with no upper year
   bound, so a past application would be penalized for its own award and for later ones. We
   recompute §XVIII (rules in SiteLine's SCORING.md §8) using only awards made before the
   application's round, and adjust the total to match.
2. Tenancy. Senior/HFOP applications score §XIII under "other tenancy" (max 2, not 3).

Caveat: the other layers (amenities, transit, schools, tract indicators) are SiteLine's 2026
data, and the rules are the 2024–25 QAP. For older rounds `sl_total` is "how this location scores
today", not the points DCA used then. Where DCA scores are available, compare against them.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import siteline
from siteline import data
from siteline.geo import haversine_mi

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"


def xviii_point_in_time(lat: float, lon: float, round_year: int) -> dict:
    """§XVIII Previous Projects, counting only awards made before `round_year`."""
    aw = data.table("lihtc_awards")
    aw = aw[aw["year"] < round_year]
    # A: awards in the site's Local Government Boundary over the last 15 years
    places = data.polygon_layer("municipalities")
    hit = places.containing_idx(lat, lon)
    a_pts, n_lgb = 0, None
    if len(hit):
        inside = places.points_inside(int(hit[0]), aw["lat"].to_numpy(), aw["lon"].to_numpy())
        recent = aw[inside & (aw["year"] >= round_year - 15).to_numpy()]
        n_lgb = len(recent)
        if n_lgb == 0:
            a_pts = 5
        elif n_lgb == 1 and recent["year"].iloc[0] < round_year - 6:
            a_pts = 4
    # B: awards within 1 mile straight-line (real locations only); windows relax near a hub
    real = aw[aw["coord_precision"] != "random_in_city"]
    d = haversine_mi(lat, lon, real["lat"].to_numpy(), real["lon"].to_numpy())
    years = real["year"].to_numpy()[d <= 1.0]
    _, hub_mi = data.point_layer("transit_hubs").nearest(lat, lon)
    w3, w2 = (3, 2) if hub_mi <= 1.0 else (6, 4)
    b_pts = 3 if (years < round_year - w3).all() else (2 if (years < round_year - w2).all() else 0)
    return {"sl_score_XVIII_pit": float(max(a_pts, b_pts)), "sl_xviii_awards_in_lgb_pit": n_lgb,
            "sl_xviii_awards_within_1mi_pit": int(len(years))}


def tenancy_for_siteline(t) -> str:
    t = str(t or "").lower()
    return "family" if t.startswith("family") or "non" in t else "elderly"


def score_one(row) -> dict:
    res = siteline.score_site(row.lat, row.lon, tenancy=tenancy_for_siteline(row.tenancy),
                              round_year=int(row.round_year))
    out = {f"sl_{k}": v for k, v in siteline.summarize(res).items() if k not in ("lat", "lon")}
    if not res["in_scope"]:
        return out
    out.update(xviii_point_in_time(row.lat, row.lon, int(row.round_year)))
    out["sl_total_pit"] = out["sl_total_score"] - out["sl_score_XVIII"] + out["sl_score_XVIII_pit"]
    out["sl_flags"] = " | ".join(res["flags"])
    return out


def main():
    apps = pd.read_parquet(PROCESSED / "applications_9pct.parquet")
    todo = apps[apps["in_arc"] & apps["lat"].notna()]
    print(f"Scoring {len(todo)} ARC applications with coordinates "
          f"({(apps['in_arc'] & apps['lat'].isna()).sum()} ARC applications lack coordinates)")
    scored = pd.DataFrame([score_one(r) for r in todo.itertuples()], index=todo.index)
    out = apps.join(scored)
    out.to_parquet(PROCESSED / "applications_9pct_scored.parquet", index=False)
    s = out[out["sl_in_scope"].eq(True)]
    print(f"{len(s)} scored. SiteLine total (point-in-time) by outcome:")
    print(s.groupby("outcome")["sl_total_pit"].describe()[["count", "mean", "50%", "min", "max"]].round(1).to_string())
    changed = (s["sl_score_XVIII"] != s["sl_score_XVIII_pit"]).mean()
    print(f"Point-in-time §XVIII differs from SiteLine's as-of-today §XVIII for {changed:.0%} of applications")


if __name__ == "__main__":
    main()
