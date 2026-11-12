"""Backtest the Fundability metrics against real DCA 9% awards and derive their weights.

    python pipelines/fundability/backtest.py [--siteline ../SitelineLIHTCStartUp-main]

Question: among applications that competed in the same round and pool, do the Fundability metrics
pick the ones DCA funded? The model is a conditional logistic regression (McFadden's conditional
logit): it compares each funded application only with its own competitors, so differences between
rounds (budget, number of applicants, QAP changes) cancel out.

Inputs are the site's own metrics, rebuilt as they stood before each round and put on the same
0-100 scale the website uses. Because the website's Fundability score is a weighted sum of those
0-100 values, setting each weight proportional to its coefficient makes the website rank sites the
way the model does. Deal features (credits per unit, team track record, nonprofit set-aside) are
included as controls so location weights aren't credited with what the deal did; they have no
weight on the map because they are not properties of a place.

Validation holds out one round at a time. Writes:
  fundability_site/data/backtest.json                 results the Weighting tab reads
  data/processed/fundability/backtest_features.csv    one row per application with every model input
  data/processed/fundability/backtest_runs.csv        one line per run (appended), so weight changes are traceable
"""
from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from statsmodels.discrete.conditional_models import ConditionalLogit

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"                 # shared: DCA applications
FUND = PROC / "fundability"                          # this tool's tables
RAW = ROOT / "data" / "raw"
SITE = ROOT / "fundability_site" / "data"
RNG = np.random.default_rng(20261015)

LOCATION = [  # (model column, metrics.json id, label)
    ("s_siteline", "sl_total", "SiteLine location score (as of the round)"),
    ("s_basis", "basis_boost", "Federal basis boost (QCT or DDA)"),
    ("s_years", "years_since_local_award", "Years since last award in the local government"),
    ("s_saturation", "lihtc_saturation", "LIHTC saturation (units per 100 renters)"),
    ("s_flood", "flood_risk", "Inland flood risk (FEMA, tract)"),
]
DEAL = [
    ("d_cost", None, "Credits requested per low-income unit (lower is better)"),
    ("d_prior", None, "Owner has won a 9% award before"),
    ("d_nonprofit", None, "Nonprofit set-aside"),
]


# ── site scaling: the same 0-100 transform app.js applies (best value 100, worst 0) ───────────
def site_scale(values: pd.Series, reference: pd.Series, direction: str) -> pd.Series:
    ref = np.sort(reference.dropna().values)
    n = len(ref)
    lower = np.searchsorted(ref, values, side="left")
    upper = np.searchsorted(ref, values, side="right")
    if direction == "lower":
        s = 100 * (n - upper) / max(1, n - np.searchsorted(ref, ref[0], side="right"))
    else:
        s = 100 * lower / max(1, np.searchsorted(ref, ref[-1], side="left"))
    return pd.Series(np.where(values.isna(), np.nan, s), index=values.index)


def points_in(polys, ids, lon, lat):
    tree = shapely.STRtree(polys)
    pi, gi = tree.query(shapely.points(lon, lat), predicate="within")
    out = np.full(len(lon), None, dtype=object)
    for p, g in zip(pi[::-1], gi[::-1]):
        out[p] = ids[g]
    return out


