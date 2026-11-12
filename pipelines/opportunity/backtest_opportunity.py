"""Backtest the opportunity score: rebuild it with 2010-era data and check it against later outcomes.

    python -m pipelines.opportunity.build_opportunity
    python -m pipelines.opportunity.backtest_opportunity

Three tests, written to data/processed/opportunity/opportunity_backtest.json:

  1. Each metric vs outcomes   every scored metric's within-county rank correlation with children's
                               upward mobility and with life expectancy, and how many counties agree
  2. Historical backtest       the score rebuilt from data that existed around 2010 (2011 school
                               results, 2005-2010 jobs, 2010 food access, MARTA rail, health centers
                               open by 2010), then tested against outcomes measured afterward:
                               life expectancy 2010-15 and poor general health 2022-23. Compared
                               with 2010 poverty alone, and with today's score
  3. Stability                 how closely each metric ranks tracts the same way across years

Within-county rank correlations are used throughout, so a score is judged on telling neighborhoods
apart inside a county, not on county-wide differences. 2010-era files are cached in
data/raw/backtest/.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LinearRegression

from pipelines.opportunity.build_opportunity import (DOMAIN_WEIGHTS, RAW, SCORED, match_schools,
                                         nearest, sum_within, to_2020, to_num, weighted_mean)
from pipelines.opportunity.pull_opportunity import _csv_bytes, get

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "data" / "processed"          # shared tables (DCA applications)
PROCESSED = SHARED / "opportunity"
BT = ROOT / "data" / "raw" / "backtest"
OUT = PROCESSED / "opportunity_backtest.json"

GOSA = "https://download.gosa.ga.gov"
LODES = "https://lehd.ces.census.gov/data/lodes/LODES8/ga/wac"


# --------------------------------------------------------------------------- data

def fetch_fara2010(s):
    url = "https://www.ers.usda.gov/media/5624/2010-food-access-research-atlas-fara-data-and-documentation.zip"
    z = zipfile.ZipFile(io.BytesIO(get(s, url, timeout=900).content))
    d = pd.read_excel(z.open("FoodAccessResearchAtlasData2010.xlsx"), sheet_name="Food Access Research Atlas",
                      dtype={"CensusTract": str})
    d["CensusTract"] = d["CensusTract"].str.zfill(11)
    return _csv_bytes(d[d["CensusTract"].str.startswith("13")])


def fetch_covariates(s):
    url = "https://opportunityinsights.org/wp-content/uploads/2018/10/tract_covariates.csv"
    d = pd.read_csv(io.BytesIO(get(s, url).content), dtype={"state": str, "county": str, "tract": str})
    return _csv_bytes(d[d["state"].astype(int) == 13])


FILES = {
    "crct_2011.csv": f"{GOSA}/2011/CRCT_2011_FEB_24_2020.csv",
    "eoct_2011.csv": f"{GOSA}/2011/EOCT_2011_FEB_24_2020.csv",
    "grad_2011.csv": f"{GOSA}/2011/Graduation_Rate_2011_MAR_23_2020.csv",
    "eog_2019.csv": f"{GOSA}/2019/EOG_2019_JUN_1ST_2020.csv",
    "grad_2019.csv": f"{GOSA}/2019/Graduation_Rate_2019_Dec2nd_2019.csv",
    "ga_wac_2005.csv.gz": f"{LODES}/ga_wac_S000_JT00_2005.csv.gz",
    "ga_wac_2010.csv.gz": f"{LODES}/ga_wac_S000_JT00_2010.csv.gz",
    "ga_rac_2010.csv.gz": LODES.replace("/wac", "/rac") + "/ga_rac_S000_JT01_2010.csv.gz",
    "fara2010_ga.csv": fetch_fara2010,
    "oi_tract_covariates_ga.csv": fetch_covariates,
}


def fetch_all():
    BT.mkdir(parents=True, exist_ok=True)
    with requests.Session() as s:
        for name, src in FILES.items():
            path = BT / name
            if path.exists() and path.stat().st_size > 0:
                continue
            path.write_bytes(src(s) if callable(src) else get(s, src).content)
            print(f"  downloaded {name}")


# --------------------------------------------------------------------------- helpers

def within(df, col, y, min_n=20):
    """Within-county rank correlation (weighted by tracts), and how many counties are positive."""
    rs, ns = [], []
    for _, g in df.groupby("county"):
        g = g.dropna(subset=[col, y])
        if len(g) < min_n or g[col].nunique() < 5:
            continue
        r = g[col].rank().corr(g[y].rank())
        if not np.isnan(r):
            rs.append(r); ns.append(len(g))
    if not rs:
        return {"r": None, "positive": 0, "counties": 0}
    return {"r": round(float(np.average(rs, weights=ns)), 3), "positive": int(sum(r > 0 for r in rs)), "counties": len(rs)}


def within_r2(df, y, cols):
    """Share of within-county variation in y explained by cols (all as ranks, county means removed)."""
    d = df.dropna(subset=[y, *cols]).copy()
    for c in [y, *cols]:
        d[c] = d[c].rank(pct=True)
        d[c] = d[c] - d.groupby("county")[c].transform("mean")
    return float(LinearRegression().fit(d[cols], d[y]).score(d[cols], d[y]))


def gosa_proficiency(df, good, tested, subjects=None):
    """Share of All Students tests at or above standard, per school (district code + school number)."""
    dist = "SCHOOL_DISTRCT_CD" if "SCHOOL_DISTRCT_CD" in df else "SCHOOL_DSTRCT_CD"
    d = df[(df["SUBGROUP_NAME"] == "All Students") & (df["INSTN_NUMBER"] != "ALL")]
    if subjects:
        d = d[d["TEST_CMPNT_TYP_NM"].isin(subjects)]
    d = d.assign(n=to_num(d[tested]), g=sum(to_num(d[c]) for c in good)).dropna(subset=["n", "g"])
    out = d.groupby([dist, "INSTN_NUMBER"]).agg(INSTN_NAME=("INSTN_NAME", "first"), n=("n", "sum"), g=("g", "sum")).reset_index()
    return out.rename(columns={dist: "SCHOOL_DISTRCT_CD"}).assign(prof=lambda x: 100 * x["g"] / x["n"])[
        ["SCHOOL_DISTRCT_CD", "INSTN_NUMBER", "INSTN_NAME", "prof"]]


def grad_rates(df):
    d = df[(df["DETAIL_LVL_DESC"] == "School") & (df["LABEL_LVL_1_DESC"] == "Grad Rate -ALL Students")]
    return d.assign(grad=to_num(d["PROGRAM_PERCENT"]))[["SCHOOL_DSTRCT_CD", "INSTN_NUMBER", "INSTN_NAME", "grad"]].rename(
        columns={"SCHOOL_DSTRCT_CD": "SCHOOL_DISTRCT_CD"})


def nces_points():
    n = pd.read_csv(RAW / "nces_public_schools_ga.csv", dtype=str)
    n = n[(n["SCHOOL_TYPE_TEXT"] == "Regular School") & (n["VIRTUAL"] == "Not Virtual") & (n["CHARTER_TEXT"] != "Yes")]
    grade = lambda s: pd.to_numeric(s.replace({"PK": "-1", "KG": "0", "UG": np.nan, "N": np.nan}), errors="coerce")
    lo, hi = grade(n["GSLO"]), grade(n["GSHI"])
    return n.assign(elem=(lo <= 5) & (hi >= 3), middle=(lo <= 8) & (hi >= 6), high=(lo <= 12) & (hi >= 9),
                    lat=n["lat"].astype(float), lon=n["lon"].astype(float))


def place_schools(table, value, nces):
    """Attach a GOSA school table to NCES points (by district and name). Returns points and match rate."""
    table = table.copy()
    table["NCESSCH"] = match_schools(table, nces)
    pts = nces.merge(table.dropna(subset=["NCESSCH"])[["NCESSCH", value]], on="NCESSCH").dropna(subset=[value])
    return pts, float(table["NCESSCH"].notna().mean())


def school_metrics(tracts, elem_mid, high, grad, nces):
    """Same rules as the live score: mean of 3 nearest elementary, nearest middle, nearest high, grad rate."""
    out = tracts[["tract"]].copy()
    p, r1 = place_schools(elem_mid, "prof", nces)
    e = p[p["elem"]]; _, j = nearest(tracts, e, k=3); out["school_prof_elem3"] = e["prof"].to_numpy()[j].mean(axis=1)
    m = p[p["middle"]]; _, j = nearest(tracts, m); out["school_prof_middle"] = m["prof"].to_numpy()[j[:, 0]]
    rates = {"elem/middle": r1}
    if high is not None:
        p2, rates["high"] = place_schools(high, "prof", nces)
        h = p2[p2["high"]]; _, j = nearest(tracts, h); out["school_prof_high"] = h["prof"].to_numpy()[j[:, 0]]
    if grad is not None:
        p3, rates["grad"] = place_schools(grad, "grad", nces)
        g = p3[p3["high"]]; _, j = nearest(tracts, g); out["school_grad_rate_high"] = g["grad"].to_numpy()[j[:, 0]]
    return out, rates


def jobs_metrics(tracts, latest, base, rac_file=None):
    """Job growth, and the skill-matched measures the live score uses: jobs held by workers without
    a bachelor's degree within 5 miles, and those jobs per non-degree worker living within 5 miles."""
    xw = pd.read_csv(RAW / "ga_lodes_block_xwalk.csv", dtype={"tabblk2020": str, "trct": str})
    pts = lambda df, key: df.merge(xw, left_on=key, right_on="tabblk2020").rename(columns={"blklatdd": "lat", "blklondd": "lon"})
    out = tracts[["tract"]].copy()
    vals = {}
    for y in (latest, base):
        w = pd.read_csv(BT / f"ga_wac_{y}.csv.gz", dtype={"w_geocode": str})
        w = pts(w.assign(nd=w[["CD01", "CD02", "CD03"]].sum(axis=1)), "w_geocode")
        vals[y] = sum_within(tracts, w, "C000", 5.0)
        if y == latest:
            out["jobs_within_5mi"] = vals[y]
            out["jobs_nondegree_5mi"] = sum_within(tracts, w, "nd", 5.0)
    out["jobs_growth_5mi"] = vals[latest] / np.where(vals[base] == 0, np.nan, vals[base]) - 1
    if rac_file is not None:
        r = pd.read_csv(rac_file, dtype={"h_geocode": str})
        workers = sum_within(tracts, pts(r.assign(nd=r[["CD01", "CD02", "CD03"]].sum(axis=1)), "h_geocode"), "nd", 5.0)
        out["jobs_per_nondegree_worker_5mi"] = out["jobs_nondegree_5mi"] / np.where(workers == 0, np.nan, workers)
    return out


