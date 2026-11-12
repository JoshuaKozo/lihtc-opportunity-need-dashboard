"""Turn the downloaded opportunity sources into tract and ZCTA tables.

    python -m pipelines.opportunity.pull_opportunity     # first: download the sources
    python -m pipelines.opportunity.build_opportunity

Outputs (data/processed/):
  opportunity_tract.parquet / .csv   one row per 2020 Georgia census tract: raw indicators,
                                     metro percentiles, domain scores, and the opportunity score
  opportunity_zcta.parquet / .csv    the same rolled up to 2020 ZCTAs (ZIP code areas) that touch
                                     the 11 ARC counties, to sit next to the ZCTA need score

"Opportunity" here means what living in a place gives a resident (mobility, jobs, schools,
transit, health care, food, environment), measured from the tract's population-weighted centroid.
It deliberately leaves out resident-need measures (income, poverty, cost burden).

Geography. Everything ends on 2020 tracts. Sources published on 2010 tracts or block groups
(Opportunity Atlas, EPA SLD, HUD AFFH) are moved to 2020 tracts with Opportunity Insights'
2010->2020 crosswalk weights. Tracts roll up to ZCTAs weighted by estimated population in the
overlap (tract population x share of the tract's land in the ZCTA; block populations would be
more exact but need a 1 GB national file).

Scoring. Each scored indicator becomes a percentile (0-100, higher = more opportunity) among the
tracts in the 11 ARC counties. A domain score is the weighted mean of its indicators' percentiles,
the opportunity index is the weighted mean of the domains (weights and their rationale are in
SCORED and DOMAIN_WEIGHTS), and opportunity_score is that index re-ranked to 0-100. Tracts
outside the ARC counties keep their raw indicators but get no percentiles or scores.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "opportunity"
OUT_DIR = ROOT / "data" / "processed" / "opportunity"

# The 11 Atlanta Regional Commission counties (same set as parse_applications.ARC_COUNTIES).
ARC_COUNTY_FIPS = {"13057": "Cherokee", "13063": "Clayton", "13067": "Cobb", "13089": "DeKalb",
                   "13097": "Douglas", "13113": "Fayette", "13117": "Forsyth", "13121": "Fulton",
                   "13135": "Gwinnett", "13151": "Henry", "13247": "Rockdale"}
EARTH_MI = 3958.8
JOBS_RADIUS_MI = 5.0
STOPS_RADIUS_MI = 0.5

# indicator -> (domain, direction). direction +1: higher is more opportunity; -1: lower is.
# Indicators not listed here (e.g. hud_*, jobs_in_tract) are kept for reference but not scored.
SCORED = {
    # mobility: the one measured outcome for low-income kids; jail rate adds a second outcome
    "oa_kfr_p25": ("mobility", +1, 0.70),
    "oa_jail_p25": ("mobility", -1, 0.30),
    # jobs: what a job seeker without a college degree can reach. Transit and car reach count
    # equally, because car access predicts employment better than transit (Blumenberg & Pierce
    # 2014); non-degree jobs nearby, and those jobs per non-degree worker competing for them
    # (competition-adjusted access, Shen 1998), count next; job growth least, because it is
    # noisy year to year and only weakly tied to outcomes (Opportunity Atlas)
    "epa_jobs_45min_transit": ("jobs", +1, 0.25),
    "epa_jobs_45min_auto": ("jobs", +1, 0.25),
    "jobs_nondegree_5mi": ("jobs", +1, 0.20),
    "jobs_per_nondegree_worker_5mi": ("jobs", +1, 0.20),
    "jobs_growth_5mi": ("jobs", +1, 0.10),
    # schools: elementary uses 3 schools (more stable) and covers the most years of a child's life
    "school_prof_elem3": ("schools", +1, 0.40),
    "school_prof_middle": ("schools", +1, 0.20),
    "school_prof_high": ("schools", +1, 0.20),
    "school_grad_rate_high": ("schools", +1, 0.20),
    "marta_rail_dist_mi": ("transit", -1, 0.40),
    "marta_stops_half_mi": ("transit", +1, 0.30),
    "epa_walkability": ("transit", +1, 0.30),
    # health: grocery distance counts least, because store supply explains little of the
    # nutrition gap between rich and poor households (Allcott, Diamond & Dube 2019)
    "fqhc_dist_mi": ("health", -1, 0.40),
    "places_uninsured": ("health", -1, 0.40),
    "food_low_access_share": ("health", -1, 0.20),
    "hud_env_health": ("environment", +1, 1.00),
}

# Optional indicators: not in the default score (weight 0), but the web module lets users switch
# them on. Same (domain, direction) convention as SCORED.
OPTIONAL = {
    "park_dist_mi": ("health", -1),              # nearest park of 1+ acres (OpenStreetMap)
    "bb_wired_100_20_share": ("jobs", +1),       # wired 100/20 Mbps broadband (FCC); needs the manual file
    "hud_school_proficiency": ("schools", +1),   # HUD AFFH indices, 2010 geography, older data
    "hud_jobs_proximity": ("jobs", +1),
    "hud_labor_market": ("jobs", +1),            # describes residents' employment, so closer to "need"
    "hud_transport_cost": ("transit", +1),
}

# Domain weights for the composite (sum to 1). Calibrated by pipelines/opportunity/calibrate_weights.py:
# literature sets a prior and an allowed range for each domain, and tract outcomes (Opportunity
# Atlas mobility, CDC life expectancy) move the weights within those ranges. The reasoning and
# sources are in docs/opportunity_weights.md. Rounded to whole percents.
#   mobility 34%  schools 27%  environment 12%  health 11%  jobs 11%  transit 5%
# jobs and transit sit at the bottom of their literature ranges: the outcome data give them no
# weight (central, job-rich tracts had worse outcomes for children born around 1980), but the
# spatial-mismatch evidence and the needs of car-free households keep a floor under them.
DOMAIN_WEIGHTS = {"mobility": 0.34, "schools": 0.27, "jobs": 0.11, "transit": 0.05,
                  "health": 0.11, "environment": 0.12}
TIERS = ["Very low", "Low", "Moderate", "High", "Very high"]


# --------------------------------------------------------------------------- helpers

def haversine_mi(lat1, lon1, lat2, lon2):
    """Pairwise great-circle miles between points (arrays broadcast)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_MI * np.arcsin(np.sqrt(a))