# ── features ────────────────────────────────────────────────────────────────────────────────
def build_features(sl_dir: Path) -> pd.DataFrame:
    allapps = pd.read_parquet(PROC / "applications_9pct_scored.parquet")
    a = allapps[allapps["sl_total_pit"].notna() & allapps["outcome"].isin(["selected", "not_selected"])].copy()
    a["y"] = (a["outcome"] == "selected").astype(int)
    a["competition"] = a["round_year"].astype(str) + " · " + a["sl_pool"].str.title()
    a["geoid"] = a["sl_tract_geoid"].astype(str)

    # basis boost as DCA recorded it on the application; SiteLine's QCT flag where blank
    yes = lambda s: s.astype(str).str.strip().str.lower().isin(["yes", "y", "1", "true"])
    known = a["qct"].notna() | a["dda"].notna()
    a["basis"] = np.where(known, (yes(a["qct"]) | yes(a["dda"])).astype(int), a["sl_in_qct"].fillna(False).astype(int))

    # awards before each round, by local government (city, or county if unincorporated)
    aw = pd.read_csv(sl_dir / "data" / "lihtc_awards.csv").dropna(subset=["lat", "lon"])
    mun = pd.read_csv(sl_dir / "data" / "municipalities.csv")
    aw["local_gov"] = points_in(shapely.from_wkt(mun["geometry_wkt"]).values, mun["name"].values,
                                aw["lon"].values, aw["lat"].values)
    aw["local_gov"] = aw["local_gov"].fillna("Unincorporated " + aw["county"].astype(str) + " County")
    a["local_gov"] = a["sl_municipality"].fillna("Unincorporated " + a["sl_county"].astype(str) + " County")
    last = []
    for lg, yr in zip(a["local_gov"], a["round_year"]):
        prior = aw.loc[(aw["local_gov"] == lg) & (aw["year"] < yr), "year"]
        last.append(yr - prior.max() if len(prior) else np.nan)
    a["years_since_local_award"] = last

    # LIHTC low-income units in the tract before the round, per 100 renter households
    tr = pd.read_csv(sl_dir / "data" / "tracts.csv", dtype={"geoid": str})
    tr = tr[tr["in_arc"]]
    tgeom, tids = shapely.from_wkt(tr["geometry_wkt"]).values, tr["geoid"].values
    hud = pd.read_csv(RAW / "hud_lihtc" / "hud_lihtc.csv").dropna(subset=["LAT", "LON"])
    hud["geoid"] = points_in(tgeom, tids, hud["LON"].values, hud["LAT"].values)
    hud["year"] = hud["YR_ALLOC"].where(hud["YR_ALLOC"] < 3000, hud["YR_PIS"].where(hud["YR_PIS"] < 3000, 1990))
    dca = aw[(aw["source"] == "DCA") & (aw["coord_precision"] != "random_in_city")].copy()
    dca["geoid"] = points_in(tgeom, tids, dca["lon"].values, dca["lat"].values)
    dca["units"] = dca["units"].fillna(dca["units"].median())
    units = pd.concat([hud[["geoid", "year"]].assign(u=hud["LI_UNITS"].fillna(0)),
                       dca[["geoid", "year"]].assign(u=dca["units"])])
    feat = pd.read_csv(FUND / "fundability_tract_features.csv", dtype={"geoid": str}).set_index("geoid")
    a["li_units_before"] = [units.loc[(units["geoid"] == g) & (units["year"] < yr), "u"].sum()
                            for g, yr in zip(a["geoid"], a["round_year"])]
    renters = a["geoid"].map(feat["acs_renter_hh"]).replace(0, np.nan)
    a["lihtc_units_per_100_renters"] = 100 * a["li_units_before"] / renters
    a["nri_inland_flood_score"] = a["geoid"].map(feat["nri_inland_flood_score"])

    # site 0-100 scale, against today's ARC tract distribution (exactly as the website scales)
    a["s_siteline"] = 100 * a["sl_total_pit"] / 46
    a["s_basis"] = 100 * a["basis"]
    a["s_years"] = site_scale(a["years_since_local_award"], feat["years_since_local_award"], "higher").fillna(100)
    a["s_saturation"] = site_scale(a["lihtc_units_per_100_renters"], feat["lihtc_units_per_100_renters"], "lower")
    a["s_flood"] = site_scale(a["nri_inland_flood_score"], feat["nri_inland_flood_score"], "lower")
    for c in ("s_saturation", "s_flood"):
        a[c] = a[c].fillna(a[c].median())

    # deal controls, known before applying
    a["credits_per_li_unit"] = a["credit_request"] / a["units_li"]
    a["d_cost"] = -np.log(a["credits_per_li_unit"])                    # higher = cheaper = better
    owner = allapps.dropna(subset=["owner_principal"])
    won = owner[owner["outcome"] == "selected"].groupby("owner_principal")["round_year"].min()
    a["d_prior"] = [int(o in won.index and won[o] < yr) for o, yr in zip(a["owner_principal"], a["round_year"])]
    a["d_nonprofit"] = a["nonprofit_set_aside"].astype(str).str.lower().isin(["yes", "non-profit"]).astype(int)
    return a


