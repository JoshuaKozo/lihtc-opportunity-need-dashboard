"""Build tract-level housing-need metrics for the 1,248 census tracts in the 11 ARC counties.

    python -m pipelines.need.build_tract_need            # download anything not cached, then build
    python -m pipelines.need.build_tract_need --offline  # build from cached downloads only

Sources (all public, no key or login):
- ACS 2020-2024 5-year table-based summary files (one pipe-delimited file per table, every
  geography). Only the ARC tract rows and the Atlanta metro row are kept, cached in data/raw/acs/.
  The 2017-2021 median gross rent table is pulled too, for rent growth (both use 2020 tracts).
- HUD's public ArcGIS services: LIHTC properties, public housing buildings, HUD multifamily
  assisted properties (points, assigned to tracts by coordinates), Housing Choice Vouchers by
  tract, and 2026 Qualified Census Tracts. Cached in data/raw/hud/.
- Census TIGERweb 2020 tract boundaries, simplified for the web map. Cached in data/raw/tiger/.

Outputs:
- data/processed/need/tract_need_metrics.csv: one row per tract, every metric with MOE, CV and its
  reliability flag, plus supply counts, QCT and student flags.
- need_site/data.js: the metrics, percentiles, default weights and simplified boundaries
  that the map tool (need_site/index.html) reads.

Reliability: a metric is flagged unreliable in a tract when its coefficient of variation
(MOE / 1.645 / estimate) is above 40% (30% for median income), unless it's a proportion whose
MOE is under 5 points and smaller than the estimate. Zero and suppressed estimates are flagged
too. The score skips flagged values.

Caveats are listed in CAVEATS below and shown in the tool.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
ACS_DIR, HUD_DIR, TIGER_DIR = RAW / "acs", RAW / "hud", RAW / "tiger"
TRACTS_CSV = ROOT / "data" / "processed" / "arc_tracts_2020.csv"
OUT_CSV = ROOT / "data" / "processed" / "need" / "tract_need_metrics.csv"
FACTOR_CSV = ROOT / "data" / "processed" / "need" / "tract_need_factor_loadings.csv"
APP_DATA = ROOT / "need_site" / "data.js"
HEADERS = {"User-Agent": "lihtc_site_selection/0.1 (open-source research; housing need)"}

ACS_URL = ("https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/"
           "data/5YRData/acsdt5y{year}-{table}.dat")
ACS_TABLES = ["B01003", "B19013", "B19113", "C17002", "B25070", "B25003", "B23025", "B23022",
              "B22003", "B25014", "B25044", "B25106", "B19001", "B25064", "B25004", "B17010",
              "B17020", "C18130", "C16002", "B25034", "B25048", "B25052", "B14007"]
METRO_GEO = "310M700US12060"          # Atlanta-Sandy Springs-Roswell, GA Metro Area
ARC_COUNTIES = ["057", "063", "067", "089", "097", "113", "117", "121", "135", "151", "247"]

HUD_BASE = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services"
TIGER_URL = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/"
             "MapServer/10/query")

STUDENT_SHARE_FLAG = 0.25   # college + grad students / population 3+

# ---------------------------------------------------------------------------------------------
# Metric catalog. direction +1: higher value = more need; -1: lower value = more need.
# ---------------------------------------------------------------------------------------------
GROUPS = [
    ("income", "Income & poverty"),
    ("burden", "Housing cost burden"),
    ("market", "Rental market pressure"),
    ("vulnerable", "Vulnerable households"),
    ("work", "Employment & mobility"),
    ("quality", "Housing quality & crowding"),
    ("supply", "Affordable supply gap"),
]
METRICS = [
    # key, group, label, direction, format, note
    ("poverty_rate", "income", "Poverty rate", 1, "pct", "C17002"),
    ("below_200pct_poverty", "income", "Below 200% of poverty", 1, "pct", "C17002"),
    ("below_60ami_share", "income", "Households under ~60% AMI", 1, "pct",
     "B19001 vs 60% of metro median family income (B19113), 3-person adjusted; estimated from brackets"),
    ("median_hh_income", "income", "Median household income", -1, "usd", "B19013"),
    ("snap_share", "income", "Households on SNAP", 1, "pct", "B22003"),
    ("low_income_renter_burden", "burden", "Renters under $35k paying 30%+", 1, "pct",
     "B25106; share of all renter households"),
    ("rent_burden_30plus", "burden", "Renters paying 30%+ of income", 1, "pct", "B25070"),
    ("severe_rent_burden_50plus", "burden", "Renters paying 50%+ of income", 1, "pct", "B25070"),
    ("renter_share", "market", "Renter households", 1, "pct", "B25003"),
    ("rental_vacancy_rate", "market", "Rental vacancy rate", -1, "pct",
     "B25004 + B25003; low vacancy = tight market"),
    ("rent_growth", "market", "Median rent growth", 1, "pct",
     "B25064, 2017-2021 to 2020-2024; periods overlap, so change is understated"),
    ("single_parent_poverty", "vulnerable", "Single-parent families in poverty", 1, "pct", "B17010"),
    ("senior_poverty", "vulnerable", "Seniors (60+) in poverty", 1, "pct", "B17020"),
    ("disability_poverty", "vulnerable", "People with disabilities in poverty", 1, "pct", "C18130"),
    ("limited_english_share", "vulnerable", "Limited-English households", 1, "pct", "C16002"),
    ("unemployment_rate", "work", "Unemployment rate", 1, "pct", "B23025"),
    ("part_time_share", "work", "Workers usually part-time", 1, "pct", "B23022"),
    ("no_vehicle_share", "work", "Households with no vehicle", 1, "pct", "B25044"),
    ("overcrowded_share", "quality", "Overcrowded homes (>1 per room)", 1, "pct", "B25014"),
    ("pre1980_share", "quality", "Homes built before 1980", 1, "pct", "B25034"),
    ("lacking_facilities", "quality", "Lacking plumbing or kitchen", 1, "pct",
     "B25048 + B25052 summed; may double count; very noisy"),
    ("unassisted_li_renter_share", "supply", "Low-income renters without assisted housing", 1, "pct",
     "Renter households under $35k (B25106) minus LIHTC, public housing and HUD multifamily "
     "assisted units in the tract, as a share of all households; programs overlap, so assisted "
     "units may be overstated"),
]

# Judgment: how directly each metric measures need for subsidized rental housing (1-3). Default
# metric weight = importance x share of tracts where the metric is reliable, so a core metric
# with noisy tract data still counts, but less than its importance alone would suggest.
IMPORTANCE = {
    "poverty_rate": 3, "below_200pct_poverty": 2, "below_60ami_share": 3, "median_hh_income": 2,
    "snap_share": 2, "low_income_renter_burden": 3, "rent_burden_30plus": 2,
    "severe_rent_burden_50plus": 3, "renter_share": 2, "rental_vacancy_rate": 2, "rent_growth": 2,
    "single_parent_poverty": 2, "senior_poverty": 2, "disability_poverty": 2,
    "limited_english_share": 2, "unemployment_rate": 2, "part_time_share": 1, "no_vehicle_share": 2,
    "overcrowded_share": 2, "pre1980_share": 1, "lacking_facilities": 1, "unassisted_li_renter_share": 3,
}
# Default group weights (sum 100): cost burden and income are the core of rental housing need.
# History: these were the first defaults (Oct 15, 2026). On Nov 1 the default briefly switched to a
# HUD Worst-Case Needs translation (income 25, burden 40, market 5, vulnerable 5, employment 0,
# quality 10, supply 15); the backtests (pipelines/need/backtest_frameworks.py, weight_tests.py) found it
# less accurate on every outcome but displacement, so these were restored the same day.
GROUP_WEIGHTS = {"income": 25, "burden": 25, "market": 10, "vulnerable": 10, "work": 10,
                 "quality": 5, "supply": 15}
HUD_GROUP_WEIGHTS = {"income": 25, "burden": 40, "market": 5, "vulnerable": 5, "work": 0,
                     "quality": 10, "supply": 15}

CAVEATS = [
    "Tracts average about 4,000 people, so Census margins of error are large. Values with a coefficient of variation above 40% (30% for median income) are skipped, unless the margin is under 5 points and smaller than the value. Rent growth, plumbing/kitchen, the supply gap and poverty among seniors, people with disabilities and single parents are skipped most often.",
    "Exact ranks are uncertain: survey error alone moves a typical tract about 150 places. Read the score as tiers and check a tract's rank range.",
    "Student-heavy tracts (25%+ of residents in college or graduate school) are left out of the ranking, because students inflate poverty and part-time work.",
    "The supply gap subtracts LIHTC, public housing and HUD multifamily assisted units from renter households under $35,000. Properties in more than one program are counted more than once, assisted units can house higher-income households, and HUD's LIHTC data has few properties placed in service after 2020.",
    "\"Under ~60% AMI\" is estimated from Census income brackets against 60% of the metro median family income (3-person adjusted), not HUD's official limits by household size.",
    "Rent growth compares 2017-2021 with 2020-2024; the periods share two years, which understates the change.",
    "Not in the score: HUD CHAS (tested; it made the score less accurate), Qualified Census Tracts (outlined for reference; they repeat the income data), eviction filings (kept out so they could test the score) and expiring affordability (the National Housing Preservation Database needs an account).",
    "The tests have limits: eviction data covers 5 of the 11 counties, and CDC estimates housing insecurity partly from income, which flatters income-heavy scores.",
    "Need in Clayton County is under-flagged: its need is high almost everywhere, so the score separates its tracts poorly.",
    "People search by ZIP, which doesn't line up with tracts. A ZIP-to-tract lookup is still needed.",
]


# ---------------------------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------------------------
def fetch_acs(table: str, year: int, tract_prefixes: tuple[str, ...], offline: bool) -> pd.DataFrame:
    """ARC tract rows (plus the metro row) of one ACS table, cached after the first download."""
    cache = ACS_DIR / f"acsdt5y{year}-{table.lower()}_arc.dat"
    if not cache.exists():
        if offline:
            raise FileNotFoundError(cache)
        ACS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  downloading ACS {year} {table} ...", flush=True)
        r = requests.get(ACS_URL.format(year=year, table=table.lower()), headers=HEADERS,
                         stream=True, timeout=120)
        r.raise_for_status()
        r.encoding = "utf-8"
        kept = []
        for i, line in enumerate(r.iter_lines(decode_unicode=True)):
            # the metro's code changes with each delineation (310M600US12060 in 2021, 310M700US12060 later)
            if i == 0 or line.startswith(tract_prefixes) or (line.startswith("310M") and "US12060|" in line[:20]):
                kept.append(line)
        cache.write_text("\n".join(kept) + "\n")
    return pd.read_csv(cache, sep="|", dtype={"GEO_ID": str}).set_index("GEO_ID")


def arcgis_query(url: str, where: str, fields: str, geometry: bool = False, **extra) -> list[dict]:
    """All features matching `where`, paging past the server's record limit."""
    feats, offset = [], 0
    while True:
        params = {"where": where, "outFields": fields, "returnGeometry": str(geometry).lower(),
                  "outSR": 4326, "f": "geojson" if geometry else "json",
                  "resultOffset": offset, "resultRecordCount": 1000, **extra}
        d = requests.get(url, params=params, headers=HEADERS, timeout=120).json()
        if "error" in d:
            raise RuntimeError(f"{url}: {d['error']}")
        page = d.get("features", [])
        feats += page
        if not page or not (d.get("exceededTransferLimit")
                            or d.get("properties", {}).get("exceededTransferLimit")):
            return feats
        offset += len(page)


