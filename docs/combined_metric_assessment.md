# Combined tool: which metrics survive

The combined tool lets a nonprofit slide between **Need** and **Opportunity**. Fundability's chance of a 9%
award sits beside them as the viability check; it is never blended into the score. This note puts every
metric from all three tools on one tract table and tests which ones can go into the slider without
counting the same thing twice.

Rerun with `make combined-assess` (`python -m pipelines.combined.assess_metrics`). Outputs are in
`data/processed/combined/`:
- `metric_assessment.csv`: each metric, the numbers behind it, and its verdict.
- `metric_correlations.csv`
- `tract_metrics.csv`: raw values and percentiles of every metric, plus both scores before and after the drops.
- `assessment_summary.json`

## Tracts

The starting set is the 1,248 tracts in the 11 ARC counties. Two kinds are removed:
- **3 tracts with no residents.**
- **20 student-heavy tracts** (25%+ of residents enrolled in college, ACS B14007). They show inflated
  poverty and part-time work.

That leaves **1,225 tracts**. Any tract with data counts. When a value is missing or unreliable,
its weight is spread over the values the tract does have, the same rule both tools already use.

## Candidates

| Side | Metrics | Source of the list |
|---|---|---|
| Need | 22 | `pipelines/need/build_tract_need.py` |
| Opportunity | 22: 17 scored and 5 optional | `pipelines/opportunity/build_opportunity.py` |
| Fundability (context only) | SiteLine points by QAP section at each tract's best grid point | `data/processed/fundability/` |

**Left out:** FCC broadband. It is public but can only be downloaded by hand, so the combined tool can't
rebuild it. The Child Opportunity Index is used only to validate the Opportunity score; it is never an input.

## The tests

Every metric becomes a percentile among the 1,225 tracts: 100 means more need, or more opportunity.
Then, in order:

1. **Same construct on both sides.**
   - If a Need metric and an Opportunity metric correlate at |ρ| ≥ 0.70 (rank correlation), they
     measure the same thing.
   - The metric stays on one side only. Resident circumstances belong to Need; what a place offers
     belongs to Opportunity.
2. **Wrong side.**
   - Each score is rebuilt without the metric being tested.
   - If the metric tracks the other side's score (at 0.50 or more) more closely than its own, and its
     construct belongs to that other side, it is dropped from its own side.
3. **Overlap without duplication.**
   - An Opportunity metric that correlates 0.50 or more with the Need score is kept but flagged.
   - It describes the place, but it partly reflects residents' income.
4. **Near-duplicates within a side** (|ρ| ≥ 0.85) are reported.
   - The Need build already splits weight between them.
   - Opportunity gives each one full weight.
5. **Reliability.** A Need metric is flagged when it passes Need's reliability test in under half of
   the tracts. The test fails a value when its CV is over 40% (30% for income), or when the estimate is zero.

## Results

### Dropped (2)

| Metric | Side | Why |
|---|---|---|
| `places_uninsured`: adults without health insurance | Opportunity (health, 40% of the part) | Same construct as household income and poverty: −0.83 with "below 200% of poverty", −0.81 with median income. Still a duplicate at the strictest cut (0.80). Insurance status describes residents, so it belongs on the Need side, which already covers it through income. |
| `hud_labor_market`: HUD labor market index | Opportunity (optional, no default weight) | −0.71 with median income. HUD's index describes residents' employment and education. |

Opportunity's health part keeps distance to a health center and grocery access. Their within-part
weights rescale automatically.

### Kept, but overlapping Need (3)

These describe the place, but they track residents' income closely enough to be worth knowing:

| Metric | With the Need score | Closest Need metric |
|---|---|---|
| `school_prof_elem3`: elementary proficiency | 0.55 | median income −0.68 |
| `hud_school_proficiency` (optional) | 0.58 | median income −0.71 |
| `oa_kfr_p25`: upward mobility of low-income kids | 0.50 | SNAP share −0.64 |

The Opportunity team already flagged the same two parts in its fairness check. The fixes they
recommend would reduce this overlap without dropping the parts:
- Replace pooled mobility with the race-specific Opportunity Atlas outcomes.
- Add a growth-based school measure alongside proficiency.

