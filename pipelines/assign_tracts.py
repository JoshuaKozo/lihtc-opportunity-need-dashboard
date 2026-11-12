"""Give every 9% application a 2020 census tract and attach the tract's opportunity data.

    python -m pipelines.parse_applications
    python -m pipelines.opportunity.build_opportunity
    python -m pipelines.assign_tracts

DCA's tract column is filled for only about a third of applications, and it is often a 2010 tract
even in 2022+ rounds. So the tract is recomputed with the Census Geocoder (public, no key). The
candidates below are tried in order, and the first one inside the county DCA lists for the
application wins (this catches mistyped coordinates and bad address matches):

  1. coordinates     the application's lat/lon -> 2020 tract
  2. address         the site address -> 2020 tract (for applications without coordinates)
  3. dca_2020        DCA's own tract, when it is a valid 2020 Georgia tract
  4. dca_2010_xwalk  DCA's 2010 tract -> the 2020 tract it overlaps most (Opportunity Insights)

`tract_source` records which one was used; applications with no candidate in their county get
no tract. Geocoder answers are cached in
data/raw/geocode/census_geocoder_cache.csv, so reruns only look up new applications.

Output: data/processed/applications_9pct_tract.parquet / .csv, the applications table plus
tract_2020, tract_source, and the tract's opportunity columns (opportunity_score, tier,
domain scores, and raw indicators) from opportunity_tract.parquet.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
RAW_OPP = ROOT / "data" / "raw" / "opportunity"
CACHE = ROOT / "data" / "raw" / "geocode" / "census_geocoder_cache.csv"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies"
PARAMS = {"benchmark": "Public_AR_Current", "vintage": "Census2020_Current",
          "layers": "Census Tracts", "format": "json"}
HEADERS = {"User-Agent": "lihtc_site_selection/0.1 (open-source research; LIHTC award analysis)"}
PAUSE_S = 0.2


def _lookup(session: requests.Session, kind: str, query: dict) -> str | None:
    """2020 tract GEOID for a point or address, or None if the geocoder finds nothing."""
    for attempt in range(4):
        try:
            r = session.get(f"{GEOCODER}/{kind}", params={**query, **PARAMS}, headers=HEADERS, timeout=60)
            r.raise_for_status()
            res = r.json()["result"]
            break
        except (requests.RequestException, ValueError, KeyError):
            if attempt == 3:
                return None
            time.sleep(2 ** (attempt + 1))
    if kind == "onelineaddress":
        if not res["addressMatches"]:
            return None
        res = res["addressMatches"][0]
    tracts = res["geographies"].get("Census Tracts") or []
    return tracts[0]["GEOID"] if tracts else None


def geocode(queries: pd.DataFrame) -> pd.Series:
    """Look up (kind, key) pairs, using and extending the cache. Returns tract GEOIDs by key."""
    cache = (pd.read_csv(CACHE, dtype=str) if CACHE.exists()
             else pd.DataFrame(columns=["kind", "key", "tract_2020"]))
    known = set(zip(cache["kind"], cache["key"]))
    todo = queries[[(k, q) not in known for k, q in zip(queries["kind"], queries["key"])]]
    new = []
    with requests.Session() as s:
        for i, r in enumerate(todo.itertuples(), 1):
            query = ({"x": r.lon, "y": r.lat} if r.kind == "coordinates" else {"address": r.key})
            new.append({"kind": r.kind, "key": r.key, "tract_2020": _lookup(s, r.kind, query)})
            if i % 100 == 0:
                print(f"  geocoded {i}/{len(todo)}")
            time.sleep(PAUSE_S)
    if new:
        cache = pd.concat([cache, pd.DataFrame(new)], ignore_index=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        cache.to_csv(CACHE, index=False)
    return cache.set_index(["kind", "key"])["tract_2020"]


def assign(apps: pd.DataFrame, tracts_2020: set[str]) -> pd.DataFrame:
    a = apps.copy()
    has_xy = a["lat"].notna() & a["lon"].notna()
    a["_xy_key"] = a["lat"].round(6).astype(str) + "," + a["lon"].round(6).astype(str)
    addr = a[["address", "city"]].fillna("").agg(" ".join, axis=1).str.strip()
    a["_addr_key"] = (addr + ", GA " + a["zip"].fillna("")).str.strip()
    has_addr = a["address"].notna() & (a["city"].notna() | a["zip"].notna())

    q = pd.concat([
        pd.DataFrame({"kind": "coordinates", "key": a.loc[has_xy, "_xy_key"],
                      "lat": a.loc[has_xy, "lat"], "lon": a.loc[has_xy, "lon"]}),
        pd.DataFrame({"kind": "onelineaddress", "key": a.loc[~has_xy & has_addr, "_addr_key"]}),
    ]).drop_duplicates(["kind", "key"])
    found = geocode(q)

    # Every application also gets an address candidate, as the fallback for bad coordinates.
    missing = a.loc[has_addr, "_addr_key"].map(lambda k: ("onelineaddress", k) not in found.index)
    if missing.any():
        found = geocode(pd.DataFrame({"kind": "onelineaddress",
                                      "key": a.loc[has_addr, "_addr_key"][missing]}).drop_duplicates())

    candidates = {
        "coordinates": a["_xy_key"].map(lambda k: found.get(("coordinates", k))).where(has_xy),
        "address": a["_addr_key"].map(lambda k: found.get(("onelineaddress", k))).where(has_addr),
        "dca_2020": a["tract"].where(a["tract"].isin(tracts_2020)),
        "dca_2010_xwalk": a["tract"].map(largest_2020_overlap()),
    }
    county_fips = (a["county"].str.lower().str.replace(r"\(county\)|county", "", regex=True)
                   .str.strip().map(county_name_to_fips()))
    a["tract_2020"] = pd.NA
    a["tract_source"] = pd.NA
    for source, tract in candidates.items():
        ok = tract.notna() & a["tract_2020"].isna() & tract.isin(tracts_2020)
        ok &= county_fips.isna() | (tract.str[:5] == county_fips)
        a.loc[ok, "tract_2020"] = tract[ok]
        a.loc[ok, "tract_source"] = source
    return a.drop(columns=["_xy_key", "_addr_key"])


def county_name_to_fips() -> dict[str, str]:
    """Lower-case Georgia county name (with or without "County") -> 5-digit FIPS."""
    c = pd.read_csv(RAW_OPP / "ga_counties_2020.txt", sep="|", dtype=str)
    names = c["COUNTYNAME"].str.lower().str.replace(r"\s+county$", "", regex=True)
    return dict(zip(names, c["STATEFP"] + c["COUNTYFP"]))


def largest_2020_overlap() -> pd.Series:
    """2010 tract -> the 2020 tract that holds the largest share of it."""
    xw = pd.read_csv(RAW_OPP / "oa_tract_2010_2020_crosswalk_ga.csv", dtype=str)
    long = pd.concat([xw[["tract_2020", f"tract_2010_{i}", f"wgt_{i}"]].set_axis(
        ["tract_2020", "tract_2010", "w"], axis=1) for i in range(1, 6)]).dropna()
    long["w"] = long["w"].astype(float)
    return long.sort_values("w").drop_duplicates("tract_2010", keep="last").set_index("tract_2010")["tract_2020"]


def main():
    apps = pd.read_parquet(PROCESSED / "applications_9pct.parquet")
    opp = pd.read_parquet(PROCESSED / "opportunity" / "opportunity_tract.parquet")
    a = assign(apps, set(opp["tract"]))

    # Where DCA's own 2020-era tract and the geocoded one both exist, report how often they agree.
    recent = a[(a["round_year"] >= 2022) & a["tract"].notna() & (a["tract_source"] == "coordinates")]
    if len(recent):
        print(f"Check: geocoded tract equals DCA's tract for {(recent['tract'] == recent['tract_2020']).mean():.0%} "
              f"of {len(recent)} 2022+ applications with both")

    keep = ["tract", "in_arc", "opportunity_score", "opportunity_tier", "opportunity_index",
            *[c for c in opp.columns if c.startswith("score_")],
            *[c for c in opp.columns if c not in ("tract", "county_fips", "county", "in_arc", "lat", "lon")
              and not c.startswith(("pctl_", "score_", "opportunity_"))]]
    out = a.merge(opp[keep].drop(columns="in_arc").rename(columns={"tract": "tract_2020"}),
                  on="tract_2020", how="left")
    out.to_parquet(PROCESSED / "applications_9pct_tract.parquet", index=False)
    out.to_csv(PROCESSED / "applications_9pct_tract.csv", index=False)

    print(f"{out['tract_2020'].notna().sum()} of {len(out)} applications have a 2020 tract:")
    print(out["tract_source"].value_counts(dropna=False).to_string())
    arc = out[out["in_arc"]]
    print(f"ARC applications with an opportunity score: {arc['opportunity_score'].notna().sum()} of {len(arc)}")
    print("Wrote data/processed/applications_9pct_tract (.parquet, .csv)")


if __name__ == "__main__":
    main()
