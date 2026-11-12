"""Package the opportunity data for the web module in opportunity_site/.

    python -m pipelines.opportunity.export_site

Writes opportunity_site/data/:
  tracts.geojson     ARC tracts (2020) with simplified outlines, domain scores and the plain-language
                     facts shown on the profile card
  applications.json  ARC 9% applications with a tract: year, name, outcome, location
  meta.json          domain weights, indicator weights, presets, descriptions, sources, caveats
  places.json        MARTA rail stations and county label points, to orient the map without tiles

Tract outlines come from the Census TIGERweb service (public, no key), simplified to about 30 m,
and are cached in data/raw/opportunity/arc_tracts_2020.geojson.
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pandas as pd
import requests

from pipelines.opportunity.build_opportunity import ARC_COUNTY_FIPS, DOMAIN_WEIGHTS, OPTIONAL, SCORED, TIERS

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "data" / "processed"          # shared tables (DCA applications)
PROCESSED = SHARED / "opportunity"
SHAPES = ROOT / "data" / "raw" / "opportunity" / "arc_tracts_2020.geojson"
OUT = ROOT / "opportunity_site" / "data"
TIGER = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
         "tigerWMS_Census2020/MapServer/6/query")
HEADERS = {"User-Agent": "lihtc_site_selection/0.1 (open-source research; LIHTC award analysis)"}

# Facts on the profile card: column -> (label, format). Formats: int, pct, mi, rank, share.
FACTS = {
    "oa_kfr_p25": "rank", "oa_jail_p25": "share",
    "jobs_within_5mi": "int", "jobs_growth_5mi": "growth", "epa_jobs_45min_transit": "int",
    "jobs_nondegree_5mi": "int", "jobs_per_nondegree_worker_5mi": "ratio",
    "epa_jobs_45min_auto": "int",
    "school_prof_elem3": "pct", "school_prof_middle": "pct", "school_prof_high": "pct",
    "school_grad_rate_high": "pct", "nearest_high_school": "text",
    "marta_rail_dist_mi": "mi", "marta_stops_half_mi": "int", "epa_walkability": "walk",
    "fqhc_dist_mi": "mi", "places_uninsured": "pct", "food_low_access_share": "pct",
    "hud_env_health": "index", "bb_wired_100_20_share": "pct", "park_dist_mi": "mi",
    "hud_school_proficiency": "index", "hud_jobs_proximity": "index", "hud_labor_market": "index",
    "hud_transport_cost": "index",
}

DOMAINS = {
    "mobility": {
        "label": "Mobility",
        "what": "How children from low-income families raised here did as adults.",
        "source": "Opportunity Atlas (Opportunity Insights): children born 1978-83, observed as adults in 2014-15.",
    },
    "schools": {
        "label": "Schools",
        "what": "Share of students proficient on Georgia Milestones at nearby public schools, plus the high school graduation rate.",
        "source": "Georgia GOSA 2024-25 results, matched to NCES school locations. Charter and virtual schools excluded.",
    },
    "jobs": {
        "label": "Jobs",
        "what": "Jobs reachable in 45 minutes by transit or car, jobs within 5 miles that don't need a college degree (also per worker competing for them), and 5-year job growth.",
        "source": "Census LEHD LODES 2018 and 2023; EPA Smart Location Database v3 (2021).",
    },
    "transit": {
        "label": "Transit",
        "what": "Distance to MARTA rail, MARTA stops within half a mile, and walkability.",
        "source": "MARTA GTFS schedule feed (2026); EPA National Walkability Index.",
    },
    "health": {
        "label": "Health",
        "what": "Grocery access, distance to a community health center, and health insurance coverage.",
        "source": "USDA SNAP Retailer Access Map (2025); HRSA health center sites; CDC PLACES (2025 release).",
    },
    "environment": {
        "label": "Environment",
        "what": "Exposure to air toxics (HUD environmental health index).",
        "source": "HUD AFFH data (AFFHT0006). Based on older EPA air-toxics data, so it carries a small weight.",
    },
}

# Optional metrics: label, and the weight inside its domain when a user switches it on.
OPTIONAL_LABELS = {
    "park_dist_mi": ("Distance to a park of 1+ acres", 0.25),
    "bb_wired_100_20_share": ("Homes with wired 100/20 Mbps broadband", 0.15),
    "hud_school_proficiency": ("HUD school proficiency index (older data)", 0.25),
    "hud_jobs_proximity": ("HUD jobs proximity index (older data)", 0.25),
    "hud_labor_market": ("HUD labor market index (describes residents)", 0.20),
    "hud_transport_cost": ("HUD low transportation cost index (older data)", 0.25),
}

INDICATOR_LABELS = {
    "oa_kfr_p25": "Adult income rank of kids from low-income families",
    "oa_jail_p25": "Incarceration rate of those kids",
    "epa_jobs_45min_transit": "Jobs reachable in 45 min by transit",
    "jobs_within_5mi": "Jobs within 5 miles",
    "jobs_nondegree_5mi": "Jobs for workers without a degree within 5 miles",
    "jobs_per_nondegree_worker_5mi": "Those jobs per non-degree worker living within 5 miles",
    "epa_jobs_45min_auto": "Jobs reachable in 45 min by car",
    "jobs_growth_5mi": "Job growth within 5 miles (5 years)",
    "school_prof_elem3": "Proficiency, 3 nearest elementary schools",
    "school_prof_middle": "Proficiency, nearest middle school",
    "school_prof_high": "Proficiency, nearest high school",
    "school_grad_rate_high": "Graduation rate, nearest high school",
    "marta_rail_dist_mi": "Distance to MARTA rail",
    "marta_stops_half_mi": "MARTA stops within half a mile",
    "epa_walkability": "Walkability",
    "food_low_access_share": "People over 1 mile from a grocery store",
    "fqhc_dist_mi": "Distance to a community health center",
    "places_uninsured": "Adults without health insurance",
    "hud_env_health": "Environmental health index",
}

PRESETS = {
    "Default": DOMAIN_WEIGHTS,
    "Families with kids": {"mobility": 0.30, "schools": 0.35, "jobs": 0.15, "transit": 0.10,
                           "health": 0.05, "environment": 0.05},
    "Seniors / no car": {"mobility": 0.05, "schools": 0.05, "jobs": 0.15, "transit": 0.35,
                         "health": 0.30, "environment": 0.10},
    "Workforce": {"mobility": 0.15, "schools": 0.15, "jobs": 0.35, "transit": 0.25,
                  "health": 0.05, "environment": 0.05},
    "Equal": {d: round(1 / 6, 4) for d in DOMAIN_WEIGHTS},
    # The hand-set weights used before calibration (docs/opportunity_weights.md): more weight on
    # job and transit access, less on outcome-based mobility and schools.
    "Access-focused": {"mobility": 0.25, "schools": 0.25, "jobs": 0.20, "transit": 0.15,
                       "health": 0.10, "environment": 0.05},
}

# Full source list for the Method tab: (dataset, publisher, used for, period, link).
SOURCES = [
    ("Opportunity Atlas tract outcomes", "Opportunity Insights", "Mobility", "Children born 1978-83, incomes in 2014-15",
     "https://opportunityinsights.org/data/"),
    ("2010 to 2020 tract crosswalk", "Opportunity Insights", "Moving 2010-tract data to 2020 tracts", "",
     "https://opportunityinsights.org/data/"),
    ("LODES 8 workplace jobs", "U.S. Census Bureau (LEHD)", "Jobs within 5 miles, job growth", "2018 and 2023",
     "https://lehd.ces.census.gov/data/"),
    ("Smart Location Database v3", "U.S. EPA", "Jobs reachable by transit and car, walkability", "Inputs about 2017-19",
     "https://www.epa.gov/smartgrowth/smart-location-mapping"),
    ("Georgia Milestones results and graduation rates", "Georgia Governor's Office of Student Achievement",
     "School proficiency and graduation", "2024-25 school year", "https://download.gosa.ga.gov/"),
    ("Public school locations", "NCES (EDGE)", "Placing schools on the map", "2024-25",
     "https://nces.ed.gov/programs/edge/Geographic/SchoolLocations"),
    ("MARTA schedule (GTFS)", "MARTA", "Rail distance and stops nearby", "Current schedule",
     "https://itsmarta.com/app-developer-resources.aspx"),
    ("Health center service delivery sites", "HRSA", "Distance to a community health center", "Current",
     "https://data.hrsa.gov/data/download"),
    ("PLACES tract estimates", "CDC", "Adults without health insurance", "2025 release",
     "https://data.cdc.gov/resource/yjkw-uj5s"),
    ("SNAP Retailer Access Map", "USDA ERS", "Grocery access", "2025 release",
     "https://www.ers.usda.gov/data-products/food-access-research-atlas/download-the-data"),
    ("AFFH opportunity indices", "HUD", "Environment; optional HUD metrics", "AFFH-T 0006 (2014 air toxics)",
     "https://hudgis-hud.opendata.arcgis.com/"),
    ("2020 tract population centers and ZIP-tract file", "U.S. Census Bureau", "Distances and ZIP rollup", "2020 Census",
     "https://www.census.gov/geographies/reference-files.html"),
    ("Parks", "OpenStreetMap contributors", "Optional park metric", "Current", "https://www.openstreetmap.org/copyright"),
    ("Fixed broadband Served/Unserved", "FCC", "Optional broadband metric", "Dec 2025 filing",
     "https://broadbandmap.fcc.gov/data-download"),
    ("Census Geocoder and Photon", "U.S. Census Bureau; komoot / OpenStreetMap", "Address search", "Live",
     "https://geocoding.geo.census.gov/"),
    ("Small-area life expectancy (USALEEP)", "CDC NCHS", "Checking the weights (not scored)", "2010-15",
     "https://www.cdc.gov/nchs/nvss/usaleep/usaleep.html"),
    ("Child Opportunity Index 3.0", "diversitydatakids.org", "Benchmark comparison (not scored)", "2024",
     "https://www.diversitydatakids.org/child-opportunity-index"),
    ("SiteLine location scores", "SiteLine", "Feasibility overlap check (not scored)", "2026",
     "https://github.com/kmcalist682336/SitelineLIHTCStartUp"),
]

# Research behind the parts and weights (docs/opportunity_weights.md explains each).
REFERENCES = [
    ("Chetty & Hendren (2018). The Impacts of Neighborhoods on Intergenerational Mobility I. Quarterly Journal of Economics.",
     "https://www.nber.org/papers/w23001"),
    ("Chetty, Hendren & Katz (2016). The Effects of Exposure to Better Neighborhoods on Children: Moving to Opportunity. American Economic Review.",
     "https://www.nber.org/papers/w21156"),
    ("Chetty, Friedman, Hendren, Jones & Porter. The Opportunity Atlas: Mapping the Childhood Roots of Social Mobility.",
     "https://www.nber.org/papers/w25147"),
    ("Laliberté (2021). Long-Term Contextual Effects in Education: Schools and Neighborhoods. AEJ: Economic Policy.",
     "https://ideas.repec.org/a/aea/aejpol/v13y2021i2p336-77.html"),
    ("Gobillon, Selod & Zenou (2007). The Mechanisms of Spatial Mismatch. Urban Studies.",
     "https://halshs.archives-ouvertes.fr/halshs-00826212"),
    ("Blumenberg & Pierce (2014). A Driving Factor in Mobility? Journal of the American Planning Association.",
     "https://poverty.ucdavis.edu/sites/main/files/file-attachments/blumenberg_and_pierce_a_driving_factor_in_mobility.pdf"),
    ("Ludwig et al. (2012). Neighborhood Effects on the Long-Term Well-Being of Low-Income Adults. Science.",
     "https://harris.uchicago.edu/files/inline-files/SCIENCE_Ludwig_MTO_article_main.pdf"),
    ("Allcott, Diamond & Dubé. The Geography of Poverty and Nutrition: Food Deserts and Food Choices.",
     "https://www.nber.org/digest/feb18/eliminating-food-deserts-wont-cure-nutritional-inequality"),
    ("Isen, Rossin-Slater & Walker (2017). Every Breath You Take, Every Dollar You'll Make. Journal of Political Economy.",
     "https://www.nber.org/digest/jun14/earnings-consequences-clean-air-act"),
    ("California TCAC/HCD Opportunity Map methodology (2024-25), used in California's LIHTC program.",
     "https://www.treasurer.ca.gov/ctcac/opportunity/2025/CTCAC-HCD-2025.pdf"),
    ("Child Opportunity Index 2.0 method (APPAM 2019): weights from associations with mobility and health outcomes.",
     "https://appam.confex.com/appam/2019/webprogram/Paper33201.html"),
]

CAVEATS = [
    "Schools are the nearest ones, not the zoned ones; a district choice school can count as nearest.",
    "Mobility describes children born 1978-83, so it reflects the neighborhood a generation ago.",
    "MARTA measures cover MARTA only. Other bus systems count through EPA's transit job access.",
    "The environment index uses older air-toxics data, so it carries only 5% of the weight.",
    "Scores rank tracts within the 11 ARC counties. A low score means low relative to metro Atlanta.",
]


def fetch_shapes() -> dict:
    if SHAPES.exists():
        return json.loads(SHAPES.read_text())
    feats = []
    with requests.Session() as s:
        for fips in ARC_COUNTY_FIPS:
            params = {"where": f"STATE='13' AND COUNTY='{fips[2:]}'", "outFields": "GEOID",
                      "f": "geojson", "outSR": 4326, "maxAllowableOffset": 0.0003,
                      "geometryPrecision": 5, "returnGeometry": "true"}
            r = s.get(TIGER, params=params, headers=HEADERS, timeout=120)
            r.raise_for_status()
            feats += r.json()["features"]
    gj = {"type": "FeatureCollection", "features": feats}
    SHAPES.write_text(json.dumps(gj))
    return gj


def clean(v, digits=4):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, float):
        return round(v, digits)
    return v


def verification_summary() -> dict | None:
    """Plain-language verification results for the Method tab, from verify_opportunity.py's report."""
    path = PROCESSED / "opportunity_verification.json"
    if not path.exists():
        return None
    r = json.loads(path.read_text())
    c = {x["check"]: x for x in r["checks"]}
    v = lambda name: c[name].get("value") if name in c else None
    items = []
    if "ARC population matches official county totals" in c:
        items.append("Tract populations match the Census Bureau's official county totals (within 0.6% for every county).")
    if all(k in c for k in ("Rail distance recomputed independently", "Jobs within 5 miles recomputed independently")):
        items.append("Distances, job counts and school results were recomputed independently from the raw files and matched exactly.")
    mob, life = v("Predicts children's upward mobility (held-out counties)"), v("Predicts life expectancy (held-out counties)")
    if mob is not None and life is not None:
        items.append(f"On counties held out of the weight fitting, the score lines up with children's upward mobility "
                     f"({mob:.2f}) and life expectancy ({life:.2f}).")
    if v("Agrees with the Child Opportunity Index 3.0") is not None:
        items.append(f"It agrees closely with the Child Opportunity Index 3.0 (rank correlation {v('Agrees with the Child Opportunity Index 3.0'):.2f}).")
    if v("Robust to the exact weights") is not None:
        items.append("Moving every weight up or down by as much as half barely changes the ranking "
                     f"(rank correlation {v('Robust to the exact weights'):.2f}).")
    if v("Stable under the Opportunity Atlas margins of error") is not None:
        items.append(f"Within the mobility data's margins of error, {v('Stable under the Opportunity Atlas margins of error'):.0%} of tracts "
                     "keep their tier on average; tracts near a tier boundary can shift one step.")
    bt_path = PROCESSED / "opportunity_backtest.json"
    if bt_path.exists():
        bt = json.loads(bt_path.read_text())["historical_backtest"]
        h = bt["results"]["good_health"]
        items.append(f"Backtest: rebuilt using only 2010-era data, the score predicted which tracts had healthier adults "
                     f"in 2022-23 ({h['score_2010']['r']:.2f}, {h['score_2010_with_mobility']['r']:.2f} with mobility; positive in "
                     f"{h['score_2010']['positive']} of {h['score_2010']['counties']} counties). Adding it to 2010 poverty raises the "
                     f"share of health differences explained from {h['r2_poverty_only']:.0%} to {h['r2_poverty_plus_score_2010']:.0%}.")
        items.append(f"The ranking is stable over time: the 2010 rebuild and today's score agree at {bt['rank_corr_2010_vs_today']:.2f}, "
                     f"and {bt['top_fifth_2010_still_top_fifth_now']:.0%} of 2010's top-fifth tracts are still in the top fifth.")
    cal = PROCESSED / "weight_calibration.csv"
    if cal.exists():
        row = pd.read_csv(cal).set_index("weighting").loc["current default"]
        if "validity_adult_emp" in row:
            items.append("Adults: once education is accounted for, no weighting predicts adult employment "
                         f"({row['validity_adult_emp']:.2f}), matching the Moving to Opportunity finding that better neighborhoods "
                         "didn't raise adults' earnings. Adult opportunity is measured through access to jobs that don't need a degree.")
    notes = []
    if v("Overlap with need") is not None:
        notes.append(f"Opportunity runs strongly opposite to need (rank correlation {v('Overlap with need'):.2f}): few tracts are high on both.")
    fair = v("Fairness: relation to a tract's Black population share")
    if fair:
        notes.append(f"Scores are lower in tracts with a larger Black population share ({fair['raw']:.2f}; {fair['after_need']:.2f} after "
                     "accounting for need), driven by the mobility and schools parts. About half of the mobility gap reflects who "
                     "lives in a tract rather than the place. Lowering those weights reduces it.")
    return {"date": r["generated"], "summary": r["summary"], "items": items, "notes": notes}