def nearest(tracts: pd.DataFrame, pts: pd.DataFrame, k=1, chunk=300) -> tuple[np.ndarray, np.ndarray]:
    """Distances (miles) and row positions of the k nearest `pts` to each tract centroid."""
    plat, plon = pts["lat"].to_numpy(float), pts["lon"].to_numpy(float)
    dists, idx = [], []
    for i in range(0, len(tracts), chunk):
        t = tracts.iloc[i:i + chunk]
        d = haversine_mi(t["lat"].to_numpy(float)[:, None], t["lon"].to_numpy(float)[:, None],
                         plat[None, :], plon[None, :])
        j = np.argsort(d, axis=1)[:, :k]
        idx.append(j)
        dists.append(np.take_along_axis(d, j, axis=1))
    return np.vstack(dists), np.vstack(idx)


def sum_within(tracts: pd.DataFrame, pts: pd.DataFrame, weight: str | None, radius: float,
               chunk=200) -> np.ndarray:
    """Sum of `weight` (or count) over points within `radius` miles of each tract centroid."""
    plat, plon = pts["lat"].to_numpy(float), pts["lon"].to_numpy(float)
    w = np.ones(len(pts)) if weight is None else pts[weight].to_numpy(float)
    out = []
    for i in range(0, len(tracts), chunk):
        t = tracts.iloc[i:i + chunk]
        d = haversine_mi(t["lat"].to_numpy(float)[:, None], t["lon"].to_numpy(float)[:, None],
                         plat[None, :], plon[None, :])
        out.append((d <= radius) @ w)
    return np.concatenate(out)


def to_2020(values: pd.DataFrame, key: str) -> pd.DataFrame:
    """Move 2010-tract values (indexed by 11-digit `key`) to 2020 tracts with the Opportunity
    Insights crosswalk. Weights are renormalized over the 2010 tracts that have a value."""
    xw = pd.read_csv(RAW / "oa_tract_2010_2020_crosswalk_ga.csv", dtype=str)
    long = []
    for i in range(1, 6):
        part = xw[["tract_2020", f"tract_2010_{i}", f"wgt_{i}"]].dropna()
        part.columns = ["tract_2020", key, "w"]
        long.append(part)
    long = pd.concat(long)
    long["w"] = long["w"].astype(float)
    long = long.merge(values, on=key)
    cols = [c for c in values.columns if c != key]
    out = {}
    for c in cols:
        ok = long[c].notna()
        g = long[ok].assign(wx=long.loc[ok, c] * long.loc[ok, "w"]).groupby("tract_2020")
        out[c] = g["wx"].sum() / g["w"].sum()
    return pd.DataFrame(out).rename_axis("tract").reset_index()


