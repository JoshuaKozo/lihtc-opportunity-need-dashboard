"""Sanity checks for the pipelines. Run: python tests/test_pipelines.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.parse_applications import _fix_coords, classify_status  # noqa: E402
from pipelines.score_applications import score_one, xviii_point_in_time  # noqa: E402
from pipelines.scrape_dca import classify  # noqa: E402

PROCESSED = ROOT / "data" / "processed"


def test_classify_link_text():
    assert classify("2019 9% Applicants for Funding")["kind"] == "applicants"
    assert classify("2022 4% Housing Credit/Bonds Non-Selected Projects List")["kind"] == "non_selected"
    c = classify("2024 9% Housing Credit Award List (revised 12-16-24)")
    assert (c["year"], c["credit_type"], c["kind"]) == (2024, "9%", "selected")


def test_status_codes():
    assert classify_status("Select-NP")[:2] == ("selected", "selected")
    assert classify_status("NS Tiebrk")[1] == "tiebreaker"
    assert classify_status("Non-Select-Geo Limitations")[1] == "geographic_limit"
    assert classify_status("NS NoTC$")[1] == "funds_exhausted"
    assert classify_status("Nonselect")[1] == "score_or_threshold"


def test_coordinate_formats():
    df = pd.DataFrame({"coords": ["33.7, -84.4", None, None],
                       "lat": [None, "32.141863N", "-84.27866"], "lon": [None, "81.621278W", "'34.23716"]})
    out = _fix_coords(df)
    assert np.allclose(out[["lat", "lon"]].to_numpy(float),
                       [[33.7, -84.4], [32.141863, -81.621278], [34.23716, -84.27866]])


def test_point_in_time_excludes_own_and_later_awards():
    # The Vortex site: SiteLine's 2026 §XVIII is 0 because of a 2025 award 0.49 mi away.
    # As of a 2016 round, that award did not exist yet, so the 1-mile test can only improve.
    later = xviii_point_in_time(33.766384, -84.349133, 2026)
    earlier = xviii_point_in_time(33.766384, -84.349133, 2016)
    assert later["sl_score_XVIII_pit"] == 0.0
    assert earlier["sl_xviii_awards_within_1mi_pit"] <= later["sl_xviii_awards_within_1mi_pit"]


def test_vortex_through_pipeline():
    row = pd.Series({"lat": 33.766384, "lon": -84.349133, "tenancy": "Family", "round_year": 2026})
    out = score_one(row)
    assert out["sl_total_score"] == 39.0 and out["sl_total_pit"] == 39.0


def test_processed_tables():
    path = PROCESSED / "applications_9pct.parquet"
    if not path.exists():
        print("  (skipped: run the pipeline first)")
        return
    a = pd.read_parquet(path)
    assert a["app_id"].str.match(r"^\d{4}-\d{3}$").all()
    assert not a.duplicated(["round_year", "app_id"]).any()
    assert a["outcome"].notna().mean() > 0.97
    assert not any("mail" in c or "phone" in c for c in a.columns)
    assert not a.astype(str).apply(lambda s: s.str.contains("@", regex=False)).any().any()
    ok = a["lat"].notna()
    assert a.loc[ok, "lat"].between(30, 35.1).all() and a.loc[ok, "lon"].between(-86, -80.7).all()

if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} tests passed")