def fetch_cached_json(name: str, fetch, offline: bool):
    path = name if isinstance(name, Path) else HUD_DIR / name
    if not path.exists():
        if offline:
            raise FileNotFoundError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {path.name} ...", flush=True)
        path.write_text(json.dumps(fetch()))
    return json.loads(path.read_text())


def fetch_tract_shapes(offline: bool) -> dict:
    def get():
        feats = []
        for c in ARC_COUNTIES:
            feats += arcgis_query(TIGER_URL, f"STATE='13' AND COUNTY='{c}'", "GEOID", geometry=True,
                                  maxAllowableOffset=0.0003, geometryPrecision=5)
        return {"type": "FeatureCollection", "features": feats}
    return fetch_cached_json(TIGER_DIR / "arc_tracts_2020_simplified.geojson", get, offline)


def fetch_county_shapes(offline: bool) -> dict:
    url = TIGER_URL.replace("Tracts_Blocks/MapServer/10", "State_County/MapServer/1")
    counties = ",".join(f"'{c}'" for c in ARC_COUNTIES)
    return fetch_cached_json(
        TIGER_DIR / "arc_counties_simplified.geojson",
        lambda: {"type": "FeatureCollection",
                 "features": arcgis_query(url, f"STATE='13' AND COUNTY IN ({counties})", "GEOID,BASENAME",
                                          geometry=True, maxAllowableOffset=0.001, geometryPrecision=4)},
        offline)


