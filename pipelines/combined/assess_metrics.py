"""Put every Need, Opportunity and Fundability metric on one tract table and test which ones survive
when Need and Opportunity are combined, so no construct is counted twice.

    python -m pipelines.combined.assess_metrics          # or: make combined-assess

The combined tool lets a nonprofit slide between Need and Opportunity; Fundability's chance of an
award is shown beside them as the viability check, not blended in. So the double-counting test is
about Need versus Opportunity. Overlap with SiteLine's QAP location points is reported for context.

Tracts: the 1,248 in the 11 ARC counties, minus those with no residents and the student-heavy tracts
the Need score already flags (25%+ enrolled in college). Every other tract counts.

Metrics (candidates):
- Need: the 22 metrics of pipelines/need/build_tract_need.py. Values its reliability test flags (CV
  over 40%, 30% for income) are left out, as the Need tool does.
- Opportunity: the 17 scored metrics and the optional ones in pipelines/opportunity/build_opportunity.py,
  except FCC broadband (it can't be downloaded by script, so it stays out of the combined tool).
- Fundability (context only): SiteLine's points by QAP section at each tract's best grid point.

Each metric becomes a percentile among the tracts, turned so 100 = more need (Need metrics) or more
opportunity (Opportunity metrics). The tests, in order:

1. Same construct on both sides. A Need metric and an Opportunity metric whose percentiles correlate
   at |rho| >= CROSS_DUP (rank correlation) measure the same thing. Keep it where it belongs: resident
   hardship goes to Need, what a place offers goes to Opportunity.
2. Wrong side. A metric that tracks the other score more closely than its own (each score rebuilt
   without that metric), and at |rho| >= MISPLACED with the other, is drop-tested from its own side.
3. Duplicates within a side (|rho| >= WITHIN_DUP) are reported; the Need build already splits weight
   between near-duplicates, so they are flagged, not dropped.
4. Reliability: a Need metric reliable in under RELIABLE_MIN of tracts is flagged.

Writes data/processed/combined/: tract_metrics.csv (raw values and percentiles of every candidate,
plus Need and Opportunity scores before and after), metric_assessment.csv (one row per metric with
its verdict), metric_correlations.csv, and assessment_summary.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipelines.need.build_tract_need import GROUP_WEIGHTS as NEED_GROUP_WEIGHTS
from pipelines.need.build_tract_need import METRICS as NEED_METRICS
from pipelines.opportunity.build_opportunity import DOMAIN_WEIGHTS, OPTIONAL, SCORED

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "combined"
NEED_DATA_JS = ROOT / "need_site" / "data.js"

CROSS_DUP = 0.70      # rank correlation at which a Need and an Opportunity metric are the same construct
MISPLACED = 0.50      # how strongly a metric must track the other score before it can be "wrong side"
WITHIN_DUP = 0.85     # near-duplicates within one side (the Need build uses the same cut)
RELIABLE_MIN = 0.50   # Need metrics reliable in fewer tracts than this are flagged
EXCLUDED_SOURCES = {"bb_wired_100_20_share": "FCC broadband: not scriptable, kept out of the combined tool"}

# Where a construct belongs when it shows up on both sides. "need": it describes the residents'
# own circumstances. "opportunity": it describes what the place offers anyone who lives there.
# Metrics not listed belong to the side that defines them.
CONSTRUCT = {
    "places_uninsured": "need",          # adults' insurance status is a resident circumstance
    "hud_labor_market": "need",          # residents' employment and education (HUD's own note)
}
CONFOUNDED_WITH_NEED = 0.50   # an Opportunity metric this correlated with the Need score is flagged

SITELINE = [("score_XI_max", "§XI amenities"), ("score_XII_max", "§XII transit"),
            ("score_XIII_max", "§XIII schools"), ("score_XVIII_max", "§XVIII previous projects"),
            ("total_score_max", "SiteLine total")]


def rank_pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100


def need_within_weights() -> dict:
    """The Need tool's default (factor-analysis) weight for each metric inside its group."""
    text = NEED_DATA_JS.read_text()
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    return {m["key"]: float(m["weight"]) for m in data["metrics"]}