def to_num(s: pd.Series) -> pd.Series:
    """GOSA/HUD numbers with suppression codes ('TFS', blanks) -> float."""
    return pd.to_numeric(s, errors="coerce")


# --------------------------------------------------------------------------- tract universe

def load_tracts() -> pd.DataFrame:
    c = pd.read_csv(RAW / "cenpop2020_tract_ga.csv", dtype=str, encoding="utf-8-sig")
    t = pd.DataFrame({
        "tract": c["STATEFP"] + c["COUNTYFP"] + c["TRACTCE"],
        "county_fips": c["STATEFP"] + c["COUNTYFP"],
        "pop_2020": c["POPULATION"].astype(int),
        "lat": c["LATITUDE"].astype(float),
        "lon": c["LONGITUDE"].astype(float),
    })
    t["county"] = t["county_fips"].map(ARC_COUNTY_FIPS)
    t["in_arc"] = t["county"].notna()
    return t


# --------------------------------------------------------------------------- sources

def mobility() -> pd.DataFrame:
    """Opportunity Atlas: mean adult household income rank (0-1) and incarceration rate of children
    raised in the tract by parents at the 25th income percentile (birth cohorts 1978-83)."""
    oa = pd.read_csv(RAW / "oa_tract_outcomes_ga.csv", dtype={"state": str, "county": str, "tract": str})
    oa["tract_2010"] = (oa["state"].str.zfill(2) + oa["county"].str.zfill(3) + oa["tract"].str.zfill(6))
    vals = oa[["tract_2010"]].assign(
        oa_kfr_p25=oa["kfr_pooled_pooled_p25"], oa_jail_p25=oa["jail_pooled_pooled_p25"],
        oa_kfr_black_p25=oa["kfr_black_pooled_p25"])
    return to_2020(vals, "tract_2010")


def epa_sld() -> pd.DataFrame:
    """EPA Smart Location Database v3 (2010 block groups), population-weighted to 2010 tracts."""
    d = pd.read_csv(RAW / "epa_sld_v3_ga.csv", dtype={"STATEFP": str, "COUNTYFP": str, "TRACTCE": str})
    d["tract_2010"] = d["STATEFP"].str.zfill(2) + d["COUNTYFP"].str.zfill(3) + d["TRACTCE"].str.zfill(6)
    d = d.replace(-99999, np.nan)
    d["D5BR"] = d["D5BR"].fillna(0)     # -99999 here means no transit service: 0 jobs by transit
    d["w"] = d["TotPop"].clip(lower=0) + 1e-6      # unpopulated tracts fall back to a plain mean
    cols = {"D5BR": "epa_jobs_45min_transit", "D5AR": "epa_jobs_45min_auto", "NatWalkInd": "epa_walkability"}
    out = {}
    for src, name in cols.items():
        ok = d[src].notna()
        g = d[ok].assign(wx=d.loc[ok, src] * d.loc[ok, "w"]).groupby("tract_2010")
        out[name] = g["wx"].sum() / g["w"].sum()
    return to_2020(pd.DataFrame(out).reset_index(), "tract_2010")


def hud_affh() -> pd.DataFrame:
    """HUD AFFH opportunity indices (AFFHT0006, 2010 geography), 0-100, higher = more opportunity.
    School proficiency and jobs proximity are block-group indices, averaged to tracts."""
    frames = []
    for key, col in {"hud_school_proficiency": "SCHL_IDX", "hud_jobs_proximity": "JOBS_IDX",
                     "hud_labor_market": "LBR_IDX", "hud_transport_cost": "TCOST_IDX",
                     "hud_env_health": "HAZ_IDX"}.items():
        d = pd.read_csv(RAW / f"{key}_ga.csv", dtype={"GEOID": str})
        if col not in d.columns:   # field name differs by layer; take the one *_IDX column
            col = next(c for c in d.columns if c.endswith("_IDX"))
        d["tract_2010"] = d["GEOID"].str[:11]
        frames.append(d.assign(v=to_num(d[col]).where(lambda x: x >= 0))
                      .groupby("tract_2010")["v"].mean().rename(key))
    return to_2020(pd.concat(frames, axis=1).reset_index(), "tract_2010")


