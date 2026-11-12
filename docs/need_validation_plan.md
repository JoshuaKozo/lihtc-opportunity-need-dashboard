# Validation plan: tract housing-need score

Written October 22, 2026, **before** any validation test was run, so the pass/fail criteria
can't be tuned to the results. Results are reported in the tool's Methodology tab and in
`data/processed/need/validation/`. Run everything with `python -m pipelines.need.validate_need`.

Baseline for every test: the tool's default settings (factor-analysis metric weights, default
group weights, unreliable values skipped, student-heavy and under-500-resident tracts not ranked).

## 1. Verification: is it calculated correctly?

| Check | Method | Pass if |
|---|---|---|
| Source values | Compare population, households, renter households and median household income for 10 randomly chosen tracts against Census Reporter's ACS 2020–2024 API (an independent copy of the same release) | All match exactly |
| Derived metrics | Recompute poverty rate and renter share for the same tracts from the independent values | All match within 0.001 |
| HUD placement | Compare our point-in-polygon tract for each HUD property against HUD's own tract code (where HUD's code is a 2020 tract in our list) | 95%+ agree |
| Coverage | Every one of the 1,248 tracts has ACS data and a boundary | 100% |
| Tool = pipeline | The map tool's scores (browser) match the pipeline's scores (Python) for the default weights | Max difference under 0.01 points |

## 2. Convergent validity: does it agree with other measures of need?

| Comparison | Pass if |
|---|---|
| CDC/ATSDR Social Vulnerability Index 2022, overall percentile (RPL_THEMES), Spearman rank correlation | ρ ≥ 0.70 |
| SVI socioeconomic theme (RPL_THEME1) | ρ ≥ 0.70 |
| Housing Choice Vouchers per 100 renter households (not a score input) | ρ ≥ 0.40 |
| 2026 Qualified Census Tract status, area under the ROC curve (partly circular; sanity check only) | AUC ≥ 0.80 |

The 15 tracts where the need score and the SVI disagree most are listed for review.

## 3. Criterion validity: does it predict housing distress?

Outcome: eviction filings per 100 renter households per year, January 2023–December 2025,
from Eviction Lab's Eviction Tracking System (2020 tracts; Clayton, Cobb, DeKalb, Fulton and
Gwinnett only). Filings with no tract ("sealed", about 2.5%) can't be used. Eviction filings are
not a score input. Rates are computed only for ranked tracts with at least 50 renter households,
so tiny denominators don't produce extreme rates. For context (no pass mark), the same tests are
run on the SVI.

| Test | Pass if |
|---|---|
| Spearman ρ between need score and filing rate, all covered tracts | ρ ≥ 0.40 |
| Lift: filing rate in the top 10% of need scores ÷ average filing rate | ≥ 2.0× |
| Consistency: ρ computed within each of the 5 counties | ρ ≥ 0.30 in every county |

## 4. Uncertainty: how sensitive is it to data error and modeling choices?

| Test | Method | Pass if |
|---|---|---|
| Data error | 200 draws, each ACS metric resampled from Normal(estimate, MOE/1.645) and clipped to valid ranges; ranks recomputed | Median width of tracts' 90% rank interval ≤ 200 ranks (about 16% of tracts) |
| Modeling choices | All 162 combinations of: normalization (percentile, z-score, min-max) × aggregation (arithmetic, geometric) × reliability cutoff (CV 30%, 40%, 50%) × student threshold (20%, 25%, 30%) × metric weights (factor analysis, expert, equal) | Every combination's ranking has ρ ≥ 0.85 with the baseline |
| Combined | 500 draws mixing data error, a random modeling choice and ±50% weight changes | Reported per tract (top-10% share, 90% rank interval); no pass mark |
| Data year | Rebuild with ACS 2019–2023 and compare with 2020–2024 | ρ ≥ 0.90 |

## Not in this round

Expert review (face validity) and stakeholder weighting need outside participants. Validating
against DCA funding decisions is deliberately excluded: where money went isn't a measure of need,
and whether funding follows need is one of the project's research questions.

## Addendum, November 1, 2026: rerun with new default group weights

The default group weights changed to the HUD Worst-Case Needs framework (income 25, burden 40,
market 5, vulnerable 5, employment 0, quality 10, supply 15; previously 25/25/10/10/10/5/15). Every
test was rerun with the same criteria and the new defaults. Results from both runs are kept in
the git history; the tool shows the current run.

## Targets for the next round (adopted November 1, 2026, before any further model changes)

These replace the round-1 pass marks for any change made from now on (new weights, new metrics,
smoothing). They were set after the round-1 validation and the backtest
(`pipelines/need/backtest_frameworks.py`), but before the next change is designed, so the next change
can't be tuned to them. A target counts as met only if the **lower end of its 95% confidence
interval** clears it, using a spatial block bootstrap (neighboring tracts resampled together).

**Why these levels.** Cohen's (1988) conventions treat correlations of 0.10, 0.30 and 0.50 as small,
medium and large. Measurement error caps what any score can reach: the ceiling is roughly
√(score reliability × outcome reliability). We measured both: the score's year-to-year rank
correlation is 0.85 and tract eviction rates correlate 0.86 between 2023–2025 and 2026, so even a
perfect measure of eviction risk would top out near 0.85, and need is not the same thing as
eviction risk. Behavioral outcomes are driven by much the score can't see (landlord practices, job
loss, local policy), so the targets for them are lower than for measures of the same concept.

| Comparison | Minimum | Strong |
|---|---|---|
| Indices of the same concept (CDC/ATSDR SVI; HUD worst-case needs if added) | ρ ≥ 0.50 | ρ ≥ 0.70 |
| Direct hardship reports (CDC PLACES housing insecurity) | ρ ≥ 0.50, **and** beat the income-only score, because PLACES is modeled partly from income | ρ ≥ 0.70 |
| Future behavioral outcomes (eviction filings; low-income renter loss) | ρ ≥ 0.30, and top-10% lift ≥ 1.5× | ρ ≥ 0.40, lift ≥ 2.0× |
| Stability (rebuild with the next ACS year) | ρ ≥ 0.80, enough to trust tiers | ρ ≥ 0.90, enough to trust exact ranks |
| Simple alternatives | Beat equal weights and the CDC SVI on the combined outcome | Also beat income alone |

Outcomes must postdate the data the score is built from (as in the backtest: score from 2017–2021,
outcomes 2022 onward), and the January–May 2026 eviction filings stay a held-out check.

## Addendum, November 1, 2026 (later the same day): original weights restored, supply gap replaced

The credibility tests (`pipelines/need/weight_tests.py`) found the supply-gap metric (assisted units per
100 low-income renters) moved the ranking by about 0.3% despite a 15% weight, and the backtests
(`pipelines/need/backtest_frameworks.py`) found the HUD weights less accurate than the original weights.
The original group weights (25/25/10/10/10/5/15) were restored and the supply gap was replaced with
low-income renters without assisted housing, as a share of households. Every round-1 test and the
backtests were rerun with the round-1 criteria; results are in the tool's Methodology tab
("Validation" and "How the weights changed") and in `data/processed/need/validation/`.
