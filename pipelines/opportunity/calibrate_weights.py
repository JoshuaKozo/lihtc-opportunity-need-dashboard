"""Calibrate the opportunity score's default domain weights: literature first, data second.

    python -m pipelines.opportunity.build_opportunity
    python -m pipelines.opportunity.calibrate_weights

1. Literature. LITERATURE gives each domain a prior weight and an allowed range, with the
   evidence behind it. docs/opportunity_weights.md explains each one.
2. Data. For two outcomes measured by tract, regress the outcome on the domain scores with
   non-negative coefficients (a good school or nearby jobs can never count against a tract):
     - upward mobility of children from low-income families (Opportunity Atlas, kfr p25)
     - life expectancy at birth, 2010-15 (CDC USALEEP)
   A third outcome, employment of adults without a bachelor's degree adjusted for their education
   mix (ACS 2019-23), is used to test every weighting but not to set the weights (see TEST_OUTCOMES).
   The mobility domain is left out of the mobility model (it contains that outcome). The
   normalized coefficients, averaged over the outcomes, are the data's suggested weights.
3. Blend. Final = BLEND x data + (1 - BLEND) x prior, then projected into the literature
   ranges so the weights still sum to 1. The data can move a weight, never out of its range.
4. Check. Every weighting (current, literature prior, data only, final, equal) is scored by how
   well it ranks tracts on each outcome within counties, with the data and final weights refit
   leaving each county out, so no weighting is graded on counties it was fit to.

Outputs data/processed/opportunity/weight_calibration.csv and prints the final weights to paste into
DOMAIN_WEIGHTS in build_opportunity.py.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from pipelines.opportunity.build_opportunity import DOMAIN_WEIGHTS, RAW, to_2020

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "data" / "processed"          # shared tables (DCA applications)
PROCESSED = SHARED / "opportunity"
BLEND = 0.5

# domain -> (low, prior, high)
LITERATURE = {
    "mobility": (0.15, 0.25, 0.35),
    "schools": (0.15, 0.25, 0.35),
    "jobs": (0.10, 0.20, 0.25),
    "transit": (0.05, 0.10, 0.15),
    "health": (0.05, 0.10, 0.15),
    "environment": (0.05, 0.10, 0.15),
}
DOMAINS = list(LITERATURE)


def project(w: dict[str, float]) -> dict[str, float]:
    """Nearest weights that sum to 1 and stay inside the literature ranges (clip, then spread the
    remainder over the domains not at a bound, repeating until nothing moves)."""
    lo = {d: LITERATURE[d][0] for d in DOMAINS}
    hi = {d: LITERATURE[d][2] for d in DOMAINS}
    w = {d: min(max(w[d], lo[d]), hi[d]) for d in DOMAINS}
    for _ in range(50):
        gap = 1 - sum(w.values())
        if abs(gap) < 1e-9:
            break
        free = [d for d in DOMAINS if (gap > 0 and w[d] < hi[d]) or (gap < 0 and w[d] > lo[d])]
        for d in free:
            w[d] = min(max(w[d] + gap / len(free), lo[d]), hi[d])
    return w


def load() -> pd.DataFrame:
    t = pd.read_parquet(PROCESSED / "opportunity_tract.parquet")
    t = t[t["opportunity_score"].notna()].copy()
    le = pd.read_csv(RAW / "usaleep_ga_2010_15.csv", dtype={"Tract ID": str})
    le = le.rename(columns={"Tract ID": "tract_2010", "e(0)": "life_exp"})[["tract_2010", "life_exp"]]
    t = t.merge(to_2020(le, "tract_2010"), on="tract", how="left")
    t = t.merge(adult_employment(set(t["tract"])), on="tract", how="left")
    return t.rename(columns={"oa_kfr_p25": "mobility_outcome"})


# ACS table B23006 (ages 25-64): the line where each education group starts. Within a group, the
# next lines are in labor force (+1), Armed Forces (+2), civilian (+3), employed (+4), unemployed
# (+5) and not in labor force (+6).
EDU_GROUPS = {"less than high school": 2, "high school": 9, "some college or associate": 16, "bachelor's or more": 23}
NON_DEGREE = ["less than high school", "high school", "some college or associate"]


def adult_employment(tracts: set[str]) -> pd.DataFrame:
    """Education-adjusted employment of adults 25-64 without a bachelor's degree.

    Who lives in a tract shapes its employment rate, so a raw rate partly measures who can afford
    to live there. This compares each tract's actual employment rate for non-degree adults with
    the rate expected from its mix of education levels at metro-wide rates for each level
    (indirect standardization). Positive = more non-degree adults working than their education
    alone predicts. Tracts with fewer than 100 non-degree adults are left out (too few survey
    responses).
    """
    d = pd.read_csv(RAW / "acs_b23006_ga.csv", dtype={"tract": str})
    d = d[d["tract"].isin(tracts)]
    e = lambda i: pd.to_numeric(d[f"B23006_E{i:03d}"], errors="coerce").fillna(0)
    tot = {g: e(b) for g, b in EDU_GROUPS.items()}
    emp = {g: e(b + 2) + e(b + 4) for g, b in EDU_GROUPS.items()}   # Armed Forces + civilian employed
    rate = {g: emp[g].sum() / tot[g].sum() for g in EDU_GROUPS}      # metro rate per education level
    n = sum(tot[g] for g in NON_DEGREE)
    actual = sum(emp[g] for g in NON_DEGREE) / n.replace(0, np.nan)
    expected = sum(tot[g] * rate[g] for g in NON_DEGREE) / n.replace(0, np.nan)
    out = pd.DataFrame({"tract": d["tract"], "adult_emp_actual": actual, "adult_emp_expected": expected,
                        "adult_emp_adj": (actual - expected).where(n >= 100)})
    return out


# Outcomes that set the weights.
OUTCOMES = {"mobility_outcome": [d for d in DOMAINS if d != "mobility"], "life_exp": DOMAINS}
# Outcomes every weighting is tested against. Education-adjusted adult employment is a test only:
# the parts explain about 1% of it (it is mostly survey noise at tract level), so letting it vote
# on the weights would add noise. That no weighting predicts it matches Moving to Opportunity,
# where moving adults to better neighborhoods did not raise their employment or earnings.
TEST_OUTCOMES = {**OUTCOMES, "adult_emp_adj": DOMAINS}


def data_weights(df: pd.DataFrame) -> dict[str, float]:
    """Average, over outcomes, of normalized non-negative regression coefficients."""
    shares = []
    for y, doms in OUTCOMES.items():
        d = df.dropna(subset=[y] + [f"score_{x}" for x in doms])
        X = d[[f"score_{x}" for x in doms]].to_numpy() / 100
        coef = LinearRegression(positive=True).fit(X, d[y].to_numpy()).coef_
        s = coef / coef.sum() if coef.sum() > 0 else np.full(len(doms), 1 / len(doms))
        shares.append(dict(zip(doms, s)))
    avg = {x: float(np.mean([s[x] for s in shares if x in s])) for x in DOMAINS}
    total = sum(avg.values())   # mobility is in fewer outcome models than the rest, so renormalize
    return {x: v / total for x, v in avg.items()}


def composite(df: pd.DataFrame, w: dict[str, float], skip=()) -> pd.Series:
    keep = {d: v for d, v in w.items() if d not in skip}
    num = sum(df[f"score_{d}"].fillna(df[f"score_{d}"].mean()) * v for d, v in keep.items())
    return num / sum(keep.values())


def within_county_validity(df: pd.DataFrame, weights_for) -> dict[str, float]:
    """Mean within-county rank correlation between the composite and each outcome. weights_for(train)
    returns the weights to use for a held-out county, fit on the other counties."""
    out = {}
    for y in TEST_OUTCOMES:
        rs, ns = [], []
        for c, test in df.groupby("county"):
            test = test.dropna(subset=[y])
            if len(test) < 20:
                continue
            w = weights_for(df[df["county"] != c])
            comp = composite(test, w, skip=("mobility",) if y == "mobility_outcome" else ())
            rs.append(comp.rank().corr(test[y].rank()))
            ns.append(len(test))
        out[y] = float(np.average(rs, weights=ns))
    return out


def main():
    df = load()
    prior = {d: LITERATURE[d][1] for d in DOMAINS}
    data = data_weights(df)
    final = project({d: BLEND * data[d] + (1 - BLEND) * prior[d] for d in DOMAINS})
    final = {d: round(v, 3) for d, v in final.items()}

    sets = {
        "current default": lambda tr: DOMAIN_WEIGHTS,
        "equal": lambda tr: {d: 1 / 6 for d in DOMAINS},
        "literature prior": lambda tr: prior,
        "data only": lambda tr: data_weights(tr),
        "final (blend in ranges)": lambda tr: project(
            {d: BLEND * data_weights(tr)[d] + (1 - BLEND) * prior[d] for d in DOMAINS}),
    }
    rows = []
    for name, f in sets.items():
        v = within_county_validity(df, f)
        w = f(df)
        rows.append({"weighting": name, **{f"w_{d}": round(w[d], 3) for d in DOMAINS},
                     "validity_mobility": round(v["mobility_outcome"], 3),
                     "validity_life_exp": round(v["life_exp"], 3),
                     "validity_adult_emp": round(v["adult_emp_adj"], 3)})
    res = pd.DataFrame(rows)
    res.to_csv(PROCESSED / "weight_calibration.csv", index=False)

    n_le = df["life_exp"].notna().sum()
    print(f"{len(df)} scored ARC tracts; life expectancy available for {n_le}")
    print("Data-suggested weights:", {d: round(v, 3) for d, v in data.items()})
    print(res.to_string(index=False))
    print("\nFinal weights for DOMAIN_WEIGHTS:", final, "sum", round(sum(final.values()), 3))


if __name__ == "__main__":
    main()