# ── model ───────────────────────────────────────────────────────────────────────────────────
def fit(df: pd.DataFrame, cols: list[str], alpha: float):
    """Conditional logit on standardized columns. Returns coefficients per 1 unit of each column."""
    mu, sd = df[cols].mean(), df[cols].std().replace(0, 1)
    X = (df[cols] - mu) / sd
    model = ConditionalLogit(df["y"].values, X.values, groups=df["competition"].values)
    res = model.fit_regularized(method="elastic_net", alpha=alpha, L1_wt=0.0) if alpha > 0 else model.fit(disp=0)
    beta_std = np.asarray(res.params)
    return pd.Series(beta_std / sd.values, index=cols), res, sd


def within_auc(df: pd.DataFrame, score: str) -> tuple[float, int]:
    num, pairs = 0.0, 0
    for _, g in df.groupby("competition"):
        w, l = g.loc[g["y"] == 1, score].values, g.loc[g["y"] == 0, score].values
        if len(w) and len(l):
            diff = w[:, None] - l[None, :]
            num += (diff > 0).sum() + 0.5 * (diff == 0).sum()
            pairs += diff.size
    return (num / pairs if pairs else np.nan), pairs


def topk(df: pd.DataFrame, score: str) -> float:
    hits = total = 0
    for _, g in df.groupby("competition"):
        k = int(g["y"].sum())
        if 0 < k < len(g):
            hits += int(g.nlargest(k, score)["y"].sum())
            total += k
    return hits / total if total else np.nan


def comp_stats(df: pd.DataFrame, score: str) -> tuple[np.ndarray, np.ndarray]:
    """Per competition: concordant (funded above unfunded) pair count, with ties as half, and pair count."""
    nums, pairs = [], []
    for _, g in df.groupby("competition"):
        w, l = g.loc[g["y"] == 1, score].values, g.loc[g["y"] == 0, score].values
        if len(w) and len(l):
            diff = w[:, None] - l[None, :]
            nums.append((diff > 0).sum() + 0.5 * (diff == 0).sum())
            pairs.append(diff.size)
    return np.array(nums, float), np.array(pairs, float)


def boot_ci(df: pd.DataFrame, score: str, reps: int = 2000, minus: str | None = None) -> list[float]:
    """95% interval for within-competition AUC by resampling whole competitions; with `minus`, for
    the difference AUC(score) - AUC(minus) on the same resamples (paired)."""
    n1, p1 = comp_stats(df, score)
    idx = RNG.integers(0, len(p1), size=(reps, len(p1)))
    vals = n1[idx].sum(1) / p1[idx].sum(1)
    if minus:
        n2, _ = comp_stats(df, minus)
        vals = vals - n2[idx].sum(1) / p1[idx].sum(1)
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def location_weights(beta: pd.Series, loc_cols: list[str]) -> pd.Series:
    """Weights for the map: positive location coefficients, rescaled to sum to 100."""
    w = beta[loc_cols].clip(lower=0)
    return 100 * w / w.sum() if w.sum() > 0 else w * 0