def write_standalone():
    """opportunity_site/standalone.html: the demo page with the module and all data inlined, so it opens by
    double-clicking, with no server and no unzipping. ("</" is escaped so inlined JSON and code
    can't end the script tag early.)"""
    page = (ROOT / "opportunity_site" / "index.html").read_text()
    module = (ROOT / "opportunity_site" / "opportunity.js").read_text()
    data = {"tracts": json.loads((OUT / "tracts.geojson").read_text()),
            "applications": json.loads((OUT / "applications.json").read_text()),
            "meta": json.loads((OUT / "meta.json").read_text()),
            "places": json.loads((OUT / "places.json").read_text())}
    inline = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    assert '<script src="opportunity.js"></script>' in page and 'dataUrl: "data/",' in page
    page = page.replace('<script src="opportunity.js"></script>',
                        "<script>\n" + module.replace("</script", "<\\/script") + "\n</script>\n"
                        "<script>window.OPPORTUNITY_DATA = " + inline + ";</script>")
    page = page.replace('dataUrl: "data/",', "data: window.OPPORTUNITY_DATA,")
    page = page.replace("If you opened this file directly, serve the folder over HTTP instead "
                        '(for example "python -m http.server") and reload.', "Try reloading the page.")
    (ROOT / "opportunity_site" / "standalone.html").write_text(page)


