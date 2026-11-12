# Opportunity score: methodology

This is the methodology for the **Opportunity** part of the LIHTC site-selection tool. It sits
beside the **Need** and **Feasibility** parts. It covers what the score measures, where every
number comes from, how the score is built, and how each piece was checked.

All verification figures come from `python -m pipelines.opportunity.verify_opportunity`, which rechecks
everything from the raw files and writes `data/processed/opportunity/opportunity_verification.json`. The
latest run had **81 checks pass, 3 warnings and 3 informational findings, with no failures.** No data
errors were found. The warnings concern how the score relates to need and race, covered in
sections 6 and 8. Backtests against later outcomes, including a rebuild of the score from
2010 data, are in section 7.

## 1. What the score measures

"Opportunity" is what living in a place gives a resident: how children raised there do as
adults, the schools they would attend, jobs within reach, transit, health care and food access,
and environmental exposure. It deliberately leaves out measures of the residents' own hardship,
such as income, poverty and cost burden. Those belong to the Need score.

- **Geography:** 2020 census tracts. All 2,796 Georgia tracts carry the raw measures. The
  **1,245 populated tracts in the 11 Atlanta Regional Commission (ARC) counties** are scored.
  The 3 tracts with no residents get no score.
- **Scale:** `opportunity_score` runs 0–100 and is a percentile among ARC tracts, so 70 means
  "more opportunity than 70% of metro tracts". That makes it directly combinable with other
  percentile scores. `opportunity_tier` splits it into fifths: Very low, Low, Moderate, High and Very high.
- **Shared key:** the 11-digit 2020 tract GEOID (`tract`, and `tract_2020` for applications).
  Need and Feasibility should use the same key.

## 2. Data sources

Every source is public. All but two need no login; the exceptions are noted below.

| Source | Publisher | Used for | Period covered | Native geography |
|---|---|---|---|---|
| Opportunity Atlas tract outcomes | Opportunity Insights | Mobility | Children born 1978–83, incomes in 2014–15 | 2010 tract |
| 2010→2020 tract crosswalk | Opportunity Insights | Moving 2010-tract data to 2020 | — | Tract |
| LODES 8 workplace jobs, resident workers, block crosswalk | Census LEHD | Non-degree jobs within 5 miles, those jobs per non-degree worker, job growth | 2018 and 2023 | Block |
| Smart Location Database v3 | EPA | Jobs reachable by transit and by car, walkability | Inputs about 2017–19 | 2010 block group |
| Georgia Milestones EOG/EOC, graduation rates | GA Governor's Office of Student Achievement | School proficiency and graduation | 2024–25 school year | School |
| Public school locations | NCES | Placing schools | 2024–25 | Point |
| MARTA GTFS schedule | MARTA | Rail distance, stops nearby | Current schedule | Point |
| Health center sites | HRSA | Distance to a community health center | Current | Point |
| PLACES | CDC | Adults without health insurance | 2025 release | 2020 tract |
| SNAP Retailer Access Map | USDA ERS | People over 1 mile from a grocery store | 2025 release | 2020 tract |
| AFFH environmental health index | HUD | Environment | AFFH-T 0006 (2014 air toxics) | 2010 tract |
| Tract population centroids, ZCTA↔tract file | Census | Distances, ZIP rollup | 2020 Census | Tract |
| Parks | OpenStreetMap | Optional park metric | Current | Point |
| Fixed broadband Served/Unserved | FCC | Optional broadband metric (manual download) | Dec 2025 filing | Location |
| Other AFFH indices | HUD | Optional metrics | AFFH-T 0006 | 2010 tract / block group |

**Used only to check the score, never in it:**
- **Life expectancy:** CDC USALEEP, 2010–15.
- **Adult employment, education-adjusted:** ACS 2019–23 table B23006 (employment by education, ages 25–64).
- **Need and fairness stand-ins:** USDA tract characteristics (poverty rate, median family income, SNAP share, racial makeup).
- **Population cross-check:** Census county population totals.
- **External benchmark:** the Child Opportunity Index 3.0, which needs a free account and isn't committed to the repo.

