"""Download the public datasets used by the Fundability tool, from keyless government services.

    python pipelines/fundability/pull_open_data.py             # pull anything not already cached
    python pipelines/fundability/pull_open_data.py --force     # re-download everything
    python pipelines/fundability/pull_open_data.py --only hud_lihtc fema_nri

Every source is a public ArcGIS feature service (HUD or FEMA) that needs no login, key, or data use
agreement. Each source is saved in its own folder under data/raw/<name>/ as it came back from the
service (CSV for tables, GeoJSON for geometry), and data/raw/open_data_manifest.csv records the URL,
filter, row count, and download time. Nothing here is committed; rerun the script to recreate it.

Why not the Census API? It now returns "Missing Key" without a key, which would break the
no-key rule. HUD republishes the ACS and CHAS tract tables on its own service, so we use those.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
MANIFEST = RAW / "open_data_manifest.csv"
HEADERS = {"User-Agent": "lihtc_site_selection/0.1 (open-source research; LIHTC fundability tool)"}
PAUSE_S = 1.0
HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services"
FEMA = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services"

# name -> (service URL, where clause, keep geometry?, what it is for)
SOURCES = {
    "hud_lihtc": (f"{HUD}/LIHTC/FeatureServer/0", "PROJ_ST='GA'", True,
                  "HUD LIHTC property database, Georgia: competition, saturation, award history"),
    "hud_qct_2026": (f"{HUD}/QUALIFIED_CENSUS_TRACTS_2026/FeatureServer/0", "STATE='13'", False,
                     "Qualified Census Tracts, 2026 designation (basis boost, QAP XIV)"),
    "hud_qct_2025": (f"{HUD}/QUALIFIED_CENSUS_TRACTS/FeatureServer/0", "STATE='13'", False,
                     "Qualified Census Tracts, 2025 designation (for the 2025 round)"),
    "hud_dda_2026": (f"{HUD}/Difficult_Development_Areas_2026/FeatureServer/0", "1=1", True,
                     "Small Area and non-metro Difficult Development Areas, 2026, by ZCTA"),
    "hud_chas_tract": (f"{HUD}/ACS_5YR_ESTIMATES_CHAS_TRACT/FeatureServer/1", "STATE='13'", False,
                       "HUD CHAS tract tables, Georgia: renter households by income and burden"),
    "hud_acs_housing_tract": (f"{HUD}/ACS_5YR_ESTIMATES_HOUSING_TRACT/FeatureServer/11", "STATE='13'",
                              False, "ACS housing tables by tract, Georgia: vacancy, structure type"),
    "atl_tad": ("https://gis.beltline.org/server/rest/services/COA_Tax_Allocation_Districts_public/FeatureServer/0",
                "1=1", True, "City of Atlanta Tax Allocation Districts (tested as a §XIV plan-area proxy)"),
    "fema_nri": (f"{FEMA}/National_Risk_Index_Census_Tracts/FeatureServer/0", "STATEABBRV='GA'", False,
                 "FEMA National Risk Index by tract, Georgia: flood and other hazard risk"),
}


def fetch_all(url: str, where: str, geometry: bool, session: requests.Session) -> tuple[list, int]:
    """Page through an ArcGIS layer. Returns GeoJSON-like features and the layer's page size."""
    meta = session.get(url, params={"f": "json"}, headers=HEADERS, timeout=60).json()
    page = int(meta.get("maxRecordCount", 1000))
    feats, offset = [], 0
    while True:
        params = {"where": where, "outFields": "*", "f": "geojson" if geometry else "json",
                  "returnGeometry": str(geometry).lower(), "outSR": 4326,
                  "resultOffset": offset, "resultRecordCount": page}
        r = session.get(f"{url}/query", params=params, headers=HEADERS, timeout=120)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"{url}: {body['error']}")
        batch = body["features"]
        feats.extend(batch if geometry else [{"properties": f["attributes"]} for f in batch])
        if len(batch) < page and not body.get("exceededTransferLimit"):
            break
        offset += len(batch)
        time.sleep(PAUSE_S)
    return feats, page


def pull(name: str, session: requests.Session, force: bool) -> dict | None:
    url, where, geometry, purpose = SOURCES[name]
    out_dir = RAW / name
    csv_path = out_dir / f"{name}.csv"
    if csv_path.exists() and not force:
        print(f"  {name}: cached, skipping")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    feats, _ = fetch_all(url, where, geometry, session)
    df = pd.DataFrame([f["properties"] for f in feats])
    df.to_csv(csv_path, index=False)
    if geometry:
        (out_dir / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection",
                                                             "features": feats}))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()[:16]
    print(f"  {name}: {len(df):,} rows, {df.shape[1]} columns")
    return {"name": name, "url": url, "where": where, "rows": len(df), "columns": df.shape[1],
            "has_geometry": geometry, "purpose": purpose, "sha256_16": digest,
            "downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    ap.add_argument("--only", nargs="+", choices=sorted(SOURCES), help="pull only these sources")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(MANIFEST) if MANIFEST.exists() else pd.DataFrame()
    rows = []
    with requests.Session() as s:
        for name in args.only or SOURCES:
            row = pull(name, s, args.force)
            if row:
                rows.append(row)
            time.sleep(PAUSE_S)
    if rows:
        new = pd.DataFrame(rows)
        if len(old):
            old = old[~old["name"].isin(new["name"])]
        pd.concat([old, new], ignore_index=True).to_csv(MANIFEST, index=False)
        print(f"manifest: {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