def jobs(tracts: pd.DataFrame) -> pd.DataFrame:
    """LODES jobs (all jobs, primary + secondary) located within JOBS_RADIUS_MI of each tract
    centroid, the latest year and five years earlier, plus jobs located in the tract itself."""
    xw = pd.read_csv(RAW / "ga_lodes_block_xwalk.csv", dtype={"tabblk2020": str, "trct": str})
    files = sorted(RAW.glob("ga_wac_S000_JT00_*.csv.gz"))
    base_year, latest_year = (int(re.search(r"(\d{4})", f.name).group(1)) for f in (files[0], files[-1]))
    out = tracts[["tract"]].copy()
    for year, f in ((latest_year, files[-1]), (base_year, files[0])):
        wac = pd.read_csv(f, dtype={"w_geocode": str}, usecols=["w_geocode", "C000"])
        pts = wac.merge(xw, left_on="w_geocode", right_on="tabblk2020")
        pts = pts.rename(columns={"blklatdd": "lat", "blklondd": "lon"})
        out[f"jobs_within_5mi_{year}"] = sum_within(tracts, pts, "C000", JOBS_RADIUS_MI)
        if year == latest_year:
            out["jobs_in_tract"] = out["tract"].map(pts.groupby("trct")["C000"].sum()).fillna(0)
    # Skill-matched, competition-adjusted access (Shen 1998): jobs held by workers without a
    # bachelor's degree within 5 miles, and those jobs per non-degree worker living within 5 miles.
    # LODES reports education for workers 30 and older.
    wac = pd.read_csv(files[-1], dtype={"w_geocode": str}, usecols=["w_geocode", "CD01", "CD02", "CD03"])
    wac = wac.assign(nd=wac[["CD01", "CD02", "CD03"]].sum(axis=1)).merge(xw, left_on="w_geocode", right_on="tabblk2020")
    out["jobs_nondegree_5mi"] = sum_within(tracts, wac.rename(columns={"blklatdd": "lat", "blklondd": "lon"}), "nd", JOBS_RADIUS_MI)
    rac_f = RAW / f"ga_rac_S000_JT01_{latest_year}.csv.gz"
    if rac_f.exists():
        rac = pd.read_csv(rac_f, dtype={"h_geocode": str}, usecols=["h_geocode", "CD01", "CD02", "CD03"])
        rac = rac.assign(nd=rac[["CD01", "CD02", "CD03"]].sum(axis=1)).merge(xw, left_on="h_geocode", right_on="tabblk2020")
        workers = sum_within(tracts, rac.rename(columns={"blklatdd": "lat", "blklondd": "lon"}), "nd", JOBS_RADIUS_MI)
        out["jobs_per_nondegree_worker_5mi"] = out["jobs_nondegree_5mi"] / np.where(workers == 0, np.nan, workers)
    out["jobs_within_5mi"] = out[f"jobs_within_5mi_{latest_year}"]
    out["jobs_growth_5mi"] = (out[f"jobs_within_5mi_{latest_year}"]
                              / out[f"jobs_within_5mi_{base_year}"].replace(0, np.nan) - 1)
    return out.drop(columns=[f"jobs_within_5mi_{latest_year}"])


# Abbreviations GOSA and NCES spell differently.
_ABBR = {"elem": "elementary", "es": "elementary", "ms": "middle", "hs": "high", "sch": "school",
         "acad": "academy", "sr": "senior", "jr": "junior", "st": "saint", "mt": "mount", "ctr": "center"}
_FILLER = {"school", "the", "of", "and", "for", "at", "academy", "charter", "magnet"}
_LEVELS = {"elementary", "middle", "high", "senior", "junior"}


def school_key(name: str, loose: bool = False) -> str:
    s = re.sub(r"\(.*?\)", " ", str(name).lower().replace("&", " and "))
    words = [_ABBR.get(w, w) for w in re.sub(r"[^a-z0-9 ]", " ", s).split()]
    drop = _FILLER | (_LEVELS if loose else set())
    return " ".join(w for w in words if w not in drop)


def match_schools(gosa: pd.DataFrame, nces: pd.DataFrame) -> pd.Series:
    """NCES id for each GOSA school (district code + name). GOSA's school numbers aren't in the
    NCES file, so names are matched within a district: first exactly (after normalizing), then
    ignoring level words ("Elementary", "High"). Only one-to-one matches are kept."""
    nces = nces.assign(dist=nces["ST_LEAID"].str[3:])
    out = pd.Series(pd.NA, index=gosa.index, dtype="object")
    for loose in (False, True):
        todo = gosa[out.isna()]
        a = todo["SCHOOL_DISTRCT_CD"] + "|" + todo["INSTN_NAME"].map(lambda x: school_key(x, loose))
        free = nces[~nces["NCESSCH"].isin(out.dropna())]
        b = free["dist"] + "|" + free["SCH_NAME"].map(lambda x: school_key(x, loose))
        a, b = a[~a.duplicated(keep=False)], b[~b.duplicated(keep=False)]
        lookup = pd.Series(free.loc[b.index, "NCESSCH"].to_numpy(), index=b.to_numpy())
        out.loc[a.index] = a.map(lookup)
    return out


