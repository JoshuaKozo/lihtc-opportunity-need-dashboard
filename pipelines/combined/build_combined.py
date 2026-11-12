"""Build the combined tool's tract table: every metric in pipelines/combined/metric_set.py, its category
scores, and the Need and Opportunity scores, plus the Fundability fields shown beside them.

    python -m pipelines.combined.build_combined          # or: make combined-data

Tracts: the 1,248 in the 11 ARC counties, minus tracts with no residents and the student-heavy tracts
the Need build flags (25%+ enrolled in college). Every other tract counts; a missing or unreliable
value is skipped and its weight spread over the tract's other metrics.

Scoring (both scores, the same way):
1. Each metric becomes a percentile among the tracts, turned so 100 = more need, or more opportunity.
   Need metrics the reliability test flags (CV over 40%, 30% for income; zero estimates) are skipped.
2. A category is the weighted mean of its metrics' percentiles.
3. A score is the weighted mean of its categories, re-ranked to a 0-100 percentile so 90 means the
   top tenth of tracts. Categories are re-ranked the same way for display.

The CHAS measure (renters under 80% of HUD area median family income paying more than 30% of income,
as a share of all renter households) is read from HUD's 2018-2022 CHAS tract files, Table 8, which
`pipelines/fundability/pull_bulk_files.py` downloads. Table 8 columns, verified against the subtotals
and HUD's named fields: est68 = renter households; each renter income band (est69 <=30%, est82 30-50%,
est95 50-80% HAMFI) splits into cost burden <=30%, 30-50%, >50% and not computed, so the burdened
renters are est73+est76, est86+est89, est99+est102. The result is cached in
data/processed/combined/chas_renter_burden.csv so the build runs without the raw files.

Writes data/processed/combined/combined_tract.csv and combined_meta.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipelines.combined.metric_set import SCORES, WEIGHTS_STATUS, metrics

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "combined"
CHAS_RAW = ROOT / "data" / "raw" / "hud_chas_2018_22" / "Table8.csv"
CHAS_CACHE = OUT / "chas_renter_burden.csv"
CHAS_BURDENED = [73, 76, 86, 89, 99, 102]   # renters <=80% HAMFI paying 30-50% or >50% of income
CHAS_RENTERS = 68


def unreliable(p: pd.Series, moe: pd.Series) -> pd.Series:
    """The Need build's rule for proportions: CV over 40% is unreliable unless the MOE is under 5
    points and smaller than the value; zero estimates are flagged too."""
    cv = moe / 1.645 / p
    ok = (cv <= 0.40) | ((moe < 0.05) & (moe < p))
    return ~(ok & (p > 0))


def chas_renter_burden() -> pd.DataFrame:
    if not CHAS_RAW.exists():
        if not CHAS_CACHE.exists():
            raise SystemExit("No CHAS data: run pipelines/fundability/pull_bulk_files.py first.")
        return pd.read_csv(CHAS_CACHE, dtype={"tract_geoid": str})
    t = pd.read_csv(CHAS_RAW, dtype={"geoid": str}, encoding="latin-1")
    t = t[(t["sumlevel"] == 140) & (t["st"] == 13)]
    num = sum(t[f"T8_est{c}"] for c in CHAS_BURDENED)
    num_moe = np.sqrt(sum(t[f"T8_moe{c}"] ** 2 for c in CHAS_BURDENED))
    den, den_moe = t[f"T8_est{CHAS_RENTERS}"], t[f"T8_moe{CHAS_RENTERS}"]
    p = (num / den.where(den > 0)).clip(upper=1)   # CHAS rounds each cell, so tiny tracts can pass 100%
    rad = num_moe ** 2 - p ** 2 * den_moe ** 2          # Census formula for a proportion's MOE
    moe = np.sqrt(np.where(rad >= 0, rad, num_moe ** 2 + p ** 2 * den_moe ** 2)) / den.where(den > 0)
    out = pd.DataFrame({"tract_geoid": t["geoid"].str[-11:], "chas_li_renter_burden": p.round(5),
                        "chas_li_renter_burden_moe": pd.Series(moe, index=t.index).round(5)})
    out["chas_li_renter_burden_unreliable"] = unreliable(out["chas_li_renter_burden"], out["chas_li_renter_burden_moe"])
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(CHAS_CACHE, index=False)
    return out


def rank_pct(s: pd.Series) -> pd.Series:
    return (s.rank(pct=True) * 100).round(1)


def main() -> None:
    need = pd.read_csv(PROCESSED / "need" / "tract_need_metrics.csv", dtype={"tract_geoid": str})
    opp = pd.read_parquet(PROCESSED / "opportunity" / "opportunity_tract.parquet").rename(columns={"tract": "tract_geoid"})
    fund = pd.read_csv(PROCESSED / "fundability" / "fundability_tract_features.csv", dtype={"geoid": str})
    chas = chas_renter_burden()
    t = (need.merge(opp.drop(columns=["county", "lat", "lon"], errors="ignore"), on="tract_geoid", how="inner")
             .merge(chas, on="tract_geoid", how="left")
             .merge(fund.rename(columns={"geoid": "tract_geoid"}), on="tract_geoid", how="left", suffixes=("", "_fund")))
    keep = (t["population"] > 0) & ~t["student_tract"].astype(bool)
    excluded = {"no_residents": int((t["population"] <= 0).sum()),
                "student_tracts": int((t["student_tract"].astype(bool) & (t["population"] > 0)).sum())}
    t = t[keep].set_index("tract_geoid")

    out = pd.DataFrame(index=t.index)
    out["county"], out["tract_name"] = t["county"], t["tract_name"]
    out["pool"], out["local_gov"] = t["pool"], t["local_gov"]
    out["population"], out["lat"], out["lon"] = t["population"], t["lat"], t["lon"]
    meta = {"weights_status": WEIGHTS_STATUS, "tracts": int(len(t)), "excluded": excluded, "scores": {}}
    for score, cats in SCORES.items():
        cat_raw, cat_w = {}, {}
        for c, label, cw, question, ms in cats:
            num = pd.Series(0.0, index=t.index)
            den = pd.Series(0.0, index=t.index)
            for k, mlabel, src, direction, w in ms:
                v = t[k].astype(float)
                if f"{k}_unreliable" in t:
                    flag = t[f"{k}_unreliable"].map(lambda x: True if pd.isna(x) else bool(x))   # no value = unusable
                    v = v.where(~flag)
                pct = rank_pct(v * direction)
                out[f"raw_{k}"], out[f"pct_{k}"] = t[k], pct
                if w > 0:
                    num += pct.fillna(0) * w
                    den += pct.notna() * w
            cat_raw[c] = num / den.where(den > 0)
            cat_w[c] = cw
            out[f"{score}_{c}"] = rank_pct(cat_raw[c])
        num = sum(cat_raw[c].fillna(0) * w for c, w in cat_w.items())
        den = sum(cat_raw[c].notna() * w for c, w in cat_w.items())
        out[f"{score}_score"] = rank_pct(num / den.where(den > 0))
        meta["scores"][score] = [{"key": c, "label": label, "weight": cw, "question": q,
                                  "metrics": [{"key": k, "label": ml, "source": src, "direction": d, "weight": w,
                                               "coverage": round(float(out[f"pct_{k}"].notna().mean()), 3)}
                                              for k, ml, src, d, w in ms]}
                                 for c, label, cw, q, ms in cats]
    # Fundability, shown beside the scores (never blended): SiteLine's best location points and the
    # basis-boost flags. The chance of an award needs a deal, so the tool computes it.
    for c in ("total_score_max", "score_XII_max", "score_XIII_max", "qct_2026", "dda_2026", "basis_boost_2026"):
        out[c] = t[c]
    out.index.name = "tract_geoid"
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "combined_tract.csv")
    meta["need_vs_opportunity_rho"] = round(float(out["need_score"].corr(out["opportunity_score"], method="spearman")), 3)
    (OUT / "combined_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"{len(out)} tracts -> {OUT.relative_to(ROOT)}/combined_tract.csv")
    for score in SCORES:
        print(f"  {score}: " + ", ".join(f"{m['key']} {m['coverage']:.0%}" for cat in meta["scores"][score]
                                        for m in cat["metrics"]))
    print("  need vs opportunity rho:", meta["need_vs_opportunity_rho"])


if __name__ == "__main__":
    main()
