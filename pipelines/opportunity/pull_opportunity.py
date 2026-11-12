"""Download the public data behind the resident-opportunity score.

    python -m pipelines.opportunity.pull_opportunity            # download anything not already cached
    python -m pipelines.opportunity.pull_opportunity --list     # only print what would be downloaded

Every source is public, needs no login or key, and is free to redistribute (see DATA_SOURCES.md).
Files are cached in data/raw/opportunity/ with a manifest and are never re-downloaded; delete a
file to refresh it. Georgia-wide files are kept whole; national files are cut to Georgia on
download so the cache stays small.

| key                | what                                                      | publisher            |
|--------------------|-----------------------------------------------------------|----------------------|
| oa_tract_outcomes  | Opportunity Atlas tract outcomes (upward mobility)        | Opportunity Insights |
| oa_crosswalk       | 2010 -> 2020 tract crosswalk                              | Opportunity Insights |
| tract_centroids    | 2020 tract population-weighted centroids, Georgia         | Census               |
| zcta_tract         | 2020 ZCTA <-> tract relationship file                     | Census               |
| ga_counties        | Georgia county names and FIPS codes                       | Census               |
| census_county_pop  | County population totals (for verification only)          | Census PEP           |
| usaleep            | Life expectancy at birth by 2010 tract, 2010-15 (USALEEP) | CDC NCHS             |
| osm_parks          | Parks (leisure=park) in metro Atlanta, with size (ODbL)   | OpenStreetMap        |
| lodes_wac_<year>   | Jobs by work block (LODES 8), latest year and 5 years ago | Census LEHD          |
| lodes_xwalk        | LODES block geography (block coordinates)                 | Census LEHD          |
| lodes_rac_<year>   | Workers by home block (LODES 8, primary jobs)             | Census LEHD          |
| acs_b23006         | Employment by education, ages 25-64 (ACS 2019-23)         | Census ACS           |
| epa_sld            | Smart Location Database v3 (transit/auto job access)      | EPA                  |
| nces_schools       | Public school points with NCES attributes                 | NCES EDGE            |
| gosa_eog / _eoc    | Georgia Milestones end-of-grade / end-of-course results   | GA GOSA              |
| gosa_grad          | 4-year graduation rates                                   | GA GOSA              |
| cdc_places         | PLACES tract health estimates                             | CDC                  |
| usda_sram          | Food access (SNAP-authorized Retailer Access Map, 2025)   | USDA ERS             |
| usda_sram_general  | Tract poverty, income, SNAP, race (for verification only) | USDA ERS             |
| hrsa_sites         | Health center (FQHC) service delivery sites               | HRSA                 |
| hud_<index>        | AFFH opportunity indices (school, jobs, labor, transport, environment) | HUD     |
| marta_gtfs         | MARTA GTFS schedule feed (stops)                          | MARTA                |
"""
from __future__ import annotations

import argparse
import gzip
import io
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "opportunity"
MANIFEST = RAW_DIR / "manifest.csv"
HEADERS = {"User-Agent": "lihtc_site_selection/0.1 (open-source research; LIHTC award analysis)"}
PAUSE_S = 1.0
GA = "13"

LODES = "https://lehd.ces.census.gov/data/lodes/LODES8/ga"
GOSA_INDEX = "https://download.gosa.ga.gov/"
NCES_SCHOOLS = ("https://services1.arcgis.com/Ua5sjt3LWTPigjyD/arcgis/rest/services/"
                "School_Characteristics_Current/FeatureServer/1/query")
HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services"
# HUD AFFH indices (AFFHT0006, 2010 geography). key -> (service, layer)
HUD_INDICES = {
    "hud_school_proficiency": ("School_Proficiency_Index", 22),
    "hud_jobs_proximity": ("Jobs_Proximity_Index", 18),
    "hud_labor_market": ("Labor_Market_Engagement_Index", 23),
    "hud_transport_cost": ("Low_Transportation_Cost_Index", 21),
    "hud_env_health": ("Environmental_Health_Hazard_Index", 19),
}