`data/raw/opportunity/manifest.csv` records when each file was downloaded.

## 3. The metrics

Every metric is measured from the tract's **population-weighted center**. Distances are straight-line miles.

| Part | Metric | Better when | Weight in part |
|---|---|---|---|
| Mobility | Average adult income rank of children raised here by parents at the 25th income percentile | higher | 70% |
| Mobility | Share of those children incarcerated as adults | lower | 30% |
| Schools | % proficient in ELA + math, the 3 nearest elementary schools | higher | 40% |
| Schools | % proficient, nearest middle school; nearest high school | higher | 20% each |
| Schools | Graduation rate, nearest high school | higher | 20% |
| Jobs | Jobs reachable in 45 min by transit; jobs reachable in 45 min by car | higher | 25% each |
| Jobs | Jobs held by workers without a bachelor's degree within 5 miles | higher | 20% |
| Jobs | Those jobs per non-degree worker living within 5 miles (competition-adjusted access) | higher | 20% |
| Jobs | Change in jobs within 5 miles, 2018–23 | higher | 10% |
| Transit | Miles to the nearest of MARTA's 38 rail stations | lower | 40% |
| Transit | MARTA stops within half a mile; walkability index | higher | 30% each |
| Health | Miles to a community health center; % of adults uninsured | lower | 40% each |
| Health | % of people over 1 mile from a SNAP grocery retailer | lower | 20% |
| Environment | HUD environmental health index | higher | 100% |

**Rules for schools:**
- Only regular, in-person, non-charter public schools count, because they serve a neighborhood.
- Proficiency pools All Students results for ELA and math (Algebra and American Literature in high school).
- GOSA results are matched to NCES school locations by district and school name. 1,886 of 2,009 GOSA schools matched.

**Optional metrics** are off by default and users can switch them on: distance to a park of 1+ acres, wired broadband coverage, and HUD's school proficiency, jobs proximity, labor market and transportation cost indices.

## 4. From metrics to score

1. **Percentiles.** Each metric becomes a percentile among the 1,245 scored tracts, with
   direction flipped where lower is better, so 100 is always best.
2. **Part scores.** Each part is the weighted mean of its metrics' percentiles. When a metric is
   missing, the weights are rescaled over the ones present.
3. **Index.** The opportunity index is the weighted mean of the six part scores:
   **mobility 34%, schools 27%, environment 12%, health 11%, jobs 11%, transit 5%.**
4. **Score and tier.** The index is re-ranked to a 0–100 percentile (`opportunity_score`) and split into fifths (`opportunity_tier`).