def school_points() -> pd.DataFrame:
    """Regular, in-person, non-charter public schools with Milestones proficiency and graduation rates.
    Proficiency = share of All Students tests scored Proficient or Distinguished, pooled over
    ELA and Math (EOG, grades 3-8) or Algebra and American Literature (EOC, high school)."""
    files = {p.name.split("_")[0]: p for p in RAW.glob("*.csv") if re.match(r"(EOG|EOC|Graduation)_", p.name)}
    tests = []
    for kind, subjects in {"EOG": ["English Language Arts", "Mathematics"],
                           "EOC": ["Algebra: Concepts and Connections",
                                   "American Literature and Composition"]}.items():
        d = pd.read_csv(files[kind], dtype=str, encoding="latin1")
        d = d[(d["SUBGROUP_NAME"] == "All Students") & (d["INSTN_NUMBER"] != "ALL")
              & d["TEST_CMPNT_TYP_NM"].isin(subjects)]
        d = d.assign(n=to_num(d["NUM_TESTED_CNT"]),
                     good=to_num(d["PROFICIENT_CNT"]) + to_num(d["DISTINGUISHED_CNT"])).dropna(subset=["n", "good"])
        g = d.groupby(["SCHOOL_DISTRCT_CD", "INSTN_NUMBER"]).agg(
            INSTN_NAME=("INSTN_NAME", "first"), n=("n", "sum"), good=("good", "sum")).reset_index()
        tests.append(g.assign(**{f"prof_{kind.lower()}": 100 * g["good"] / g["n"]})
                     .drop(columns=["n", "good"]))
    gosa = tests[0].merge(tests[1], on=["SCHOOL_DISTRCT_CD", "INSTN_NUMBER"], how="outer",
                          suffixes=("", "_eoc"))
    gosa["INSTN_NAME"] = gosa["INSTN_NAME"].fillna(gosa.pop("INSTN_NAME_eoc"))

    gr = pd.read_csv(files["Graduation"], dtype=str, encoding="latin1")
    gr = gr[(gr["DETAIL_LVL_DESC"] == "School") & (gr["LABEL_LVL_1_DESC"] == "Grad Rate -ALL Students")]
    gr = gr.assign(grad_rate=to_num(gr["PROGRAM_PERCENT"]))[
        ["SCHOOL_DSTRCT_CD", "INSTN_NUMBER", "INSTN_NAME", "grad_rate"]].rename(
        columns={"SCHOOL_DSTRCT_CD": "SCHOOL_DISTRCT_CD"})
    gosa = gosa.merge(gr, on=["SCHOOL_DISTRCT_CD", "INSTN_NUMBER"], how="outer", suffixes=("", "_gr"))
    gosa["INSTN_NAME"] = gosa["INSTN_NAME"].fillna(gosa.pop("INSTN_NAME_gr"))

    nces = pd.read_csv(RAW / "nces_public_schools_ga.csv", dtype=str)
    # Charter and virtual schools don't serve a neighborhood, so they don't count as "nearby".
    nces = nces[(nces["SCHOOL_TYPE_TEXT"] == "Regular School") & (nces["VIRTUAL"] == "Not Virtual")
                & (nces["CHARTER_TEXT"] != "Yes") & (nces["SY_STATUS_TEXT"] != "Closed")]
    gosa["NCESSCH"] = match_schools(gosa, nces)
    pts = nces.merge(gosa.dropna(subset=["NCESSCH"]).drop(columns="INSTN_NAME"), on="NCESSCH")
    grade = lambda s: pd.to_numeric(s.replace({"PK": "-1", "KG": "0", "UG": np.nan, "N": np.nan}),
                                    errors="coerce")
    lo, hi = grade(pts["GSLO"]), grade(pts["GSHI"])
    pts["serves_elem"] = (lo <= 5) & (hi >= 3)
    pts["serves_middle"] = (lo <= 8) & (hi >= 6)
    pts["serves_high"] = (lo <= 12) & (hi >= 9)
    pts["lat"], pts["lon"] = pts["lat"].astype(float), pts["lon"].astype(float)
    print(f"schools: {gosa['NCESSCH'].notna().sum()} of {len(gosa)} GOSA schools matched to NCES points")
    return pts


