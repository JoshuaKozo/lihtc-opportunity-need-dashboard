"""The combined tool's metric set: every metric that survived the assessment, grouped into categories.

This is the one place to change what goes into the Need and Opportunity scores and how they are
weighted. Change a number here, rerun `make combined-data` and `make combined-site-data`, and the tool
picks it up.

Weights:
- Opportunity category weights are FINAL: calibrated on this metric set by
  pipelines/combined/calibrate_opportunity.py with the Opportunity team's method (research ranges, moved
  by tract outcomes, tested on held-out counties). Rerun it after changing Opportunity's metrics. The
  weights inside each category are the Opportunity team's research-based ones.
- Need category weights are FINAL: calibrated by pipelines/combined/calibrate_need.py with the Opportunity
  team's method on the Need team's backtest design (research and federal-definition ranges around the Need
  team's weights, moved by later outcomes, one per kind of hardship: evictions, low-income renter loss and
  CDC housing insecurity; tested on held-out counties and outcomes). The weights inside each category are the Need team's factor analysis, recomputed on this set.

Decisions behind this set (docs/combined_metric_assessment.md, team review November 2026):
- Swapped: "renters under $35k paying 30%+" (reliable in 30% of tracts) for HUD CHAS "renters under
  80% of area income paying 30%+" (53%), which also uses LIHTC's income bands. Tested against the Need
  team's outcomes (pipelines/combined/test_chas_swap.py): never worse, significantly better on housing
  insecurity, utility shutoffs and the CDC SVI.
- Supply: the Need team replaced "assisted units per 100 low-income renters" (tied at zero in most tracts,
  moved the ranking under 1%) with "low-income renters without assisted housing, share of households".
- Dropped from Need: overcrowding and missing plumbing/kitchen (reliable in 6% and 2% of tracts),
  rental vacancy (9%), and poverty among single parents, seniors and people with disabilities (9-19%).
- Dropped from Opportunity: adults without health insurance and HUD's labor-market index (both
  measure residents' income, which Need already counts), and the optional HUD school-proficiency
  and jobs-proximity indices (near-duplicates of metrics already in).
- Jobs: the Opportunity team replaced "jobs within 5 miles" with skill-matched, competition-adjusted access
  (jobs for workers without a degree within 5 miles, and those jobs per non-degree worker; Shen 1998).
- Left out: FCC broadband (not scriptable). The Child Opportunity Index is validation only.
"""
from __future__ import annotations

# Each metric: (key, label, source, direction, weight within its category).
# source: "need" = data/processed/need/tract_need_metrics.csv (ACS, HUD), "chas" = HUD CHAS 2018-2022
# (built in build_combined.py), "opportunity" = data/processed/opportunity/opportunity_tract.parquet.
# direction +1: a higher value means more need (Need) or more opportunity (Opportunity); -1: lower does.
# Each category: (key, label, weight in its score, what it answers, [metrics]).

NEED = [
    ("income", "Income and poverty", 35, "How many households have low incomes?", [
        ("poverty_rate", "Poverty rate", "need", +1, 18),
        ("below_200pct_poverty", "Below 200% of poverty", "need", +1, 22),
        ("below_60ami_share", "Households under ~60% of area income", "need", +1, 22),
        ("median_hh_income", "Median household income", "need", -1, 21),
        ("snap_share", "Households on SNAP", "need", +1, 17),
    ]),
    ("rent_burden", "Rent burden", 25, "How many renters pay more than they can afford?", [
        ("chas_li_renter_burden", "Renters under 80% of area income paying 30%+ (CHAS)", "chas", +1, 31),
        ("rent_burden_30plus", "Renters paying 30%+ of income", "need", +1, 37),
        ("severe_rent_burden_50plus", "Renters paying 50%+ of income", "need", +1, 32),
    ]),
    ("supply", "Affordable housing supply gap", 17, "How many low-income renters have no assisted housing?", [
        ("unassisted_li_renter_share", "Low-income renters without assisted housing, share of households", "need", +1, 100),
    ]),
    ("market", "Rental market and housing stock", 8, "Is it a renter area under price pressure, with older housing?", [
        ("renter_share", "Renter households", "need", +1, 50),
        ("rent_growth", "Median rent growth", "need", +1, 1),
        ("pre1980_share", "Homes built before 1980", "need", +1, 50),
    ]),
    ("barriers", "Work and access barriers", 15, "Do residents face barriers to work and services?", [
        ("unemployment_rate", "Unemployment rate", "need", +1, 47),
        ("part_time_share", "Workers usually part-time", "need", +1, 18),
        ("no_vehicle_share", "Households with no vehicle", "need", +1, 35),
        ("limited_english_share", "Limited-English households", "need", +1, 1),
    ]),
]