def leave_one_round_out(a: pd.DataFrame, cols: list[str], alpha: float, name: str, loc_cols=None):
    """Held-out predictions. With loc_cols, also scores each held-out round with the map weights
    derived from that fold alone (column name + '_weights'), so the weighting procedure is tested
    on rounds it never saw."""
    pred = pd.Series(np.nan, index=a.index)
    widx = pd.Series(np.nan, index=a.index)
    fold_coefs = []
    for yr in sorted(a["round_year"].unique()):
        train, test = a[a["round_year"] != yr], a[a["round_year"] == yr]
        beta, _, _ = fit(train, cols, alpha)
        fold_coefs.append(beta.rename(yr))
        pred.loc[test.index] = test[cols].values @ beta.values
        if loc_cols:
            widx.loc[test.index] = test[loc_cols].values @ location_weights(beta, loc_cols).values
    a[name] = pred
    if loc_cols:
        a[name + "_weights"] = widx
    return pd.DataFrame(fold_coefs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--siteline", default=str(ROOT.parent / "SitelineLIHTCStartUp-main"))
    ap.add_argument("--alpha", type=float, default=0.02, help="L2 penalty on standardized coefficients")
    args = ap.parse_args()
    a = build_features(Path(args.siteline)).reset_index(drop=True)
    loc_cols = [c for c, _, _ in LOCATION]
    deal_cols = [c for c, _, _ in DEAL]
    all_cols = loc_cols + deal_cols
    informative = a.groupby("competition")["y"].transform(lambda s: 0 < s.sum() < len(s)).astype(bool)

    # held-out (leave-one-round-out) predictions for each candidate model
    folds = leave_one_round_out(a, all_cols, args.alpha, "p_full", loc_cols)
    leave_one_round_out(a, loc_cols, args.alpha, "p_location")
    leave_one_round_out(a, ["s_siteline"], args.alpha, "p_siteline_fit")

    # final model on every round: coefficients, CIs (unpenalized fit), and weights
    beta, res, sd = fit(a, all_cols, args.alpha)
    _, res_ml, sd_ml = fit(a, all_cols, 0.0)
    ci = np.asarray(res_ml.conf_int()) / sd_ml.values[:, None]
    pvals = np.asarray(res_ml.pvalues)
    sign_share = (np.sign(folds) == np.sign(beta)).mean()

    factors, raw_w = [], {}
    for i, (col, mid, label) in enumerate(LOCATION + DEAL):
        b = float(beta[col])
        flag = None
        if col in loc_cols:
            if b <= 0:
                flag = "wrong direction"
            elif sign_share[col] < 0.75:
                flag = "unstable across rounds"
            raw_w[col] = b if flag is None else 0.0
        factors.append({"metric_id": mid, "column": col, "label": label,
                        "kind": "location" if col in loc_cols else "deal control",
                        "coef": b, "ci_low": float(ci[i, 0]), "ci_high": float(ci[i, 1]), "p_value": float(pvals[i]),
                        "odds_ratio_per_10pts": float(np.exp(10 * b)) if col in loc_cols else float(np.exp(b * sd[col])),
                        "sign_stable_share": float(sign_share[col]), "flag": flag})
    total = sum(raw_w.values())
    for f in factors:
        if f["kind"] == "location":
            f["weight"] = round(100 * raw_w[f["column"]] / total, 1) if total > 0 else 0.0

    # fixed indexes the website could use, scored on the same held-out rounds
    wmap = {f["column"]: f.get("weight", 0) for f in factors}
    a["idx_backtest"] = sum(wmap[c] * a[c] for c in loc_cols)       # final weights (in-sample, for the CSV)
    a["idx_default"] = 70 * a["s_siteline"] + 10 * (a["s_basis"] + a["s_years"] + a["s_saturation"])
    models = [
        ("Location + deal controls (fitted, held out)", "p_full", "model"),
        ("Location metrics only (fitted, held out)", "p_location", "model"),
        ("Backtest weighting on the map metrics (held out)", "p_full_weights", "index"),
        ("Earlier hand-set weights (70/10/10/10)", "idx_default", "index"),
        ("SiteLine score alone", "s_siteline", "baseline"),
        ("Applicant's own self-score (reference)", "self_score", "reference"),
    ]
    ev = a[informative]
    comparison = []
    for label, col, kind in models:
        auc, pairs = within_auc(ev, col)
        comparison.append({"label": label, "kind": kind, "auc": float(auc), "auc_ci": boot_ci(ev, col),
                           "topk_hit_rate": float(topk(ev, col)), "pairs": int(pairs)})
    by_round = {int(yr): float(within_auc(g, "p_full")[0]) for yr, g in ev.groupby("round_year")
                if not np.isnan(within_auc(g, "p_full")[0])}
    base_rate_topk = float(ev.groupby("competition")["y"].mean().mean())

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {
        "_schema": json.loads((SITE / "backtest.json").read_text()).get("_schema") if (SITE / "backtest.json").exists() else "",
        "status": "complete", "run_date": run_date,
        "method": {"model": "Conditional logistic regression (McFadden), grouped by round x pool",
                   "penalty": f"L2 (ridge), alpha = {args.alpha} on standardized inputs",
                   "validation": "Leave one round out: fit on all other rounds, score the held-out round",
                   "weights": "Location coefficients on the site's 0-100 scale, set to 0 if they point the wrong way or flip sign in more than a quarter of held-out fits, then rescaled to sum to 100"},
        "data": {"applications": int(len(a)), "funded": int(a["y"].sum()), "rounds": f"{a['round_year'].min()}-{a['round_year'].max()}",
                 "competitions": int(a["competition"].nunique()), "informative_competitions": int(ev["competition"].nunique()),
                 "region": "11 ARC counties, 9% competitive round"},
        "factors": factors,
        "validation": {"auc_mean": comparison[0]["auc"], "auc_ci": comparison[0]["auc_ci"],
                       "topk_hit_rate": comparison[0]["topk_hit_rate"], "chance_topk": base_rate_topk,
                       "auc_by_round": by_round,
                       "baseline": {"label": "SiteLine score alone", "auc_mean": comparison[4]["auc"]},
                       "models": comparison},
        "notes": "AUC here = the share of funded-vs-unfunded pairs in the same competition that the score puts in the right order (0.5 = coin flip). Small sample: read confidence intervals before trusting a weight.",
    }
    (SITE / "backtest.json").write_text(json.dumps(out, indent=1))
    keep = ["app_id", "round_year", "project_name", "competition", "y", "local_gov", "geoid", "sl_total_pit", "basis",
            "years_since_local_award", "lihtc_units_per_100_renters", "nri_inland_flood_score", "credits_per_li_unit",
            "d_prior", "d_nonprofit", *all_cols, "p_full", "p_location", "p_full_weights", "idx_backtest", "self_score"]
    a[keep].to_csv(FUND / "backtest_features.csv", index=False)
    log = pd.DataFrame([{"run_date": run_date, "alpha": args.alpha, "n": len(a), "auc_full": comparison[0]["auc"],
                         "auc_location": comparison[1]["auc"], "auc_siteline": comparison[4]["auc"],
                         **{f"w_{f['metric_id']}": f.get("weight") for f in factors if f["kind"] == "location"},
                         **{f"b_{f['column']}": f["coef"] for f in factors}}])
    runs = FUND / "backtest_runs.csv"
    log.to_csv(runs, mode="a", header=not runs.exists(), index=False)

    print(f"{len(a)} applications, {a['y'].sum()} funded, {a['competition'].nunique()} competitions "
          f"({ev['competition'].nunique()} informative)")
    print(pd.DataFrame(comparison)[["label", "auc", "auc_ci", "topk_hit_rate"]].to_string(index=False))
    print(pd.DataFrame(factors)[["label", "coef", "ci_low", "ci_high", "p_value", "sign_stable_share", "flag", "weight"]].round(4).to_string(index=False))
    print("AUC by held-out round:", {k: round(v, 2) for k, v in by_round.items()})


if __name__ == "__main__":
    main()