def schools(tracts: pd.DataFrame) -> pd.DataFrame:
    """Elementary: mean proficiency of the 3 nearest elementary schools (HUD's AFFH method).
    Middle and high: the nearest school. Attendance zones would be better (see DATA_SOURCES.md)."""
    pts = school_points()
    out = tracts[["tract"]].copy()
    elem = pts[pts["serves_elem"] & pts["prof_eog"].notna()]
    _, j = nearest(tracts, elem, k=3)
    out["school_prof_elem3"] = elem["prof_eog"].to_numpy()[j].mean(axis=1)
    mid = pts[pts["serves_middle"] & pts["prof_eog"].notna()]
    _, j = nearest(tracts, mid)
    out["school_prof_middle"] = mid["prof_eog"].to_numpy()[j[:, 0]]
    high = pts[pts["serves_high"] & pts["prof_eoc"].notna()]
    d, j = nearest(tracts, high)
    out["school_prof_high"] = high["prof_eoc"].to_numpy()[j[:, 0]]
    out["nearest_high_school"] = high["SCH_NAME"].to_numpy()[j[:, 0]]
    out["nearest_high_school_mi"] = d[:, 0]
    grad = high.dropna(subset=["grad_rate"])
    _, j = nearest(tracts, grad)
    out["school_grad_rate_high"] = grad["grad_rate"].to_numpy()[j[:, 0]]
    return out


def transit(tracts: pd.DataFrame) -> pd.DataFrame:
    """MARTA: distance to the nearest rail station and number of stops within half a mile.
    MARTA only; Cobb, Gwinnett, and Xpress service are captured by EPA's transit job access."""
    z = zipfile.ZipFile(RAW / "marta_gtfs.zip")
    stops = pd.read_csv(z.open("stops.txt"), dtype=str)
    stops["lat"], stops["lon"] = stops["stop_lat"].astype(float), stops["stop_lon"].astype(float)
    stations = stops[(stops["location_type"] == "1")      # parent stations, minus bus park & rides
                     & ~stops["stop_name"].str.contains("PARK & RIDE")]
    boardable = stops[stops["location_type"].fillna("0") == "0"]
    out = tracts[["tract"]].copy()
    d, _ = nearest(tracts, stations)
    out["marta_rail_dist_mi"] = d[:, 0]
    out["marta_stops_half_mi"] = sum_within(tracts, boardable, None, STOPS_RADIUS_MI)
    return out


def health(tracts: pd.DataFrame) -> pd.DataFrame:
    out = tracts[["tract"]].copy()
    h = pd.read_csv(RAW / "hrsa_health_center_sites_ga.csv", dtype=str)
    h = h[h["Site Status Description"] == "Active"].assign(
        lon=lambda x: to_num(x["Geocoding Artifact Address Primary X Coordinate"]),
        lat=lambda x: to_num(x["Geocoding Artifact Address Primary Y Coordinate"])).dropna(subset=["lat", "lon"])
    d, _ = nearest(tracts, h)
    out["fqhc_dist_mi"] = d[:, 0]

    p = pd.read_csv(RAW / "cdc_places_tract_ga.csv", dtype={"tractfips": str})
    p = p.set_index("tractfips")
    out["places_uninsured"] = out["tract"].map(p["access2_crudeprev"])
    out["places_checkup"] = out["tract"].map(p["checkup_crudeprev"])
    out["places_poor_health"] = out["tract"].map(p["ghlth_crudeprev"])

    f = pd.read_csv(RAW / "usda_sram_straight_line_ga.csv", dtype={"CensusTract20": str})
    f = f.set_index("CensusTract20")
    out["food_low_access_share"] = out["tract"].map(f["SD_SRAM_lapop1share"])   # % beyond 1 mile
    out["food_lila_flag"] = out["tract"].map(f["SD_SRAM_LILATracts_1And10"])
    return out


# --------------------------------------------------------------------------- scoring