# --------------------------------------------------------------------------- the 2010 score

def score_from(t, cols, domain_weights):
    """Percentiles among scored tracts -> part scores (live indicator weights) -> composite percentile."""
    t = t.copy()
    parts = {}
    for c in cols:
        dom, sign, w = SCORED[c]
        t[f"p_{c}"] = (sign * t[c]).rank(pct=True) * 100
        parts.setdefault(dom, {})[f"p_{c}"] = w
    for dom, ws in parts.items():
        t[f"s_{dom}"] = weighted_mean(t, ws)
    dw = {f"s_{d}": domain_weights[d] for d in parts}
    return weighted_mean(t, dw).rank(pct=True) * 100, sorted(parts)


def build_2010(arc):
    """Opportunity metrics as they could have been measured around 2010."""
    t = arc[["tract", "county", "lat", "lon", "oa_kfr_p25", "oa_jail_p25"]].copy()
    nces = nces_points()
    crct = pd.read_csv(BT / "crct_2011.csv", dtype=str, encoding="latin1")
    eoct = pd.read_csv(BT / "eoct_2011.csv", dtype=str, encoding="latin1")
    grad = pd.read_csv(BT / "grad_2011.csv", dtype=str, encoding="latin1")
    em = gosa_proficiency(crct, ["MEETS_CNT", "EXCEEDS_CNT"], "NUM_TESTED_CNT", ["English Language Arts", "Mathematics"])
    hs = gosa_proficiency(eoct, ["MEETS_CNT", "EXCEEDS_CNT"], "NUM_TESTED_CNT")
    sch, rates = school_metrics(t, em, hs, grad_rates(grad), nces)
    t = t.merge(sch, on="tract")
    t = t.merge(jobs_metrics(t, 2010, 2005, BT / "ga_rac_2010.csv.gz"), on="tract")
    z = zipfile.ZipFile(RAW / "marta_gtfs.zip")
    stops = pd.read_csv(z.open("stops.txt"), dtype=str)
    st = stops[(stops["location_type"] == "1") & ~stops["stop_name"].str.contains("PARK & RIDE")]
    d, _ = nearest(t, st.assign(lat=st["stop_lat"].astype(float), lon=st["stop_lon"].astype(float)))
    t["marta_rail_dist_mi"] = d[:, 0]   # all 38 rail stations opened by 2000
    h = pd.read_csv(RAW / "hrsa_health_center_sites_ga.csv", dtype=str)
    h = h[pd.to_datetime(h["Site Added to Scope this Date"], errors="coerce") < "2011-01-01"]
    h = h.assign(lon=to_num(h["Geocoding Artifact Address Primary X Coordinate"]),
                 lat=to_num(h["Geocoding Artifact Address Primary Y Coordinate"])).dropna(subset=["lat", "lon"])
    d, _ = nearest(t, h)
    t["fqhc_dist_mi"] = d[:, 0]
    f = pd.read_csv(BT / "fara2010_ga.csv", dtype={"CensusTract": str})
    f = to_2020(f[["CensusTract", "lapop1share"]].rename(columns={"CensusTract": "tract_2010"})
                .assign(lapop1share=lambda x: 100 * x["lapop1share"]), "tract_2010")
    t = t.merge(f.rename(columns={"lapop1share": "food_low_access_share"}), on="tract", how="left")
    return t, {"school_match_rates": rates, "health_centers_open_by_2010": int(len(h))}


