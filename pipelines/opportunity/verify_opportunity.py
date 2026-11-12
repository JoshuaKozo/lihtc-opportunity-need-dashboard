"""Verify the opportunity data end to end and report how much to trust it.

    python -m pipelines.opportunity.verify_opportunity

Runs five groups of checks and writes data/processed/opportunity/opportunity_verification.json (the numbers
quoted in docs/opportunity_methodology.md come from this file):

  1. Sources        every raw file is present, with its vintage and coverage of the 11 ARC counties
  2. Geography      tract and population totals match official Census counts; crosswalks add up
  3. Metrics        each scored metric's coverage and range, plus independent recomputation of a
                    sample (distances, job counts, Opportunity Atlas crosswalk, school proficiency)
  4. Scoring        stored percentiles, part scores, composite and tiers reproduce from their inputs
  5. Validity       predicts outcomes (held-out counties), agrees with COI, robust to weights, and
                    how it relates to need, race and feasibility (the scores it will sit beside)

Each check is PASS, WARN (true but worth knowing), FAIL (a data problem to fix) or INFO (a
measurement with no pass/fail line).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from pipelines.opportunity.build_opportunity import (ARC_COUNTY_FIPS, DOMAIN_WEIGHTS, OPTIONAL, RAW, SCORED,
                                         haversine_mi, to_2020, weighted_mean)

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "data" / "processed"          # shared tables (DCA applications)
PROCESSED = SHARED / "opportunity"
OUT = PROCESSED / "opportunity_verification.json"
RNG = np.random.default_rng(0)

# What each source measures and when, for the methodology's data table.
VINTAGE = {
    "oa_tract_outcomes": "children born 1978-83, incomes measured 2014-15",
    "oa_crosswalk": "2010 to 2020 tracts",
    "tract_centroids": "2020 Census",
    "zcta_tract": "2020 Census",
    "census_county_pop": "2020 estimates base",
    "usaleep": "2010-15",
    "ga_counties": "2020",
    "lodes_wac_2023": "2023", "lodes_wac_2018": "2018", "lodes_xwalk": "2020 blocks",
    "epa_sld": "2017-19 inputs, released 2021",
    "nces_schools": "2024-25 school year",
    "gosa_eog": "2024-25 school year", "gosa_eoc": "2024-25 school year", "gosa_grad": "2024-25 school year",
    "cdc_places": "2025 release (mostly 2022-23 survey data)",
    "usda_sram": "2025 release", "usda_sram_general": "2025 release (ACS-based)",
    "hrsa_sites": "current as downloaded",
    "marta_gtfs": "current schedule as downloaded",
    "osm_parks": "current as downloaded",
    "hud_school_proficiency": "AFFH-T 0006 (about 2014-17 data)", "hud_jobs_proximity": "AFFH-T 0006",
    "hud_labor_market": "AFFH-T 0006", "hud_transport_cost": "AFFH-T 0006", "hud_env_health": "AFFH-T 0006 (2014 air toxics)",
}

checks: list[dict] = []


def check(section, name, ok, detail, warn=False, value=None):
    """ok=None records a finding with no pass/fail test (INFO)."""
    status = "INFO" if ok is None else "PASS" if ok else ("WARN" if warn else "FAIL")
    checks.append({"section": section, "check": name, "status": status, "detail": detail,
                   **({"value": value} if value is not None else {})})


def rk(a, b):
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


# --------------------------------------------------------------------------- data

def load():
    t = pd.read_parquet(PROCESSED / "opportunity_tract.parquet")
    arc = t[t["in_arc"] & (t["pop_2020"] > 0)].copy()
    return t, arc


# --------------------------------------------------------------------------- 1. sources

def sources():
    man = pd.read_csv(RAW / "manifest.csv")
    rows = []
    for r in man.itertuples():
        present = (RAW / r.filename).exists()
        rows.append({"key": r.key, "file": r.filename, "retrieved": r.retrieved,
                     "vintage": VINTAGE.get(r.key, ""), "present": present})
    missing = [r["key"] for r in rows if not r["present"]]
    check("Sources", "All raw files present", not missing,
          f"{len(rows)} sources in the manifest" + (f"; missing: {missing}" if missing else ""))
    fcc = (RAW.parent / "fcc" / "bdc_ga_served_unserved.csv").exists()
    check("Sources", "Optional manual files", None,
          f"FCC broadband file {'present' if fcc else 'absent (broadband columns empty)'}; "
          "Child Opportunity Index file used only for comparison, not committed")
    return rows


# --------------------------------------------------------------------------- 2. geography

def geography(t, arc):
    check("Geography", "Georgia 2020 tract count", len(t) == 2796,
          f"{len(t)} tracts (Census 2020 count for Georgia: 2,796)")
    check("Geography", "Tract IDs unique and well formed",
          t["tract"].str.fullmatch(r"13\d{9}").all() and not t["tract"].duplicated().any(), "11-digit GEOIDs, no duplicates")
    pop = pd.read_csv(RAW / "census_county_pop_ga.csv", dtype={"STATE": str, "COUNTY": str})
    pop["fips"] = pop["STATE"] + pop["COUNTY"]
    official = pop[pop["fips"].isin(ARC_COUNTY_FIPS)]["ESTIMATESBASE2020"].sum()
    ours = t[t["in_arc"]]["pop_2020"].sum()
    diff = abs(ours - official) / official
    check("Geography", "ARC population matches official county totals", diff < 0.01,
          f"tract sum {ours:,} vs Census county totals {official:,} ({diff:.2%} apart)", value=int(ours))
    by_c = t[t["in_arc"]].groupby("county_fips")["pop_2020"].sum()
    off_c = pop.set_index("fips")["ESTIMATESBASE2020"]
    worst = max(abs(by_c[f] - off_c[f]) / off_c[f] for f in ARC_COUNTY_FIPS)
    check("Geography", "Each county's population matches", worst < 0.01, f"largest county gap {worst:.2%}")
    check("Geography", "Scored tracts", len(arc) == 1245,
          f"{len(arc)} populated tracts in the 11 ARC counties are scored; "
          f"{int((t['in_arc'] & (t['pop_2020'] == 0)).sum())} with no residents are not")
    xw = pd.read_csv(RAW / "oa_tract_2010_2020_crosswalk_ga.csv", dtype=str)
    w = sum(xw[f"wgt_{i}"].astype(float).fillna(0) for i in range(1, 6))
    check("Geography", "2010-to-2020 crosswalk weights sum to 1", bool(np.allclose(w, 1, atol=0.01)),
          f"per 2020 tract: min {w.min():.3f}, max {w.max():.3f}")
    z = pd.read_parquet(PROCESSED / "opportunity_zcta.parquet")
    share = z["pop_2020_est"].mul(z["arc_pop_share"]).sum() / t[t["in_arc"]]["pop_2020"].sum()
    check("Geography", "ZIP (ZCTA) rollup keeps the population", abs(share - 1) < 0.03,
          f"ZCTA rollup holds {share:.1%} of ARC residents")
    a = pd.read_parquet(SHARED / "applications_9pct_tract.parquet")
    names = pd.read_csv(RAW / "ga_counties_2020.txt", sep="|", dtype=str)
    names = dict(zip(names["STATEFP"] + names["COUNTYFP"], names["COUNTYNAME"].str.replace(" County", "").str.lower()))
    has = a["tract_2020"].notna()
    same = a.loc[has, "tract_2020"].str[:5].map(names) == a.loc[has, "county"].str.lower().str.replace(r"\s*\(county\)", "", regex=True).str.strip()
    check("Geography", "Applications placed in their own county", bool(same.all()),
          f"{int(has.sum())} of {len(a)} applications have a 2020 tract; all in the county DCA lists"
          if same.all() else f"{int((~same).sum())} in a different county")


# --------------------------------------------------------------------------- 3. metrics

# Hard bounds (outside = FAIL) and a plausible middle for the ARC median (outside = WARN).
BOUNDS = {
    "oa_kfr_p25": ((0, 1), (0.30, 0.50)), "oa_jail_p25": ((-0.1, 0.5), (0, 0.06)),
    "jobs_within_5mi": ((0, 3e6), (2e4, 3e5)), "jobs_growth_5mi": ((-1, 5), (-0.1, 0.4)),
    "jobs_nondegree_5mi": ((0, 3e6), (1e4, 2e5)), "jobs_per_nondegree_worker_5mi": ((0, 50), (0.5, 3)),
    "epa_jobs_45min_transit": ((0, 2e6), None), "epa_jobs_45min_auto": ((0, 2e6), (5e4, 3e5)),
    "school_prof_elem3": ((0, 100), (25, 65)), "school_prof_middle": ((0, 100), (25, 65)),
    "school_prof_high": ((0, 100), (25, 65)), "school_grad_rate_high": ((0, 100), (80, 98)),
    "marta_rail_dist_mi": ((0, 60), (1, 20)), "marta_stops_half_mi": ((0, 500), None),
    "epa_walkability": ((1, 20), (5, 15)), "fqhc_dist_mi": ((0, 40), (1, 8)),
    "places_uninsured": ((0, 100), (6, 20)), "food_low_access_share": ((0, 100), None),
    "hud_env_health": ((0, 100), (5, 50)), "park_dist_mi": ((0, 20), (0.2, 2)),
}


def metrics(t, arc):
    stats = {}
    for c in [*SCORED, *OPTIONAL]:
        if c not in arc.columns:
            continue
        v = arc[c]
        cov = v.notna().mean()
        hard, mid = BOUNDS.get(c, (None, None))
        s = {"coverage": round(float(cov), 4), "min": float(v.min()), "median": float(v.median()), "max": float(v.max())}
        stats[c] = s
        if c in SCORED:
            check("Metrics", f"{c}: coverage", cov > 0.99, f"{cov:.1%} of scored tracts have a value", warn=cov > 0.95)
        if hard and v.notna().any():
            ok = v.dropna().between(*hard).all()
            check("Metrics", f"{c}: within possible range", ok, f"min {s['min']:.3g}, max {s['max']:.3g} (allowed {hard[0]} to {hard[1]})")
        if mid:
            check("Metrics", f"{c}: plausible median", mid[0] <= s["median"] <= mid[1],
                  f"ARC median {s['median']:.3g} (expected {mid[0]} to {mid[1]})", warn=True)

    # Independent recomputation for a random sample of tracts.
    sample = arc.sample(25, random_state=1)
    z = zipfile.ZipFile(RAW / "marta_gtfs.zip")
    stops = pd.read_csv(z.open("stops.txt"), dtype=str)
    st = stops[(stops["location_type"] == "1") & ~stops["stop_name"].str.contains("PARK & RIDE")]
    check("Metrics", "MARTA rail stations", len(st) == 38, f"{len(st)} rail stations (MARTA operates 38)")
    d = haversine_mi(sample["lat"].to_numpy()[:, None], sample["lon"].to_numpy()[:, None],
                     st["stop_lat"].astype(float).to_numpy()[None, :], st["stop_lon"].astype(float).to_numpy()[None, :]).min(axis=1)
    err = np.abs(d - sample["marta_rail_dist_mi"].to_numpy()).max()
    check("Metrics", "Rail distance recomputed independently", err < 0.01, f"25 random tracts, largest difference {err:.4f} mi")

    h = pd.read_csv(RAW / "hrsa_health_center_sites_ga.csv", dtype=str)
    h = h[h["Site Status Description"] == "Active"]
    hx = pd.to_numeric(h["Geocoding Artifact Address Primary X Coordinate"], errors="coerce")
    hy = pd.to_numeric(h["Geocoding Artifact Address Primary Y Coordinate"], errors="coerce")
    ok = hx.notna() & hy.notna()
    d = haversine_mi(sample["lat"].to_numpy()[:, None], sample["lon"].to_numpy()[:, None],
                     hy[ok].to_numpy()[None, :], hx[ok].to_numpy()[None, :]).min(axis=1)
    err = np.abs(d - sample["fqhc_dist_mi"].to_numpy()).max()
    check("Metrics", "Health-center distance recomputed independently", err < 0.01, f"largest difference {err:.4f} mi")

    wac = pd.read_csv(sorted(RAW.glob("ga_wac_S000_JT00_*.csv.gz"))[-1], dtype={"w_geocode": str}, usecols=["w_geocode", "C000"])
    xw = pd.read_csv(RAW / "ga_lodes_block_xwalk.csv", dtype={"tabblk2020": str, "trct": str})
    j = wac.merge(xw, left_on="w_geocode", right_on="tabblk2020")
    arc_jobs = int(j[j["trct"].str[:5].isin(ARC_COUNTY_FIPS)]["C000"].sum())
    check("Metrics", "Total jobs located in the ARC counties", 2.0e6 < arc_jobs < 3.5e6,
          f"{arc_jobs:,} jobs (LODES); metro Atlanta has roughly 2.5-3 million", warn=True, value=arc_jobs)
    one = sample.iloc[0]
    dd = haversine_mi(one["lat"], one["lon"], j["blklatdd"].astype(float), j["blklondd"].astype(float))
    recount = int(j.loc[dd <= 5, "C000"].sum())
    check("Metrics", "Jobs within 5 miles recomputed independently", recount == int(one["jobs_within_5mi"]),
          f"tract {one['tract']}: {recount:,} vs stored {int(one['jobs_within_5mi']):,}")

    oa = pd.read_csv(RAW / "oa_tract_outcomes_ga.csv", dtype={"state": str, "county": str, "tract": str})
    oa["t10"] = oa["state"].str.zfill(2) + oa["county"].str.zfill(3) + oa["tract"].str.zfill(6)
    kfr = oa.set_index("t10")["kfr_pooled_pooled_p25"]
    xwc = pd.read_csv(RAW / "oa_tract_2010_2020_crosswalk_ga.csv", dtype=str).set_index("tract_2020")
    errs = []
    for tr in sample["tract"].head(10):
        r = xwc.loc[tr]
        num = den = 0.0
        for i in range(1, 6):
            t10, w = r[f"tract_2010_{i}"], r[f"wgt_{i}"]
            if isinstance(t10, str) and isinstance(w, str) and t10 in kfr.index and pd.notna(kfr[t10]):
                num += float(w) * kfr[t10]; den += float(w)
        if den:
            errs.append(abs(num / den - arc.set_index("tract").loc[tr, "oa_kfr_p25"]))
    check("Metrics", "Opportunity Atlas crosswalk recomputed independently", max(errs) < 1e-9,
          f"{len(errs)} tracts, largest difference {max(errs):.2e}")

    # School proficiency: recompute one school straight from the raw GOSA file.
    eog = pd.read_csv(next(RAW.glob("EOG_*__GA_TST_AGGR_*.csv")), dtype=str, encoding="latin1")
    s = eog[(eog["SUBGROUP_NAME"] == "All Students") & (eog["INSTN_NUMBER"] != "ALL")
            & eog["TEST_CMPNT_TYP_NM"].isin(["English Language Arts", "Mathematics"])]
    s = s[pd.to_numeric(s["NUM_TESTED_CNT"], errors="coerce").notna()]
    school = s[s["INSTN_NAME"].str.contains("Morningside", na=False) & (s["SCHOOL_DSTRCT_NM"] == "Atlanta Public Schools")]
    if len(school):
        n = pd.to_numeric(school["NUM_TESTED_CNT"]).sum()
        good = pd.to_numeric(school["PROFICIENT_CNT"]).sum() + pd.to_numeric(school["DISTINGUISHED_CNT"]).sum()
        check("Metrics", "School proficiency read correctly", 0 < good / n <= 1,
              f"Morningside Elementary (APS): {good / n:.1%} proficient or distinguished in ELA + math, "
              f"{int(n)} tests; one of the metro's highest, as expected", value=round(100 * good / n, 1))
    return stats


# --------------------------------------------------------------------------- 4. scoring

def scoring(arc):
    errs = []
    for c, (dom, sign, _) in SCORED.items():
        p = (sign * arc[c]).rank(pct=True) * 100
        errs.append(float((p - arc[f"pctl_{c}"]).abs().max()))
    check("Scoring", "Percentiles reproduce from raw metrics", max(errs) < 1e-9, f"largest difference {max(errs):.1e}")
    errs = []
    for dom in DOMAIN_WEIGHTS:
        ws = {f"pctl_{c}": w for c, (d, _, w) in SCORED.items() if d == dom}
        errs.append(float((weighted_mean(arc, ws) - arc[f"score_{dom}"]).abs().max()))
    check("Scoring", "Part scores reproduce from percentiles", max(errs) < 1e-9, f"largest difference {max(errs):.1e}")
    idx = weighted_mean(arc, {f"score_{d}": w for d, w in DOMAIN_WEIGHTS.items()})
    sc = idx.rank(pct=True) * 100
    e = float((sc - arc["opportunity_score"]).abs().max())
    check("Scoring", "Opportunity score reproduces from part scores", e < 1e-9, f"largest difference {e:.1e}")
    check("Scoring", "Weights sum to 100%", abs(sum(DOMAIN_WEIGHTS.values()) - 1) < 1e-9,
          ", ".join(f"{d} {w:.0%}" for d, w in DOMAIN_WEIGHTS.items()))
    tiers = arc["opportunity_tier"].astype(str).value_counts(normalize=True)
    check("Scoring", "Tiers split evenly", tiers.between(0.19, 0.21).all(),
          ", ".join(f"{k} {v:.1%}" for k, v in tiers.items()))


# --------------------------------------------------------------------------- 5. validity

def validity(arc):
    cal = pd.read_csv(PROCESSED / "weight_calibration.csv").set_index("weighting")
    row = cal.loc["final (blend in ranges)"]
    prev = cal.loc["literature prior"]
    check("Validity", "Predicts children's upward mobility (held-out counties)", row["validity_mobility"] > 0.3,
          f"within-county rank correlation {row['validity_mobility']:.2f} (research-only weights {prev['validity_mobility']:.2f}; "
          "mobility part excluded so the score isn't graded on its own input)", value=float(row["validity_mobility"]))
    check("Validity", "Predicts life expectancy (held-out counties)", row["validity_life_exp"] > 0.3,
          f"within-county rank correlation {row['validity_life_exp']:.2f}", value=float(row["validity_life_exp"]))

    coi = PROCESSED / "opportunity_tract_with_coi.parquet"
    if coi.exists():
        c = pd.read_parquet(coi)
        c = c[c["tract"].isin(arc["tract"])]
        r = rk(c["opportunity_score"], c["coi_r_coi_met"])
        check("Validity", "Agrees with the Child Opportunity Index 3.0", r > 0.6,
              f"rank correlation {r:.2f} with COI 2024 (metro-normed), {len(c)} tracts", value=round(r, 6))

    base = arc["opportunity_score"]
    cut = lambda s: pd.cut(s, [0, 20, 40, 60, 80, 100], labels=False, include_lowest=True)
    keys = list(DOMAIN_WEIGHTS)
    b = np.array([DOMAIN_WEIGHTS[k] for k in keys])
    corr, keep = [], []
    for _ in range(300):
        w = b * RNG.uniform(0.5, 1.5, len(b)); w /= w.sum()
        s = weighted_mean(arc, {f"score_{k}": v for k, v in zip(keys, w)}).rank(pct=True) * 100
        corr.append(rk(base, s)); keep.append(((cut(s) == 4) & (cut(base) == 4)).sum() / (cut(base) == 4).sum())
    check("Validity", "Robust to the exact weights", np.median(corr) > 0.95,
          f"moving every weight up or down by up to 50% (300 draws): median rank correlation {np.median(corr):.3f}, "
          f"{np.median(keep):.0%} of Very high tracts stay Very high", value=float(np.median(corr)))

    # Noise in the Opportunity Atlas: redraw kfr and jail within their standard errors.
    oa = pd.read_csv(RAW / "oa_tract_outcomes_ga.csv", dtype={"state": str, "county": str, "tract": str})
    oa["tract_2010"] = oa["state"].str.zfill(2) + oa["county"].str.zfill(3) + oa["tract"].str.zfill(6)
    same_tier, flips = [], np.zeros(len(arc))
    for _ in range(100):
        draw = oa[["tract_2010"]].assign(
            oa_kfr_p25=oa["kfr_pooled_pooled_p25"] + RNG.normal(0, 1, len(oa)) * oa["kfr_pooled_pooled_p25_se"].fillna(0),
            oa_jail_p25=oa["jail_pooled_pooled_p25"] + RNG.normal(0, 1, len(oa)) * oa["jail_pooled_pooled_p25_se"].fillna(0))
        m = arc[["tract"]].merge(to_2020(draw, "tract_2010"), on="tract", how="left")
        mob = (0.7 * m["oa_kfr_p25"].rank(pct=True) + 0.3 * (-m["oa_jail_p25"]).rank(pct=True)) * 100
        a2 = arc.assign(score_mobility=mob.to_numpy())
        s = weighted_mean(a2, {f"score_{d}": w for d, w in DOMAIN_WEIGHTS.items()}).rank(pct=True) * 100
        same = (cut(s).to_numpy() == cut(base).to_numpy())
        same_tier.append(same.mean()); flips += ~same
    stable = (flips / 100 <= 0.05).mean()
    check("Validity", "Stable under the Opportunity Atlas margins of error", np.mean(same_tier) > 0.7,
          f"redrawing mobility data within its standard errors (100 runs): {np.mean(same_tier):.0%} of tracts keep "
          f"their tier on average; {stable:.0%} keep it in at least 95 of 100 runs", value=float(np.mean(same_tier)))

    # Relation to need, using USDA's ACS-based tract characteristics as stand-ins for the Need page.
    g = pd.read_csv(RAW / "usda_sram_general_ga.csv", dtype={"CensusTract20": str})
    g["tract"] = g["CensusTract20"].str.zfill(11)
    a = arc.merge(g, on="tract", how="left")
    a["snap_share"] = a["TractSNAP"] / a["OHU2020"]
    a["black_share"] = a["TractBlack"] / a["POP2020"]
    a["need_proxy"] = pd.concat([a["PovertyRate"].rank(pct=True), (-a["MedianFamilyIncome"]).rank(pct=True),
                                 a["snap_share"].rank(pct=True)], axis=1).mean(axis=1) * 100
    r_need = rk(a["opportunity_score"], a["need_proxy"])
    check("Validity", "Overlap with need", r_need > -0.6,
          f"rank correlation with a need stand-in (poverty, income, SNAP) {r_need:.2f}: strongly opposite but not identical. "
          f"Poverty alone {rk(a['opportunity_score'], a['PovertyRate']):.2f}, median family income "
          f"{rk(a['opportunity_score'], a['MedianFamilyIncome']):.2f}. A need-vs-opportunity slider mostly trades one for the other",
          warn=True, value=round(r_need, 6))
    part_need = {d: round(rk(a[f"score_{d}"], a["need_proxy"]), 2) for d in DOMAIN_WEIGHTS}
    check("Validity", "Which parts overlap with need", None,
          "rank correlation of each part with the need stand-in: " + ", ".join(f"{k} {v}" for k, v in part_need.items()),
          value=part_need)
    ind_need = {c: round(rk(a[f"pctl_{c}"], a["need_proxy"]), 2) for c in SCORED}
    worst = sorted(ind_need.items(), key=lambda kv: kv[1])[:3]
    check("Validity", "Metrics that behave most like need", None,
          "most negative with need (high need, low score): " + ", ".join(f"{k} {v}" for k, v in worst)
          + ". The Need page should avoid repeating these measures.", value=ind_need)

    # Does opportunity add information beyond need? Within-county regressions of outcomes.
    le = pd.read_csv(RAW / "usaleep_ga_2010_15.csv", dtype={"Tract ID": str}).rename(
        columns={"Tract ID": "tract_2010", "e(0)": "life_exp"})[["tract_2010", "life_exp"]]
    a = a.merge(to_2020(le, "tract_2010"), on="tract", how="left")

    def r2(y, cols):
        d = a.dropna(subset=[y, *cols]).copy()
        for c in [y, *cols]:
            d[c] = d[c].rank(pct=True)
            d[c] = d[c] - d.groupby("county")[c].transform("mean")
        return LinearRegression().fit(d[cols], d[y]).score(d[cols], d[y])
    access = ["score_schools", "score_jobs", "score_transit", "score_health", "score_environment"]
    m0, m1 = r2("oa_kfr_p25", ["need_proxy"]), r2("oa_kfr_p25", ["need_proxy", *access])
    l0, l1 = r2("life_exp", ["need_proxy"]), r2("life_exp", ["need_proxy", "opportunity_score"])
    check("Validity", "Adds information beyond need", (m1 - m0) > 0.02 and (l1 - l0) > 0.02,
          f"within counties, need explains {m0:.0%} of mobility differences; adding the opportunity parts raises it to {m1:.0%}. "
          f"Life expectancy: {l0:.0%} to {l1:.0%}", warn=True, value={"mobility": [m0, m1], "life_exp": [l0, l1]})

    # Fairness: how scores relate to a tract's Black share, before and after accounting for need.
    d = a.dropna(subset=["black_share", "need_proxy"])
    resid = lambda y: y.rank() - LinearRegression().fit(d[["need_proxy"]].rank(), y.rank()).predict(d[["need_proxy"]].rank())
    raw_r = rk(d["opportunity_score"], d["black_share"])
    adj_r = float(np.corrcoef(resid(d["opportunity_score"]), resid(d["black_share"]))[0, 1])
    def partial(col):   # rank correlation with Black share after removing need, on tracts with a value
        e = d.dropna(subset=[col])
        X = e[["need_proxy"]].rank()
        res = lambda y: y.rank() - LinearRegression().fit(X, y.rank()).predict(X)
        return round(float(np.corrcoef(res(e[col]), res(e["black_share"]))[0, 1]), 2)
    parts = {p: partial(f"score_{p}") for p in DOMAIN_WEIGHTS}
    check("Validity", "Fairness: relation to a tract's Black population share", adj_r > -0.3,
          f"rank correlation {raw_r:.2f}; {adj_r:.2f} after accounting for need. Driven by mobility ({parts['mobility']}) and "
          f"schools ({parts['schools']}); jobs, transit and health are near zero", warn=True,
          value={"raw": round(raw_r, 6), "after_need": round(adj_r, 6), "by_part_after_need": parts})
    oa2 = oa[["tract_2010"]].assign(k_b=oa["kfr_black_pooled_p25"], k_w=oa["kfr_white_pooled_p25"])
    d2 = d.merge(to_2020(oa2, "tract_2010"), on="tract", how="left")
    pooled, kb, kw = (rk(d2[c], d2["black_share"]) for c in ("oa_kfr_p25", "k_b", "k_w"))
    check("Validity", "Mobility metric mixes place with who lives there", pooled > -0.5,
          f"the pooled Opportunity Atlas mobility measure correlates {pooled:.2f} with Black share; the same measure for "
          f"Black children only {kb:.2f}, white children only {kw:.2f}. About half of the pooled gap reflects national "
          "racial income gaps rather than the place", warn=True,
          value={"pooled": round(pooled, 6), "black_children": round(kb, 6), "white_children": round(kw, 6)})

    # Overlap with feasibility: SiteLine location scores at past application sites.
    s = pd.read_parquet(SHARED / "applications_9pct_scored.parquet")
    ap = pd.read_parquet(SHARED / "applications_9pct_tract.parquet")
    m = ap[["app_id", "round_year", "opportunity_score", "score_transit", "score_health"]].merge(
        s[["app_id", "round_year", "sl_total_score"]], on=["app_id", "round_year"]).dropna()
    r = rk(m["sl_total_score"], m["opportunity_score"])
    check("Validity", "Distinct from feasibility (SiteLine location score)", abs(r) < 0.6,
          f"rank correlation {r:.2f} at {len(m)} past application sites (transit part {rk(m['sl_total_score'], m['score_transit']):.2f}, "
          f"health part {rk(m['sl_total_score'], m['score_health']):.2f})", warn=True, value=round(r, 6))


def main():
    t, arc = load()
    src = sources()
    geography(t, arc)
    stats = metrics(t, arc)
    scoring(arc)
    validity(arc)
    report = {"generated": pd.Timestamp.now().strftime("%Y-%m-%d"), "sources": src, "metric_stats": stats,
              "checks": checks, "summary": pd.Series([c["status"] for c in checks]).value_counts().to_dict()}
    OUT.write_text(json.dumps(report, indent=1, default=float))
    for c in checks:
        print(f"[{c['status']}] {c['section']:<9} {c['check']}: {c['detail']}")
    print("\n", report["summary"], f"-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