def fetch_hud(offline: bool) -> dict:
    counties = ",".join(f"'{c}'" for c in ARC_COUNTIES)
    layers = {
        "lihtc": ("LIHTC", "PROJ_ST='GA'",
                  "HUD_ID,PROJECT,PROJ_CTY,N_UNITS,LI_UNITS,YR_PIS,TRACT2KX,CNTY2KX"),
        "public_housing": ("Public_Housing_Buildings", "STATE2KX='13'",
                           "NATIONAL_BLDG_ID,PROJECT_NAME,TOTAL_DWELLING_UNITS,ACC_UNITS,"
                           "TRACT2KX,CNTY2KX"),
        "hud_multifamily": ("MULTIFAMILY_PROPERTIES_ASSISTED", "STATE2KX='13'", "PROPERTY_ID,PROPERTY_NAME_TEXT,TOTAL_ASSISTED_UNIT_COUNT,"
                            "TOTAL_UNIT_COUNT,TRACT2KX,CNTY2KX"),
        "vouchers": ("Housing_Choice_Vouchers_by_Tract", f"STATE='13' AND COUNTY IN ({counties})",
                     "GEOID,HCV_PUBLIC,HCV_PUBLIC_PCT"),
        # HUD overwrote this service with the 2027 designations on Oct 28, 2026; the cached
        # data/raw/hud/qct_2026.json (downloaded Oct 15) holds the 2026 list and is committed
        "qct_2026": ("QUALIFIED_CENSUS_TRACTS_2026", f"STATE='13' AND COUNTY IN ({counties})", "GEOID"),
    }
    # point layers are filtered to Georgia only; point-in-polygon keeps the ARC tracts
    out = {}
    for key, (service, where, fields) in layers.items():
        url = f"{HUD_BASE}/{service}/FeatureServer/0/query"
        # point layers come back with geometry so they can be placed in 2020 tracts
        point = key in ("lihtc", "public_housing", "hud_multifamily")
        out[key] = fetch_cached_json(
            f"{key}.json", lambda u=url, w=where, f=fields, p=point: arcgis_query(u, w, f, geometry=p),
            offline)
    return out


