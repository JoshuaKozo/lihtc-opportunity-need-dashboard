"""Package the combined tool's data for combined_site/.

    python -m pipelines.combined.export_site          # or: make combined-site-data

Reads data/processed/combined/ (built by build_combined.py), the tract shapes and Fundability models the
Fundability site already ships, and the metric set. Writes combined_site/data/:
  tracts.geojson    all 1,248 ARC tracts. Scored tracts carry their Need and Opportunity scores,
                    category scores, every metric's percentile and value, and the Fundability fields.
                    "lx" holds the tract's best location points for each answer the deal checklist can
                    change them by: index 11 * phased + xiv, for §XIV revitalization points 0-10 and
                    §XVII phasing off/on (phasing replaces §XVIII's points when higher; §XIV shares the
                    10-point block with §XV, §XVI and §XIX, so the block takes the higher of the two).
                    Tracts left out (no residents, student-heavy) carry the reason instead.
  meta.json         categories, metrics, labels, sources, placeholder weights, coverage, and notes.
  grid.json         the Fundability Map's 0.25-mile SiteLine grid, slimmed to what the chance of an award at
                    one spot needs: lat, lon, base (§XI + §XII + §XIII + §XXII), §XVIII, the 10-point block,
                    and §XII / §XIII for display. A looked-up address is scored at its nearest grid point,
                    exactly as the Fundability Map scores it.
  win_model.json, funding_lines.json   copied from fundability_site/data/ (chance of an award).
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pandas as pd

from pipelines.combined.metric_set import SCORES, WEIGHTS_STATUS, metrics

ROOT = Path(__file__).resolve().parents[2]
COMBINED = ROOT / "data" / "processed" / "combined"
FUND_SITE = ROOT / "fundability_site" / "data"
OUT = ROOT / "combined_site" / "data"
NEED = ROOT / "data" / "processed" / "need" / "tract_need_metrics.csv"
GRID = ROOT / "data" / "processed" / "fundability" / "siteline_grid_arc.parquet"


def location_by_deal() -> pd.DataFrame:
    """Each tract's best location points for every §XIV score (0-10) with phasing off and on: 22 columns.

    Location points at a grid cell are §XI + §XII + §XIII + §XXII + §XVIII + the 10-point block, as on the
    Fundability Map; the checklist's §XIV can raise the block and its §XVII phasing can replace §XVIII.
    """
    g = pd.read_parquet(GRID, columns=["tract_geoid", "score_XI", "score_XII", "score_XIII", "score_XXII",
                                       "score_XVIII", "block_score"]).dropna(subset=["tract_geoid"])
    base = g["score_XI"] + g["score_XII"] + g["score_XIII"] + g["score_XXII"]
    cols = {}
    for phased in (0, 4):
        for xiv in range(11):
            loc = base + g["score_XVIII"].clip(lower=phased) + g["block_score"].clip(lower=xiv)
            cols[(phased > 0) * 11 + xiv] = loc.groupby(g["tract_geoid"]).max()
    return pd.DataFrame(cols).sort_index(axis=1)


def num(v, nd=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), nd)


def round_coords(geom: dict, nd: int = 5) -> dict:
    def r(c):
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    t = pd.read_csv(COMBINED / "combined_tract.csv", dtype={"tract_geoid": str}).set_index("tract_geoid")
    meta = json.loads((COMBINED / "combined_meta.json").read_text())
    need = pd.read_csv(NEED, dtype={"tract_geoid": str}).set_index("tract_geoid")
    shapes = json.loads((FUND_SITE / "tracts.geojson").read_text())
    lx = location_by_deal()
    cats = {s: [c for c, *_ in cs] for s, cs in SCORES.items()}
    keys = {s: [m[1] for m in metrics(s)] for s in SCORES}

    feats = []
    for f in shapes["features"]:
        p, g = f["properties"], f["properties"]["geoid"]
        props = {"g": g, "n": p.get("tract_name"), "c": p.get("county"), "lg": p.get("local_gov"),
                 "pool": p.get("pool")}
        if g in t.index:
            r = t.loc[g]
            props.update({
                "pop": int(r["population"]),
                "ns": num(r["need_score"], 1), "os": num(r["opportunity_score"], 1),
                "nc": [num(r[f"need_{c}"], 1) for c in cats["need"]],
                "oc": [num(r[f"opportunity_{c}"], 1) for c in cats["opportunity"]],
                # each metric: [percentile, value]; percentile is null when the value is missing or unreliable
                "nm": [[num(r[f"pct_{k}"], 1), num(r[f"raw_{k}"], 4)] for k in keys["need"]],
                "om": [[num(r[f"pct_{k}"], 1), num(r[f"raw_{k}"], 4)] for k in keys["opportunity"]],
                "loc": num(r["total_score_max"], 1), "xii": num(r["score_XII_max"], 1),
                "xiii": num(r["score_XIII_max"], 1), "qct": int(r["qct_2026"] or 0), "dda": int(r["dda_2026"] or 0),
                "lx": [num(v, 1) for v in lx.loc[g]] if g in lx.index else None,
            })
        else:
            row = need.loc[g] if g in need.index else None
            reason = ("No residents" if row is not None and row["population"] <= 0 else
                      "Student-heavy tract (25%+ enrolled in college), left out of the scores")
            props["ex"] = reason
        feats.append({"type": "Feature", "properties": props, "geometry": round_coords(f["geometry"])})
    (OUT / "tracts.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                   separators=(",", ":")))

    meta_out = {"weights_status": WEIGHTS_STATUS, "tracts": meta["tracts"], "excluded": meta["excluded"],
                "need_vs_opportunity_rho": meta["need_vs_opportunity_rho"], "scores": meta["scores"]}
    for key, f in (("opportunity_calibration", "opportunity_weights.json"),   # calibrate_opportunity.py
                   ("need_weights", "need_weights.json"),                   # calibrate_need.py
                   ("need_validation", "chas_swap_test.json")):             # test_chas_swap.py
        if (COMBINED / f).exists():
            meta_out[key] = json.loads((COMBINED / f).read_text())
    (OUT / "meta.json").write_text(json.dumps(meta_out, indent=1))
    for f in ("win_model.json", "funding_lines.json"):
        shutil.copyfile(FUND_SITE / f, OUT / f)
    grid = json.loads((FUND_SITE / "grid.json").read_text())
    ix = {c: i for i, c in enumerate(grid["columns"])}
    rows = [[r[ix["lat"]], r[ix["lon"]], round(r[ix["XI"]] + r[ix["XII"]] + r[ix["XIII"]] + r[ix["XXII"]], 2),
             r[ix["XVIII"]], r[ix["block_score"]], r[ix["XII"]], r[ix["XIII"]]] for r in grid["rows"]]
    (OUT / "grid.json").write_text(json.dumps({"columns": ["lat", "lon", "base", "XVIII", "block", "XII", "XIII"],
                                               "rows": rows}, separators=(",", ":")))
    size = sum(p.stat().st_size for p in OUT.iterdir()) / 1e6
    print(f"{len(feats)} tracts ({meta['tracts']} scored) -> {OUT.relative_to(ROOT)}/ ({size:.1f} MB)")


if __name__ == "__main__":
    main()
