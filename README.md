# Where to build affordable rental housing in metro Atlanta

*Emory AI Data Lab, Fall 2026 (Group 20). Built on [SiteLine](https://github.com/kmcalist682336/SitelineLIHTCStartUp).*

**Which places in metro Atlanta most need affordable rental housing, which would give residents the most
opportunity, and where could a Low-Income Housing Tax Credit (LIHTC) project actually win funding?**

This project gives nonprofit housing developers three lenses on every census tract in the 11 Atlanta
Regional Commission (ARC) counties, and one tool that brings them together:

| Lens | Question it answers | Built by |
|---|---|---|
| **Need** | How much does this tract need affordable rental housing? | jaimesoto1216-web |
| **Opportunity** | What does living here give a resident: mobility, schools, jobs, transit, health, clean air? | brendanhcli |
| **Fundability** | Could a 9% LIHTC application here realistically win credits from Georgia DCA? | JoshuaKozo |

The **LIHTC Opportunity & Need Dashboard** combines them. A nonprofit slides between Need and Opportunity to set what it
values, sees each tract's chance of a 9% award beside it, and gets a ranked list of tracts worth a closer look.

Everything runs in the browser (no server, no database, no keys), and every dataset is public: no login,
data use agreement, or paid key is needed.

## Use it

**[Open the LIHTC Opportunity & Need Dashboard](https://joshuakozo.github.io/lihtc-opportunity-need-dashboard/)**. A one-minute guided tour starts on your first visit; replay it any time
from **How to use** at the top of the page.

1. **Pick a place.** Look up an address or click the map. The tool scores that exact spot.
2. **Say what matters more.** Slide toward *need* (places with the most housing hardship) or *opportunity* (places
   with strong schools, jobs and health). The map and the ranked list follow.
3. **Describe your deal.** Leave the deal points at a typical funded deal, slide them for a quick what-if, or check
   off the deal checklist for an exact count under Georgia's 2024–2025 Qualified Allocation Plan.
4. **Read the result.** The panel on the right gives the chance of a 9% award in plain words and why the area scores
   the way it does. **Top tracts** lists the best-ranked places for your settings, and downloads them all.

The other tools are online too: the [Fundability Map](https://joshuakozo.github.io/lihtc-opportunity-need-dashboard/fundability_site/) (an exact address, with every QAP
location point explained), the Need team's [Need map](https://joshuakozo.github.io/lihtc-opportunity-need-dashboard/need_site/), and the Opportunity team's
[Opportunity module](https://joshuakozo.github.io/lihtc-opportunity-need-dashboard/opportunity_site/).

## Use the data

Everything that went into the tool is in this repository, so you can check it, reuse it, or build on it. The
tables most nonprofits will want:

| File | What's in it |
|---|---|
| [`data/processed/combined/combined_tract.csv`](data/processed/combined/combined_tract.csv) | Every ARC census tract: the Need and Opportunity scores, their category scores, each metric's value and percentile, the best SiteLine location points, and 2026 QCT / Difficult Development Area status |
| [`combined_site/data/tracts.geojson`](combined_site/data/tracts.geojson) | The same scores with tract boundaries, ready for QGIS or ArcGIS |
| [`data/processed/need/tract_need_metrics.csv`](data/processed/need/tract_need_metrics.csv) | The Need team's Census measures with their margins of error |
| [`data/processed/opportunity/opportunity_tract.csv`](data/processed/opportunity/opportunity_tract.csv) | The Opportunity team's measures: mobility, schools, jobs, transit, health and environment |
| [`data/processed/fundability/siteline_tract_scores.csv`](data/processed/fundability/siteline_tract_scores.csv) | SiteLine's QAP location points for each tract, by section, at its best and median spot |
| [`data/processed/applications_9pct.csv`](data/processed/applications_9pct.csv) | Every Georgia DCA 9% LIHTC application from 2013 to 2025 (927 of them), with scores and outcomes |

Columns are described in [`docs/combined_metric_set.md`](docs/combined_metric_set.md) and
[`docs/fundability_data_dictionary.md`](docs/fundability_data_dictionary.md); every source is listed in
[`DATA_SOURCES.md`](DATA_SOURCES.md).

## The tools

| Tool | Folder | Run it | What it does |
|---|---|---|---|
| **LIHTC Opportunity & Need Dashboard** | `combined_site/` | `make combined-serve` → <http://localhost:8768/combined_site/> | Need ↔ Opportunity slider, adjustable metric weights, deal points slider and checklist, chance of an award, map, Need × Opportunity chart, top tracts |
| **Fundability Map** | `fundability_site/` | `make fundability-serve` → <http://localhost:8765> | Chance of a 9% award at an exact address, QAP location points, deal checklist |
| Need map | `need_site/` | `make need-serve` → <http://localhost:8766> | The Need team's original 22-metric map and weighting tool |
| Opportunity module | `opportunity_site/` | `make opportunity-serve` → <http://localhost:8767> | The Opportunity team's map, site profiles and weight presets |

Each tool's folder has its own README.

## Methodology

### Geography and scale

- **Tracts.** All scores use 2020 census tracts: the 1,248 in the 11 ARC counties, joined on the 11-digit GEOID.
- **Tracts left out.** The Need and Opportunity scores leave out:
  - **3 tracts with no residents.**
  - **20 student-heavy tracts** (25%+ of residents enrolled in college, ACS B14007), where students inflate poverty and part-time work.

  That leaves **1,225 scored tracts**.
- **Percentiles.** Every metric becomes a percentile among the scored tracts, oriented so 100 always means
  more need (Need) or more opportunity (Opportunity).
- **Categories and scores.** A category is the weighted mean of its metrics. A score is the weighted mean of
  its categories, re-ranked so 90 means the top tenth.
- **Reliability.** Census tract estimates come from a sample and can be noisy. A Need value is skipped where
  its margin of error is too large: a coefficient of variation over 40% (30% for income), or a zero estimate.
  The tract's other metrics then carry the weight.

### Need score

How much a tract needs affordable rental housing: 16 metrics in 5 categories.

| Category | Metrics |
|---|---|
| **Income and poverty** | Poverty rate; below 200% of poverty; households under ~60% of area income; median household income; households on SNAP |
| **Rent burden** | Renters under 80% of area income paying 30%+ (HUD CHAS); renters paying 30%+; renters paying 50%+ |
| **Affordable housing supply gap** | Low-income renters without assisted housing (LIHTC, public housing, HUD multifamily), as a share of households |
| **Rental market and housing stock** | Renter share; median rent growth; homes built before 1980 |
| **Work and access barriers** | Unemployment; part-time workers; households with no vehicle; limited-English households |

Sources: ACS 2020–2024 5-year summary files, HUD CHAS 2018–2022, and HUD's LIHTC, public housing and
multifamily data. Details: [`docs/combined_metric_set.md`](docs/combined_metric_set.md), [`docs/need_data_risks.md`](docs/need_data_risks.md).

### Opportunity score

What living in a tract gives a resident: 18 metrics in 6 categories. Two of the metrics are optional and
carry no default weight.

| Category | Metrics |
|---|---|
| **Upward mobility** | Adult income of children raised here by low-income parents; their incarceration rate (Opportunity Atlas) |
| **Schools** | Proficiency at the 3 nearest elementary schools and the nearest middle and high school; graduation rate (GOSA) |
| **Environment** | HUD environmental health index |
| **Health and daily needs** | Distance to a community health center; share far from a grocery store; distance to a park (optional) |
| **Jobs access** | Jobs reachable in 45 minutes by transit and by car; jobs for workers without a degree within 5 miles, and those jobs per non-degree worker nearby; job growth |
| **Transit** | Distance to MARTA rail; MARTA stops within half a mile; walkability; HUD transportation cost index (optional) |

Details: [`docs/opportunity_methodology.md`](docs/opportunity_methodology.md); one-page summary:
[`docs/opportunity_summary.md`](docs/opportunity_summary.md).

### Fundability: chance of a 9% award

| Step | What it does |
|---|---|
| **1. Location points** | SiteLine scores the eight location criteria in DCA's 2024–2025 Qualified Allocation Plan (QAP) (§XI–§XXII, up to 46 points) at every 0.25-mile grid point. |
| **2. Deal points** | The QAP's developer criteria (readiness, compliance, financing, services and the rest), scored with the QAP's own point values. Both the Fundability Map and the dashboard count them with the same deal checklist; the dashboard also has a slider for a quick estimate. |
| **3. Chance of an award** | A logistic regression, fit on 810 past statewide 9% applications, turns how far location + deal sits above or below the pool's typical applicant into a probability. |

| Evidence | Number |
|---|---|
| SiteLine's location score ranks funded above unfunded competitors (held-out rounds) | 62% (50% = coin flip) |
| Location sections that decide awards | §XII transit, §XIII schools |
| Other location layers tested (basis boost, award recency, saturation, flood, Atlanta TADs) | no signal; shown as context |
| Win-probability model on rounds it never saw | 82% ranking accuracy, well calibrated |
| Deal points claimed by funded ARC applications, 2024–25 | median 48.5 of ~51 |

Details: [`fundability_site/README.md`](fundability_site/README.md) and the Fundability Map's Methodology menu.

### Combining them

- **The priority slider** blends Need and Opportunity with a **weighted geometric mean**:
  priority = need<sup>(1 − t)</sup> × opportunity<sup>t</sup>, where t is the slider's share for opportunity.
  - At either end it is one score alone.
  - In between, a tract strong on one side and weak on the other ranks below a tract decent on both. With a
    straight average, a 95/5 tract would tie a 50/50 one.
- **Need and opportunity run opposite** across metro Atlanta (rank correlation −0.60): places that offer the
  most tend to house the fewest people in need. Only 27 tracts are in the top third of both. The slider makes
  that trade-off explicit instead of hiding it in fixed weights.
- **Fundability is shown, not blended.** The chance of an award sits beside the priority. It comes from the tract's best
  SiteLine spot plus your deal points: a slider for a quick estimate, or the **deal checklist**, the QAP's developer
  criteria scored section by section (the same checklist and rules as the Fundability Map). A tract can be a high priority and still a poor bet for a 9% award, and nonprofits should see both.

### No double counting

Before combining, all 44 candidate metrics were put on one table and tested
([`docs/combined_metric_assessment.md`](docs/combined_metric_assessment.md)):
- **Same thing on both sides.** A Need and an Opportunity metric correlating at |ρ| ≥ 0.70 measure the same
  thing, so the metric is kept on one side only. Resident circumstances go to Need; what a place offers goes to Opportunity.
- **Reliability.** Metrics that are rarely reliable at tract level were dropped.

| Change | Why |
|---|---|
| Dropped adults without health insurance from Opportunity | Same thing as household income (ρ −0.83 with "below 200% of poverty"), which Need already counts |
| Dropped HUD's labor-market, school-proficiency and jobs-proximity indices | Resident measures, or near-duplicates of metrics already in |
| Dropped overcrowding, missing plumbing or kitchen, rental vacancy, and poverty among single parents, seniors and people with disabilities | Reliable in only 2–19% of tracts. CHAS's pooled "housing problems" measure is 97% cost burden, so it would count rent burden twice |
| Swapped renters under $35k paying 30%+ for the CHAS version under 80% of area income | Reliable in 53% of tracts instead of 30%, and it uses LIHTC's income bands |
| Left out FCC broadband and the Child Opportunity Index | Broadband can't be downloaded by script; the Child Opportunity Index needs an account, so it's used only to validate |

## How the weights are set, and why they're defensible

Weights are where a composite score is easiest to attack, so each team grounds its weights in something
outside its own judgment, and says plainly where judgment remains.

### Fundability: fit to real funding decisions

The Fundability weights aren't chosen at all; they're estimated.
- **Location weight.** A conditional logistic regression compares each funded DCA application only with the
  applications it competed against, in the same round and pool. Every candidate location metric was tested on
  rounds held out of the fit. Only SiteLine's location score improved predictions, so it is the whole location
  score; the others are shown as context.
- **Deal points** use DCA's own point values from the QAP.
- **The chance of an award** is tested on rounds the model never saw: 82% ranking accuracy, and predicted
  chances match how often applications were actually funded.

The defense is simple: these are the weights that best reproduce DCA's real decisions, 2013–2025.

### Opportunity: research ranges, calibrated to outcomes

brendanhcli's method ([`docs/opportunity_weights.md`](docs/opportunity_weights.md)) has three steps:

1. **Research sets a range for each category.** For example, mobility 15–35% (Chetty & Hendren; Moving to
   Opportunity), schools 15–35% (Laliberté), and transit 5–15% (Blumenberg & Pierce).
2. **Outcomes move the weights inside those ranges.** Tract outcomes (children's upward mobility, life
   expectancy) shift each category within its range, through a regression whose coefficients can't be negative.
   The final weights are half the data's suggestion and half the research starting point.
3. **Testing on held-out counties.** On counties left out of the fit, the score tracks:
   - mobility: rank correlation 0.45 (0.26 under the earlier hand-set weights);
   - life expectancy: 0.52 (0.44 earlier);
   - the Child Opportunity Index: 0.81.

   Moving every weight by up to ±50% leaves the ranking almost unchanged (0.99).

Two later additions from the team:
- **Skill-matched jobs access.** "All jobs within 5 miles" became jobs held by workers without a bachelor's
  degree within 5 miles, plus those jobs per non-degree worker living nearby (competition-adjusted access,
  Shen 1998).
- **A third outcome, used as a test only.** Employment of adults without a degree, adjusted for education mix.
  No weighting predicts it (0.00–0.08), matching Moving to Opportunity's adult results, so it grades the
  weightings but doesn't set them.

The team also rebuilt the score from 2010-era data. It predicts which tracts had healthier adults in 2022–23
(0.40; 0.53 with mobility), positive in 10 of 11 counties.

**Why it holds up:** no weight sits outside what published research supports, and within those bounds the
data picks the weights that best predict real outcomes in places the fit never saw.

**Where judgment remains:**
- The ranges themselves are judgment.
- The outcome data is old (children born around 1980).
- Mobility and school proficiency partly reflect residents' income, which the team's fairness review flags.

#### Finalized for the combined tool

The double-counting review removed adults without health insurance from Opportunity's health category, so the
published weights no longer matched the metrics. We reran the Opportunity team's calibration unchanged on the
final metric set, including its new skill-matched jobs metrics:
- the same research ranges, outcomes and 50/50 blend;
- the dashboard's 1,225 tracts, with student tracts left out.

The code is [`pipelines/combined/calibrate_opportunity.py`](pipelines/combined/calibrate_opportunity.py)
(`make combined-calibrate`). It first reproduces the team's published weights exactly, then recalibrates.

| Category | Research range | Research start | Data suggests | Published | **Final** |
|---|---|---|---|---|---|
| Upward mobility | 15–35% | 25% | 51% | 34.5% | **35%** |
| Schools | 15–35% | 25% | 34% | 27.1% | **30%** |
| Environment | 5–15% | 10% | 12% | 12.1% | **12%** |
| Jobs access | 10–25% | 20% | 3% | 10.6% | **12%** |
| Transit | 5–15% | 10% | 0% | 5% | **6%** |
| Health and daily needs | 5–15% | 10% | 0% | 10.7% | **5%** |

- **Health falls to near its floor.** Without the uninsured rate, the remaining health metrics (distance to a
  health center, grocery access) don't predict either outcome. The category's earlier weight was riding on a
  measure of residents' income, which confirms that dropping it removed double counting.
- **The final weights test better on this metric set.** On held-out counties:

  | | Published weights | Final weights | Data only |
  |---|---|---|---|
  | Upward mobility | 0.35 | **0.40** | 0.50 |
  | Life expectancy | 0.49 | **0.51** | 0.53 |
  | Adult employment (test only) | 0.03 | 0.02 | 0.00 |

  The final weights keep most of the data-only predictive power while keeping a floor under jobs, transit and health.
- **The change is small overall.** Final and published weights rank tracts almost identically (0.997).
- **The 50/50 blend isn't driving the answer.** At 25% data the weights move toward the research starting
  points (jobs 16%); at 75% they barely move from the final ones. Mobility sits at its 35% ceiling either way.
- **Robust.** Moving every weight by up to ±50% leaves the ranking at 0.99.

### Need: group weights chosen by backtest, factor analysis inside them

jaimesoto1216-web's method ([`docs/need_validation_plan.md`](docs/need_validation_plan.md), `pipelines/need/`):

- **Group weights: chosen by backtest.** Income 25, cost burden 25, supply 15, rental market 10, vulnerable
  households 10, employment 10, housing quality 5.
  - **The test.** Rebuild the score from 2017–2021 data and test it against outcomes that came later:
    eviction filings in 2023–25 and 2026, CDC PLACES housing insecurity and utility shutoff threats,
    low-income renters leaving, and rent growth.
  - **The alternatives.** HUD's Worst-Case Needs weighting, equal weights, two CHAS-based versions, weights
    fitted to evictions, and a blend.
  - **The result.** These weights beat every alternative on nearly every outcome; the HUD version was briefly
    the default and was dropped for doing worse.
- **Inside a group: factor analysis.** Each metric's weight is its share of the group's common signal
  (squared first-component loadings, the OECD/JRC *Handbook on Constructing Composite Indicators* method).
  Metrics that share little signal get a floor weight.
- **Tested before trusted.** The pass marks were written down before any test ran (verification against an
  independent copy of the ACS, agreement with the CDC Social Vulnerability Index, prediction of evictions,
  sensitivity to survey error and modeling choices). Later targets count only if the bottom of a 95%
  spatial-bootstrap interval clears them.

**Why it holds up:** the group weights are the ones that best predicted real hardship that came later, against
every alternative the team could construct, and the weights inside each group come from the data.

**What the team's own tests say to be careful about:**
- **Income does most of the work.** An income-only score predicts most outcomes as well or better (housing
  insecurity 0.87 vs 0.74), and only income measurably adds to predicting evictions. The other groups keep the
  score about rental housing need rather than income alone, but they add little predictive power.
- **Evictions are only modestly predicted** (about 0.35–0.37), below the 0.40 the team hoped for.
- **Exact ranks are uncertain.** Survey error alone moves a typical tract a few hundred places, so read
  ranks as tiers.

#### Finalized for the combined tool: calibrated like Opportunity

Jaime's weights were chosen by comparing a handful of candidate weight sets, not estimated, which is why they
are round numbers. For the dashboard we put them through the same calibration Opportunity uses, on Jaime's
backtest design ([`pipelines/combined/calibrate_need.py`](pipelines/combined/calibrate_need.py),
`make combined-calibrate`):

1. **Ranges.** Federal need definitions and research set a range for each category, with Jaime's weights as
   the starting point.
2. **Fit to later outcomes, one per kind of hardship.** The category scores are rebuilt from data that ends in
   2021 (ACS 2017–2021, CHAS 2017–2021). Three outcomes that came **later** move each weight inside its range,
   each counted once:
   - **eviction:** filings 2023–25;
   - **displacement:** low-income renters leaving a tract, 2021 to 2024;
   - **reported hardship:** CDC PLACES housing insecurity, adults struggling to pay rent or fearing losing their
     home.

   CDC's utility-shutoff measure correlates 0.99 with housing insecurity, so only one of the two votes. Jaime's
   regression check showed that letting near-duplicate, income-modeled measures each vote would tilt the
   weights toward income.
3. **Blend.** Final weight = half the data's suggestion + half the starting point, kept in range.
4. **Test.** Every weighting is graded on counties left out of the fit, and on outcomes the fit never saw.

| Category | Range | Why this range | Start (Jaime) | Data suggests | **Final** |
|---|---|---|---|---|---|
| Income and poverty | 20–35% | Income is the gate in every federal need definition (HUD worst case needs, LIHTC, CHAS bands); capped so the score stays about housing, not income alone | 25% | 53% | **35%** |
| Rent burden | 20–40% | HUD's Worst Case Housing Needs reports find severe rent burden the primary driver; Jaime's HUD-based variant used 40% | 25% | 23% | **25%** |
| Affordable housing supply gap | 10–20% | Worst case needs counts only unassisted renters; NLIHC's *The Gap* finds about 27 affordable and available homes per 100 extremely low-income renters in metro Atlanta | 15% | 17% | **17%** |
| Work and access barriers | 5–20% | Job loss predicts eviction even after missed rent (Desmond & Gershenson 2017); the CDC SVI counts unemployment, no vehicle, limited English | 20% | 8% | **15%** |
| Rental market and housing stock | 5–15% | Severely inadequate housing is a small share of worst case needs; renter share and rent pressure are context | 15% | 0% | **8%** |

**The results, against Jaime's starting weights.** The ρ column is metro-wide, on 2021 data. The change and
its 95% interval come from a spatial block bootstrap.

| Outcome | ρ | Change | 95% interval |
|---|---|---|---|
| Eviction filings 2023–25 (fitted) | 0.38 | **+0.023** | +0.010 to +0.038 |
| Eviction filings 2026 (held out) | 0.35 | **+0.021** | +0.008 to +0.035 |
| Housing insecurity (fitted) | 0.74 | **+0.056** | +0.042 to +0.073 |
| Utility shutoff threat (held out, near-duplicate of housing insecurity) | 0.74 | **+0.055** | +0.040 to +0.073 |
| CDC Social Vulnerability Index (held out) | 0.72 | **+0.047** | +0.034 to +0.062 |
| Low-income renters leaving (fitted) | 0.18 | +0.005 | −0.004 to +0.014 |

- **Significantly better on five of six outcomes, and no worse on displacement.** That includes 2026
  evictions, which the Need team held out of everything, and the CDC index. On counties left out of the fit it
  also beats equal weights and the HUD-based weighting on five of six outcomes.
- **Where the weight moves:** toward income (capped at 35%) and the supply gap, and away from rental market and
  barriers. Rent burden holds at 25%: it carries the displacement outcome almost alone.
- **Why the ranges matter.** Income alone predicts evictions and reported hardship best, but it is the *worst*
  of every weighting at predicting low-income renters leaving (0.13 vs 0.20 on held-out counties). The cap keeps
  the score about housing need, not income alone.
- **Reconciled with Jaime's regression check.** His check and this calibration agree on what drives each
  outcome: income drives evictions and reported hardship, and rent burden drives displacement. The difference
  was which outcomes vote. Counting each kind of hardship once answers his concern about near-duplicate,
  income-modeled measures, and keeps displacement in.
- **Robust.** Shifting the data/starting-point blend to 25% or 75% moves each weight by a few points in the same
  direction (income 32–35%, rent burden 24–26%).

**Inside each category,** the Need team's factor analysis is recomputed on the final metrics. Income reproduces
the team's weights exactly. In the merged categories, rent growth and limited-English households share little
signal and get the floor weight.

**Two metric changes,** both tested against the team's outcomes with the team's significance method
([`pipelines/combined/test_chas_swap.py`](pipelines/combined/test_chas_swap.py)). Today's final Need score:

| | Eviction filings 2023–25 | Eviction filings 2026 (held out) | Housing insecurity | Utility shutoff threat | CDC Social Vulnerability Index |
|---|---|---|---|---|---|
| Final Need score (ρ, 95% interval) | 0.38 (0.28–0.46) | 0.33 (0.23–0.42) | **0.77** (0.72–0.81) | **0.78** (0.73–0.81) | **0.75** (0.70–0.78) |
| CHAS instead of ACS low-income rent burden | +0.005, n.s. | +0.005, n.s. | **+0.008** | **+0.008** | **+0.012** |

- **The CHAS swap** is never worse and significantly better on three outcomes, and it is usable in 53% of
  tracts instead of 30%. The team's whole-score CHAS versions did worse because they replaced several metrics at once.
- **The supply gap is the team's new metric:** low-income renters without assisted housing, as a share of
  households. It replaced assisted units per 100 low-income renters, which tied at zero in most tracts and moved
  the ranking under 1%.
  - **Its coverage is low.** Under the team's reliability rule it is usable in only 23% of tracts: many tracts
    compute to exactly zero gap, which the rule treats as unreliable.
  - **So its 17% weight applies to fewer than a quarter of tracts.** That's worth revisiting, since a zero gap
    may be real information.
- **Against the team's targets,** the final score meets the minimum for housing insecurity, utility shutoffs and
  the CDC index. On evictions the point estimate clears the 0.30 minimum, but the interval's lower end (0.28) doesn't.

### The combined tool: both scores final

The dashboard uses 16 Need and 18 Opportunity metrics, regrouped after the double-counting review. Both sets
of category weights are final:
- **Opportunity:** calibrated to outcomes (above).
- **Need:** calibrated to later outcomes, one per kind of hardship, inside research ranges, starting from the Need team's backtested weights (above).

All weights live in one file, [`pipelines/combined/metric_set.py`](pipelines/combined/metric_set.py), and the
tool's Methodology menu shows them with their calibration and validation tables.

How much the weights matter (500 random weight draws per test):

| Test | Need | Opportunity |
|---|---|---|
| Every category weight moved by up to ±50% | ranking barely changes (ρ 0.99) | ranking barely changes (ρ 0.99) |
| … top-10% tracts that stay in the top 10% in 90%+ of draws | 94 of 123 | 78 of 123 |
| Any weights at all | ranking mostly holds (ρ 0.87) | ranking changes a lot (ρ 0.58) |

What this means:
- **Overall rankings are stable.** Moderate weight changes don't move them.
- **The very top of the list is sensitive.** Treat the top tracts as a shortlist to investigate, not a final answer.
- **Opportunity's weights matter most.** Its categories pull in different directions (urban access versus
  suburban mobility and schools). That is why they are calibrated against outcomes rather than chosen.

## Data

All sources are public and need no login, data use agreement, or paid key:
- Census ACS
- HUD: CHAS, LIHTC, public housing, multifamily, QCT/DDA, AFFH indices
- Georgia DCA's applicant and award lists and QAP
- SiteLine
- Opportunity Atlas
- GOSA and NCES
- Census LODES and EPA's Smart Location Database
- MARTA, HRSA, USDA, FEMA
- OpenStreetMap

[`DATA_SOURCES.md`](DATA_SOURCES.md) links each tool's full source list.

## How the repo is organized

Each lens owns its pieces, with the DCA application data shared and the combined tool on top:

| | Fundability | Need | Opportunity | Combined / shared |
|---|---|---|---|---|
| Website | `fundability_site/` | `need_site/` | `opportunity_site/` | `combined_site/` (LIHTC Opportunity & Need Dashboard) |
| Data builds | `pipelines/fundability/` | `pipelines/need/` | `pipelines/opportunity/` | `pipelines/combined/`; `pipelines/` (DCA applications) |
| Processed tables | `data/processed/fundability/` | `data/processed/need/` | `data/processed/opportunity/` | `data/processed/combined/`; `applications_9pct*`, `arc_tracts_2020.csv` |
| Docs | `docs/fundability_*.md` | `docs/need_*.md` | `docs/opportunity_*.md` | `docs/combined_*.md` |
| Tests | `tests/test_fundability.py` | `tests/test_need.py` | `tests/test_opportunity.py` | `tests/test_combined.py`, `tests/test_pipelines.py` |
| Make targets | `make fundability-*` | `make need-*` | `make opportunity-*` | `make combined-*`, `make data`, `make test` |

## Rebuild the data

You need Python 3.10–3.12 and a copy of SiteLine next to this repo. From the folder that holds this repo:

```bash
git clone https://github.com/kmcalist682336/SitelineLIHTCStartUp.git
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e "SitelineLIHTCStartUp[geo]"
cd <this repo's folder>
pip install -r requirements.txt
make test
```

To run any of the websites on your own computer, `make combined-serve` and open <http://localhost:8768/>.

`make help` lists every command. The usual order:
1. `make data`: the shared DCA applications.
2. `make fundability-data`, `make need-data`, `make opportunity-data`: each lens.
3. `make combined-data`, then `make combined-calibrate` (after any change to the metrics; it prints the weights to
   put in `metric_set.py`), then `make combined-test-chas`, then `make combined-site-data`: the dashboard.

Downloads are cached.

## Limits

- **The Need score predicts evictions only modestly** (about 0.35), and its exact ranks are uncertain; read them as tiers.
- **Opportunity's weights rest on old outcome data** (children born around 1980).
- **Census tract estimates are noisy.** Rent burden has no usable value in about a quarter of tracts.
- **Some data is old.** Upward mobility describes children born around 1980, and the environment index uses 2014 air-toxics data.
- **Distances are approximate.** They're straight-line from each tract's population center, and schools are the nearest ones, not the zoned ones.
- **The chance of an award is approximate in the dashboard.** The map and list use each tract's best spot. A looked-up address is scored at that address, the same way the Fundability Map scores it.
- **Scores rank tracts within metro Atlanta**, not nationally.

## Credits and license

Code in this repository is MIT-licensed ([`LICENSE`](LICENSE)). Location scoring comes from **SiteLine**
(kmcalist682336; an InvestATL × Emory collaboration), used as provided. SiteLine has no license file yet, so
check with its maintainer before redistributing data derived from it. Research and data credits are in each
lens's methodology and source documents.
