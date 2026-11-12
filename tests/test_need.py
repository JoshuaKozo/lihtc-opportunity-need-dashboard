"""Tests for the Need score: its metric catalog and processed tract table."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.need.build_tract_need import GROUP_WEIGHTS, GROUPS, IMPORTANCE, METRICS  # noqa: E402

NEED = ROOT / "data" / "processed" / "need" / "tract_need_metrics.csv"


def test_catalog():
    keys = [k for k, *_ in METRICS]
    assert len(keys) == len(set(keys)) == 22
    assert {g for _, g, *_ in METRICS} == {g for g, _ in GROUPS} == set(GROUP_WEIGHTS)
    assert sum(GROUP_WEIGHTS.values()) == 100
    assert set(IMPORTANCE) == set(keys)


def test_tract_table():
    t = pd.read_csv(NEED, dtype={"tract_geoid": str})
    arc = pd.read_csv(ROOT / "data" / "processed" / "arc_tracts_2020.csv", dtype=str)
    assert len(t) == 1248 and t["tract_geoid"].is_unique
    assert t["tract_geoid"].str.match(r"^13\d{9}$").all()
    assert set(t["tract_geoid"]) == set(arc.iloc[:, 0])
    shares = [k for k, _, _, _, fmt, _ in METRICS if fmt == "pct" and k != "rent_growth"]   # growth can be negative
    for k in shares:   # shares are stored as 0-1 fractions
        v = t[k].dropna()
        assert v.between(-1e-9, 1 + 1e-9).all(), k
    assert t["student_tract"].isin([True, False]).all()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} tests passed")
