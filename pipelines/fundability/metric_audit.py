"""Audit every Fundability metric (and candidate replacements) against past DCA 9% awards.

    python pipelines/fundability/metric_audit.py [--siteline ../SitelineLIHTCStartUp-main]

For each metric, on the same 190 ARC applications as pipelines/backtest.py:
  single AUC     does the metric alone put funded above unfunded competitors? (no fitting, so
                 nothing to overfit; direction fixed in advance)
  lift           held-out AUC of SiteLine + this metric minus SiteLine alone (conditional logit,
                 leave one round out), with a paired bootstrap interval
  spread         how much applicants differ on it (a metric everyone shares can't separate them)
  r with SiteLine  overlap with the score we already have
  map share      share of the variation in today's map Fundability that comes from this metric
Writes data/processed/fundability/metric_audit.csv.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import shapely  # noqa: E402
from pipelines.fundability.backtest import boot_ci, build_features, fit, site_scale, within_auc  # noqa: E402

PROC = ROOT / "data" / "processed"
FUND = PROC / "fundability"

# (column, label, group, expected direction, QAP link)
CANDIDATES = [
    ("s_siteline", "SiteLine location score (total)", "current map metric", "higher", "§XI–§XXII"),
    ("s_basis", "Basis boost (QCT or DDA)", "current map metric", "higher", "Core §V"),
    ("s_years", "Years since last local award", "current map metric", "higher", "Core §V tiebreak"),
    ("s_saturation", "LIHTC saturation", "current map metric", "higher", "Threshold §IV"),
    ("s_flood", "Tract flood risk (FEMA)", "current map metric", "higher", "Threshold §VI"),
    ("sec_XI", "§XI Desirable / undesirable activities", "SiteLine section", "higher", "§XI"),
    ("sec_XII", "§XII Transit", "SiteLine section", "higher", "§XII"),
    ("sec_XIII", "§XIII Quality schools", "SiteLine section", "higher", "§XIII"),
    ("sec_XV", "§XV Stable communities", "SiteLine section", "higher", "§XV"),
    ("sec_XVI", "§XVI Community designations", "SiteLine section", "higher", "§XVI"),
    ("sec_XVIII", "§XVIII Previous projects (as of round)", "SiteLine section", "higher", "§XVIII"),
    ("sec_XXII", "§XXII Historic district", "SiteLine section", "higher", "§XXII"),
    ("sec_block", "10-point block (best of §XV/§XVI/§XIX)", "SiteLine section", "higher", "§XV/§XVI/§XIX"),
    ("c_qct", "QCT alone (as recorded by DCA)", "candidate", "higher", "§XIV +1, Core §V"),
    ("c_dda", "DDA alone (as recorded by DCA)", "candidate", "higher", "Core §V"),
    ("c_lg_crowding", "Local-government crowding: past applications per round", "candidate", "lower", "Core §V award cap (2 per local govt)"),
    ("c_lg_winrate", "Local government's past win rate", "candidate", "higher", "Core §V caps + tiebreaks"),
    ("c_tad", "Inside an Atlanta Tax Allocation District (§XIV plan-area proxy)", "candidate", "higher", "§XIV revitalization plans"),
    ("d_cost", "Credits per low-income unit (cheaper)", "deal feature", "higher", "Core §V tiebreak"),
    ("d_prior", "Owner has won before", "deal feature", "higher", "Core §V tiebreak / §VI"),
    ("d_nonprofit", "Nonprofit set-aside", "deal feature", "higher", "Core §V set-aside"),
    ("self_score", "Applicant's self-score (reference)", "reference", "higher", "whole QAP"),
]


def add_candidates(a: pd.DataFrame) -> pd.DataFrame:
    for s in ("XI", "XII", "XIII", "XV", "XVI", "XXII"):
        a[f"sec_{s}"] = a[f"sl_score_{s}"]
    a["sec_XVIII"] = a["sl_score_XVIII_pit"]
    a["sec_block"] = a["sl_block_score"]
    yes = lambda s: s.astype(str).str.strip().str.lower().isin(["yes", "y", "1", "true"])
    a["c_qct"] = yes(a["qct"]).astype(int)
    a["c_dda"] = yes(a["dda"]).astype(int)
    # local-government history from earlier rounds only (ARC applications, 2013 on)
    allapps = pd.read_parquet(PROC / "applications_9pct_scored.parquet")
    h = allapps[allapps["sl_total_pit"].notna() & allapps["outcome"].isin(["selected", "not_selected"])].copy()
    h["local_gov"] = h["sl_municipality"].fillna("Unincorporated " + h["sl_county"].astype(str) + " County")
    first = int(h["round_year"].min())
    crowd, rate = [], []
    for lg, yr in zip(a["local_gov"], a["round_year"]):
        prior = h[(h["local_gov"] == lg) & (h["round_year"] < yr)]
        crowd.append(len(prior) / max(1, yr - first))
        # shrink toward the overall rate so one past application doesn't read as 0% or 100%
        rate.append((prior["outcome"].eq("selected").sum() + 2 * 0.55) / (len(prior) + 2))
    a["c_lg_crowding"], a["c_lg_winrate"] = crowd, rate
    # Atlanta Tax Allocation Districts in force before the round (pull_open_data.py --only atl_tad)
    tad = json.loads((ROOT / "data" / "raw" / "atl_tad" / "atl_tad.geojson").read_text())["features"]
    pts = shapely.points(a["lon"].values, a["lat"].values)
    inside = np.zeros(len(a), bool)
    for f in tad:
        start = 1970 + f["properties"]["zonestart"] / 1000 / 86400 / 365.25
        inside |= shapely.contains(shapely.from_geojson(json.dumps(f["geometry"])), pts) & (start < a["round_year"].values)
    a["c_tad"] = inside.astype(int)
    return a


def loro(a: pd.DataFrame, cols: list[str], alpha: float) -> pd.Series:
    pred = pd.Series(np.nan, index=a.index)
    for yr in sorted(a["round_year"].unique()):
        tr, te = a[a["round_year"] != yr], a[a["round_year"] == yr]
        beta, _, _ = fit(tr, cols, alpha)
        pred.loc[te.index] = te[cols].values @ beta.values
    return pred


def map_shares(site_dir: Path) -> dict[str, float]:
    """Share of the variance of today's map Fundability contributed by each weighted metric."""
    m = json.loads((site_dir / "metrics.json").read_text())
    feat = pd.read_csv(FUND / "fundability_tract_features.csv", dtype={"geoid": str})
    cols = {}
    for mm in m["metrics"]:
        if not (mm["in_fundability"] and mm["weight"] > 0):
            continue
        v = feat[mm["column"]]
        if mm["scale"] == "points":
            s = 100 * v / mm["max_points"]
        elif mm["scale"] == "flag":
            s = 100 * v
        else:
            s = site_scale(v, v, mm["direction"])
        cols[mm["id"]] = mm["weight"] * s.fillna(s.median())
    parts = pd.DataFrame(cols)
    total = parts.sum(axis=1)
    return {k: float(parts[k].cov(total) / total.var()) for k in parts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--siteline", default=str(ROOT.parent / "SitelineLIHTCStartUp-main"))
    ap.add_argument("--alpha", type=float, default=0.02)
    args = ap.parse_args()
    a = add_candidates(build_features(Path(args.siteline)).reset_index(drop=True))
    informative = a.groupby("competition")["y"].transform(lambda s: 0 < s.sum() < len(s)).astype(bool)
    a["p_base"] = loro(a, ["s_siteline"], args.alpha)
    shares = map_shares(ROOT / "fundability_site")
    id_for = {"s_siteline": "sl_total", "s_basis": "basis_boost", "s_years": "years_since_local_award",
              "s_saturation": "lihtc_saturation", "s_flood": "flood_risk"}

    rows = []
    for col, label, group, direction, qap in CANDIDATES:
        x = a[col].astype(float)
        a["_x"] = x if direction == "higher" else -x
        ev = a[informative]
        auc, _ = within_auc(ev, "_x")
        lift = lift_ci = None
        if col not in ("s_siteline", "self_score"):
            a["_p"] = loro(a.assign(_x=a["_x"].fillna(a["_x"].median())), ["s_siteline", "_x"], args.alpha)
            ev = a[informative]
            lift = within_auc(ev, "_p")[0] - within_auc(ev, "p_base")[0]
            lift_ci = boot_ci(ev, "_p", minus="p_base")
        modal = x.value_counts(normalize=True, dropna=True).iloc[0] if x.notna().any() else np.nan
        rows.append({"metric": label, "group": group, "qap_link": qap, "direction": direction,
                     "coverage": float(x.notna().mean()), "modal_share": float(modal),
                     "id": col, "metric_id": id_for.get(col, col.replace("sec_", "") if col.startswith("sec_") else None),
                     "single_auc": float(auc), "single_auc_ci": boot_ci(ev, "_x"),
                     "lift_over_siteline": lift, "lift_ci": lift_ci,
                     "r_with_siteline": float(x.corr(a["s_siteline"])),
                     "map_share": shares.get(id_for.get(col, ""), 0.0)})
    out = pd.DataFrame(rows)
    out.to_csv(FUND / "metric_audit.csv", index=False)
    (ROOT / "fundability_site" / "data" / "metric_audit.json").write_text(json.dumps(
        {"run_date": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"), "applications": int(len(a)),
         "funded": int(a["y"].sum()), "rows": out.replace({np.nan: None}).to_dict(orient="records")}, indent=1))
    show = out.copy()
    show["single_auc_ci"] = show["single_auc_ci"].apply(lambda c: f"{c[0]:.2f}-{c[1]:.2f}")
    show["lift_ci"] = show["lift_ci"].apply(lambda c: f"{c[0]:+.2f} to {c[1]:+.2f}" if c else "")
    pd.set_option("display.width", 250)
    print(show[["metric", "group", "coverage", "modal_share", "single_auc", "single_auc_ci", "lift_over_siteline",
                "lift_ci", "r_with_siteline", "map_share"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