# --------------------------------------------------------------------------- HTTP helpers

def get(session: requests.Session, url: str, *, params=None, timeout=300, tries=4) -> requests.Response:
    """GET with retries; large downloads through proxies drop now and then."""
    for attempt in range(tries):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            if attempt == tries - 1 or (isinstance(e, requests.HTTPError)
                                        and e.response.status_code in (403, 404)):
                raise
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


def arcgis_table(session, url: str, where: str, points=False, page=2000) -> pd.DataFrame:
    """All rows of an ArcGIS feature layer query, paged. Point layers get lat/lon columns."""
    rows, offset = [], 0
    while True:
        params = {"where": where, "outFields": "*", "f": "json", "outSR": 4326,
                  "returnGeometry": str(points).lower(), "resultOffset": offset,
                  "resultRecordCount": page, "orderByFields": "OBJECTID"}
        data = get(session, url, params=params).json()
        if "error" in data:
            raise RuntimeError(f"{url}: {data['error']}")
        for f in data["features"]:
            r = dict(f["attributes"])
            g = f.get("geometry") or {}
            if "x" in g:
                r["lon"], r["lat"] = g["x"], g["y"]
            rows.append(r)
        if not data["features"]:   # servers cap pages below `page`, so stop on an empty one
            break
        offset += len(data["features"])
        time.sleep(PAUSE_S)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- source fetchers
# Each fetcher returns the bytes to cache (already cut to Georgia where the source is national).

def _csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def fetch_oa_outcomes(s):
    url = "https://opportunityinsights.org/wp-content/uploads/2018/10/tract_outcomes_simple.csv"
    df = pd.read_csv(io.BytesIO(get(s, url).content), dtype={"state": str, "county": str, "tract": str})
    return _csv_bytes(df[df["state"].astype(int) == int(GA)])


def fetch_oa_crosswalk(s):
    url = "https://opportunityinsights.org/wp-content/uploads/2021/05/us_tract_2010_2020_crosswalk.csv"
    df = pd.read_csv(io.BytesIO(get(s, url).content), dtype=str)
    return _csv_bytes(df[df["tract_2020"].str.startswith(GA)])


def fetch_centroids(s):
    return get(s, "https://www2.census.gov/geo/docs/reference/cenpop2020/tract/"
                  f"CenPop2020_Mean_TR{GA}.txt").content


def fetch_zcta_tract(s):
    url = ("https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
           "tab20_zcta520_tract20_natl.txt")
    df = pd.read_csv(io.BytesIO(get(s, url).content), sep="|", dtype=str, encoding="utf-8-sig")
    return _csv_bytes(df[df["GEOID_TRACT_20"].str.startswith(GA) & df["GEOID_ZCTA5_20"].notna()])


def lodes_years(s) -> tuple[int, int]:
    html = get(s, f"{LODES}/wac/").text
    years = sorted({int(y) for y in re.findall(r"ga_wac_S000_JT00_(\d{4})\.csv\.gz", html)})
    return years[-1], years[-1] - 5


def fetch_lodes_wac(year):
    return lambda s: get(s, f"{LODES}/wac/ga_wac_S000_JT00_{year}.csv.gz").content


def fetch_lodes_xwalk(s):
    raw = gzip.decompress(get(s, f"{LODES}/ga_xwalk.csv.gz").content)
    df = pd.read_csv(io.BytesIO(raw), dtype=str, usecols=["tabblk2020", "trct", "blklatdd", "blklondd"])
    return _csv_bytes(df)