OPPORTUNITY = [
    ("mobility", "Upward mobility", 35, "How do children from low-income families here do as adults?", [
        ("oa_kfr_p25", "Adult income of kids raised here by low-income parents", "opportunity", +1, 0.70),
        ("oa_jail_p25", "Incarceration of those kids as adults", "opportunity", -1, 0.30),
    ]),
    ("schools", "Schools", 30, "How well do the nearby public schools do?", [
        ("school_prof_elem3", "Proficiency, 3 nearest elementary schools", "opportunity", +1, 0.40),
        ("school_prof_middle", "Proficiency, nearest middle school", "opportunity", +1, 0.20),
        ("school_prof_high", "Proficiency, nearest high school", "opportunity", +1, 0.20),
        ("school_grad_rate_high", "Graduation rate, nearest high school", "opportunity", +1, 0.20),
    ]),
    ("environment", "Environment", 12, "How clean is the air?", [
        ("hud_env_health", "HUD environmental health index", "opportunity", +1, 1.00),
    ]),
    ("health", "Health and daily needs", 5, "How close are health care, groceries and parks?", [
        ("fqhc_dist_mi", "Miles to a community health center", "opportunity", -1, 0.40),
        ("food_low_access_share", "Share over 1 mile from a grocery store", "opportunity", -1, 0.20),
        ("park_dist_mi", "Miles to a park of 1+ acres", "opportunity", -1, 0.00),   # optional
    ]),
    ("jobs", "Jobs access", 12, "How many jobs can residents, especially those without a degree, reach?", [
        ("epa_jobs_45min_transit", "Jobs reachable in 45 minutes by transit", "opportunity", +1, 0.25),
        ("epa_jobs_45min_auto", "Jobs reachable in 45 minutes by car", "opportunity", +1, 0.25),
        ("jobs_nondegree_5mi", "Jobs for workers without a degree within 5 miles", "opportunity", +1, 0.20),
        ("jobs_per_nondegree_worker_5mi", "Those jobs per non-degree worker nearby", "opportunity", +1, 0.20),
        ("jobs_growth_5mi", "Job growth within 5 miles, 2018-2023", "opportunity", +1, 0.10),
    ]),
    ("transit", "Transit", 6, "How easy is it to get around without a car?", [
        ("marta_rail_dist_mi", "Miles to MARTA rail", "opportunity", -1, 0.40),
        ("marta_stops_half_mi", "MARTA stops within half a mile", "opportunity", +1, 0.30),
        ("epa_walkability", "Walkability index", "opportunity", +1, 0.30),
        ("hud_transport_cost", "HUD transportation cost index", "opportunity", +1, 0.00),   # optional
    ]),
]

SCORES = {"need": NEED, "opportunity": OPPORTUNITY}
WEIGHTS_STATUS = {
    "need": "final: research ranges around the Need team's weights, calibrated to later evictions, low-income "
            "renter loss and housing insecurity, one per kind of hardship (pipelines/combined/calibrate_need.py)",
    "opportunity": "final: calibrated on this metric set with the Opportunity team's method "
                   "(pipelines/combined/calibrate_opportunity.py)",
}


def metrics(score: str):
    """Flat list of (category_key, key, label, source, direction, weight) for one score."""
    return [(c, k, lab, src, d, w) for c, _, _, _, ms in SCORES[score] for k, lab, src, d, w in ms]


def check() -> None:
    for score, cats in SCORES.items():
        assert sum(w for _, _, w, _, _ in cats) == 100, f"{score} category weights must sum to 100"
        keys = [m[1] for m in metrics(score)]
        assert len(keys) == len(set(keys)), f"duplicate metric in {score}"
        for c, _, _, _, ms in cats:
            assert any(w > 0 for *_, w in ms), f"{score}/{c} has no weighted metric"


check()