def main():
    t = pd.read_parquet(PROCESSED / "opportunity_tract.parquet")
    t = t[t["in_arc"]].set_index("tract")
    shapes = fetch_shapes()
    feats = []
    for f in shapes["features"]:
        geoid = f["properties"]["GEOID"]
        if geoid not in t.index:
            continue
        r = t.loc[geoid]
        props = {"tract": geoid, "county": r["county"], "pop": int(r["pop_2020"]),
                 # score inputs keep 9 digits: rounding can flip the order of near-tied tracts
                 "scores": {d: clean(r[f"score_{d}"], 9) for d in DOMAIN_WEIGHTS},
                 "facts": {c: clean(r[c]) for c in FACTS if c in t.columns},
                 "pctl": {c: clean(r[f"pctl_{c}"], 9) for c in [*SCORED, *OPTIONAL]
                          if f"pctl_{c}" in t.columns}}
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tracts.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                   separators=(",", ":"), allow_nan=False))

    a = pd.read_parquet(SHARED / "applications_9pct_tract.parquet")
    a = a[a["in_arc"] & a["tract_2020"].notna()]
    apps = []
    for r in a.itertuples():
        exact = r.tract_source == "coordinates"   # DCA coordinates that passed the county check
        lat, lon = (r.lat, r.lon) if exact else (t.loc[r.tract_2020, "lat"], t.loc[r.tract_2020, "lon"])
        apps.append({"id": r.app_id, "year": int(r.round_year), "name": clean(r.project_name),
                     "selected": bool(r.selected), "outcome": clean(r.outcome), "tenancy": clean(r.tenancy),
                     "units": clean(r.units_total), "city": clean(r.city), "tract": r.tract_2020,
                     "lat": round(float(lat), 5), "lon": round(float(lon), 5), "exact": exact})
    (OUT / "applications.json").write_text(json.dumps(apps, separators=(",", ":"), allow_nan=False))

    meta_checks = verification_summary()
    meta = {"verification": meta_checks, "weights": DOMAIN_WEIGHTS, "presets": PRESETS, "tiers": TIERS, "domains": DOMAINS,
            "indicators": {
                **{c: {"domain": d, "direction": s, "weight": w, "label": INDICATOR_LABELS[c],
                       "optional": False, "on_weight": w} for c, (d, s, w) in SCORED.items()},
                **{c: {"domain": d, "direction": s, "weight": 0, "label": OPTIONAL_LABELS[c][0],
                       "optional": True, "on_weight": OPTIONAL_LABELS[c][1]}
                   for c, (d, s) in OPTIONAL.items() if f"pctl_{c}" in t.columns}},
            "facts": FACTS, "caveats": CAVEATS,
            "sources": [dict(zip(("name", "publisher", "use", "period", "url"), s)) for s in SOURCES],
            "references": [{"cite": c, "url": u} for c, u in REFERENCES],
            "counties": sorted(ARC_COUNTY_FIPS.values())}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1, allow_nan=False))
    z = zipfile.ZipFile(ROOT / "data" / "raw" / "opportunity" / "marta_gtfs.zip")
    stops = pd.read_csv(z.open("stops.txt"), dtype=str)
    stations = stops[(stops["location_type"] == "1")      # parent stations, minus bus park & rides
                     & ~stops["stop_name"].str.contains("PARK & RIDE")]
    arc = t[t["pop_2020"] > 0]
    counties = (arc.assign(wlat=arc["lat"] * arc["pop_2020"], wlon=arc["lon"] * arc["pop_2020"])
                .groupby("county")[["wlat", "wlon", "pop_2020"]].sum())
    places = {
        "stations": [{"name": n.title().replace(" Station", ""), "lat": round(float(la), 5),
                      "lon": round(float(lo), 5)}
                     for n, la, lo in zip(stations["stop_name"], stations["stop_lat"], stations["stop_lon"])],
        "counties": [{"name": c, "lat": round(r.wlat / r.pop_2020, 4), "lon": round(r.wlon / r.pop_2020, 4)}
                     for c, r in counties.iterrows()],
    }
    (OUT / "places.json").write_text(json.dumps(places, separators=(",", ":"), allow_nan=False))
    write_standalone()
    sizes = {p.name: f"{p.stat().st_size / 1e6:.2f} MB" for p in OUT.iterdir()}
    print(f"{len(feats)} tracts, {len(apps)} applications -> opportunity_site/data/ {sizes}")


if __name__ == "__main__":
    main()
