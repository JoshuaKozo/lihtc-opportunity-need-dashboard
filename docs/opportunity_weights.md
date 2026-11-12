# How the opportunity score is weighted

The opportunity score combines six parts: mobility, schools, jobs, transit, health and
environment. This note explains how the default weights were set. Users can still change them in
the web module.

**Default weights:** mobility 34%, schools 27%, environment 12%, health 11%, jobs 11%, transit 5%.

> **In the combined tool (LIHTC Opportunity & Need Dashboard)** the same method was rerun on the final metric set, after
> adults without health insurance moved out of Opportunity to avoid double counting with Need. The final
> weights are mobility 35%, schools 30%, environment 12%, jobs 12%, transit 6% and health 5%. The details are
> in the project README under "How the weights are set" and in `pipelines/combined/calibrate_opportunity.py`.
> The weights in this note still apply to the Opportunity module in `opportunity_site/`.

They come from three steps:
1. Published research sets a starting weight and an allowed range for each part.
2. Real outcomes measured by census tract move each weight within its range.
3. Each candidate weighting is tested on counties it wasn't fitted to.

The code is `pipelines/opportunity/calibrate_weights.py`. Its results table is
`data/processed/opportunity/weight_calibration.csv`.

## Why not let the data decide on its own

Fitting weights only to outcome data gives job and transit access **zero** weight. The outcomes
available by tract describe people who grew up or lived in metro Atlanta years ago:

- **Mobility:** children born 1978–83.
- **Life expectancy:** 2010–15.

At that time, central, job-rich, transit-served neighborhoods had worse outcomes than affluent
suburbs. A model fitted only to those outcomes learns "affluent suburb = opportunity" and drops
access entirely.

Research on adults and employment says access matters, so we don't accept a zero. The research
sets a floor and a ceiling for every part, and the data moves weights only inside those bounds.

## Step 1: what the research supports

| Part | Range | Starting weight | Evidence |
|---|---|---|---|
| Mobility | 15–35% | 25% | Children whose families move to a better area gain about **4% of the gap per year of childhood** spent there (Chetty & Hendren 2018). In the Moving to Opportunity experiment, children who moved to lower-poverty areas before age 13 earned **31% more** in their mid-twenties (Chetty, Hendren & Katz 2016). About 60% of the variation in Opportunity Atlas outcomes across neighborhoods is causal (Chetty et al., Opportunity Atlas). This is the most direct evidence there is, but it describes children born around 1980. |
| Schools | 15–35% | 25% | In Montreal, **50–70%** of the educational benefit of moving to a better area came through access to better schools (Laliberté 2021). California's LIHTC opportunity map devotes 4 of its 9 indicators to education. |
| Jobs | 10–25% | 20% | The spatial-mismatch literature, reviewed by Gobillon, Selod & Zenou (2007), is "rather supportive": distance from jobs hurts low-skilled workers' employment. Job **growth** is only weakly correlated with children's outcomes (Opportunity Atlas), so it gets the smallest weight inside the jobs part. |
| Transit | 5–15% | 10% | Car access predicts getting and keeping a job better than transit access (Blumenberg & Pierce 2014, using Moving to Opportunity data). Transit was associated with keeping a job, not finding one. Transit still matters for LIHTC households without a car, so it keeps a floor. |
| Health | 5–15% | 10% | Moving to Opportunity adults who moved to low-poverty areas had better physical and mental health and well-being 10–15 years later, without income gains (Ludwig et al. 2012). Grocery store supply explains only about **9%** of the nutrition gap between rich and poor households (Allcott, Diamond & Dubé 2019), so grocery distance gets the smallest weight inside the health part. |
| Environment | 5–15% | 10% | Lower particulate pollution in the year of birth raised adult earnings (Isen, Rossin-Slater & Walker 2017, studying the 1970 Clean Air Act). Our only tract measure is HUD's environmental health index, which uses older air-toxics data, so the ceiling is low. California's map gives environment 1 of 9 indicators. |

**How other programs weight opportunity:**
- **Child Opportunity Index:** combines its indicators using empirical weights based on each indicator's association with Opportunity Atlas outcomes and CDC child health outcomes (according to the COI team's 2019 description of COI 2.0). That is essentially our step 2.
- **California (CTCAC/HCD):** the opportunity map used in California's LIHTC program counts how many of 9 indicators a tract meets: 4 economic, 4 education, 1 environment. The tract's category comes from that count. California states the map is "narrowly tailored towards upward mobility for children of low-income families."

