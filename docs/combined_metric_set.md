# Combined tool: the metric set

What goes into the combined tool's **Need** and **Opportunity** scores, by category. The definitive list
and the weights live in `pipelines/combined/metric_set.py`. `make combined-data` rebuilds
`data/processed/combined/combined_tract.csv` from it.

**Weights.**
- **Opportunity's category weights are final:** 35 / 30 / 12 / 12 / 6 / 5 for mobility, schools, environment,
  jobs, transit and health. They were calibrated on this metric set with the Opportunity team's method by
  `pipelines/combined/calibrate_opportunity.py` (`make combined-calibrate`). The results are in
  `data/processed/combined/opportunity_weight_calibration.csv` and `opportunity_weights.json`, and the
  reasoning is in the project README.
- **Need's category weights are final:** 35 / 25 / 17 / 15 / 8 for income, rent burden, supply gap, work and
  access barriers, and rental market and housing stock.
  - **How:** calibrated by `pipelines/combined/calibrate_need.py` with Opportunity's method on the Need team's
    backtest design. Research and federal-definition ranges sit around the Need team's weights, which later
    outcomes move inside their ranges, one per kind of hardship: evictions, low-income renter loss, and CDC
    housing insecurity.
  - **Inside each category:** the Need team's factor analysis.
  - **Results:** `data/processed/combined/need_calibration.csv` and `need_weights.json`; the reasoning is in the
    project README.

Changing a weight means editing numbers in `metric_set.py`; nothing else changes.

"Usable in" is the share of the 1,225 scored tracts where the value passes Need's reliability test.
Opportunity's metrics come from administrative and modeled sources, so they're usable everywhere.

## Need: how much a tract needs affordable rental housing

| Category | What it answers | Data that goes into it | Usable in | Source |
|---|---|---|---|---|
| **Income and poverty** | How many households have low incomes? | Poverty rate | 64% | ACS 2020–24 |
| | | Below 200% of poverty | 89% | ACS |
| | | Households under ~60% of area income | 92% | ACS |
| | | Median household income (lower = more need) | 92% | ACS |
| | | Households on SNAP | 47% | ACS |
| **Rent burden** | How many renters pay more than they can afford? | Renters under 80% of area income paying 30%+ | 53% | **HUD CHAS 2018–22** |
| | | Renters paying 30%+ of income | 72% | ACS |
| | | Renters paying 50%+ of income | 48% | ACS |
| **Affordable housing supply gap** | How many low-income renters have no assisted housing? | Low-income renters without assisted housing (LIHTC, public housing, HUD multifamily), as a share of households | 23% | HUD, ACS |
| **Rental market and housing stock** | Is it a renter area under price pressure, with older housing? | Renter households | 87% | ACS |
| | | Median rent growth | 28% | ACS |
| | | Homes built before 1980 | 76% | ACS |
| **Work and access barriers** | Do residents face barriers to work and services? | Unemployment rate | 47% | ACS |
| | | Workers usually part-time | 92% | ACS |
| | | Households with no vehicle | 25% | ACS |
| | | Limited-English households | 17% | ACS |

## Opportunity: what living in a tract gives a resident

| Category | What it answers | Data that goes into it | Source |
|---|---|---|---|
| **Upward mobility** | How do children from low-income families here do as adults? | Adult income of kids raised here by low-income parents | Opportunity Atlas |
| | | Incarceration of those kids as adults (lower = better) | Opportunity Atlas |
| **Schools** | How well do the nearby public schools do? | Proficiency, 3 nearest elementary schools | GOSA, NCES |
| | | Proficiency, nearest middle school | GOSA, NCES |
| | | Proficiency, nearest high school | GOSA, NCES |
| | | Graduation rate, nearest high school | GOSA, NCES |
| **Environment** | How clean is the air? | HUD environmental health index | HUD AFFH |
| **Health and daily needs** | How close are health care, groceries and parks? | Miles to a community health center (closer = better) | HRSA |
| | | Share over 1 mile from a grocery store (lower = better) | USDA |
| | | Miles to a park of 1+ acres (optional, no default weight) | OpenStreetMap |
| **Jobs access** | How many jobs can residents reach? | Jobs reachable in 45 minutes by transit | EPA |
| | | Jobs for workers without a degree within 5 miles | Census LODES |
| | | Those jobs per non-degree worker nearby (competition-adjusted) | Census LODES |
| | | Jobs reachable in 45 minutes by car | EPA |
| | | Job growth within 5 miles, 2018–23 | Census LODES |
| **Transit** | How easy is it to get around without a car? | Miles to MARTA rail (closer = better) | MARTA |
| | | MARTA stops within half a mile | MARTA |
| | | Walkability index | EPA |
| | | HUD transportation cost index (optional, no default weight) | HUD AFFH |

## Fundability: shown beside the scores, never blended

- The **chance of a 9% award**, from SiteLine's location points plus the deal checklist.
- **Location points**, including §XII transit and §XIII schools.
- **Basis boost**: QCT and Difficult Development Area status.

## How a score is built

1. **Percentiles.** Each metric becomes a percentile among the 1,225 tracts, turned so 100 = more need
   or more opportunity. Values that fail the reliability test are skipped.
2. **Categories.** A category is the weighted mean of its metrics.
3. **Scores.** A score is the weighted mean of its categories, re-ranked so 90 means the top tenth.
4. **Missing values.** When a tract lacks a value, its weight is spread over what the tract does have.
   Rent burden has no usable value in 291 tracts (24%), so in those tracts Need rests on the other categories.

## What was dropped, and why

| Metric | Why |
|---|---|
| Renters under $35k paying 30%+ (ACS) | Replaced by the CHAS version: usable in 53% of tracts instead of 30%, and it uses LIHTC's income bands |
| Overcrowding; missing plumbing or kitchen | Usable in 6% and 2% of tracts. CHAS's pooled "housing problems" measure is 97% cost burden, so it would count rent burden twice |
| Rental vacancy; poverty among single parents, seniors, people with disabilities | Usable in only 9–19% of tracts |
| Adults without health insurance | Measures residents' income (−0.83 with "below 200% of poverty"), which Need already counts |
| HUD labor market index | Describes residents' jobs and schooling, not the place |
| HUD school proficiency and jobs proximity indices | Near-duplicates of metrics already in (0.79 and 0.86) |
| FCC broadband | Can't be downloaded by script |

The evidence is in [`combined_metric_assessment.md`](combined_metric_assessment.md).

## Data note

HUD's site now answers scripts with a bot check, so `pull_bulk_files.py` may no longer fetch the CHAS
zip automatically. The CHAS measure is cached in `data/processed/combined/chas_renter_burden.csv`
(every Georgia tract, with its margin of error and reliability flag), so the combined build runs
without the raw files. To refresh it, download HUD's 2018–2022 tract CHAS zip by hand and unzip
`Table8.csv` into `data/raw/hud_chas_2018_22/`.