**How the weights were set.** They are calibrated, not hand-picked.
- Published research sets a starting weight and an allowed range for each part.
- Tract outcomes (children's mobility and life expectancy) move each weight within its range.
- Each candidate weighting was tested on counties it wasn't fitted to.

The reasoning and sources are in [`opportunity_weights.md`](opportunity_weights.md).

**Moving data onto 2020 tracts:**
- **2010-tract sources** (Opportunity Atlas, EPA, HUD) use the Opportunity Insights crosswalk weights.
- **ZIP code (ZCTA) values** are population-weighted averages of tracts, using tract population times the share of its land in the ZIP.
- **DCA applications** were geocoded with the Census Geocoder (coordinates first, then street address). A tract was only accepted if it falls in the county DCA lists. 864 of 927 applications got a 2020 tract.

## 5. Verification: is the data right?

**Geography**

| Check | Result |
|---|---|
| Georgia tract count | 2,796, matching the Census 2020 count |
| Metro population vs official Census county totals | 4,967,514 vs 4,969,483 (0.04% apart); every county within 0.6% |
| 2010→2020 crosswalk weights | Sum to exactly 1 for every 2020 tract |
| ZIP-code rollup | Keeps 100% of metro residents |
| Applications' tracts | All 864 lie in the county DCA lists for them |

**Metrics**
- **Coverage:** every scored metric covers at least 99.8% of scored tracts.
- **Ranges:** every metric is inside its possible range (for example, percentages between 0 and 100).
- **Typical values:** each metro median sits in a plausible band. Examples:
  - median elementary proficiency 44%
  - median graduation rate 91%
  - median 8.5 miles to MARTA rail
  - 11% of adults uninsured
  - 2,667,976 jobs located in the 11 counties, in line with metro Atlanta's roughly 2.5–3 million

**Recomputed independently from the raw files** (separate code from the pipeline):

| Check | Result |
|---|---|
| Distance to MARTA rail, 25 random tracts | Identical |
| Distance to a health center, 25 random tracts | Identical |
| Jobs within 5 miles, a sample tract | Identical (138,859) |
| Opportunity Atlas values after the 2010→2020 crosswalk, 10 tracts | Identical |
| MARTA rail stations | 38 after removing bus park-and-rides, matching MARTA's 38 stations |
| School proficiency | Read straight from GOSA's file, Morningside Elementary (APS) is 78.6% proficient or distinguished across 690 ELA and math tests, one of the metro's highest as expected |

**Scoring**
- **Rebuilt from inputs:** percentiles, part scores and the composite were each rebuilt from their inputs, with zero difference.
- **Weights and tiers:** the weights sum to 100%, and each tier holds exactly 20% of tracts.
- **Browser matches pipeline:** a browser test confirmed the web module computes identical scores for all 1,245 tracts.

## 6. Validity: does the score mean what it claims?

| Question | Result |
|---|---|
| Does a higher score go with better lives? | Within counties, on counties held out of the weight fitting, the score ranks tracts in line with **children's upward mobility (0.45)** and **life expectancy (0.52)**. The mobility test excludes the score's own mobility part. |
| Does it agree with an established index? | Rank correlation **0.81** with the Child Opportunity Index 3.0 (2024, metro-normed). |
| Is it fragile to the weights? | Moving every weight up or down by as much as 50% (300 random draws): median rank correlation with the default ranking **0.99**; 91% of Very high tracts stay Very high. |
| Is it fragile to data noise? | The mobility data was redrawn within its published margins of error 100 times. On average **81% of tracts keep their tier**, but only **32% keep it in at least 95 of 100 runs**. Many tracts sit near a tier boundary, so near-boundary tiers should be read as "about Moderate/High", not as exact. |
| Is it just measuring wealth? *(warning)* | No, but it overlaps heavily with need. Rank correlation with a need stand-in (poverty, income, SNAP share) is **−0.73**. Mobility (−0.62) and schools (−0.67) drive the overlap; jobs (+0.13) and transit (+0.24) run the other way. |
| Does it add anything beyond need? | Yes, modestly. Within counties, need explains 40% of differences in children's mobility; adding the opportunity parts raises that to 47%. For life expectancy it rises from 37% to 47%. |
| Does it predict adults' employment? | Not once education is accounted for. Employment of adults without a degree, compared with what their education mix predicts (ACS 2019–23), isn't predicted by any weighting (0.00–0.06). This matches Moving to Opportunity, where better neighborhoods didn't raise adults' employment. It is therefore a test, not a weight-setting outcome; see `opportunity_weights.md`. |
| Does it duplicate Feasibility? | Partly. Rank correlation **0.40** with SiteLine's location score at 192 past application sites, through schools and mobility rather than transit or health. |

## 7. Backtests: does each metric, and the score, hold up?

From `python -m pipelines.opportunity.backtest_opportunity` (results in
`data/processed/opportunity/opportunity_backtest.json`). Every number is a **within-county rank
correlation**: does the metric tell neighborhoods apart correctly inside the same county? The
numbers in parentheses count the counties where the link is positive.

**Each metric against real outcomes**

| Part | Metric | Kids' upward mobility | Life expectancy 2010–15 |
|---|---|---|---|
| Mobility | Adult income rank of low-income kids | (the outcome itself) | 0.51 (8 of 11) |
| Mobility | Incarceration rate (lower better) | — | 0.36 (8 of 11) |
| Schools | Elementary proficiency, 3 nearest | 0.46 (11 of 11) | 0.41 (8 of 11) |
| Schools | Middle school proficiency | 0.44 (11 of 11) | 0.41 (11 of 11) |
| Schools | High school proficiency | 0.38 (10 of 11) | 0.34 (9 of 11) |
| Schools | Graduation rate | 0.30 (10 of 11) | 0.29 (10 of 11) |
| Health | Fewer adults uninsured | 0.55 (11 of 11) | 0.52 (9 of 11) |
| Environment | Cleaner air | 0.19 (8 of 11) | 0.12 (5 of 11) |
| Jobs | Non-degree jobs nearby, per worker, by car, growth | −0.10 to −0.04 | −0.05 to 0.07 |
| Jobs | Jobs reachable by transit | −0.41 (0 of 5) | −0.30 (0 of 5) |
| Transit | Rail distance, stops nearby, walkability | −0.44 to −0.10 | −0.39 to −0.04 |
| Health | Closer to a health center | −0.37 (2 of 11) | −0.26 (2 of 11) |
| Health | Closer to a grocery store | −0.29 (2 of 11) | −0.23 (3 of 11) |

- **Strong and consistent:** schools, mobility and insurance coverage.
- **Zero or negative:** jobs, transit, clinic distance and grocery distance. This is a
  *placement effect*, not harm. Transit lines, clinics and job centers are deliberately located
  where lower-income residents live, so in a snapshot they sit next to worse outcomes. The
  research (section 4 and `opportunity_weights.md`) is what keeps a minimum weight on access. This pattern is also why the
  calibration put Jobs and Transit at the bottom of their ranges.

**Historical backtest: the score rebuilt from 2010 data**

The score was rebuilt using only data that existed around 2010:
- **Schools:** 2011 CRCT and EOCT results, and 2011 graduation rates.
- **Jobs:** 2005 and 2010 job counts.
- **Grocery access:** 2010 USDA food access.
- **Transit:** MARTA's 38 rail stations, all open by 2000.
- **Health centers:** the 63 sites open by 2010.

Environment and uninsured rates have no 2010 data, so they were left out. The rebuilt score was then tested against outcomes measured afterward.

| Outcome measured later | 2010 score | 2010 score + mobility | 2010 poverty alone | Poverty + 2010 score (share explained) |
|---|---|---|---|---|
| Adults not in poor health, **2022–23** (CDC PLACES) | 0.38 (8 of 11) | 0.52 (10 of 11) | 0.56 | 36% → **46%** |
| Life expectancy, 2010–15 (CDC USALEEP) | 0.30 (8 of 11) | 0.46 (9 of 11) | 0.49 | 31% → **37%** |
| Kids' adult income (Opportunity Atlas) | 0.38 (9 of 11) | — | 0.62 | — |

- **It predicts forward.** Tracts the 2010 score rated higher had healthier residents 12
  years later: positive in 8 of 11 counties, and 10 of 11 with mobility included.
- **It adds information beyond poverty.** Poverty alone predicts these outcomes strongly, as
  expected, because health tracks income and poverty is a Need measure. Adding the 2010
  opportunity score to poverty explains noticeably more (36% → 46% for health, 31% → 37% for
  life expectancy). So opportunity measures something poverty doesn't.
- **Today's full score** does better still: 0.66 for 2022–23 health, 0.53 for life expectancy.

**Stability across years** (how closely each metric ranks tracts the same way)

| Metric | Years compared | Rank correlation |
|---|---|---|
| Overall score | 2010 rebuild vs today | **0.92**; 76% of 2010's top-fifth tracts are still top fifth |
| Jobs within 5 miles (all, and non-degree) | 2010 vs 2023 | 0.99 |
| Non-degree jobs per non-degree worker | 2010 vs 2023 | 0.99 |
| Elementary proficiency, 3 nearest | 2019 vs 2025 / 2011 vs 2025 | 0.85 / 0.53 |
| Middle school proficiency | 2019 vs 2025 / 2011 vs 2025 | 0.80 / 0.63 |
| Graduation rate | 2019 vs 2025 / 2011 vs 2025 | 0.74 / 0.50 |
| Grocery access | 2010 vs 2025 | 0.64 |
| Distance to a health center | 2010 sites vs today | 0.51 (many clinics opened since) |
| Job growth within 5 miles | 2005–10 vs 2018–23 | **0.32** |

- **Steady:** the overall ranking and most metrics move slowly.
- **Schools:** stay fairly steady over 6 years, and change more over 14.
- **Job growth is mostly noise from period to period,** which supports its small weight (10% of Jobs).

**Limits of the backtest:**
- **Missing 2010 data.** 2010-era school results match about 82–85% of schools, because schools
  that have since closed can't be placed on the map. The 2010 clinic list only includes sites
  still operating today.
- **Model-based health outcome.** The 2022–23 health measure is a CDC model estimate, not a direct count.
- **Association, not cause.** The backtest shows the score predicted later outcomes, not that it caused them.

## 8. Fairness finding *(warning)*

Scores are much lower in tracts with a larger Black population share: rank correlation
**−0.80**, still **−0.60** after accounting for need. Two parts drive it: mobility (−0.46) and
schools (−0.46). Jobs, transit and health are near zero.

Part of this is how two metrics are built, not the places themselves:
- **Mobility mixes place with who lives there.** The Opportunity Atlas measure averages outcomes
  across all children in a tract. Because of nationwide racial income gaps, a tract with more
  Black children shows lower outcomes even when the place itself isn't worse. The pooled measure
  correlates −0.70 with Black share. The same measure computed for Black children only correlates
  −0.34, and for white children only −0.43. So about half of the gap comes from composition.
- **School proficiency** reflects students' family income as well as school quality.

**Recommended fixes** (not yet applied, because they change scores):
- Replace the pooled mobility measure with a composition-adjusted one: race-specific
  Opportunity Atlas outcomes, combined with fixed weights. They are available for 84% of tracts.
- Add a growth-based school measure alongside proficiency.

Until then, the web module lets users lower the mobility and schools weights. The Child
Opportunity Index, which our score tracks closely, shows a similar pattern.

## 9. Notes for combining with Need and Feasibility

- **Expect a trade-off.** Opportunity and need run strongly opposite (−0.73). A slider between
  them mostly chooses between two different kinds of site, which is the point, but users should
  know that few tracts score high on both.
- **Avoid double counting.** The opportunity metrics that behave most like need are: adults
  without health insurance (−0.83 with the need stand-in), elementary proficiency (−0.66) and
  pooled mobility (−0.63). If the Need page includes uninsured rates, drop that metric from
  Opportunity (it can be switched off in the web module) or from Need.
- **Feasibility overlap is moderate (0.40).** Avoid adding amenity-distance metrics that SiteLine
  already scores, such as grocery stores and pharmacies.
- **Use the same scale and key.** Combine 0–100 percentiles on 2020 tract GEOIDs.

## 10. Limitations

- **Old outcome data.** Mobility describes children born around 1980, and the environment index uses 2014 air-toxics data.
- **Nearest school, not zoned school.** District choice schools can count as "nearest".
- **Straight-line distances** from the tract's population center, not travel time.
  Transit job access comes from EPA's network model, but MARTA stop counts cover MARTA only.
- **Correlation, not cause.** The tract-level relationships are associations. The causal
  evidence (Moving to Opportunity, Chetty & Hendren) supports the parts, not the exact weights.
- **Within-metro ranking.** Scores rank tracts against each other within the 11 ARC counties.
  A "Low" tract is low relative to metro Atlanta, not nationally.
- **Missing applications.** 63 of 927 DCA applications have no usable location, mostly from
  2013–16. 15 of them are in the metro.

## 11. Reproduce

```bash
python -m pipelines.opportunity.pull_opportunity      # download all sources (cached)
python -m pipelines.opportunity.build_opportunity     # metrics, percentiles, scores
python -m pipelines.opportunity.calibrate_weights     # optional: re-derive the default weights
python -m pipelines.assign_tracts         # 2020 tracts for DCA applications
python -m pipelines.opportunity.verify_opportunity    # every check in sections 5, 6 and 8
python -m pipelines.opportunity.backtest_opportunity  # backtests in section 7 (downloads 2010-era data)
python -m pipelines.opportunity.export_site         # data for the web module
```
