# Opportunity score: pipeline and tables

*From the Opportunity branch's README, kept with the Opportunity docs. Methodology: [`opportunity_methodology.md`](opportunity_methodology.md); weights: [`opportunity_weights.md`](opportunity_weights.md); web module: [`opportunity_site/README.md`](../opportunity_site/README.md).*

## Rebuild

```bash
python -m pipelines.opportunity.pull_opportunity     # download the resident-opportunity sources (~2 min, cached)
python -m pipelines.opportunity.build_opportunity    # build tract and ZCTA opportunity tables
python -m pipelines.assign_tracts        # give each application a 2020 tract + its opportunity data (~5 min first run)
python -m pipelines.opportunity.calibrate_weights    # optional: re-derive the default domain weights
python -m pipelines.opportunity.verify_opportunity   # recheck all opportunity data; writes a verification report
python -m pipelines.opportunity.backtest_opportunity # backtest each metric and a 2010 rebuild of the score against later outcomes
python -m pipelines.opportunity.export_site        # package the data for the web module in opportunity_site/
```

## Tables

| Output (`data/processed/`) | Contents |
|---|---|
| `applications_9pct.parquet` / `.csv` | One row per 9% application, 2013–2025 (927 rows): self-score, DCA score, status, outcome, non-selection reason, funnel stage, location, tract, QCT/DDA, units, tenancy, pool, set-asides, credits requested and reserved |
| `applications_9pct_scored.parquet` | The table above plus `sl_*` SiteLine columns for applications in the 11 ARC counties |
| `applications_9pct_parse_log.csv` | For each round: which file and sheet was read and how each column was mapped |
| `applications_9pct_tract.parquet` / `.csv` | The applications table plus `tract_2020`, `tract_source`, and that tract's opportunity score, tier, domain scores and indicators |
| `opportunity_tract.parquet` / `.csv` | One row per 2020 Georgia census tract (2,796): resident-opportunity indicators, plus percentiles, domain scores and a default composite for the 1,248 tracts in the 11 ARC counties |
| `opportunity_zcta.parquet` / `.csv` | The same rolled up to the 155 ZCTAs (ZIP code areas) that touch the ARC counties, to pair with a ZCTA need score |

### Resident opportunity

"Opportunity" means what living in a place gives a resident, measured from the tract's
population-weighted centroid. It leaves out need measures such as income, poverty and cost burden,
so the two can be weighed against each other.

| Domain | Indicator (column) | Source | Better when |
|---|---|---|---|
| Mobility | `oa_kfr_p25`: adult income rank of kids raised here by parents at the 25th percentile | Opportunity Atlas | higher |
| Mobility | `oa_jail_p25`: incarceration rate of those kids | Opportunity Atlas | lower |
| Jobs | `jobs_nondegree_5mi`: jobs held by workers without a bachelor's degree within 5 miles | LEHD LODES 8 | higher |
| Jobs | `jobs_per_nondegree_worker_5mi`: those jobs per non-degree worker living within 5 miles (competition-adjusted) | LEHD LODES 8 | higher |
| Jobs | `jobs_growth_5mi`: change in all jobs within 5 miles over 5 years | LEHD LODES 8 | higher |
| Jobs | `epa_jobs_45min_transit`, `epa_jobs_45min_auto`: jobs reachable in 45 minutes | EPA Smart Location DB | higher |
| Schools | `school_prof_elem3`: % proficient (ELA + math) at the 3 nearest elementary schools | GOSA + NCES | higher |
| Schools | `school_prof_middle`, `school_prof_high`, `school_grad_rate_high`: nearest middle/high school | GOSA + NCES | higher |
| Transit | `marta_rail_dist_mi`: miles to the nearest of MARTA's 38 rail stations (bus park & rides excluded) | MARTA GTFS | lower |
| Transit | `marta_stops_half_mi`: MARTA stops within half a mile | MARTA GTFS | higher |
| Transit | `epa_walkability`: National Walkability Index | EPA Smart Location DB | higher |
| Health | `fqhc_dist_mi`: miles to the nearest community health center | HRSA | lower |
| Health | `places_uninsured`: % of adults without health insurance | CDC PLACES | lower |
| Health | `food_low_access_share`: % of people over 1 mile from a SNAP grocery retailer | USDA ERS | lower |
| Environment | `hud_env_health`: HUD environmental health index | HUD AFFH | higher |

**Optional metrics** (`OPTIONAL` in `build_opportunity.py`) get percentiles but count for nothing
by default. Users can switch them on in the web module's Advanced panel:

| Domain | Optional metric | Source |
|---|---|---|
| Health | `park_dist_mi`: miles to the edge of the nearest park of 1+ acres | OpenStreetMap |
| Jobs | `bb_wired_100_20_share`: % of locations with wired 100/20 Mbps (only if the FCC file is present) | FCC |
| Schools / Jobs / Jobs / Transit | `hud_school_proficiency`, `hud_jobs_proximity`, `hud_labor_market`, `hud_transport_cost` | HUD AFFH (2010 geography, older data) |