def fetch_epa_sld(s):
    url = "https://edg.epa.gov/EPADataCommons/public/OA/EPA_SmartLocationDatabase_V3_Jan_2021_Final.csv"
    keep = ["STATEFP", "COUNTYFP", "TRACTCE", "BLKGRPCE", "TotPop", "TotEmp", "D4A", "D5AR",
            "D5BR", "D5BE", "NatWalkInd"]
    df = pd.read_csv(io.BytesIO(get(s, url, timeout=900).content), usecols=keep,
                     dtype={"STATEFP": str, "COUNTYFP": str, "TRACTCE": str, "BLKGRPCE": str})
    return _csv_bytes(df[df["STATEFP"].astype(int) == int(GA)])


def fetch_nces_schools(s):
    return _csv_bytes(arcgis_table(s, NCES_SCHOOLS, "ST_LEAID LIKE 'GA-%'", points=True))


def gosa_links(s) -> dict[str, str]:
    """Latest school year's Milestones EOG/EOC and graduation-rate files on GOSA's download page.
    File names carry upload timestamps, so they're read from the page rather than hard-coded."""
    html = get(s, GOSA_INDEX).text
    links = sorted(set(re.findall(r"https://download\.gosa\.ga\.gov/(\d{4})/([^\"'<> ]+\.csv)", html)))
    out = {}
    for key, pat in {"gosa_eog": r"^EOG_\d{4}-\d\d__GA_TST_AGGR", "gosa_eoc": r"^EOC_\d{4}-\d\d__GA_TST_AGGR",
                     "gosa_grad": r"^Graduation_Rate_\d{4}-\d\d_"}.items():
        hits = [(y, f) for y, f in links if re.search(pat, f)]
        if hits:
            y, f = max(hits)
            out[key] = f"{GOSA_INDEX}{y}/{f}"
    return out


ACS_YEAR = 2023


def fetch_acs_table(table):
    """One ACS 5-year detailed table for every Georgia tract, from the Census table-based summary
    file (a bulk download that needs no API key)."""
    def f(s):
        url = (f"https://www2.census.gov/programs-surveys/acs/summary_file/{ACS_YEAR}/table-based-SF/"
               f"data/5YRData/acsdt5y{ACS_YEAR}-{table}.dat")
        df = pd.read_csv(io.BytesIO(get(s, url, timeout=900).content), sep="|", dtype={"GEO_ID": str})
        df = df[df["GEO_ID"].str.startswith("1400000US" + GA)]
        return _csv_bytes(df.assign(tract=df["GEO_ID"].str[-11:]))
    return f


def fetch_url(url, **kw):
    return lambda s: get(s, url, **kw).content


def fetch_county_pop(s):
    url = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/"
           "co-est2024-alldata.csv")
    df = pd.read_csv(io.BytesIO(get(s, url).content), encoding="latin1", dtype={"STATE": str, "COUNTY": str})
    df = df[(df["STATE"] == GA) & (df["COUNTY"] != "000")]
    return _csv_bytes(df[["STATE", "COUNTY", "CTYNAME", "ESTIMATESBASE2020", "POPESTIMATE2024"]])


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
ARC_BBOX = "33.20,-84.95,34.45,-83.75"   # south, west, north, east around the 11 ARC counties


def _ring_acres(coords) -> float:
    """Area of a closed lat/lon ring in acres (shoelace on a local equirectangular projection)."""
    import math
    lat0 = math.radians(sum(c["lat"] for c in coords) / len(coords))
    xs = [math.radians(c["lon"]) * math.cos(lat0) * 6371000 for c in coords]
    ys = [math.radians(c["lat"]) * 6371000 for c in coords]
    a = 0.5 * abs(sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(xs) - 1)))
    return a / 4046.86