# ---------------------------------------------------------------------------------------------
# Geometry helpers (no GIS dependencies)
# ---------------------------------------------------------------------------------------------
def _rings(geom: dict) -> list[list[list[list[float]]]]:
    """Polygons as lists of rings, for Polygon and MultiPolygon."""
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def _in_ring(x: float, y: float, ring) -> bool:
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_to_tract(points: list[tuple[float, float]], shapes: dict) -> list[str | None]:
    """2020 tract GEOID for each (lon, lat), by point-in-polygon with a bounding-box prefilter."""
    index = []
    for f in shapes["features"]:
        polys = _rings(f["geometry"])
        xs = [p[0] for poly in polys for p in poly[0]]
        ys = [p[1] for poly in polys for p in poly[0]]
        index.append((min(xs), min(ys), max(xs), max(ys), f["properties"]["GEOID"], polys))
    out = []
    for x, y in points:
        hit = None
        for x0, y0, x1, y1, geoid, polys in index:
            if x0 <= x <= x1 and y0 <= y <= y1 and any(
                    _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, h) for h in poly[1:])
                    for poly in polys):
                hit = geoid
                break
        out.append(hit)
    return out


# ---------------------------------------------------------------------------------------------
# Metric math (Census formulas for MOEs of sums and derived proportions)
# ---------------------------------------------------------------------------------------------
def col(df: pd.DataFrame, table: str, line: int, kind: str = "E") -> pd.Series:
    s = df[f"{table}_{kind}{line:03d}"].astype(float)
    return s.where(s >= 0) if kind == "E" else s.where(s >= 0, 0.0)   # negative = suppressed / N.A.


def summed(df, table, lines):
    est = sum(col(df, table, ln) for ln in lines)
    moe = np.sqrt(sum(col(df, table, ln, "M") ** 2 for ln in lines))
    return est, moe


def proportion(num, num_moe, den, den_moe):
    p = num / den.where(den > 0)
    inner = num_moe ** 2 - p ** 2 * den_moe ** 2
    # Census rule: use the ratio formula when the proportion formula's radicand is negative
    inner = inner.where(inner >= 0, num_moe ** 2 + p ** 2 * den_moe ** 2)
    return p, np.sqrt(inner) / den.where(den > 0)


def ratio_of_parts(df, table, num_lines, den_lines):
    n, nm = summed(df, table, num_lines)
    d, dm = summed(df, table, den_lines)
    return proportion(n, nm, d, dm)


def below_threshold_share(df: pd.DataFrame, threshold: float) -> tuple[pd.Series, pd.Series]:
    """Share of households under an income threshold, interpolating linearly within B19001 brackets."""
    edges = [0, 10e3, 15e3, 20e3, 25e3, 30e3, 35e3, 40e3, 45e3, 50e3, 60e3, 75e3, 100e3, 125e3,
             150e3, 200e3]
    num = 0.0
    num_var = 0.0
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else math.inf
        frac = 1.0 if threshold >= hi else max(0.0, (threshold - lo) / (hi - lo)) if hi < math.inf else 0.0
        if frac == 0:
            continue
        num = num + frac * col(df, "B19001", i + 2)
        num_var = num_var + (frac * col(df, "B19001", i + 2, "M")) ** 2
    return proportion(num, np.sqrt(num_var), col(df, "B19001", 1), col(df, "B19001", 1, "M"))