Each indicator becomes a percentile among ARC tracts (`pctl_*`, 0–100, where higher always means
more opportunity). Each domain score (`score_<domain>`) is a weighted mean of its percentiles, and
`opportunity_index` is a weighted mean of the domains. **`opportunity_score`** is that index
re-ranked to 0–100 across ARC tracts, so it spreads evenly and can be combined with need and
fundability scores on the same scale. **`opportunity_tier`** puts it in quintiles: Very low, Low,
Moderate, High, Very high. ZCTAs re-rank their rolled-up index among the 138 ZCTAs that have at
least half their people in the ARC counties.

| Domain | Weight | Literature range |
|---|---|---|
| Mobility | 34% | 15–35% |
| Schools | 27% | 15–35% |
| Environment | 12% | 5–15% |
| Health | 11% | 5–15% |
| Jobs | 11% | 10–25% |
| Transit | 5% | 5–15% |

The default weights are calibrated, not hand-set: research sets a range for each domain, and tract
outcomes (Opportunity Atlas mobility and CDC life expectancy) move the weight within it. Jobs and
transit sit at the bottom of their ranges because the outcome data give them no weight. The
method, sources and results are in [`docs/opportunity_weights.md`](docs/opportunity_weights.md);
rerun it with `python -m pipelines.opportunity.calibrate_weights`. Indicator weights inside each domain are in
`SCORED` in `pipelines/opportunity/build_opportunity.py`, with `DOMAIN_WEIGHTS` beside them. The previous
hand-set weights are kept in the web module as the "Access-focused" preset.

**How much do small weight changes matter?** Not much. Across 500 random draws that move each
domain weight up or down by as much as 50%, the median rank correlation with the default ranking
is 0.99 and about 91% of "Very high" tracts stay "Very high". Bigger changes in emphasis do
matter: with equal weights, 45% of tracts stay in the same tier (94% move at most one). Mobility
(0.88) and schools (0.86) correlate most with the final score.

**FCC broadband (optional).** The FCC site blocks scripted downloads, so get the Georgia
"Served/Unserved" fixed-broadband file by hand (broadbandmap.fcc.gov → Data Download) and save it
as `data/raw/fcc/bdc_ga_served_unserved.csv`. The build then adds `bb_wired_100_20_share` and
`bb_terrestrial_100_20_share` (% of locations with 100/20 Mbps service) and `bb_locations`. They
aren't scored: in the ARC counties the median tract is at 99.8% wired coverage and only 7 tracts
are below 90%.

**Child Opportunity Index (optional).** COI 3.0 needs a free diversitydatakids.org account, so it
isn't downloaded automatically. Save the Georgia rows of its tract file as
`data/raw/coi/coi30_ga.csv`, and `build_opportunity` also writes `*_with_coi` tables (COI scores and
levels, metro- and state-normed) and prints how our score compares. Those tables stay out of git
until COI's redistribution terms are checked. Against COI 2024 (metro-normed), `opportunity_score`
has a rank correlation of 0.81 across the 1,245 ARC tracts (0.68 under the earlier hand-set
weights). Schools (0.78) and mobility (0.65) track COI closely. Jobs and transit don't (−0.20 to
−0.08): COI rewards affluent suburbs, while those domains reward central, job-rich places.

## Caveats

- **Use `tract_2020`, not DCA's `tract`.** DCA's tract column is filled for only 38% of
  applications and is often a 2010 tract even in 2022+ rounds. `assign_tracts.py` geocodes each
  application with the Census Geocoder: coordinates first, then the street address, then DCA's own
  tract (crosswalked from 2010 if needed). The first candidate that falls in the county DCA lists
  wins, which throws out mistyped coordinates (a few point to other counties or other states).
  864 of 927 applications get a tract; the 63 without one have no coordinates and only vague
  addresses ("Highway 53 East"), mostly 2013–2016. 15 of them are in the ARC counties.
- **Opportunity: nearest school isn't the zoned school.** Schools are matched by distance, not
  attendance zone, and district choice schools (Innovation Academy, for example) can count as
  "nearest". Charter and virtual schools are excluded. GOSA results are matched to NCES school
  locations by district and name, which works for 1,886 of 2,009 GOSA schools.
- **Opportunity: data vintages differ.** Opportunity Atlas covers children born 1978–83; EPA SLD
  is from 2021; the HUD environmental index is from around 2014–2020. Sources on 2010 geography are
  moved to 2020 tracts with Opportunity Insights' crosswalk. MARTA stop and station measures cover
  MARTA only (other operators count through EPA's transit job access). ZCTA values weight tracts by
  population × share of land in the ZCTA, which approximates block-level weighting.
