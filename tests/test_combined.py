"""Tests for the combined metric assessment (data/processed/combined/)."""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "processed" / "combined"


def test_assessment():
    m = pd.read_csv(OUT / "metric_assessment.csv", index_col=0)
    assert set(m["side"]) == {"need", "opportunity"}
    assert "bb_wired_100_20_share" not in m.index           # FCC broadband stays out of the combined tool
    assert m.loc["places_uninsured", "verdict"] == "drop"   # same construct as income and poverty
    assert m["verdict"].str.match(r"^(keep|drop|review)").all()


def test_tracts():
    t = pd.read_csv(OUT / "tract_metrics.csv", dtype={"tract_geoid": str})
    need = pd.read_csv(ROOT / "data" / "processed" / "need" / "tract_need_metrics.csv", dtype={"tract_geoid": str})
    students = set(need.loc[need["student_tract"].astype(bool), "tract_geoid"])
    assert t["tract_geoid"].is_unique and not (set(t["tract_geoid"]) & students)
    for c in ("need_score", "opportunity_score"):
        assert t[c].between(0, 100).all() and t[c].notna().mean() > 0.99
    s = json.loads((OUT / "assessment_summary.json").read_text())
    assert s["tracts"] == len(t)


def test_metric_set():
    from pipelines.combined.metric_set import SCORES, metrics
    need = {m[1] for m in metrics("need")}
    opp = {m[1] for m in metrics("opportunity")}
    assert "chas_li_renter_burden" in need and "low_income_renter_burden" not in need   # swapped to CHAS
    assert "unassisted_li_renter_share" in need and "assisted_units_per_100" not in need   # the Need team's new supply gap
    dropped = {"overcrowded_share", "lacking_facilities", "rental_vacancy_rate", "single_parent_poverty",
               "senior_poverty", "disability_poverty", "places_uninsured", "hud_labor_market",
               "hud_school_proficiency", "hud_jobs_proximity", "bb_wired_100_20_share"}
    assert not (dropped & (need | opp)) and not (need & opp)
    assert all(sum(c[2] for c in cats) == 100 for cats in SCORES.values())


def test_combined_table():
    t = pd.read_csv(OUT / "combined_tract.csv", dtype={"tract_geoid": str})
    meta = json.loads((OUT / "combined_meta.json").read_text())
    assert len(t) == meta["tracts"] and t["tract_geoid"].is_unique
    for s in ("need_score", "opportunity_score"):
        assert t[s].notna().all() and t[s].between(0, 100).all()
    chas = pd.read_csv(OUT / "chas_renter_burden.csv", dtype={"tract_geoid": str})
    v = chas["chas_li_renter_burden"].dropna()
    assert v.between(0, 1).all() and 0.5 < chas.loc[chas["tract_geoid"].isin(t["tract_geoid"]), "chas_li_renter_burden_unreliable"].eq(False).mean() < 0.6


def test_site_data():
    site = ROOT / "combined_site" / "data"
    g = json.loads((site / "tracts.geojson").read_text())
    meta = json.loads((site / "meta.json").read_text())
    feats = g["features"]
    assert len(feats) == 1248
    scored = [f["properties"] for f in feats if "ns" in f["properties"]]
    assert len(scored) == meta["tracts"]
    n_need = sum(len(c["metrics"]) for c in meta["scores"]["need"])
    n_opp = sum(len(c["metrics"]) for c in meta["scores"]["opportunity"])
    assert all(len(p["nm"]) == n_need and len(p["om"]) == n_opp for p in scored)
    assert all("ex" in f["properties"] for f in feats if "ns" not in f["properties"])
    for f in ("win_model.json", "funding_lines.json"):
        assert (site / f).exists()
    # the slim SiteLine grid that scores a looked-up address, matching the Fundability Map's grid cell for cell
    grid = json.loads((site / "grid.json").read_text())
    full = json.loads((ROOT / "fundability_site" / "data" / "grid.json").read_text())
    c = full["columns"]
    assert grid["columns"] == ["lat", "lon", "base", "XVIII", "block", "XII", "XIII"] and len(grid["rows"]) == len(full["rows"])
    for r, s in zip(grid["rows"][::500], full["rows"][::500]):
        assert abs(r[2] + r[3] + r[4] - s[c.index("total_score")]) < 1e-6
    # location points for each deal-checklist case: 11 §XIV scores x phasing off/on, never below the base case
    with_lx = [p for p in scored if p.get("lx")]
    assert len(with_lx) >= len(scored) - 2
    for p in with_lx:
        lx = p["lx"]
        assert len(lx) == 22 and lx[0] == p["loc"]
        assert all(a <= b for a, b in zip(lx[:10], lx[1:11])) and all(a <= b for a, b in zip(lx[:11], lx[11:]))


def test_site_scores_rebuild_from_metrics():
    """The Metric weights setting rebuilds scores from the site's data, so that data must reproduce them."""
    site = ROOT / "combined_site" / "data"
    scored = [f["properties"] for f in json.loads((site / "tracts.geojson").read_text())["features"] if "ns" in f["properties"]]
    meta = json.loads((site / "meta.json").read_text())
    for score, s_key, m_key in (("need", "ns", "nm"), ("opportunity", "os", "om")):
        num, den, first = pd.Series(0.0, index=range(len(scored))), pd.Series(0.0, index=range(len(scored))), 0
        for cat in meta["scores"][score]:
            w = pd.Series([m["weight"] for m in cat["metrics"]], dtype=float)
            pct = pd.DataFrame([[row[0] for row in p[m_key][first:first + len(w)]] for p in scored], dtype=float)
            first += len(w)
            raw = pct.mul(w.values).sum(axis=1, min_count=1) / pct.notna().mul(w.values).sum(axis=1).where(lambda x: x > 0)
            num += raw.fillna(0) * cat["weight"]
            den += raw.notna() * cat["weight"]
        rebuilt = (num / den.where(den > 0)).rank(pct=True) * 100
        assert (rebuilt - pd.Series([p[s_key] for p in scored])).abs().max() < 0.11


if __name__ == "__main__":
    tests =[v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} tests passed")