def weighted_mean(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Row-wise weighted mean that renormalizes over the columns a row actually has."""
    v = df[list(weights)]
    w = pd.DataFrame(np.broadcast_to(list(weights.values()), v.shape), index=v.index,
                     columns=v.columns).where(v.notna())
    return (v * w).sum(axis=1) / w.sum(axis=1)


def rank_score(index: pd.Series) -> tuple[pd.Series, pd.Series]:
    """0-100 percentile of a composite and its quintile tier (Very low ... Very high)."""
    pct = index.rank(pct=True) * 100
    tier = pd.cut(pct, [0, 20, 40, 60, 80, 100], labels=TIERS, include_lowest=True)
    return pct, tier


def parks(tracts: pd.DataFrame) -> pd.DataFrame:
    """Miles to the nearest park of 1+ acres (OpenStreetMap), measured to the park's edge,
    approximated as its center minus the radius of a circle of the same area."""
    p = pd.read_csv(RAW / "osm_parks_arc.csv")
    p = p[p["acres"] >= 1].reset_index(drop=True)
    radius_mi = np.sqrt(p["acres"].to_numpy() * 4046.86 / np.pi) / 1609.34
    d = haversine_mi(tracts["lat"].to_numpy(float)[:, None], tracts["lon"].to_numpy(float)[:, None],
                     p["lat"].to_numpy(float)[None, :], p["lon"].to_numpy(float)[None, :])
    edge = np.clip(d - radius_mi[None, :], 0, None)
    return tracts[["tract"]].assign(park_dist_mi=edge.min(axis=1))


def score(t: pd.DataFrame, domain_weights: dict[str, float] = DOMAIN_WEIGHTS) -> pd.DataFrame:
    """Metro percentiles of each indicator (ARC tracts with people), weighted domain scores, and
    the composite.

    opportunity_index  weighted mean of the domain scores (each 0-100)
    opportunity_score  percentile of the index among ARC tracts, 0-100, evenly spread, so it can
                       be combined with need and fundability scores on the same footing
    opportunity_tier   quintile of opportunity_score (Very low ... Very high)
    """
    scored = t["in_arc"] & (t["pop_2020"] > 0)
    domains: dict[str, dict[str, float]] = {}
    for col, (domain, sign, w) in SCORED.items():
        t[f"pctl_{col}"] = (sign * t.loc[scored, col]).rank(pct=True) * 100
        domains.setdefault(domain, {})[f"pctl_{col}"] = w
    for col, (domain, sign) in OPTIONAL.items():
        if col in t.columns:
            t[f"pctl_{col}"] = (sign * t.loc[scored, col]).rank(pct=True) * 100
    for domain, weights in domains.items():
        t[f"score_{domain}"] = weighted_mean(t, weights)
    t["opportunity_index"] = weighted_mean(t, {f"score_{d}": w for d, w in domain_weights.items()})
    cols = [f"score_{d}" for d in domains] + ["opportunity_index"]
    t.loc[~scored, cols] = np.nan
    t["opportunity_score"], t["opportunity_tier"] = rank_score(t["opportunity_index"])
    return t


def to_zcta(t: pd.DataFrame) -> pd.DataFrame:
    rel = pd.read_csv(RAW / "zcta520_tract20_ga.csv", dtype=str)
    rel = rel.assign(part=rel["AREALAND_PART"].astype(float),
                     tract_land=rel["AREALAND_TRACT_20"].astype(float))
    rel = rel.rename(columns={"GEOID_ZCTA5_20": "zcta", "GEOID_TRACT_20": "tract"})
    rel = rel.merge(t, on="tract")
    rel["w"] = rel["pop_2020"] * (rel["part"] / rel["tract_land"].replace(0, np.nan)).fillna(0)
    rel = rel[rel["w"] > 0]
    num = [c for c in t.columns if c not in ("tract", "county_fips", "county", "in_arc", "lat", "lon",
                                             "pop_2020", "nearest_high_school", "food_lila_flag", "bb_locations",
                                             "opportunity_score", "opportunity_tier")
           and pd.api.types.is_numeric_dtype(t[c])]
    rows = {}
    for z, g in rel.groupby("zcta"):
        r = {"pop_2020_est": g["w"].sum(), "arc_pop_share": g.loc[g["in_arc"], "w"].sum() / g["w"].sum(),
             "n_tracts": len(g)}
        for c in num:
            ok = g[c].notna()
            r[c] = np.average(g.loc[ok, c], weights=g.loc[ok, "w"]) if ok.any() else np.nan
        rows[z] = r
    z = pd.DataFrame.from_dict(rows, orient="index").rename_axis("zcta").reset_index()
    z = z[z["arc_pop_share"] > 0].reset_index(drop=True)
    # The tract percentile doesn't average into a percentile, so re-rank the rolled-up index among
    # ZCTAs that are mostly (>= half their people) in the ARC counties; the rest keep the index only.
    main = z["arc_pop_share"] >= 0.5
    z["opportunity_score"], z["opportunity_tier"] = rank_score(z["opportunity_index"].where(main))
    return z


# FCC Broadband Data Collection, "Served/Unserved" fixed-broadband file for Georgia. The FCC site
# blocks scripted downloads, so it's fetched by hand (broadbandmap.fcc.gov > Data Download >
# Georgia > Served/Unserved) and saved as data/raw/fcc/bdc_ga_served_unserved.csv. Public domain.
FCC_FILE = ROOT / "data" / "raw" / "fcc" / "bdc_ga_served_unserved.csv"


def broadband() -> pd.DataFrame | None:
    """Share of broadband serviceable locations with 100/20 Mbps fixed service: over wires (cable,
    fiber, copper) and over any terrestrial technology (wires or fixed wireless). Not scored: in
    the ARC counties the median tract is above 99%, so it barely separates tracts."""
    if not FCC_FILE.exists():
        return None
    d = pd.read_csv(FCC_FILE, dtype={"block_geoid": str},
                    usecols=["block_geoid", "wired_dl100_ul20", "terrestrial_dl100_ul20"])
    g = d.assign(tract=d["block_geoid"].str[:11]).groupby("tract")
    return pd.DataFrame({"bb_wired_100_20_share": 100 * g["wired_dl100_ul20"].mean(),
                         "bb_terrestrial_100_20_share": 100 * g["terrestrial_dl100_ul20"].mean(),
                         "bb_locations": g.size()}).reset_index()


# Child Opportunity Index 3.0 (diversitydatakids.org). Needs a free account, so it isn't in
# pull_opportunity.py: download the tract file by hand, keep Georgia rows, and save it as
# data/raw/coi/coi30_ga.csv. Its terms haven't been checked for redistribution, so COI columns
# go to separate *_with_coi outputs that git ignores, never into the committed tables.
COI_FILE = ROOT / "data" / "raw" / "coi" / "coi30_ga.csv"
COI_COLS = {f"{kind}_{dom}_{norm}": f"coi_{kind}_{dom.lower()}_{norm}"
            for kind in ("r", "c5") for dom in ("COI", "ED", "HE", "SE") for norm in ("met", "stt")}


def coi() -> pd.DataFrame | None:
    """Latest year of COI 3.0: scores (r_, 1-100) and levels (c5_, very low to very high),
    metro- and state-normed, for the overall index and its three domains."""
    if not COI_FILE.exists():
        return None
    d = pd.read_csv(COI_FILE, dtype=str)
    d = d[d["year"] == d["year"].max()]
    out = d[["geoid20", *COI_COLS]].rename(columns={"geoid20": "tract", **COI_COLS})
    for c in out.columns:
        if c.startswith("coi_r_"):
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.assign(coi_year=int(d["year"].iloc[0]))


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    t = load_tracts()
    for part in (mobility(), epa_sld(), hud_affh(), jobs(t), schools(t), transit(t), health(t),
                 parks(t)):
        t = t.merge(part, on="tract", how="left")
    bb = broadband()
    if bb is not None:
        t = t.merge(bb, on="tract", how="left")
    t = score(t)
    return t, to_zcta(t)


def main():
    t, z = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in (("opportunity_tract", t), ("opportunity_zcta", z)):
        df.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        df.to_csv(OUT_DIR / f"{name}.csv", index=False)
    arc = t[t["in_arc"]]
    print(f"{len(t)} Georgia tracts ({len(arc)} in ARC counties); {len(z)} ZCTAs touch the ARC counties")
    print("Missing share among ARC tracts:")
    print(arc[list(SCORED)].isna().mean().round(3).to_string())
    print("Wrote data/processed/opportunity/opportunity_tract and opportunity_zcta (.parquet, .csv)")

    c = coi()
    if c is None:
        print(f"(No COI file at {COI_FILE.relative_to(ROOT)}; skipping the COI comparison)")
        return
    tc = t.merge(c, on="tract", how="left")
    zc = z.merge(to_zcta(tc)[["zcta", *[col for col in c.columns if col.startswith("coi_r_")]]],
                 on="zcta", how="left")
    for name, df in (("opportunity_tract_with_coi", tc), ("opportunity_zcta_with_coi", zc)):
        df.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        df.to_csv(OUT_DIR / f"{name}.csv", index=False)
    arc = tc[tc["in_arc"] & tc["opportunity_score"].notna()]
    cols = ["opportunity_score", *[f"score_{d}" for d in DOMAIN_WEIGHTS]]
    coi_cols = ["coi_r_coi_met", "coi_r_ed_met", "coi_r_he_met", "coi_r_se_met"]
    print(f"COI {c['coi_year'].iloc[0]} matched {arc['coi_r_coi_met'].notna().sum()} of {len(arc)} ARC tracts. "
          "Rank correlation with COI (metro-normed):")
    print(arc[cols + coi_cols].corr(method="spearman").loc[cols, coi_cols].round(2).to_string())
    print("Wrote data/processed/*_with_coi (.parquet, .csv); git ignores these")


if __name__ == "__main__":
    main()