def fetch_parks(s):
    """OpenStreetMap parks (leisure=park), reduced to name, size and center point. OSM data is
    (c) OpenStreetMap contributors, ODbL. Relations count their closed outer rings minus inner
    ones; a relation with no closed ring falls back to 60% of its bounding box."""
    import math
    q = f'[out:json][timeout:300];(way["leisure"="park"]({ARC_BBOX});relation["leisure"="park"]({ARC_BBOX}););out geom;'
    last = None
    for url in OVERPASS * 3:   # the public Overpass servers drop connections now and then
        try:
            r = s.post(url, data={"data": q}, headers=HEADERS, timeout=400)
            r.raise_for_status()
            elements = r.json()["elements"]
            break
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(5)
    else:
        raise last
    rows = []
    for e in elements:
        b = e.get("bounds")
        if not b:
            continue
        if e["type"] == "way":
            g = e.get("geometry") or []
            acres = _ring_acres(g) if len(g) > 3 and g[0] == g[-1] else 0.0
        else:
            acres = 0.0
            for m in e.get("members", []):
                g = m.get("geometry") or []
                if len(g) > 3 and g[0] == g[-1]:
                    acres += _ring_acres(g) * (-1 if m.get("role") == "inner" else 1)
            if acres <= 0:
                h = (b["maxlat"] - b["minlat"]) * 111320
                w = (b["maxlon"] - b["minlon"]) * 111320 * math.cos(math.radians(b["minlat"]))
                acres = 0.6 * h * w / 4046.86
        rows.append({"osm_id": f'{e["type"]}/{e["id"]}', "name": (e.get("tags") or {}).get("name"),
                     "acres": round(acres, 2), "lat": round((b["minlat"] + b["maxlat"]) / 2, 6),
                     "lon": round((b["minlon"] + b["maxlon"]) / 2, 6)})
    return _csv_bytes(pd.DataFrame(rows))


def fetch_places(s):
    # Wide ("GIS friendly") tract file, 2025 release, 2020 tracts.
    url = "https://data.cdc.gov/resource/yjkw-uj5s.csv"
    return get(s, url, params={"stateabbr": "GA", "$limit": 10000}).content


SRAM_URL = "https://www.ers.usda.gov/media/29395/2025-snap-authorized-retailer-access-map-sram-data.zip"


def fetch_usda_sram_table(member):
    def f(s):
        zf = zipfile.ZipFile(io.BytesIO(get(s, SRAM_URL, timeout=900).content))
        df = pd.read_csv(zf.open(member), dtype={"CensusTract20": str}, encoding="latin1")
        df["CensusTract20"] = df["CensusTract20"].str.zfill(11)
        return _csv_bytes(df[df["CensusTract20"].str.startswith(GA)])
    return f


fetch_usda_sram = fetch_usda_sram_table("SRAM Straight Line Distance Data.csv")


def fetch_hrsa(s):
    url = "https://data.hrsa.gov/DataDownload/DD_Files/Health_Center_Service_Delivery_and_LookAlike_Sites.csv"
    df = pd.read_csv(io.BytesIO(get(s, url).content), dtype=str, encoding="latin1")
    return _csv_bytes(df[df["Site State Abbreviation"] == "GA"])


def fetch_hud(service, layer):
    def f(s):
        df = arcgis_table(s, f"{HUD}/{service}/FeatureServer/{layer}/query", f"STATE='{GA}'")
        return _csv_bytes(df.drop(columns=[c for c in df.columns if c.startswith("Shape__")]))
    return f


