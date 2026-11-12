"""Build the tract table behind the Fundability website: SiteLine scores plus the added layers.

    python pipelines/fundability/build_fund_features.py [--siteline ../SitelineLIHTCStartUp-main]

Reads data/processed/fundability/siteline_tract_scores.csv (from score_grid_tracts.py) and the raw downloads
(from pull_open_data.py and pull_bulk_files.py). Writes:
  data/processed/fundability/fundability_tract_features.csv    one row per ARC tract (1,248), every metric column
  fundability_site/data/tracts.geojson                  the same columns on simplified tract polygons

To add a metric: compute a new column here (keyed on `geoid`), then describe it in
fundability_site/metrics.json. The website reads both and needs no other change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "fundability"
SITE = ROOT / "fundability_site" / "data"
CURRENT_ROUND = 2026


def acs(table: str) -> pd.DataFrame:
    """One ACS 2020-2024 table for Georgia tracts, indexed by 11-digit GEOID, estimates only."""
    d = pd.read_csv(RAW / "census_acs5_2024" / f"acsdt5y2024-{table}.dat", sep="|", dtype=str)
    d = d[d["GEO_ID"].str.startswith("1400000US13")]
    d.index = d.pop("GEO_ID").str[-11:]
    est = [c for c in d.columns if "_E" in c]
    return d[est].apply(pd.to_numeric, errors="coerce")


def points_in(polys: pd.Series, ids: pd.Series, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """For each point, the id of the first polygon that contains it (None if none)."""
    tree = shapely.STRtree(polys.values)
    pts = shapely.points(lon, lat)
    pi, gi = tree.query(pts, predicate="within")
    out = np.full(len(pts), None, dtype=object)
    for p, g in zip(pi[::-1], gi[::-1]):          # reversed so the first match wins
        out[p] = ids.iloc[g]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--siteline", default=str(ROOT.parent / "SitelineLIHTCStartUp-main"))
    args = ap.parse_args()
    sl_data = Path(args.siteline) / "data"

    # ── base: ARC tracts and SiteLine's tract-level scores ────────────────────────────────────
    tr = pd.read_csv(sl_data / "tracts.csv", dtype={"geoid": str, "county_fips": str})
    tr = tr[tr["in_arc"]].copy()
    tr["geometry"] = shapely.from_wkt(tr["geometry_wkt"])
    df = tr[["geoid", "county", "pool", "tract_name", "land_area_sqmi", "internal_lat",
             "internal_lon", "in_qct"]].rename(columns={"in_qct": "sl_in_qct"}).set_index("geoid")
    sl = pd.read_csv(OUT / "siteline_tract_scores.csv", dtype={"geoid": str}).set_index("geoid")
    df = df.join(sl)

    # municipality containing the tract's internal point (for local-government award history)
    mun = pd.read_csv(sl_data / "municipalities.csv", dtype={"geoid": str})
    mun_geom = pd.Series(shapely.from_wkt(mun["geometry_wkt"]))
    df["municipality"] = points_in(mun_geom, mun["name"], df["internal_lon"].values,
                                   df["internal_lat"].values)
    # QAP "Local Government boundary": the city, or the county for unincorporated land
    df["local_gov"] = df["municipality"].fillna("Unincorporated " + df["county"] + " County")

    # ── basis boost: QCT (2025 and 2026 lists) and Small Area DDA (2026) ──────────────────────
    for yr in (2025, 2026):
        q = pd.read_csv(RAW / f"hud_qct_{yr}" / f"hud_qct_{yr}.csv", dtype=str)
        df[f"qct_{yr}"] = df.index.isin(q["GEOID"]).astype(int)
    dda = json.loads((RAW / "hud_dda_2026" / "hud_dda_2026.geojson").read_text())
    feats = [f for f in dda["features"] if f["geometry"]]
    dda_geom = pd.Series(shapely.from_geojson([json.dumps(f["geometry"]) for f in feats]))
    dda_name = pd.Series([f["properties"].get("DDA_NAME") or "DDA" for f in feats])
    hit = points_in(dda_geom, dda_name, df["internal_lon"].values, df["internal_lat"].values)
    df["dda_2026"] = pd.notna(hit).astype(int)
    df["basis_boost_2026"] = ((df["qct_2026"] == 1) | (df["dda_2026"] == 1)).astype(int)

    # ── market: ACS 2020-2024 renter households (saturation denominator) and vacancy ──────────
    # Funding rules only: rent burden, rent, and income are community-need measures and belong to
    # the Need tool, so they are not built here.
    ten, occ = acs("b25003"), acs("b25002")
    df["acs_renter_hh"] = ten["B25003_E003"]
    df["acs_vacancy_rate"] = occ["B25002_E003"] / occ["B25002_E001"]   # 4% round tiebreaker

    # ── competition / saturation: existing LIHTC near each tract ──────────────────────────────
    aw = pd.read_csv(sl_data / "lihtc_awards.csv")
    aw = aw.dropna(subset=["lat", "lon"])
    tract_geom = pd.Series(tr["geometry"].values)
    hud = pd.read_csv(RAW / "hud_lihtc" / "hud_lihtc.csv")
    hud = hud.dropna(subset=["LATITUDE", "LONGITUDE"]) if "LATITUDE" in hud else hud
    lat_c = "LATITUDE" if "LATITUDE" in hud else "LAT"
    lon_c = "LONGITUDE" if "LONGITUDE" in hud else "LON"
    hud = hud.dropna(subset=[lat_c, lon_c])
    hud["geoid"] = points_in(tract_geom, tr["geoid"], hud[lon_c].values, hud[lat_c].values)
    df["lihtc_li_units"] = hud.groupby("geoid")["LI_UNITS"].sum().reindex(df.index).fillna(0)
    df["lihtc_units_per_100_renters"] = 100 * df["lihtc_li_units"] / df["acs_renter_hh"].replace(0, np.nan)
    # QAP tiebreaker: earlier year of the most recent award in the local government wins
    aw["local_gov"] = points_in(mun_geom, mun["name"], aw["lon"].values, aw["lat"].values)
    aw["local_gov"] = aw["local_gov"].fillna("Unincorporated " + aw["county"].astype(str) + " County")
    last = aw.groupby("local_gov")["year"].max()
    # NaN = no award on record in that local government (the best case for the tiebreaker)
    df["years_since_local_award"] = CURRENT_ROUND - df["local_gov"].map(last)

    # ── site risk: FEMA National Risk Index ───────────────────────────────────────────────────
    nri = pd.read_csv(RAW / "fema_nri" / "fema_nri.csv", dtype={"TRACTFIPS": str}).set_index("TRACTFIPS")
    df["nri_inland_flood_score"] = nri["IFLD_RISKS"]
    df["nri_inland_flood_rating"] = nri["IFLD_RISKR"]

    # ── write ─────────────────────────────────────────────────────────────────────────────────
    df = df.reset_index()
    df.round(4).to_csv(OUT / "fundability_tract_features.csv", index=False)
    print(f"fundability_tract_features: {len(df):,} tracts x {df.shape[1]} columns")
    print(df.isna().sum()[lambda s: s > 0].to_string())

    SITE.mkdir(parents=True, exist_ok=True)
    geom = tr.set_index("geoid").loc[df["geoid"], "geometry"].values
    geom = shapely.set_precision(shapely.simplify(geom, 0.0003, preserve_topology=True), 1e-5)
    props = df.drop(columns=["internal_lat", "internal_lon"]).round(3)
    feats = []
    for g, (_, row) in zip(geom, props.iterrows()):
        p = {k: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)) for k, v in row.items()}
        feats.append({"type": "Feature", "properties": p, "geometry": json.loads(shapely.to_geojson(g))})
    (SITE / "tracts.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                    separators=(",", ":")))
    print(f"fundability_site/data/tracts.geojson: {(SITE / 'tracts.geojson').stat().st_size / 1e6:.1f} MB")

    # SiteLine grid cells, so a click on the map can show the score at that spot (like SiteLine)
    grid = pd.read_parquet(OUT / "siteline_grid_arc.parquet")
    grid = grid[grid["in_scope"] & grid["tract_geoid"].notna()]
    cols = ["lat", "lon", "score_XI", "score_XII", "score_XIII", "score_XV", "score_XVI",
            "score_XVIII", "score_XIX", "score_XXII", "block_score", "total_score"]
    rows = grid[cols].round({c: (5 if c in ("lat", "lon") else 2) for c in cols})
    out = {"columns": [c.replace("score_", "") for c in cols],
           "block_winner": grid["block_winner"].fillna("").tolist(),
           "rows": [[None if pd.isna(v) else v for v in r] for r in rows.itertuples(index=False)]}
    (SITE / "grid.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"fundability_site/data/grid.json: {len(rows):,} cells, {(SITE / 'grid.json').stat().st_size / 1e6:.1f} MB")

    # The "why" behind each cell's SiteLine points, row-aligned with grid.json. The site loads it
    # only when someone looks up an address or clicks the map. §XI amenity points are packed as
    # one character per category (points x 2, so "5" = 2.5 pts) to keep the file small.
    xi_ids = "abcdefghijlmnopqrs"                                 # QAP has no category k
    group1 = set("acefghjlmo")                                    # 2.5 / 2 / 1.5 pts, else 2 / 1.5 / 1

    def xi_pts(cid: str, mi: float) -> float:
        hi, mid, lo = (2.5, 2.0, 1.5) if cid in group1 else (2.0, 1.5, 1.0)
        if pd.isna(mi):
            return 0.0
        return hi if mi <= 0.5 else mid if mi <= 1.0 else lo if mi <= 1.5 else 0.0

    codes = []
    for _, r in grid.iterrows():
        dist = {c: r[f"xi_{c}_mi"] for c in xi_ids}
        pts = {c: xi_pts(c, dist[c]) for c in xi_ids}
        # SiteLine reports distances rounded to 0.01 mi, so a category sitting exactly on a 0.5 / 1.0
        # / 1.5 cutoff can look one tier better than it scored. Step those down until the
        # categories add up to SiteLine's actual §XI score.
        ded = 2 * (r["xi_undesirables_within_0_25mi"] + float(r["xi_food_desert_deduction"] or 0))
        excess = min(20.0, max(0.0, sum(pts.values()) - ded)) - r["score_XI"]
        for c in sorted(xi_ids, key=lambda c: -dist[c] if pd.notna(dist[c]) else 0):
            if excess <= 0.01:
                break
            if pd.notna(dist[c]) and round(dist[c], 2) in (0.5, 1.0, 1.5) and pts[c] > 0:
                drop = pts[c] - xi_pts(c, dist[c] + 0.01)
                pts[c] -= drop
                excess -= drop
        codes.append("".join(str(int(2 * pts[c])) for c in xi_ids))
    det_cols = ["xi_undesirables_within_0_25mi", "xi_food_desert_deduction", "xii_hub_mi", "xii_stop_mi",
                "xiii_qualifying_grades", "xv_n_above_median", "xvi_cni_mi", "xvi_pbc_mi",
                "xviii_awards_in_lgb_15yr", "xviii_awards_within_1mi", "xix_severe_housing_pct",
                "xxii_nearest_district_mi"]
    det = grid[det_cols].astype(float).round(2)
    detail = {"xi_categories": list(xi_ids), "columns": ["xi_codes", *det_cols, "xv_basis"],
              "rows": [[code, *[None if pd.isna(v) else v for v in vals], basis]
                       for code, vals, basis in zip(codes, det.itertuples(index=False),
                                                    grid["xv_basis"].fillna("").tolist())]}
    (SITE / "grid_detail.json").write_text(json.dumps(detail, separators=(",", ":")))
    print(f"fundability_site/data/grid_detail.json: {(SITE / 'grid_detail.json').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
