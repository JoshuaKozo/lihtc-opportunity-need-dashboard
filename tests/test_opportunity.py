"""Tests for the Opportunity score: school matching, distances, and its processed tables."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.opportunity.build_opportunity import (DOMAIN_WEIGHTS, SCORED, haversine_mi,  # noqa: E402
                                                     match_schools, school_key)

PROCESSED = ROOT / "data" / "processed"


def test_school_name_matching():
    assert school_key("Terrell Co. Elem. Sch.") != school_key("Terrell County High School")
    gosa = pd.DataFrame({"SCHOOL_DISTRCT_CD": ["601", "601", "602"],
                         "INSTN_NAME": ["Appling County Middle School", "Altamaha Elementary School",
                                        "Pearson Elementary School"]})
    nces = pd.DataFrame({"ST_LEAID": ["GA-601", "GA-601", "GA-603"],
                         "SCH_NAME": ["Appling County Middle", "Altamaha Elem", "Pearson Elementary School"],
                         "NCESSCH": ["a", "b", "c"]})
    # Pearson is in a different district, so it must not match.
    assert match_schools(gosa, nces).fillna("none").tolist() == ["a", "b", "none"]


def test_haversine():
    # New York City Hall to Los Angeles City Hall: about 2,445 miles great-circle.
    assert 2435 < haversine_mi(40.7128, -74.0060, 34.0522, -118.2437) < 2455


def test_opportunity_tables():
    path = PROCESSED / "opportunity" / "opportunity_tract.parquet"
    if not path.exists():
        print("  (skipped: run make opportunity-data first)")
        return
    t = pd.read_parquet(path)
    assert t["tract"].str.match(r"^13\d{9}$").all() and not t["tract"].duplicated().any()
    arc = t[t["in_arc"] & (t["pop_2020"] > 0)]
    assert arc["county"].nunique() == 11
    assert arc[list(SCORED)].notna().mean().min() > 0.99
    assert arc["opportunity_score"].between(0, 100).all()
    assert abs(sum(DOMAIN_WEIGHTS.values()) - 1) < 1e-9
    assert {d for d, _, _ in SCORED.values()} == set(DOMAIN_WEIGHTS)
    # Quintile tiers hold about a fifth of tracts each.
    assert arc["opportunity_tier"].value_counts(normalize=True).between(0.18, 0.22).all()
    assert t.loc[~t["in_arc"], "opportunity_score"].isna().all()
    # Optional metrics get percentiles but no weight.
    assert arc["pctl_park_dist_mi"].notna().mean() > 0.99
    z = pd.read_parquet(PROCESSED / "opportunity" / "opportunity_zcta.parquet")
    assert z["zcta"].str.match(r"^\d{5}$").all() and z["opportunity_index"].notna().all()
    # Only ZCTAs mostly inside the ARC counties are ranked.
    assert z["opportunity_score"].notna().eq(z["arc_pop_share"] >= 0.5).all()



def test_applications_tracts():
    path = PROCESSED / "applications_9pct_tract.parquet"
    if not path.exists():
        print("  (skipped: run assign_tracts first)")
        return
    a = pd.read_parquet(path)
    opp = pd.read_parquet(PROCESSED / "opportunity" / "opportunity_tract.parquet")
    has = a["tract_2020"].notna()
    assert has.mean() > 0.9 and a.loc[has, "tract_2020"].isin(opp["tract"]).all()
    assert a.loc[has, "tract_source"].notna().all()
    arc = a[a["in_arc"] & has]
    assert arc["opportunity_score"].notna().all()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} tests passed")