def sources(s) -> list[dict]:
    latest, base = lodes_years(s)
    src = [
        ("oa_tract_outcomes", "oa_tract_outcomes_ga.csv", fetch_oa_outcomes),
        ("oa_crosswalk", "oa_tract_2010_2020_crosswalk_ga.csv", fetch_oa_crosswalk),
        ("tract_centroids", "cenpop2020_tract_ga.csv", fetch_centroids),
        ("zcta_tract", "zcta520_tract20_ga.csv", fetch_zcta_tract),
        ("osm_parks", "osm_parks_arc.csv", fetch_parks),
        ("usaleep", "usaleep_ga_2010_15.csv",
         fetch_url("https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Datasets/NVSS/USALEEP/CSV/GA_A.CSV")),
        # Official county population totals, an independent cross-check for verify_opportunity.py
        ("census_county_pop", "census_county_pop_ga.csv", fetch_county_pop),
        ("ga_counties", "ga_counties_2020.txt",
         fetch_url("https://www2.census.gov/geo/docs/reference/codes2020/cou/st13_ga_cou2020.txt")),
        (f"lodes_wac_{latest}", f"ga_wac_S000_JT00_{latest}.csv.gz", fetch_lodes_wac(latest)),
        (f"lodes_wac_{base}", f"ga_wac_S000_JT00_{base}.csv.gz", fetch_lodes_wac(base)),
        # Workers by where they live (primary jobs), for competition-adjusted job access
        (f"lodes_rac_{latest}", f"ga_rac_S000_JT01_{latest}.csv.gz",
         fetch_url(f"{LODES}/rac/ga_rac_S000_JT01_{latest}.csv.gz")),
        # Employment by education, ages 25-64 (ACS 5-year): the education-adjusted adult
        # employment outcome used by calibrate_weights.py; never scored
        ("acs_b23006", "acs_b23006_ga.csv", fetch_acs_table("b23006")),
        ("lodes_xwalk", "ga_lodes_block_xwalk.csv", fetch_lodes_xwalk),
        ("epa_sld", "epa_sld_v3_ga.csv", fetch_epa_sld),
        ("nces_schools", "nces_public_schools_ga.csv", fetch_nces_schools),
        ("cdc_places", "cdc_places_tract_ga.csv", fetch_places),
        ("usda_sram", "usda_sram_straight_line_ga.csv", fetch_usda_sram),
        # Tract poverty, income, SNAP, vehicles and race (ACS-based): used only by
        # verify_opportunity.py to test overlap with need and the fairness check, never scored.
        ("usda_sram_general", "usda_sram_general_ga.csv",
         fetch_usda_sram_table("SRAM General Tract Characteristics Data.csv")),
        ("hrsa_sites", "hrsa_health_center_sites_ga.csv", fetch_hrsa),
        ("marta_gtfs", "marta_gtfs.zip",
         fetch_url("https://itsmarta.com/google_transit_feed/google_transit.zip")),
    ]
    for key, url in gosa_links(s).items():
        src.append((key, url.rsplit("/", 1)[1], fetch_url(url)))
    for key, (service, layer) in HUD_INDICES.items():
        src.append((key, f"{key}_ga.csv", fetch_hud(service, layer)))
    return [{"key": k, "filename": f, "fetch": fn} for k, f, fn in src]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="only list the sources")
    args = ap.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    with requests.Session() as s:
        for src in sources(s):
            path = RAW_DIR / src["filename"]
            if args.list:
                print(f"{src['key']:<24} {src['filename']}")
                continue
            if path.exists() and path.stat().st_size > 0:
                status = "cached"
            else:
                try:
                    path.write_bytes(src["fetch"](s))
                    status = "downloaded"
                except Exception as e:  # keep going; the manifest records what failed
                    status = f"failed: {type(e).__name__}: {str(e)[:120]}"
                time.sleep(PAUSE_S)
            print(f"{src['key']:<24} {status}")
            rows.append({"key": src["key"], "filename": src["filename"], "status": status,
                         "bytes": path.stat().st_size if path.exists() else None})
    if args.list:
        return
    manifest = pd.DataFrame(rows)
    manifest["retrieved"] = pd.Timestamp.now().strftime("%Y-%m-%d")
    if MANIFEST.exists():   # keep the original retrieval date of cached files
        old = pd.read_csv(MANIFEST).set_index("filename")["retrieved"]
        cached = manifest["status"].eq("cached")
        manifest.loc[cached, "retrieved"] = manifest.loc[cached, "filename"].map(old).fillna(
            manifest.loc[cached, "retrieved"])
    manifest.to_csv(MANIFEST, index=False)
    print(f"Manifest written to {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
