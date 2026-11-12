"""Download the larger public bulk files used by the Fundability tool.

    python pipelines/fundability/pull_bulk_files.py             # download anything not already cached
    python pipelines/fundability/pull_bulk_files.py --list      # print what would be downloaded
    python pipelines/fundability/pull_bulk_files.py --only hud_lihtc_full census_acs5_2024

All files are public and need no login or key (HUD user files, Census FTP files). Each source goes
in its own folder under data/raw/<name>/. Zip files are unpacked there with a safety check on every
member path, keeping only the tract-level (summary level 140) tables for CHAS. The record of what
was downloaded is data/raw/bulk_manifest.csv.

Why these:
  hud_lihtc_full    HUD's public LIHTC database file (DBF). Checked 2026-10-14: Georgia allocations stop
                    at 2014, so it is OLDER than HUD's map service (which reaches about 2019); keep it
                    only for its 2015 data dictionary and pre-2015 history
  hud_chas_2018_22  CHAS 2018-2022 tract tables (the map service only has 2013-2017)
  census_acs5_2024  ACS 2020-2024 5-year tables, by tract, from Census bulk files (the Census API
                    now requires a key)
  census_tract_rel  2010 <-> 2020 tract relationship file, to put old award rounds on 2020 tracts
"""
from __future__ import annotations

import argparse
import hashlib
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
MANIFEST = RAW / "bulk_manifest.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lihtc_site_selection/0.1; open-source research)"}
ACS = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData"
ACS_TABLES = {
    "b01003": "total population",
    "b19013": "median household income",
    "b25002": "occupancy status",
    "b25003": "tenure (renter vs. owner households)",
    "b25004": "vacancy status",
    "b25064": "median gross rent",
    "b25070": "gross rent as % of household income (rent burden)",
}

# name -> list of (url, filename, unzip?)
SOURCES: dict[str, list[tuple[str, str, bool]]] = {
    "hud_lihtc_full": [("https://www.huduser.gov/portal/datasets/lihtc/LIHTCPUB.ZIP",
                        "LIHTCPUB.zip", True)],
    "hud_chas_2018_22": [("https://www.huduser.gov/portal/datasets/cp/2018thru2022-140-csv.zip",
                          "2018thru2022-140-csv.zip", True)],
    "census_acs5_2024": [(f"{ACS}/acsdt5y2024-{t}.dat", f"acsdt5y2024-{t}.dat", False)
                         for t in ACS_TABLES],
    "census_tract_rel": [("https://www2.census.gov/geo/docs/maps-data/data/rel2020/tract/"
                          "tab20_tract20_tract10_natl.txt", "tab20_tract20_tract10_natl.txt", False)],
}


def stream(url: str, dest: Path, session: requests.Session) -> int:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(url, headers=HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest.stat().st_size


def safe_unzip(zpath: Path, out_dir: Path) -> list[str]:
    """Extract data files only, refusing any member whose path would leave out_dir."""
    out_root = out_dir.resolve()
    kept = []
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            if m.is_dir() or not m.filename.lower().endswith((".csv", ".dbf", ".txt", ".xls", ".xlsx",
                                                               ".pdf", ".docx")):
                continue
            target = (out_dir / Path(m.filename).name).resolve()    # flatten: no subfolders
            if out_root not in target.parents:
                raise RuntimeError(f"unsafe path in zip: {m.filename}")
            with z.open(m) as src, open(target, "wb") as dst:
                dst.write(src.read())
            kept.append(target.name)
    return kept


def pull(name: str, session: requests.Session, force: bool) -> list[dict]:
    out_dir = RAW / name
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for url, fname, unzip in SOURCES[name]:
        dest = out_dir / fname
        if (dest.exists() or (unzip and any(out_dir.glob("*.csv")))) and not force:
            print(f"  {name}/{fname}: cached")
            continue
        size = stream(url, dest, session)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()[:16] if size < 300_000_000 else ""
        extracted = []
        if unzip:
            extracted = safe_unzip(dest, out_dir)
            dest.unlink()                     # keep the extracted data files, not the archive
        print(f"  {name}/{fname}: {size / 1e6:,.1f} MB" + (f", {len(extracted)} files" if unzip else ""))
        rows.append({"name": name, "url": url, "file": fname, "mb": round(size / 1e6, 1),
                     "extracted_files": len(extracted), "sha256_16": digest,
                     "downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")})
        time.sleep(1.0)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    ap.add_argument("--only", nargs="+", choices=sorted(SOURCES))
    args = ap.parse_args()
    names = args.only or list(SOURCES)
    if args.list:
        for n in names:
            for url, fname, _ in SOURCES[n]:
                print(f"{n:18s} {fname:36s} {url}")
        return
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    with requests.Session() as s:
        for n in names:
            rows += pull(n, s, args.force)
    if rows:
        new = pd.DataFrame(rows)
        if MANIFEST.exists():
            old = pd.read_csv(MANIFEST)
            old = old[~old["file"].isin(new["file"])]
            new = pd.concat([old, new], ignore_index=True)
        new.to_csv(MANIFEST, index=False)
        print(f"manifest: {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
