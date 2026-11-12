"""Sanity checks for the Fundability Map's data. Run: python tests/test_fundability.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "fundability_site"
FUND = ROOT / "data" / "processed" / "fundability"


def test_site_data():
    """Every file the website loads exists, parses, and agrees with metrics.json."""
    if not (SITE / "data" / "tracts.geojson").exists():
        print("  (skipped: run make fundability-data first)")
        return
    metrics = json.loads((SITE / "metrics.json").read_text())
    tracts = json.loads((SITE / "data" / "tracts.geojson").read_text())["features"]
    assert len(tracts) == 1248
    props = tracts[0]["properties"]
    for m in metrics["metrics"]:
        cols = [m["column"]] + [b["column"] for b in m.get("breakdown", [])] + [d["column"] for d in m.get("details", [])]
        missing = [c for c in cols if c not in props]
        assert not missing, f"{m['id']}: columns missing from tracts.geojson: {missing}"
    grid = json.loads((SITE / "data" / "grid.json").read_text())
    detail = json.loads((SITE / "data" / "grid_detail.json").read_text())
    assert len(grid["rows"]) == len(detail["rows"]) == len(grid["block_winner"])
    win = json.loads((SITE / "data" / "win_model.json").read_text())
    assert win["coef"]["margin"] > 0 and set(win["next_round"]["pools"]) >= {"Atlanta Metro", "Other Metro"}
    sources = json.loads((SITE / "data" / "sources.json").read_text())
    for d in sources["downloads"]:
        assert (SITE / d["file"]).exists(), f"download listed but missing: {d['file']}"
    assert all(s["url"].startswith("http") for s in sources["sources"])


def test_processed_tables():
    """The tool's processed tables are where the pipelines expect them, keyed on text tract codes."""
    path = FUND / "fundability_tract_features.csv"
    if not path.exists():
        print("  (skipped: run make fundability-data first)")
        return
    header = path.read_text().splitlines()[0].split(",")
    assert header[0] == "geoid"
    rows = path.read_text().splitlines()[1:]
    assert len(rows) == 1248 and all(len(r.split(",")[0]) == 11 for r in rows)
    for name in ("siteline_tract_scores.csv", "backtest_features.csv", "metric_audit.csv"):
        assert (FUND / name).exists(), f"missing {name}"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} tests passed")