**Changes inside parts, from the same research:**
- **Jobs:** jobs reachable by car went from 20% to 30%, and job growth from 15% to 10%.
- **Health:** grocery distance went from 40% to 20%.

## Step 2: what the data says

We used two outcomes measured by tract in the 11 metro Atlanta counties:
- **Upward mobility:** adult income rank of children from low-income families (Opportunity Atlas).
- **Life expectancy at birth, 2010–15:** CDC USALEEP, moved from 2010 to 2020 tracts with a crosswalk.

**Method:**
1. For each outcome, we fitted a regression on the six part scores.
2. Coefficients could not be negative, so a good school or nearby jobs can never count against a tract.
3. The mobility part was left out of the mobility model, because it contains that outcome.
4. Each model's coefficients were turned into weight shares, then averaged across the two outcomes.

**The data's suggested weights:** mobility 44%, schools 29%, environment 14%, health 11%, jobs 1%, transit 0%.

## Step 3: combine and test

**How the final weights were set:**
- **Combine:** final weight = half the data's suggestion + half the research starting point.
- **Keep in range:** the result is pulled back inside each part's range, so the weights still sum to 100%.

**How each weighting was tested:** by how well it ranks tracts on each outcome *within* a county, using counties it wasn't fitted to. The numbers are average within-county rank correlations; higher is better.

| Weighting | Mobility | Schools | Jobs | Transit | Health | Env. | Predicts mobility | Predicts life expectancy | Predicts adult employment (education-adjusted) |
|---|---|---|---|---|---|---|---|---|---|
| Earlier hand-set weights | 25% | 25% | 20% | 15% | 10% | 5% | 0.26 | 0.44 | 0.06 |
| Equal | 17% | 17% | 17% | 17% | 17% | 17% | 0.30 | 0.44 | 0.05 |
| Research starting point | 25% | 25% | 20% | 10% | 10% | 10% | 0.34 | 0.48 | 0.05 |
| Data only | 44% | 29% | 1% | 0% | 11% | 14% | 0.50 | 0.54 | 0.00 |
| **Final (default)** | **34%** | **27%** | **11%** | **5%** | **11%** | **12%** | **0.45** | **0.52** | **0.01** |

The final weights keep most of the data's predictive power (0.45 vs 0.50, and 0.52 vs 0.54) while keeping a floor under job and transit access. They predict both outcomes clearly better than the earlier hand-set weights.

## A third outcome for adults, used as a test

To check adults' economic opportunity, we added **employment of adults 25–64 without a bachelor's degree, adjusted for their education mix** (ACS 2019–23, table B23006). Each tract's actual employment rate is compared with the rate expected from its residents' education levels, at metro-wide rates for each level. This removes most of the "who can afford to live here" effect: the adjusted measure is unrelated to tract income (rank correlation 0.04), while raw employment tracks poverty.

**Results:**
- **No weighting predicts it** (0.00 to 0.06 in the table above). The parts together explain only about 1% of it.
- **Job access is the only part with a positive sign.** Jobs is at 0.12 and transit at 0.07; schools and mobility are near 0. But all of these are weak.
- **It matches the strongest causal evidence.** In the Moving to Opportunity experiment, moving adults to lower-poverty neighborhoods did not raise their employment or earnings, while it did improve their health and their children's adult earnings (Ludwig et al. 2012; Chetty, Hendren & Katz 2016).

So this outcome is reported as a test, not used to set the weights. An outcome the parts can't explain would only add noise to them, and giving it a vote moved weight toward mobility for no real reason. Adults' economic opportunity is represented instead by the Jobs part's skill-matched measures: jobs that don't require a degree, and those jobs per worker competing for them (competition-adjusted access; Shen 1998). Their weights come from research.

## What changed on the map

Compared with the earlier hand-set weights:
- **Tiers:** 44% of tracts keep their tier and 94% move at most one tier.
- **Rankings:** the overall ranking correlates 0.85 with the old one.
- **Rises:** established suburbs. County medians: Fayette 62 to 80, Forsyth 65 to 81, Cherokee 53 to 66.
- **Falls:** in-town areas. Fulton 75 to 53, DeKalb 52 to 34.
- **Folio House** (downtown Atlanta, funded 2025) goes from 65 to 32. It has strong job and transit access but weak mobility and school results.
- **Child Opportunity Index:** agreement rose from 0.68 to 0.81.

