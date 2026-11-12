"""Win-probability model: chance a 9% application is funded, given its estimated score.

    python pipelines/fundability/win_probability.py

Learned from every statewide 9% application with a self-score and an outcome (2013-2025). The input
is the applicant's own claimed score, because that is what the website's estimate is: location
points plus the deal points a team expects to claim. Scores are compared with the competition, not
taken raw, because the scale changes with each QAP (median applicant scores ran ~55 in 2013-2020,
~71 in 2022-2023, ~82 in 2024-2025):

    margin = claimed score - median claimed score of the applications in the same round and pool
    P(funded) = 1 / (1 + exp(-(a + b * margin + c * nonprofit_set_aside)))

Logistic regression, validated by holding out one round at a time (AUC, Brier score, calibration).
For a new site, the next round's median is unknown, so the website uses the latest round's median
for the pool plus the typical year-to-year drift, and widens the curve for that uncertainty.
Writes fundability_site/data/win_model.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.metrics import brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pipelines.fundability.backtest import within_auc  # noqa: E402

PROC = ROOT / "data" / "processed"
SITE = ROOT / "fundability_site" / "data"
SAME_QAP_PAIRS = [(2022, 2023), (2024, 2025)]   # consecutive rounds scored under one QAP (same score scale)


def load() -> pd.DataFrame:
    d = pd.read_parquet(PROC / "applications_9pct.parquet")
    d = d[d["outcome"].isin(["selected", "not_selected"]) & d["self_score"].gt(0)].copy()
    # new-affordability competition only: preservation applications are scored on their own scale
    pres = d["activity_type"].astype(str).str.contains("Preservation") | \
        ~d["preservation_set_aside"].astype(str).isin(["None", "No", "0", "nan"])
    d = d[~pres]
    named = d["pool"].isin(["Atlanta Metro", "Other Metro", "Rural"])
    metro = d["pool"].eq("Flexible") | d["urban_rural"].eq("Urban")
    d["pool_group"] = np.where(named, d["pool"], np.where(metro, "Metro", np.where(d["urban_rural"].eq("Rural"), "Rural", None)))
    d = d[d["pool_group"].notna()].copy()
    d["competition"] = d["round_year"].astype(str) + " · " + d["pool_group"]
    d["y"] = (d["outcome"] == "selected").astype(int)
    d["field_median"] = d.groupby("competition")["self_score"].transform("median")
    d["margin"] = d["self_score"] - d["field_median"]
    # older lists don't flag preservation / USDA set-aside applications; their claimed scores sit far below
    # the field (20-40 vs ~55) on a different scale, so drop anything more than 15 points under the median
    d = d[d["margin"] >= -15].copy()
    d["nonprofit"] = d["nonprofit_set_aside"].astype(str).str.lower().isin(["yes", "non-profit"]).astype(int)
    return d


def fit(df: pd.DataFrame, cols: list[str]):
    return sm.Logit(df["y"], sm.add_constant(df[cols], has_constant="add")).fit(disp=0)


def predict(res, df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return np.asarray(res.predict(sm.add_constant(df[cols], has_constant="add")))


def main() -> None:
    d = load().reset_index(drop=True)
    variants = {"margin": ["margin"], "margin + nonprofit": ["margin", "nonprofit"]}
    results, held = {}, {}
    for name, cols in variants.items():
        p = pd.Series(np.nan, index=d.index)
        for yr in sorted(d["round_year"].unique()):
            tr, te = d[d["round_year"] != yr], d[d["round_year"] == yr]
            p.loc[te.index] = predict(fit(tr, cols), te, cols)
        held[name] = p
        d["_p"] = p
        results[name] = {"auc": float(roc_auc_score(d["y"], p)), "within_competition_auc": float(within_auc(d, "_p")[0]),
                         "brier": float(brier_score_loss(d["y"], p))}
    base_brier = float(brier_score_loss(d["y"], np.full(len(d), d["y"].mean())))
    best = "margin + nonprofit" if results["margin + nonprofit"]["brier"] <= results["margin"]["brier"] else "margin"
    cols = variants[best]
    final = fit(d, cols)

    # calibration of the held-out predictions: does "70%" come true about 70% of the time?
    d["_p"] = held[best]
    d["bin"] = pd.cut(d["_p"], [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0], include_lowest=True)
    calib = [{"bin": str(b), "n": int(len(g)), "predicted": float(g["_p"].mean()), "observed": float(g["y"].mean())}
             for b, g in d.groupby("bin", observed=True)]

    # where the next round's field median will sit: latest median per pool + drift seen within one QAP
    med = d.groupby(["round_year", "pool_group"])["self_score"].median()
    drifts = [med[(b, p)] - med[(a, p)] for a, b in SAME_QAP_PAIRS for p in ("Atlanta Metro", "Other Metro", "Rural")
              if (a, p) in med and (b, p) in med]
    latest = int(d["round_year"].max())
    pools = {p: {"field_median": float(med[(latest, p)]),
                 "applications": int(((d["round_year"] == latest) & (d["pool_group"] == p)).sum()),
                 "funded": int(d[(d["round_year"] == latest) & (d["pool_group"] == p)]["y"].sum())}
             for p in ("Atlanta Metro", "Other Metro", "Rural")}

    params = final.params
    out = {
        "run_date": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"),
        "model": "Logistic regression: P(funded) = 1 / (1 + exp(-(a + b*margin + c*nonprofit)))",
        "features": cols,
        "coef": {"intercept": float(params["const"]), **{c: float(params[c]) for c in cols}},
        "se": {"intercept": float(final.bse["const"]), **{c: float(final.bse[c]) for c in cols}},
        "data": {"applications": int(len(d)), "funded": int(d["y"].sum()), "rounds": f"{d['round_year'].min()}-{latest}",
                 "competitions": int(d["competition"].nunique()), "region": "statewide 9% competition"},
        "validation": {"held_out": results, "chosen": best, "base_rate_brier": base_brier, "calibration": calib},
        "next_round": {"based_on": latest, "pools": pools,
                       "drift_mean": float(np.mean(drifts)), "drift_sd": float(np.std(drifts, ddof=1)),
                       "drift_pairs": [f"{a}->{b}" for a, b in SAME_QAP_PAIRS]},
        "notes": "Margin uses applicants' claimed scores; DCA's review lowers some claims, which the model absorbs on average. "
                 "Set-aside, tiebreaker, and award-limit outcomes are part of the data, not modeled separately. "
                 "The 2026 round may use a new QAP, which can shift the scale.",
    }
    (SITE / "win_model.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("coef", "se", "data")}, indent=1))
    print(pd.DataFrame(results).T.round(3))
    print("base-rate Brier", round(base_brier, 3))
    print(pd.DataFrame(calib).round(2).to_string(index=False))
    print("next round:", json.dumps(out["next_round"], indent=1))


if __name__ == "__main__":
    main()
