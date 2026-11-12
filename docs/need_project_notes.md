# Need score: project notes

*Working notes from the Need branch (originally its `CLAUDE.md`), kept here so they don't apply to the whole repo.*

## Housing-need score (started Oct 14, 2026)

Goal: a housing-need score to pair with the existing suitability tool, exposed in the web app so
developers and nonprofits can filter by need.

**Read first:** `docs/need_data_risks.md` (10 data risks and what each means for the build).

**Rule: census tract is the default geography (decided Oct 14, 2026)**
- Pull and score need metrics at the tract level for the 1,248 tracts in the 11 ARC counties, listed in
  `data/processed/arc_tracts_2020.csv` (2020 tract boundaries, from the 2024 Census Gazetteer).
  ACS tract GEO_IDs in the summary files start `1400000US`.
- Tract-native sources (HUD CHAS, QCTs, HUD Picture of Subsidized Households, Eviction Lab) join
  directly with no crosswalk. LIHTC properties are assigned to tracts by their coordinates.
- Tract caveats, to note with any tract output:
  - Smaller areas (about 4,000 people) mean larger MOEs, so more values get flagged unreliable,
    especially overcrowding, plumbing/kitchen and vacancy.
  - Tracts were redrawn in 2020; comparing with 2015–2019 or older data needs the Census 2010–2020
    tract relationship file.
  - Campus tracts (Georgia State, Georgia Tech, Emory, West Georgia) are even more student-skewed than
    campus ZCTAs; adjust with B14007.
  - Users search by ZIP, so the app needs a ZIP/ZCTA lookup that shows the tracts inside it.
- This replaces the block-group plan under "Decisions and plan"; ZCTAs are now only a search and
  summary layer.

**Rule: if ZCTA data is requested, pull only the 137-ZCTA working set**
- When asked to pull or build ZCTA-level data, restrict it to ZCTAs with `in_arc_majority` = True in
  `data/processed/metro_atlanta_zctas.csv` (the 137 ZCTAs mostly inside the 11 ARC counties).
  Don't include the other 100 MSA ZCTAs unless explicitly asked.

**Caveats to carry into any ZCTA analysis or score**
- Campus ZCTAs (30303, 30313, 30332, 30322, 30118) show inflated poverty and part-time rates from
  off-campus students; 30303 has 60% poverty and suppressed median income. Flag or adjust (B14007).
- Overcrowding is noisy: 44 of the 137 ARC ZCTAs have CV > 40%. Low weight or tract-level only.
- Check `unreliable_metrics` (CV > 40%, > 30% for income) and `low_population_flag` before ranking.
- The 137 set uses a land-area majority rule, not population; a few rural edge ZCTAs may be misassigned.
- ZCTAs are coarse (often 30k–50k people) and fixed to 2020 boundaries; PO-box ZIPs have no data.
- ZIP 31206 (Macon) is flagged `in_arc` in `applications_9pct.csv`; verify before modeling.

**Data built so far**
- `data/processed/metro_atlanta_zctas.csv`: all 237 ZCTAs touching the 29-county Atlanta MSA, with
  county land-area shares. `in_arc_majority` = True gives the 137-ZCTA working set for the 11 ARC
  counties. Source: Census 2020 ZCTA-to-county relationship file.
- `data/processed/zcta_need_metrics_acs2024.csv`: ACS 2020–2024 5-year need metrics per ZCTA, each
  with MOE and CV: median household income, poverty rate, share below 200% poverty, renter share,
  rent burden 30%+ / 50%+, unemployment, part-time share of workers, SNAP share, overcrowding,
  no-vehicle share. Flags: `unreliable_metrics` (CV > 40%, or > 30% for income), `low_population_flag`.

- `data/processed/need/tract_need_metrics.csv` and the map tool `need_site/index.html` (+ `data.js`):
  22 need metrics in 7 groups for the 1,248 ARC tracts (ACS 2020–2024 + HUD LIHTC, public housing,
  multifamily assisted, vouchers, 2026 QCTs), with MOE/CV/reliability flags and slider weights.
  Rebuild with `python -m pipelines.need.build_tract_need` (`--offline` reuses cached downloads in
  data/raw/). Methods, default weights and caveats are in that script's docstring and `CAVEATS`.
  Published copy: https://claude.ai/artifact/SDbavXFuMKiEdMWbYsKFQ7 (private until shared).
- Weighting (Oct 15, 2026): within-group defaults come from factor analysis (squared PC1 loadings,
  OECD/JRC method; loadings in `data/processed/need/tract_need_factor_loadings.csv`). Expert-judgment and
  equal presets remain; group weights are a values choice left to users. A Monte Carlo robustness test
  (in the pipeline and live in the tool) reports how often each tract stays in the top 10%.
  Not done yet: stakeholder AHP for group weights.
- Validation (Oct 22, 2026): plan with pre-set pass marks in `docs/need_validation_plan.md`; run
  `python -m pipelines.need.validate_need` after the build (it patches data.js). Results in
  `data/processed/need/validation/` and the tool's Methodology tab. 7 of 14 passed: verification and
  CDC SVI agreement pass; vouchers, eviction filings (rho 0.34, lift 1.38x; SVI does no better), survey
  error (median 90% rank interval 302), modeling choices (min rho 0.83) and data year (rho 0.85) fail.
  Eviction data is summed from Eviction Lab's Atlanta file in a browser (site blocks scripts).

**How the ACS data was pulled**
- api.census.gov now requires a key (none set up yet). Instead, use the table-based summary files:
  `https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-<table>.dat`
  (pipe-delimited, all geographies; ZCTA GEO_IDs start `860Z200US`, block groups `1500000US`).
- Tables used: B01003, B19013, C17002, B25070, B25003, B23025, B23022, B25014, B22003, B25044.
- Derived-proportion MOEs use the Census formula; CV = MOE / 1.645 / estimate.
  Negative estimates (e.g. -666666666) mean suppressed.

**Decisions and plan**
- ~~Score at block group~~ Superseded: score at census tract (see the rule above); summarize up to
  ZIP/ZCTA for display and filtering.
- ZIP input: direct ZCTA match → UDS Mapper ZIP-to-ZCTA crosswalk → "no Census data" message.
  Address input: geocode to lat/lon and look up ZCTA, tract and block group directly.
- Campus ZCTAs (30303, 30313, 30332, 30322, 30118) need a student adjustment (ACS B14007).

**Next steps**
1. Pull the same ACS tables at tract level for the 1,248 ARC tracts.
2. Add LEHD LODES low-wage jobs (block level → block group) and HUD CHAS (tract).
3. Fix or verify ZIP 31206 (Macon) flagged `in_arc` in `applications_9pct.csv`.

- Default group weights (final, Nov 1, 2026): original 25/25/10/10/10/5/15, restored after a brief
  switch to a HUD Worst-Case Needs translation (25/40/5/5/0/10/15), which backtested worse. CHAS is
  not a metric (team decision; a HUD+CHAS variant backtested worst). The supply gap is now
  `unassisted_li_renter_share` (renters under $35k minus assisted units, per household), replacing
  assisted units per 100 low-income renters, which carried ~0.3% of the ranking. Rebuild order:
  build_tract_need -> validate_need -> backtest_frameworks -> significance_frameworks -> weight_tests
  -> regress_weights (regression of later outcomes on the 7 group scores, plus a rounding check)
  (validate and weight_tests patch need_site/data.js; the tool-vs-pipeline browser check is
  recorded by hand in data/processed/need/validation/tool_check.json).