The earlier weights stay available in the web module as the **"Access-focused"** preset.

## Limits and next steps

- **The outcomes are old.** Mobility describes children born around 1980. Atlanta's center has changed a lot since then. The weights inherit that.
- **Correlation, not cause.** The tract regressions are associational. The causal evidence (Moving to Opportunity, Chetty & Hendren) is about moving to lower-poverty areas in general, not about any single part.
- **The ranges are judgment calls.** In particular, the floors on jobs and transit decide how much access counts. The planned next step is a short structured survey of Atlanta housing nonprofits and developers: pairwise comparisons, the analytic hierarchy process. That would confirm or adjust those ranges.
- **Optional metrics** (parks, broadband, HUD's older opportunity indices) are available in the web module but carry no weight by default, so they don't affect the calibration. A user who switches them on gets a score marked "custom".

## Sources

- Chetty, R. & Hendren, N. (2018). The Impacts of Neighborhoods on Intergenerational Mobility I: Childhood Exposure Effects. *Quarterly Journal of Economics* 133(3). [NBER w23001](https://www.nber.org/papers/w23001)
- Chetty, R., Hendren, N. & Katz, L. (2016). The Effects of Exposure to Better Neighborhoods on Children: New Evidence from the Moving to Opportunity Experiment. *American Economic Review* 106(4): 855–902. [NBER w21156](https://www.nber.org/papers/w21156)
- Chetty, R., Friedman, J., Hendren, N., Jones, M. & Porter, S. The Opportunity Atlas: Mapping the Childhood Roots of Social Mobility. [NBER w25147](https://www.nber.org/papers/w25147)
- Chetty, R. et al. (2022). Social Capital I: Measurement and Associations with Economic Mobility. *Nature* 608: 108–121. [NBER w30313](https://www.nber.org/papers/w30313)
- Laliberté, J.-W. (2021). Long-Term Contextual Effects in Education: Schools and Neighborhoods. *AEJ: Economic Policy* 13(2): 336–77. [IDEAS](https://ideas.repec.org/a/aea/aejpol/v13y2021i2p336-77.html)
- Gobillon, L., Selod, H. & Zenou, Y. (2007). The Mechanisms of Spatial Mismatch. *Urban Studies* 44: 2401–2427. [HAL](https://halshs.archives-ouvertes.fr/halshs-00826212)
- Blumenberg, E. & Pierce, G. (2014). A Driving Factor in Mobility? Transportation's Role in Connecting Subsidized Housing and Employment Outcomes in the Moving to Opportunity Program. *Journal of the American Planning Association* 80(1): 52–66. [PDF](https://poverty.ucdavis.edu/sites/main/files/file-attachments/blumenberg_and_pierce_a_driving_factor_in_mobility.pdf)
- Ludwig, J. et al. (2012). Neighborhood Effects on the Long-Term Well-Being of Low-Income Adults. *Science* 337: 1505–1510. [PDF](https://harris.uchicago.edu/files/inline-files/SCIENCE_Ludwig_MTO_article_main.pdf)
- Allcott, H., Diamond, R. & Dubé, J.-P. The Geography of Poverty and Nutrition: Food Deserts and Food Choices Across the United States. [NBER w24094](https://www.nber.org/digest/feb18/eliminating-food-deserts-wont-cure-nutritional-inequality)
- Isen, A., Rossin-Slater, M. & Walker, W. R. (2017). Every Breath You Take—Every Dollar You'll Make: The Long-Term Consequences of the Clean Air Act of 1970. *Journal of Political Economy* 125(3): 848–902. [NBER summary](https://www.nber.org/digest/jun14/earnings-consequences-clean-air-act)
- California Tax Credit Allocation Committee & HCD. Opportunity Map methodology (2024) and 2025 update. [treasurer.ca.gov](https://www.treasurer.ca.gov/ctcac/opportunity/2025/CTCAC-HCD-2025.pdf)
- diversitydatakids.org. Child Opportunity Index 2.0 (APPAM 2019 poster) and COI 3.0 technical documentation. [APPAM](https://appam.confex.com/appam/2019/webprogram/Paper33201.html)
- CDC NCHS. U.S. Small-Area Life Expectancy Estimates Project (USALEEP), 2010–15.
