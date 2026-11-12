# Data dictionary: Fundability Map

Columns of the tables the Fundability Map is built from. Sources and links are in
[`fundability_data_sources.md`](fundability_data_sources.md).

**Conventions.** Tracts are 2020 census tracts in the 11 ARC counties, keyed by `geoid`, the 11-digit tract
code stored as **text** so leading zeros survive. Rates are 0–1 fractions. A blank means unknown, never zero.

## `data/processed/fundability/fundability_tract_features.csv` (also `fundability_site/data/tracts.geojson`)

One row per tract (1,248). Built by `pipelines/fundability/build_fund_features.py`; the GeoJSON adds simplified polygons.

| Column | Meaning | Source |
|---|---|---|
| `geoid` | 2020 census tract code (text) | Census via SiteLine |
| `county`, `tract_name` | County and tract name | Census via SiteLine |
| `pool` | DCA geographic pool: `atlanta metro` (Cobb, DeKalb, Fulton, Gwinnett) or `other metro` | SiteLine |
| `land_area_sqmi`, `internal_lat`, `internal_lon` | Land area; a point inside the tract | Census via SiteLine |
| `sl_in_qct` | QCT flag as SiteLine has it (2025 list) | SiteLine |
| `score_<S>_max`, `score_<S>_median` | Best and median SiteLine points among the tract's 0.25-mile grid points, for QAP section S = XI, XII, XIII, XV, XVI, XVIII, XIX, XXII | SiteLine |
| `total_score_max`, `total_score_median` | Best and median total location points (out of 46); `total_score_max` is the map's location score | SiteLine |
| `n_cells`, `share_cells_ge30` | Grid points in the tract; share scoring 30+ | SiteLine |
| `municipality`, `local_gov` | City containing the tract's center; local government for DCA's award limits (the city, or "Unincorporated X County") | Census places via SiteLine |
| `qct_2025`, `qct_2026` | 1 if a HUD Qualified Census Tract that year | HUD |
| `dda_2026` | 1 if the tract's center is in a 2026 Small Area DDA | HUD |
| `basis_boost_2026` | 1 if QCT or DDA in 2026 (context) | derived |
| `acs_renter_hh` | Renter households | Census ACS 2020–2024, B25003 |
| `acs_vacancy_rate` | Vacant share of housing units | Census ACS 2020–2024, B25002 |
| `lihtc_li_units` | Existing LIHTC low-income units located in the tract | HUD LIHTC database |
| `lihtc_units_per_100_renters` | LIHTC saturation (context) | derived |
| `years_since_local_award` | 2026 minus the year of the last LIHTC award in the local government; blank = none on record (context) | SiteLine award list |
| `nri_inland_flood_score`, `nri_inland_flood_rating` | FEMA inland flood risk score and rating (context) | FEMA National Risk Index |

## `data/processed/applications_9pct.csv`

One row per Georgia DCA 9% application, 2013–2025 (927 rows), parsed by `pipelines/parse_applications.py`
from DCA's public lists. Key columns: `app_id`, `round_year`, `project_name`, `status` (DCA's code),
`outcome` (`selected` / `not_selected` / …), `nonselect_reason`, `self_score` (applicant's claimed score),
`dca_score` (DCA's final score), `pool`, `city`, `county`, `tract`, `lat`, `lon`, `qct`, `dda`,
`credit_request`, `units_total`, `units_li`, `tenancy`, `construction`, `nonprofit_set_aside`,
`owner_principal`. Applicant emails and phone numbers are dropped. `applications_9pct_parse_log.csv` records
which file and sheet each round came from and how columns were mapped.

## `data/processed/fundability/backtest_features.csv`

The 190 ARC applications with a SiteLine score and a clear outcome, as used by `pipelines/fundability/backtest.py` and
`fundability/metric_audit.py`. Every input is rebuilt **as of its round**: `sl_total_pit` (SiteLine with later awards
removed), `basis`, `years_since_local_award`, `lihtc_units_per_100_renters`, `nri_inland_flood_score`,
`credits_per_li_unit`, `d_prior` (owner won before), `d_nonprofit`; the `s_*` columns are the same values on
the website's 0–100 scale; `competition` is round × pool; `y` = funded.

## Model and evidence files (`fundability_site/data/`)

| File | Contents |
|---|---|
| `win_model.json` | Win-probability model: coefficients, held-out validation, calibration, next-round pool medians and drift |
| `funding_lines.json` | By round and pool: highest score turned down on points (`line`), median and lowest funded; deal points of funded ARC applications |
| `backtest.json` | Conditional-logit weighting backtest: factors, coefficients, weights, held-out comparison of scores |
| `metric_audit.json` | Each metric's predictive power alone and added to SiteLine |
| `grid.json` | Per grid point: `lat`, `lon`, points by QAP section, `block_score`, `total_score` |
| `grid_detail.json` | Per grid point, the reasons: §XI amenity points by category, transit distances, qualifying school grades, award counts, district distance |
| `metrics.json` | The metric registry (labels, QAP sections, scaling, whether scored or context) |
| `sources.json` | Every data source; generates `docs/fundability_data_sources.md` |