# --------------------------------------------------------------------------- tests

def main():
    fetch_all()
    tr = pd.read_parquet(PROCESSED / "opportunity_tract.parquet")
    arc = tr[tr["opportunity_score"].notna()].copy()
    le = pd.read_csv(RAW / "usaleep_ga_2010_15.csv", dtype={"Tract ID": str}).rename(
        columns={"Tract ID": "tract_2010", "e(0)": "life_exp"})[["tract_2010", "life_exp"]]
    cov = pd.read_csv(BT / "oi_tract_covariates_ga.csv", dtype={"state": str, "county": str, "tract": str})
    cov["tract_2010"] = cov["state"].str.zfill(2) + cov["county"].str.zfill(3) + cov["tract"].str.zfill(6)
    arc = (arc.merge(to_2020(le, "tract_2010"), on="tract", how="left")
              .merge(to_2020(cov[["tract_2010", "poor_share2010"]], "tract_2010"), on="tract", how="left"))
    arc["good_health"] = -arc["places_poor_health"]       # 2022-23: lower poor-health share is better
    arc["low_poverty_2010"] = -arc["poor_share2010"]
    report = {}

    # 1. Each current metric against outcomes.
    per = []
    for c, (dom, _, _) in SCORED.items():
        per.append({"part": dom, "metric": c,
                    "mobility": within(arc, f"pctl_{c}", "oa_kfr_p25") if dom != "mobility" else None,
                    "life_expectancy": within(arc, f"pctl_{c}", "life_exp")})
    for dom in DOMAIN_WEIGHTS:
        per.append({"part": dom, "metric": f"PART {dom}",
                    "mobility": within(arc, f"score_{dom}", "oa_kfr_p25") if dom != "mobility" else None,
                    "life_expectancy": within(arc, f"score_{dom}", "life_exp")})
    report["per_metric"] = per

    # 2. Historical backtest.
    t10, info = build_2010(arc)
    t10 = t10.rename(columns={"jobs_within_5mi": "jobs_all_5mi_2010"})
    cols = ["school_prof_elem3", "school_prof_middle", "school_prof_high", "school_grad_rate_high",
            "jobs_nondegree_5mi", "jobs_per_nondegree_worker_5mi", "jobs_growth_5mi",
            "marta_rail_dist_mi", "fqhc_dist_mi", "food_low_access_share"]
    t10["score_2010"], parts = score_from(t10, cols, DOMAIN_WEIGHTS)
    t10["score_2010_mob"], parts_m = score_from(t10, cols + ["oa_kfr_p25", "oa_jail_p25"], DOMAIN_WEIGHTS)
    a = arc.merge(t10[["tract", "score_2010", "score_2010_mob", "jobs_all_5mi_2010"] + cols].rename(
        columns={c: c + "_2010" for c in cols}), on="tract")
    outcomes = {"life_exp": "Life expectancy 2010-15 (CDC USALEEP)",
                "good_health": "Adults not in poor health 2022-23 (CDC PLACES)"}
    bt = {"inputs": "2011 school results (CRCT, EOCT, graduation), jobs 2005 and 2010 (LODES), 2010 food access (USDA), "
                    "MARTA rail stations (all open by 2000), health centers open by 2010 (HRSA)",
          "parts_used": parts, **info, "results": {}}
    for y, label in outcomes.items():
        bt["results"][y] = {
            "label": label,
            "score_2010": within(a, "score_2010", y),
            "score_2010_with_mobility": within(a, "score_2010_mob", y),
            "poverty_2010_only": within(a, "low_poverty_2010", y),
            "todays_score": within(a, "opportunity_score", y),
            "r2_poverty_only": round(within_r2(a, y, ["low_poverty_2010"]), 3),
            "r2_poverty_plus_score_2010": round(within_r2(a, y, ["low_poverty_2010", "score_2010"]), 3),
        }
    bt["results"]["mobility"] = {"label": "Children's adult income (Opportunity Atlas), for the score without mobility",
                                 "score_2010": within(a, "score_2010", "oa_kfr_p25"),
                                 "poverty_2010_only": within(a, "low_poverty_2010", "oa_kfr_p25")}
    top10 = pd.qcut(a["score_2010_mob"], 5, labels=False) == 4
    topnow = pd.qcut(a["opportunity_score"], 5, labels=False) == 4
    bt["top_fifth_2010_still_top_fifth_now"] = round(float((top10 & topnow).sum() / top10.sum()), 3)
    bt["rank_corr_2010_vs_today"] = round(float(a["score_2010_mob"].rank().corr(a["opportunity_score"].rank())), 3)
    report["historical_backtest"] = bt

    # 3. Stability across years.
    nces = nces_points()
    eog19 = pd.read_csv(BT / "eog_2019.csv", dtype=str, encoding="latin1")
    em19 = gosa_proficiency(eog19, ["PROFICIENT_CNT", "DISTINGUISHED_CNT"], "NUM_TESTED_CNT", ["English Language Arts", "Mathematics"])
    gr19 = grad_rates(pd.read_csv(BT / "grad_2019.csv", dtype=str, encoding="latin1"))
    s19, _ = school_metrics(arc[["tract", "lat", "lon"]], em19, None, gr19, nces)
    a = a.merge(s19.rename(columns={c: c + "_2019" for c in s19.columns if c != "tract"}), on="tract", how="left")
    rc = lambda x, y: round(float(a[x].rank().corr(a[y].rank())), 3)
    report["stability"] = [
        {"metric": "Elementary proficiency (3 nearest)", "years": "2019 vs 2025", "rank_corr": rc("school_prof_elem3_2019", "school_prof_elem3")},
        {"metric": "Elementary proficiency (3 nearest)", "years": "2011 vs 2025", "rank_corr": rc("school_prof_elem3_2010", "school_prof_elem3")},
        {"metric": "Middle school proficiency", "years": "2019 vs 2025", "rank_corr": rc("school_prof_middle_2019", "school_prof_middle")},
        {"metric": "Middle school proficiency", "years": "2011 vs 2025", "rank_corr": rc("school_prof_middle_2010", "school_prof_middle")},
        {"metric": "Graduation rate", "years": "2019 vs 2025", "rank_corr": rc("school_grad_rate_high_2019", "school_grad_rate_high")},
        {"metric": "Graduation rate", "years": "2011 vs 2025", "rank_corr": rc("school_grad_rate_high_2010", "school_grad_rate_high")},
        {"metric": "Jobs within 5 miles", "years": "2010 vs 2023", "rank_corr": rc("jobs_all_5mi_2010", "jobs_within_5mi")},
        {"metric": "Non-degree jobs within 5 miles", "years": "2010 vs 2023", "rank_corr": rc("jobs_nondegree_5mi_2010", "jobs_nondegree_5mi")},
        {"metric": "Non-degree jobs per non-degree worker", "years": "2010 vs 2023", "rank_corr": rc("jobs_per_nondegree_worker_5mi_2010", "jobs_per_nondegree_worker_5mi")},
        {"metric": "Job growth within 5 miles", "years": "2005-10 vs 2018-23", "rank_corr": rc("jobs_growth_5mi_2010", "jobs_growth_5mi")},
        {"metric": "Grocery access (share over 1 mile)", "years": "2010 vs 2025", "rank_corr": rc("food_low_access_share_2010", "food_low_access_share")},
        {"metric": "Distance to a health center", "years": "2010 sites vs today", "rank_corr": rc("fqhc_dist_mi_2010", "fqhc_dist_mi")},
        {"metric": "Overall score", "years": "2010 rebuild vs today", "rank_corr": bt["rank_corr_2010_vs_today"]},
    ]

    OUT.write_text(json.dumps(report, indent=1))
    print("== Each metric vs outcomes (within-county rank correlation; counties positive)")
    for p in per:
        m, l = p["mobility"], p["life_expectancy"]
        print(f"  {p['metric']:<26} mobility {'' if m is None else m['r']!s:>6} ({'' if m is None else str(m['positive']) + '/' + str(m['counties'])})"
              f"   life exp {l['r']!s:>6} ({l['positive']}/{l['counties']})")
    print("== Historical backtest:", json.dumps({k: v for k, v in bt.items() if k != "results"}, indent=None))
    for y, r in bt["results"].items():
        print("  ", y, json.dumps(r))
    print("== Stability")
    for s in report["stability"]:
        print(f"  {s['metric']:<38} {s['years']:<22} {s['rank_corr']}")
    print(f"-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