### Kept with low reliability (13)

Most of Need's 22 metrics fail its reliability test in most tracts. Tract samples are small, and
zero estimates are flagged too.

| Reliable in | Metrics |
|---|---|
| Under 10% of tracts | lacking plumbing or kitchen (2%), overcrowding (6%), rental vacancy (9%), single-parent poverty (9%) |
| 10–30% | disability poverty (15%), limited English (17%), senior poverty (19%), no vehicle (25%), rent growth (28%), low-income renter burden (30%) |
| 30–50% | unemployment (47%), SNAP share (47%), severe rent burden (48%) |

These aren't double counting, so they stay. But a typical tract's Need score rests on fewer than half
of its 22 metrics. That is a bigger open question for Need's weights than any overlap. Options:
- Pool the noisy metrics into fewer, steadier ones.
- Pull HUD CHAS, which reports cost burden by income band at tract level from a larger sample.
- Weight each metric by its reliability.

### Everything else (26) keeps

Near-duplicates inside a side, reported and not dropped:
- **Need:** median income, below 60% AMI, and below 200% of poverty correlate at 0.87–0.91.
- **Opportunity:** jobs reachable by car and distance to MARTA rail correlate at 0.91, and jobs within
  5 miles and HUD's jobs proximity at 0.86. Each carries full weight in its part.

## What the drops change

| | Before | After |
|---|---|---|
| Need vs Opportunity (rank correlation) | −0.60 | −0.55 |
| Need ranking | | unchanged (ρ 1.00; same top 10%) |
| Opportunity ranking | | ρ 0.99; 90% of the top 10% stay |
| Tracts in the top third of both | | 37 |

The two scores still run opposite. That is a real trade-off, not double counting: places that offer
the most tend to house the fewest people in need. That's why the slider matters, and why only
**37 of 1,225 tracts** are in the top third of both.

The Opportunity team reported −0.73 against a USDA need stand-in (poverty, income, SNAP share). This
assessment uses the Need team's 22-metric score instead, which is a broader measure.

## Overlap with Fundability (context, not double counting)

Fundability isn't in the slider, so its overlap can't double count. It does mean the same feature
shows up twice on screen:
- Opportunity's transit and access metrics track SiteLine's §XII transit points closely (0.60–0.78).
- Its school metrics track §XIII schools (0.41–0.62).

A transit-rich tract will look good on Opportunity and on chance of an award for the same reason. The
tool should say so where the two appear together.

## Settled by the team

- **The slider biases between Need and Opportunity.** It isn't a straight average of the scores.
- **Fundability's chance of an award is shown, not blended.**
- **Student tracts are dropped**; every other tract with data counts.
- **Validation-only data** (Child Opportunity Index) never reaches the tool; FCC broadband stays out.

## Team decisions (November 2026)

Applied in `pipelines/combined/metric_set.py`; the category table is in [`combined_metric_set.md`](combined_metric_set.md).

- **Swapped** renters under $35k paying 30%+ for the HUD CHAS version: renters under 80% of area income
  (usable in 53% of tracts, up from 30%).
- **Dropped:**
  - from Need: overcrowding, missing plumbing or kitchen, rental vacancy, and poverty among single
    parents, seniors and people with disabilities.
  - from Opportunity: adults without health insurance, the HUD labor-market index, and the HUD
    school-proficiency and jobs-proximity indices.
- **Weights** are final for both scores (see the project README): Opportunity calibrated to outcomes on this metric set, and Need using
  its team's backtested group weights with factor analysis inside each category.

## Still open

1. **Need's group weights** (income 25, burden 25, supply 15, market 10, vulnerable 10, work 10, quality 5)
   are still judgment.
2. **Need's reliability:** how to handle metrics that are unreliable in most tracts (above).
3. **How the slider combines the two scores.** A weighted geometric mean of the two percentiles, re-ranked,
   leans toward tracts that are decent on both; a weighted arithmetic mean doesn't. This is worth
   choosing deliberately before building the site.
4. **Opportunity's fairness fixes** for mobility and schools.