def build_metrics(acs: dict[str, pd.DataFrame], rent_2021: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df = pd.concat([t for t in acs.values()], axis=1)
    metro_id = next(i for i in df.index if i.startswith("310M") and i.endswith("US12060"))
    metro = df.loc[metro_id]
    df = df.drop(index=metro_id)
    m = pd.DataFrame(index=df.index)
    m["population"] = col(df, "B01003", 1)
    m["households"] = col(df, "B25003", 1)
    m["renter_households"] = col(df, "B25003", 3)

    def put(key, est_moe):
        m[key], m[key + "_moe"] = est_moe

    put("poverty_rate", ratio_of_parts(df, "C17002", [2, 3], [1]))
    put("below_200pct_poverty", ratio_of_parts(df, "C17002", [2, 3, 4, 5, 6, 7], [1]))
    mfi = float(metro["B19113_E001"])
    ami60_3p = 0.6 * mfi * 0.9          # HUD income limits scale a 3-person household to 90%
    put("below_60ami_share", below_threshold_share(df, ami60_3p))
    m["median_hh_income"], m["median_hh_income_moe"] = col(df, "B19013", 1), col(df, "B19013", 1, "M")
    put("snap_share", ratio_of_parts(df, "B22003", [2], [1]))

    put("low_income_renter_burden", ratio_of_parts(df, "B25106", [28, 32], [24]))
    computed = [2, 3, 4, 5, 6, 7, 8, 9, 10]
    put("rent_burden_30plus", ratio_of_parts(df, "B25070", [7, 8, 9, 10], computed))
    put("severe_rent_burden_50plus", ratio_of_parts(df, "B25070", [10], computed))

    put("renter_share", ratio_of_parts(df, "B25003", [3], [1]))
    for_rent, fr_m = col(df, "B25004", 2), col(df, "B25004", 2, "M")
    rented, rn_m = col(df, "B25004", 3), col(df, "B25004", 3, "M")
    stock = for_rent + rented + col(df, "B25003", 3)
    stock_m = np.sqrt(fr_m ** 2 + rn_m ** 2 + col(df, "B25003", 3, "M") ** 2)
    put("rental_vacancy_rate", proportion(for_rent, fr_m, stock, stock_m))
    r24, r24m = col(df, "B25064", 1), col(df, "B25064", 1, "M")
    r21 = col(rent_2021.reindex(df.index), "B25064", 1)
    r21m = col(rent_2021.reindex(df.index), "B25064", 1, "M")
    m["rent_growth"] = r24 / r21 - 1
    m["rent_growth_moe"] = np.sqrt(r24m ** 2 + (r24 / r21) ** 2 * r21m ** 2) / r21  # ratio MOE
    m["median_gross_rent"] = r24

    put("single_parent_poverty", ratio_of_parts(df, "B17010", [11, 17], [11, 17, 31, 37]))
    put("senior_poverty", ratio_of_parts(df, "B17020", [7, 8, 9], [7, 8, 9, 15, 16, 17]))
    put("disability_poverty", ratio_of_parts(df, "C18130", [4, 11, 18], [3, 10, 17]))
    put("limited_english_share", ratio_of_parts(df, "C16002", [4, 7, 10, 13], [1]))

    put("unemployment_rate", ratio_of_parts(df, "B23025", [5], [3]))
    put("part_time_share", ratio_of_parts(df, "B23022", [11, 18, 35, 42], [3, 27]))
    put("no_vehicle_share", ratio_of_parts(df, "B25044", [3, 10], [1]))

    put("overcrowded_share", ratio_of_parts(df, "B25014", [5, 6, 7, 11, 12, 13], [1]))
    put("pre1980_share", ratio_of_parts(df, "B25034", [7, 8, 9, 10, 11], [1]))
    lack = col(df, "B25048", 3) + col(df, "B25052", 3)
    lack_m = np.sqrt(col(df, "B25048", 3, "M") ** 2 + col(df, "B25052", 3, "M") ** 2)
    put("lacking_facilities", proportion(lack, lack_m, col(df, "B25048", 1), col(df, "B25048", 1, "M")))

    li_renters, li_renters_m = summed(df, "B25106", [25, 29])
    m["low_income_renters"] = li_renters
    m["low_income_renters_moe"] = li_renters_m
    m["households_moe"] = col(df, "B25003", 1, "M")
    put("student_share", ratio_of_parts(df, "B14007", [17, 18], [1]))

    m.index = m.index.str.replace("1400000US", "", regex=False)
    return m, {"metro_median_family_income": mfi, "ami60_3person": round(ami60_3p)}


def add_supply_gap(m: pd.DataFrame) -> None:
    """Supply gap: low-income renter households not matched by an assisted unit, per household.

    Replaced "assisted units per 100 low-income renters" on Nov 1, 2026. That ratio was zero in most
    tracts (they all tied) and blank wherever a tract had under 25 low-income renters, so a 15% weight
    moved the ranking by under 1% (weight_tests.py, test 2). This version is continuous, defined for
    every tract with households, and measures the gap directly: need minus supply, as a share of
    households. Assisted units are treated as exact, so its MOE comes from the renter count.
    """
    gap = (m["low_income_renters"] - m["assisted_units"]).clip(lower=0)
    m["unassisted_li_renters"] = gap
    m["unassisted_li_renter_share"], m["unassisted_li_renter_share_moe"] = proportion(
        gap, m["low_income_renters_moe"], m["households"], m["households_moe"])
    # kept for reference in the tract details; no longer scored
    m["assisted_units_per_100"] = 100 * m["assisted_units"] / m["low_income_renters"].where(
        m["low_income_renters"] >= 25)


def reliability(m: pd.DataFrame) -> None:
    for key, *_ in METRICS:
        est, moe = m[key], m[key + "_moe"]
        cv = moe / 1.645 / est.abs().where(est.abs() > 0)
        m[key + "_cv"] = cv
        limit = 0.30 if key == "median_hh_income" else 0.40
        # a proportion can pass on absolute terms (MOE <= 5 points) only if it's clearly above zero;
        # zero and near-zero estimates of rare things carry no ranking information
        abs_ok = ((moe <= 0.05) & (est >= moe)) if key != "median_hh_income" else est.isna() & False
        noisy = cv.isna() | ((cv > limit) & ~abs_ok)
        m[key + "_unreliable"] = est.isna() | noisy


def default_weights(m: pd.DataFrame, scored: pd.Series) -> dict:
    """Default metric weights: importance x reliable share, split among near-duplicate metrics."""
    keys = [k for k, *_ in METRICS]
    pct = pd.DataFrame({k: m.loc[scored, k].rank(pct=True) for k in keys})
    corr = pct.corr(method="spearman")
    weights = {}
    for k, g, *_ in METRICS:
        reliable = 1 - m.loc[scored, k + "_unreliable"].mean()
        peers = [o for o, og, *_ in METRICS if og == g and o != k]
        # metrics nearly duplicating a peer in the same group share their weight
        redundant = sum(1 for o in peers if abs(corr.loc[k, o]) >= 0.85)
        w = IMPORTANCE[k] * reliable / (1 + redundant)
        weights[k] = {"weight": max(1, round(float(w) / 3 * 100)), "importance": IMPORTANCE[k],
                      "reliable_share": round(float(reliable), 3),
                      "redundant_with": [o for o in peers if abs(corr.loc[k, o]) >= 0.85]}
    return {"metrics": weights, "corr": corr.round(2).to_dict()}


def need_percentiles(m: pd.DataFrame, rows: pd.Series, reliable_only: bool) -> pd.DataFrame:
    """Need percentile of every metric (1 = most need) among `rows`."""
    out = {}
    for k, _, _, direction, *_ in METRICS:
        v = m[k].where(rows & ~m[k + "_unreliable"]) if reliable_only else m[k].where(rows)
        out[k] = (v * direction).rank(pct=True)
    return pd.DataFrame(out)


def factor_weights(m: pd.DataFrame, base: pd.Series) -> dict:
    """Data-driven within-group weights (step 1 of the weighting plan).

    For each group, the first principal component of its metrics' need percentiles measures the
    signal they share. Each metric's weight is its squared loading divided by the group total, the
    OECD/JRC composite-indicator method. Metrics loading under 0.2, or against the group, add little
    shared signal and get a floor weight of 1 and a flag. A varimax factor analysis of all metrics
    is also run to check the grouping; its loadings are saved to FACTOR_CSV.

    All values are used here, unreliable ones included (noise lowers a metric's correlations and so
    its loading); missing values are set to the median percentile.
    """
    from sklearn.decomposition import PCA, FactorAnalysis

    keys = [k for k, *_ in METRICS]
    X = need_percentiles(m, base, reliable_only=False).loc[base[base].index].fillna(0.5)
    Z = (X - X.mean()) / X.std()
    out, groups = {}, {}
    for g, _ in GROUPS:
        ks = [k for k, kg, *_ in METRICS if kg == g]
        if len(ks) == 1:
            out[ks[0]] = {"weight": 100, "loading": 1.0, "flag": None}
            groups[g] = {"pc1_share": 1.0}
            continue
        pca = PCA().fit(Z[ks])
        load = pca.components_[0] * np.sqrt(pca.explained_variance_[0])   # correlation with PC1
        load = load * np.sign(load.sum())
        strong = np.where(load >= 0.2, load ** 2, 0.0)
        for k, l, sq in zip(ks, load, strong):
            flag = "against the group" if l < 0 else ("weak link" if l < 0.2 else None)
            out[k] = {"weight": max(1, round(float(sq / strong.sum()) * 100)), "loading": round(float(l), 2),
                      "flag": flag}
        groups[g] = {"pc1_share": round(float(pca.explained_variance_ratio_[0]), 3)}

    eig = np.sort(np.linalg.eigvalsh(Z.corr().values))[::-1]
    n = int((eig > 1).sum())
    fa = FactorAnalysis(n_components=n, rotation="varimax", random_state=0).fit(Z)
    loadings = pd.DataFrame(fa.components_.T, index=keys, columns=[f"factor_{i + 1}" for i in range(n)])
    loadings.insert(0, "group", [g for _, g, *_ in METRICS])
    loadings.round(3).to_csv(FACTOR_CSV)
    return {"metrics": out, "groups": groups, "eigenvalues": [round(float(e), 2) for e in eig[:8]],
            "first_factor_share": round(float(eig[0] / len(keys)), 3)}


def score_draws(P: np.ndarray, avail: np.ndarray, group_of: np.ndarray, gw: np.ndarray,
                mw: np.ndarray) -> np.ndarray:
    """Scores for many weight sets at once. gw: draws x groups, mw: draws x metrics."""
    n_groups = gw.shape[1]
    num = np.zeros((gw.shape[0], P.shape[0]))
    den = np.zeros_like(num)
    Pz = np.where(avail, P, 0.0)
    for g in range(n_groups):
        cols = group_of == g
        w = mw[:, cols]                                       # draws x k
        gn = w @ Pz[:, cols].T                                # draws x tracts
        gd = w @ avail[:, cols].T.astype(float)
        has = gd > 0
        num += np.where(has, gw[:, [g]] * gn / np.where(has, gd, 1), 0)
        den += np.where(has, gw[:, [g]], 0)
    return num / np.where(den > 0, den, np.nan)


def robustness(m: pd.DataFrame, ranked: pd.Series, gw0: np.ndarray, mw0: np.ndarray,
               spread: float | None, draws: int = 1000, seed: int = 7) -> pd.DataFrame:
    """Monte Carlo weight uncertainty (step 4). spread=0.5 scales every weight by a random factor in
    [0.5, 1.5]; spread=None draws any weights at all (flat Dirichlet within and across groups)."""
    rng = np.random.default_rng(seed)
    P = need_percentiles(m, ranked, reliable_only=True).loc[ranked[ranked].index]
    avail = P.notna().to_numpy()
    P = P.to_numpy()
    gkeys = [g for g, _ in GROUPS]
    group_of = np.array([gkeys.index(g) for _, g, *_ in METRICS])
    if spread is None:
        gw = rng.dirichlet(np.ones(len(gkeys)), draws)
        mw = rng.dirichlet(np.ones(len(METRICS)), draws)        # only within-group ratios matter
    else:
        gw = gw0 * rng.uniform(1 - spread, 1 + spread, (draws, len(gkeys)))
        mw = mw0 * rng.uniform(1 - spread, 1 + spread, (draws, len(METRICS)))
    S = score_draws(P, avail, group_of, gw, mw)
    ranks = (-S).argsort(axis=1).argsort(axis=1) + 1            # 1 = highest need
    top = int(round(0.1 * S.shape[1]))
    return pd.DataFrame({"top10_share": (ranks <= top).mean(axis=0),
                         "rank_median": np.median(ranks, axis=0),
                         "rank_p05": np.percentile(ranks, 5, axis=0),
                         "rank_p95": np.percentile(ranks, 95, axis=0)}, index=ranked[ranked].index)


def main(offline: bool = False) -> None:
    tracts = pd.read_csv(TRACTS_CSV, dtype={"tract_geoid": str, "county_fips": str})
    prefixes = tuple(f"1400000US13{c}" for c in ARC_COUNTIES)
    acs = {t: fetch_acs(t, 2024, prefixes, offline) for t in ACS_TABLES}
    rent_2021 = fetch_acs("B25064", 2021, prefixes, offline)
    shapes = fetch_tract_shapes(offline)
    county_shapes = fetch_county_shapes(offline)
    hud = fetch_hud(offline)

    m, meta = build_metrics(acs, rent_2021)
    m = m.reindex(tracts["tract_geoid"])
    m.index.name = "tract_geoid"
    print(f"ACS rows matched: {m['population'].notna().sum()} of {len(m)} tracts")

    # --- supply: place HUD points in 2020 tracts -------------------------------------------
    supply = pd.DataFrame(0.0, index=m.index, columns=["lihtc_units", "public_housing_units",
                                                       "hud_mf_assisted_units"])
    for key, units_field, col_name in [("lihtc", "LI_UNITS", "lihtc_units"),
                                       ("public_housing", "TOTAL_DWELLING_UNITS", "public_housing_units"),
                                       ("hud_multifamily", "TOTAL_ASSISTED_UNIT_COUNT", "hud_mf_assisted_units")]:
        feats = [f for f in hud[key] if f.get("geometry")]
        pts = [tuple(f["geometry"]["coordinates"][:2]) for f in feats]
        geoids = point_to_tract(pts, shapes)
        units = pd.Series([float(f["properties"].get(units_field) or 0) for f in feats])
        placed = pd.DataFrame({"g": geoids, "u": units}).dropna(subset=["g"])
        supply[col_name] = placed.groupby("g")["u"].sum().reindex(m.index).fillna(0)
        print(f"{key}: {len(hud[key])} records, {len(feats)} with coordinates, "
              f"{len(placed)} placed in ARC tracts, {placed['u'].sum():,.0f} units")
    m = m.join(supply)
    m["assisted_units"] = supply.sum(axis=1)
    add_supply_gap(m)

    vouchers = pd.DataFrame([f["attributes"] for f in hud["vouchers"]])
    m["vouchers"] = vouchers.set_index("GEOID")["HCV_PUBLIC"].reindex(m.index)
    m["vouchers"] = m["vouchers"].where(m["vouchers"] >= 0)     # negative = 10 or fewer, suppressed
    qct = {f["attributes"]["GEOID"] for f in hud["qct_2026"]}
    m["qct_2026"] = m.index.isin(qct)
    print(f"vouchers matched: {m['vouchers'].notna().sum()} tracts; QCTs: {m['qct_2026'].sum()}")

    m = m.copy()
    m["student_tract"] = m["student_share"] >= STUDENT_SHARE_FLAG
    m["low_population"] = m["population"].fillna(0) < 500
    reliability(m)
    m["unreliable_metrics"] = [";".join(k for k, *_ in METRICS if row[k + "_unreliable"])
                               for _, row in m.iterrows()]
    m = m.join(tracts.set_index("tract_geoid")[["county", "land_sqmi", "lat", "lon"]])
    # --- weights: expert judgment, factor analysis, and robustness of the ranking ------------
    scored = ~m["low_population"] & m["population"].notna()
    ranked = scored & ~m["student_tract"]
    weights = default_weights(m, ranked)
    fa = factor_weights(m, ranked)
    print(f"factor analysis: first factor explains {fa['first_factor_share']:.0%} of variance; "
          f"eigenvalues {fa['eigenvalues']}")
    gw0 = np.array([GROUP_WEIGHTS[g] for g, _ in GROUPS], dtype=float)
    mw0 = np.array([fa["metrics"][k]["weight"] for k, *_ in METRICS], dtype=float)
    # how much the within-group method matters: factor-analysis vs expert weights, same group weights
    mw_exp = np.array([weights["metrics"][k]["weight"] for k, *_ in METRICS], dtype=float)
    both = robustness(m, ranked, gw0, mw0, spread=0.0, draws=1)["rank_median"], \
        robustness(m, ranked, gw0, mw_exp, spread=0.0, draws=1)["rank_median"]
    top = int(round(0.1 * len(both[0])))
    fa["vs_expert"] = {"spearman": round(float(both[0].corr(both[1], method="spearman")), 3),
                       "top10_overlap": int(((both[0] <= top) & (both[1] <= top)).sum()), "top10": top}
    print(f"factor-analysis vs expert weights: {fa['vs_expert']}")
    for label, spread in [("small", 0.5), ("any", None)]:
        r = robustness(m, ranked, gw0, mw0, spread)
        m = m.join(r.add_prefix(f"robust_{label}_"))
        fa[f"robust_{label}"] = int((r["top10_share"] >= 0.9).sum())
        print(f"robustness ({label} weight changes): {fa[f'robust_{label}']} tracts in the "
              f"top 10% in 90%+ of draws")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(OUT_CSV, float_format="%.10g")
    print(f"wrote {OUT_CSV.relative_to(ROOT)} and {FACTOR_CSV.relative_to(ROOT)}")
    write_app_data(m, shapes, county_shapes, weights, fa, meta, scored)


def write_app_data(m, shapes, county_shapes, weights, fa, meta, scored) -> None:
    rows = {}
    keys = [k for k, *_ in METRICS]
    pct, pct_all = {}, {}
    for k, _, _, direction, *_ in METRICS:
        # need percentile: 1 = most need. "p" ranks among tracts with a reliable value (the default);
        # "pa" ranks every value, for the tool's "use unreliable values anyway" option.
        pct[k] = (m[k].where(scored & ~m[k + "_unreliable"]) * direction).rank(pct=True)
        pct_all[k] = (m[k].where(scored) * direction).rank(pct=True)
    for g in m.index:
        r = m.loc[g]
        rows[g] = {
            "c": r["county"], "pop": None if pd.isna(r["population"]) else int(r["population"]),
            "hh": None if pd.isna(r["households"]) else int(r["households"]),
            "st": round(float(r["student_share"]), 3) if pd.notna(r["student_share"]) else None,
            "q": bool(r["qct_2026"]), "lp": bool(r["low_population"]),
            "v": [None if pd.isna(r[k]) else round(float(r[k]), 4) for k in keys],
            "p": [None if pd.isna(pct[k][g]) else round(float(pct[k][g]), 5) for k in keys],
            "pa": [None if pd.isna(pct_all[k][g]) else round(float(pct_all[k][g]), 5) for k in keys],
            "u": [bool(r[k + "_unreliable"]) for k in keys],
            "s": [int(r["lihtc_units"]), int(r["public_housing_units"]), int(r["hud_mf_assisted_units"]),
                  None if pd.isna(r["vouchers"]) else int(r["vouchers"])],
        }
    for f in shapes["features"]:
        f["properties"] = {"g": f["properties"]["GEOID"]}
    data = {
        "groups": [{"key": k, "label": lbl, "weight": GROUP_WEIGHTS[k], **fa["groups"][k]}
                   for k, lbl in GROUPS],
        "metrics": [{"key": k, "group": g, "label": lbl, "dir": d, "fmt": fmt, "src": src,
                     **{kk: vv for kk, vv in weights["metrics"][k].items() if kk != "weight"},
                     "weight_expert": weights["metrics"][k]["weight"],
                     "weight": fa["metrics"][k]["weight"], "loading": fa["metrics"][k]["loading"],
                     "fa_flag": fa["metrics"][k]["flag"]} for k, g, lbl, d, fmt, src in METRICS],
        "tracts": rows, "shapes": shapes, "counties": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": f["geometry"], "properties": {"n": f["properties"]["BASENAME"]}}
            for f in county_shapes["features"]]}, "caveats": CAVEATS,
        "meta": {**meta, "acs": "ACS 2020-2024 5-year", "n_tracts": len(m),
                 "eigenvalues": fa["eigenvalues"], "first_factor_share": fa["first_factor_share"],
                 "vs_expert": fa["vs_expert"], "robust_small": fa["robust_small"],
                 "robust_any": fa["robust_any"],
                 "student_flag": STUDENT_SHARE_FLAG},
    }
    APP_DATA.parent.mkdir(parents=True, exist_ok=True)
    APP_DATA.write_text("window.NEED_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"wrote {APP_DATA.relative_to(ROOT)} ({APP_DATA.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="use cached downloads only")
    main(ap.parse_args().offline)