def weighted_score(P: pd.DataFrame, metric_w: dict, group_of: dict, group_w: dict) -> pd.Series:
    """Both tools' rule: each group is the weighted mean of its available percentiles, the score is the
    weighted mean of available groups. Re-ranked to a 0-100 percentile so 90 means the top tenth."""
    num = pd.Series(0.0, index=P.index)
    den = pd.Series(0.0, index=P.index)
    for g, gw in group_w.items():
        ks = [k for k in P.columns if group_of.get(k) == g and metric_w.get(k, 0) > 0]
        if not ks:
            continue
        w = pd.Series({k: metric_w[k] for k in ks})
        avail = P[ks].notna()
        gnum = (P[ks].fillna(0) * w).sum(axis=1)
        gden = (avail * w).sum(axis=1)
        has = gden > 0
        num += np.where(has, gw * gnum / gden.where(has, 1), 0)
        den += np.where(has, gw, 0)
    return rank_pct(num / den.where(den > 0))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    need = pd.read_csv(PROCESSED / "need" / "tract_need_metrics.csv", dtype={"tract_geoid": str})
    opp = pd.read_parquet(PROCESSED / "opportunity" / "opportunity_tract.parquet")
    fund = pd.read_csv(PROCESSED / "fundability" / "fundability_tract_features.csv", dtype={"geoid": str})
    t = need.merge(opp, left_on="tract_geoid", right_on="tract", how="inner", suffixes=("", "_opp"))
    t = t.merge(fund[["geoid", *[c for c, _ in SITELINE]]], left_on="tract_geoid", right_on="geoid", how="left")
    keep = (t["population"] > 0) & ~t["student_tract"].astype(bool)
    dropped = {"no residents": int((t["population"] <= 0).sum()),
               "student tracts": int((t["student_tract"].astype(bool) & (t["population"] > 0)).sum())}
    t = t[keep].set_index("tract_geoid")

    # ── candidates, oriented and ranked ──
    within = need_within_weights()
    rows, P = [], {}
    for k, g, label, direction, *_ in NEED_METRICS:
        v = t[k].where(~t[k + "_unreliable"].astype(bool))
        P[k] = rank_pct(v * direction)
        rows.append({"metric": k, "label": label, "side": "need", "group": g,
                     "weight_in_group": within.get(k, 0), "group_weight": NEED_GROUP_WEIGHTS[g] / 100,
                     "coverage": round(float(v.notna().mean()), 3), "optional": False})
    opt = {k: (d, s) for k, (d, s) in OPTIONAL.items() if k not in EXCLUDED_SOURCES}
    for k, spec in {**SCORED, **{k: (d, s, 0.0) for k, (d, s) in opt.items()}}.items():
        domain, direction, w = spec
        P[k] = rank_pct(t[k] * direction)
        rows.append({"metric": k, "label": k, "side": "opportunity", "group": domain,
                     "weight_in_group": w, "group_weight": DOMAIN_WEIGHTS[domain],
                     "coverage": round(float(t[k].notna().mean()), 3), "optional": k in opt})
    P = pd.DataFrame(P)
    meta = pd.DataFrame(rows).set_index("metric")
    side_of, group_of = meta["side"].to_dict(), meta["group"].to_dict()
    need_keys = [k for k in P if side_of[k] == "need"]
    opp_keys = [k for k in P if side_of[k] == "opportunity"]
    need_w = {k: meta.at[k, "weight_in_group"] for k in need_keys}
    opp_w = {k: meta.at[k, "weight_in_group"] for k in opp_keys}
    need_gw = {g: w / 100 for g, w in NEED_GROUP_WEIGHTS.items()}

    def score(side: str, drop: set = frozenset()) -> pd.Series:
        if side == "need":
            return weighted_score(P[[k for k in need_keys if k not in drop]], need_w, group_of, need_gw)
        return weighted_score(P[[k for k in opp_keys if k not in drop]], opp_w, group_of, DOMAIN_WEIGHTS)

    need0, opp0 = score("need"), score("opportunity")
    corr = P.corr(method="spearman")

    # ── tests ──
    for k in P.columns:
        own, other = side_of[k], "opportunity" if side_of[k] == "need" else "need"
        own_loo = score(own, {k})
        other_s = need0 if other == "need" else opp0
        meta.at[k, "rho_own_score"] = round(float(P[k].corr(own_loo, method="spearman")), 2)
        meta.at[k, "rho_other_score"] = round(float(P[k].corr(other_s, method="spearman")), 2)
        cross = corr.loc[k, [c for c in P.columns if side_of[c] == other]].abs()
        meta.at[k, "closest_other_side"] = cross.idxmax()
        meta.at[k, "rho_closest_other"] = round(float(corr.loc[k, cross.idxmax()]), 2)
        same = corr.loc[k, [c for c in P.columns if side_of[c] == own and c != k]].abs()
        meta.at[k, "closest_same_side"] = same.idxmax()
        meta.at[k, "rho_closest_same"] = round(float(corr.loc[k, same.idxmax()]), 2)
        sl = {lab: t[c].rank(pct=True).corr(P[k] / 100, method="spearman") for c, lab in SITELINE}
        best = max(sl, key=lambda x: abs(sl[x]))
        meta.at[k, "siteline_overlap"] = f"{best} ({sl[best]:+.2f})"

    verdict, reason = {}, {}
    for k in P.columns:
        own = side_of[k]
        r_other, r_own = abs(meta.at[k, "rho_other_score"]), abs(meta.at[k, "rho_own_score"])
        partner, r_pair = meta.at[k, "closest_other_side"], abs(meta.at[k, "rho_closest_other"])
        home = CONSTRUCT.get(k, own)
        if r_pair >= CROSS_DUP and home != own:
            verdict[k] = "drop"
            reason[k] = (f"same construct as {partner} on the {home} side (|rho| {r_pair:.2f}); "
                         f"it describes {'residents' if home == 'need' else 'the place'}, so it counts there only")
        elif r_other >= MISPLACED and r_other > r_own and home != own:
            verdict[k] = "drop"
            reason[k] = (f"tracks the {'Opportunity' if own == 'need' else 'Need'} score ({r_other:.2f}) "
                         f"more than its own ({r_own:.2f})")
        elif own == "opportunity" and r_other >= CONFOUNDED_WITH_NEED:
            verdict[k] = "keep, overlaps need"
            reason[k] = (f"a place measure, but {r_other:.2f} with the Need score: it partly reflects residents' "
                         f"income (closest Need metric {partner}, {meta.at[k, 'rho_closest_other']:+.2f})")
        elif own == "need" and r_other >= MISPLACED and r_other > r_own:
            verdict[k] = "keep"
            reason[k] = (f"a resident measure that tracks Opportunity ({r_other:.2f}) about as much as Need "
                         f"({r_own:.2f}), because Opportunity's place measures carry income signal")
        else:
            verdict[k] = "keep"
            reason[k] = ""
        if own == "need" and meta.at[k, "coverage"] < RELIABLE_MIN:
            reason[k] = "; ".join(x for x in [reason[k], f"reliable in only {meta.at[k, 'coverage']:.0%} of tracts"] if x)
            if verdict[k] == "keep":
                verdict[k] = "keep, low reliability"
        if abs(meta.at[k, "rho_closest_same"]) >= WITHIN_DUP:
            shared = "the Need build already splits their weight" if own == "need" else "both carry full weight"
            reason[k] = "; ".join(x for x in [reason[k], f"near-duplicate of {meta.at[k, 'closest_same_side']} "
                                              f"({meta.at[k, 'rho_closest_same']:.2f}); {shared}"] if x)
    meta["verdict"], meta["reason"] = pd.Series(verdict), pd.Series(reason)

    # ── scores with the survivors ──
    dropped_metrics = {k for k, v in verdict.items() if v == "drop"}
    need1, opp1 = score("need", dropped_metrics), score("opportunity", dropped_metrics)
    out = pd.DataFrame({"need_score_before": need0, "opportunity_score_before": opp0,
                        "need_score": need1, "opportunity_score": opp1}).round(1)
    tract_out = pd.concat([t[list(P.columns)].add_prefix("raw_"), P.round(1).add_prefix("pctl_"), out], axis=1)
    tract_out.index.name = "tract_geoid"
    tract_out.to_csv(OUT / "tract_metrics.csv")
    meta.to_csv(OUT / "metric_assessment.csv")
    corr.round(3).to_csv(OUT / "metric_correlations.csv")

    def top(s, q=0.9):
        return set(s[s >= s.quantile(q)].index)
    summary = {
        "tracts": int(len(t)), "excluded": dropped, "candidates": {"need": len(need_keys), "opportunity": len(opp_keys)},
        "excluded_metrics": EXCLUDED_SOURCES, "thresholds": {"cross_duplicate": CROSS_DUP, "wrong_side": MISPLACED,
                                                             "within_duplicate": WITHIN_DUP, "reliable_min": RELIABLE_MIN},
        "dropped": sorted(dropped_metrics), "review": sorted(k for k, v in verdict.items() if v == "review"),
        "need_vs_opportunity_rho": {"before": round(float(need0.corr(opp0, method="spearman")), 3),
                                    "after": round(float(need1.corr(opp1, method="spearman")), 3)},
        "rank_stability": {"need_rho": round(float(need0.corr(need1, method="spearman")), 3),
                           "opportunity_rho": round(float(opp0.corr(opp1, method="spearman")), 3),
                           "need_top10_kept": round(len(top(need0) & top(need1)) / len(top(need0)), 3),
                           "opportunity_top10_kept": round(len(top(opp0) & top(opp1)) / len(top(opp0)), 3)},
        "cross_pairs_by_threshold": {f"{c:.1f}": sorted(
            f"{a} / {b} ({corr.loc[a, b]:+.2f})" for a in need_keys for b in opp_keys if abs(corr.loc[a, b]) >= c)
            for c in (0.6, 0.7, 0.8)},
        "both_high": {"top_third_on_both": int(((need1 >= 200 / 3) & (opp1 >= 200 / 3)).sum())},
    }
    (OUT / "assessment_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    cols = ["side", "group", "coverage", "rho_own_score", "rho_other_score", "closest_other_side",
            "rho_closest_other", "verdict"]
    with pd.option_context("display.width", 200, "display.max_rows", 60):
        print(meta[cols].sort_values(["side", "group"]))


if __name__ == "__main__":
    main()
