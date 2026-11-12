"""Backtest weighting frameworks against later outcomes.

    python -m pipelines.need.backtest_frameworks

Each framework's need score is rebuilt from data that ends in 2021 (ACS 2017-2021, CHAS 2017-2021,
both on 2020 tracts), then compared with outcomes measured afterwards:
- eviction filings per 100 renter households, 2023-2025 (Eviction Lab; 5 core counties)
- eviction filings per 100 renter households, January-May 2026, annualized (same source)
- median gross rent growth, 2017-2021 to 2020-2024 (ACS; the periods share 2020-2021)
- housing insecurity: % of adults who couldn't pay rent/mortgage or feared losing housing (CDC PLACES
  2025 release, 2022-2023 BRFSS; model-based, using ACS 2019-2023 covariates)
- utility shutoff threat: % of adults threatened with a power, gas or water shutoff (same source)
- loss of low-income renters: % drop in renter households under $35,000 (2021 dollars) from ACS
  2017-2021 to 2020-2024; the 2024 cutoff is inflated by CPI-U ($40,517) and interpolated within the
  $35,000-49,999 bracket. A displacement signal, but noisy and prone to regression to the mean.
- combined: the average percentile of evictions 2023-2025, housing insecurity, utility shutoff and
  low-income renter loss, for tracts with all four (the 5 eviction counties). combined_dedup averages
  the two CDC measures first, since they correlate 0.99 and are effectively one signal.

Frameworks (group weights in the order income / burden / market / vulnerable / work / quality / supply;
metric weights within groups are the tool's factor-analysis weights):
- original: 25/25/10/10/10/5/15, the tool's first default
- hud: 25/40/5/5/0/10/15, HUD Worst-Case Needs, the current default
- hud_chas: same group weights, but the Burden group is HUD CHAS alone (renters at or below 80% of
  area median income with a severe housing problem) and the metrics CHAS already counts are removed:
  the three ACS rent-burden metrics, overcrowding and missing plumbing/kitchen. CHAS has no margins
  of error in HUD's map service, so a tract's CHAS value is used only with 25+ low-income renters.
- hud_chas_prevalence (post hoc): the same, but CHAS as a share of all households rather than of
  low-income renters. Added after the first run; treat it as exploratory.
Benchmarks: equal weights, the income group alone, and the CDC/ATSDR SVI 2022 (built from 2018-2022
data, so it isn't strictly earlier than the outcomes).

Rent growth is left out of every framework here: there's no earlier rent on 2020 tracts to compute it.
Existing assisted housing (the supply gap) comes from the current HUD files, which mostly predate 2021.

Writes data/processed/need/validation/backtest_frameworks.json and .csv.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import requests

from pipelines.need.build_tract_need import (ACS_TABLES, APP_DATA, ARC_COUNTIES, GROUPS, HEADERS, OUT_CSV,
                                        RAW, ROOT, add_supply_gap, build_metrics, fetch_acs,
                                        reliability)
from pipelines.need.validate_need import KEYS, SVI_CSV, VAL_DIR, aggregate, normalize, unreliable_flags

CHAS_URL = ("https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/"
            "ACS_5YR_ESTIMATES_CHAS_TRACT/FeatureServer/1/query")
CHAS_NUM = ["T2_EST128", "T2_EST136", "T2_EST144"]   # renter <=80% AMI with a severe housing problem
CHAS_DEN = ["T8_EST69", "T8_EST82", "T8_EST95"]      # all renter <=80% AMI (same fields SiteLine uses)
CHAS_ALL = "T2_EST1"                                  # all households
GKEYS = [g for g, _ in GROUPS]
CHAS_REPLACES = ["low_income_renter_burden", "rent_burden_30plus", "severe_rent_burden_50plus",
                 "overcrowded_share", "lacking_facilities"]
CHAS_SLOT = "low_income_renter_burden"                # CHAS takes this column's place in the burden group
BOOT = 1000
rng = np.random.default_rng(20261101)


def gw(*w) -> np.ndarray:
    return np.array(w, dtype=float)


def fetch_chas() -> pd.DataFrame:
    path = RAW / "hud" / "chas_tract_2017_2021_v2.json"
    if not path.exists():
        rows, off = [], 0
        where = "STATE='13' AND COUNTY IN (%s)" % ",".join(f"'{c}'" for c in ARC_COUNTIES)
        while True:
            d = requests.get(CHAS_URL, params={"where": where, "outFields": ",".join(["GEOID", CHAS_ALL] + CHAS_NUM + CHAS_DEN),
                                               "returnGeometry": "false", "f": "json", "resultOffset": off,
                                               "resultRecordCount": 2000}, headers=HEADERS, timeout=60).json()
            page = d.get("features", [])
            rows += [f["attributes"] for f in page]
            if not page or not d.get("exceededTransferLimit"):
                break
            off += len(page)
        path.write_text(json.dumps(rows))
    c = pd.DataFrame(json.loads(path.read_text())).set_index("GEOID")
    num, den = c[CHAS_NUM].sum(axis=1), c[CHAS_DEN].sum(axis=1)
    allhh = c[CHAS_ALL]
    return pd.DataFrame({"chas_severe_share": num / den.where(den > 0), "chas_le80_renters": den,
                         "chas_prevalence": num / allhh.where(allhh > 0), "chas_households": allhh})


PLACES_URL = "https://data.cdc.gov/resource/yjkw-uj5s.json"     # CDC PLACES tract data, 2025 release


def fetch_places() -> pd.DataFrame:
    """Housing insecurity and utility-shutoff estimates for the ARC tracts, downloaded once."""
    path = RAW / "places_2025_arc.json"
    if not path.exists():
        counties = "','".join("13" + c for c in ARC_COUNTIES)
        rows = requests.get(PLACES_URL, params={
            "$select": "tractfips,totalpopulation,housinsecu_crudeprev,housinsecu_crude95ci,"
                       "shututility_crudeprev,shututility_crude95ci",
            "$where": f"countyfips in('{counties}')", "$limit": 5000}, headers=HEADERS, timeout=60).json()
        path.write_text(json.dumps(rows))
    return pd.DataFrame(json.loads(path.read_text())).set_index("tractfips")


def lift(score: pd.Series, num: pd.Series, den: pd.Series) -> float:
    """Pooled outcome rate in the top 10% of scores divided by the pooled rate overall."""
    top = score >= score.quantile(0.9)
    return float((num[top].sum() / den[top].sum()) / (num.sum() / den.sum()))


def main() -> None:
    m = pd.read_csv(OUT_CSV, dtype={"tract_geoid": str}).set_index("tract_geoid")
    app = json.loads(APP_DATA.read_text()[len("window.NEED_DATA = "):-2])
    mw_fa = np.array([x["weight"] for x in app["metrics"]], dtype=float)

    # ---- the score inputs as of 2021 ------------------------------------------------------
    prefixes = tuple(f"1400000US13{c}" for c in ARC_COUNTIES)
    acs21 = {t: fetch_acs(t, 2021, prefixes, offline=False) for t in ACS_TABLES}
    m21, _ = build_metrics(acs21, acs21["B25064"])
    m21 = m21.reindex(m.index).copy()
    m21["rent_growth"] = np.nan                                       # no earlier rent on 2020 tracts
    m21["assisted_units"] = m["assisted_units"]
    add_supply_gap(m21)
    reliability(m21)
    scored = (m21["population"].fillna(0) >= 500) & m21["population"].notna()
    ranked = scored & (m21["student_share"] < 0.25)
    flags = unreliable_flags(m21, 0.40)

    chas = fetch_chas().reindex(m.index)
    mC = m21.copy()
    mC[CHAS_SLOT] = chas["chas_severe_share"]
    flagsC = flags.copy()
    flagsC[CHAS_SLOT] = chas["chas_severe_share"].isna() | (chas["chas_le80_renters"] < 25)
    mw_chas = mw_fa.copy()
    for k in CHAS_REPLACES:
        mw_chas[KEYS.index(k)] = 0.0
    mw_chas[KEYS.index(CHAS_SLOT)] = 100.0

    # Post-hoc variant, added after the first run showed the within-group share barely tracks poverty:
    # severely burdened low-income renters as a share of ALL households (how common the need is).
    mP = m21.copy()
    mP[CHAS_SLOT] = chas["chas_prevalence"]
    flagsP = flags.copy()
    flagsP[CHAS_SLOT] = chas["chas_prevalence"].isna() | (chas["chas_households"] < 100)

    def need_score(mm, ff, mw, g):
        return aggregate(normalize(mm, ff, scored, "pct"), mw, g, "arith", "pct").where(ranked)

    income_only = gw(1, 0, 0, 0, 0, 0, 0)
    svi = pd.read_csv(SVI_CSV, dtype={"FIPS": str}).set_index("FIPS")["RPL_THEMES"]
    frameworks = {
        "original": need_score(m21, flags, mw_fa, gw(25, 25, 10, 10, 10, 5, 15)),
        "hud": need_score(m21, flags, mw_fa, gw(25, 40, 5, 5, 0, 10, 15)),
        "hud_chas": need_score(mC, flagsC, mw_chas, gw(25, 40, 5, 5, 0, 10, 15)),
        "hud_chas_prevalence": need_score(mP, flagsP, mw_chas, gw(25, 40, 5, 5, 0, 10, 15)),
        "equal": need_score(m21, flags, np.full(len(KEYS), 50.0), gw(1, 1, 1, 1, 1, 1, 1)),
        "income_only": need_score(m21, flags, mw_fa, income_only),
        "cdc_svi": svi.where(svi >= 0).reindex(m.index).where(ranked),
    }

    # ---- later outcomes ------------------------------------------------------------------
    renters = m["renter_households"]                                  # ACS 2020-2024, the outcome period
    ev25 = pd.Series({g: v[0] for g, v in json.loads((RAW / "eviction" / "atlanta_tract_filings_2023_2025.json")
                                                     .read_text())["data"].items()}, dtype=float).reindex(m.index)
    ev26 = pd.Series(json.loads((RAW / "eviction" / "atlanta_tract_filings_2026_jan_may.json").read_text())["data"],
                     dtype=float).reindex(m.index)
    places = fetch_places()
    hous = pd.to_numeric(places["housinsecu_crudeprev"], errors="coerce").reindex(m.index)
    util = pd.to_numeric(places["shututility_crudeprev"], errors="coerce").reindex(m.index)

    def li_renters(year: int, cutoff: float) -> pd.Series:
        t = fetch_acs("B25106", year, prefixes, offline=True)
        t.index = t.index.str.replace("1400000US", "")
        base = t["B25106_E025"] + t["B25106_E029"]                    # renters under $20k + $20-35k
        extra = t["B25106_E033"] * max(0.0, (cutoff - 35000) / 15000)  # share of the $35-50k bracket
        return (base + extra).where(t["B25106_E025"] >= 0).reindex(m.index).astype(float)

    cpi = 313.689 / 270.970                                           # CPI-U annual average, 2024 / 2021
    li21, li24 = li_renters(2021, 35000), li_renters(2024, 35000 * cpi)
    li_loss = ((li21 - li24) / li21).where(li21 >= 50)

    ev_rate = (ev25 / 3 / renters).where(renters >= 50)
    parts = pd.DataFrame({"ev": ev_rate, "hous": hous, "util": util, "loss": li_loss}).where(ranked, axis=0).dropna()
    combined = parts.rank(pct=True).mean(axis=1).reindex(m.index)
    # housing insecurity and utility shutoff correlate 0.99 (both modeled by CDC from the same survey and
    # ACS inputs), so the 4-outcome average counts one signal twice; this version counts it once
    pr = parts.rank(pct=True)
    combined_dedup = ((pr["ev"] + (pr["hous"] + pr["util"]) / 2 + pr["loss"]) / 3).reindex(m.index)

    one = pd.Series(1.0, index=m.index)
    outcomes = {
        "evictions_2023_2025": (ev25 / 3, renters),                   # filings per year / renters
        "evictions_2026": (ev26 * 12 / 5, renters),                   # Jan-May, annualized
        "rent_growth": (m["rent_growth"], one),
        "housing_insecurity": (hous, one),
        "utility_shutoff": (util, one),
        "low_income_renter_loss": (li_loss, one),
        "combined_4": (combined, one),
        "combined_dedup": (combined_dedup, one),
    }

    # per-tract scores and outcomes, for the significance tests (pipelines/need/significance_frameworks.py)
    pd.DataFrame({**{f"score_{k}": v for k, v in frameworks.items()},
                  **{f"out_{k}": num / den for k, (num, den) in outcomes.items()},
                  "ev25_per_year": ev25 / 3, "ev26_per_year": ev26 * 12 / 5, "renters": renters,
                  "ranked": ranked, "county": m["county"], "lat": m["lat"], "lon": m["lon"]}
                 ).to_csv(VAL_DIR / "backtest_tract_scores.csv")

    rows, res = [], {"frameworks": {}, "outcomes": {}}
    for oname, (num, den) in outcomes.items():
        base_ok = ranked & num.notna() & den.notna()
        if oname.startswith("evictions"):
            base_ok &= renters >= 50
        ok = base_ok & pd.concat(frameworks, axis=1).notna().all(axis=1)   # same tracts for every framework
        rate = num / den
        idx = np.flatnonzero(ok.to_numpy())
        R = {f: s[ok].rank().to_numpy() for f, s in frameworks.items()}
        y = rate[ok].rank().to_numpy()
        boots = {f: [] for f in frameworks}
        for _ in range(BOOT):
            b = rng.integers(0, len(idx), len(idx))
            for f in frameworks:
                boots[f].append(np.corrcoef(pd.Series(R[f][b]).rank(), pd.Series(y[b]).rank())[0, 1])
        res["outcomes"][oname] = {"tracts": int(ok.sum())}
        for f, s in frameworks.items():
            rho = float(s[ok].corr(rate[ok], method="spearman"))
            bt = np.array(boots[f])
            entry = {"outcome": oname, "framework": f, "tracts": int(ok.sum()), "rho": round(rho, 3),
                     "rho_lo": round(float(np.percentile(bt, 2.5)), 3), "rho_hi": round(float(np.percentile(bt, 97.5)), 3)}
            if oname.startswith("evictions"):
                entry["lift"] = round(lift(s[ok], num[ok], den[ok]), 2)
                by = [s[ok & (m["county"] == c)].corr(rate[ok & (m["county"] == c)], method="spearman")
                      for c in sorted(m.loc[ok, "county"].unique())]
                entry["county_rho_mean"] = round(float(np.mean(by)), 3)
                entry["county_rho_min"] = round(float(np.min(by)), 3)
            else:
                top = s[ok] >= s[ok].quantile(0.9)
                entry["top10_minus_all"] = round(float(rate[ok][top].mean() - rate[ok].mean()), 3)
                entry["top10_mean"] = round(float(rate[ok][top].mean()), 3)
                entry["all_mean"] = round(float(rate[ok].mean()), 3)
            if f != "original":
                d = np.array(boots[f]) - np.array(boots["original"])
                entry["diff_vs_original"] = round(rho - float(frameworks["original"][ok].corr(rate[ok], method="spearman")), 3)
                entry["diff_ci"] = [round(float(np.percentile(d, 2.5)), 3), round(float(np.percentile(d, 97.5)), 3)]
            rows.append(entry)
    out = pd.DataFrame(rows)
    out.to_csv(VAL_DIR / "backtest_frameworks.csv", index=False)
    res["rows"] = rows
    res["chas"] = {"tracts_with_value": int(chas["chas_severe_share"].notna().sum()),
                   "usable_25plus_renters": int(((chas["chas_le80_renters"] >= 25) & ranked).sum())}
    res["agreement_with_original"] = {f: round(float(s.corr(frameworks["original"], method="spearman")), 3)
                                      for f, s in frameworks.items() if f != "original"}
    (VAL_DIR / "backtest_frameworks.json").write_text(json.dumps(res, indent=2))
    pd.set_option("display.width", 220)
    print(out.drop(columns=["diff_ci"]).to_string(index=False))
    print("CHAS:", res["chas"], "| rank agreement with original:", res["agreement_with_original"])


if __name__ == "__main__":
    main()
