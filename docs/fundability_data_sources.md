# Data sources: Fundability Map

Every dataset behind the Fundability Map. Generated from `fundability_site/data/sources.json` by
`pipelines/export_downloads.py`; edit that file, not this one. The website's Methodology menu (Data and sources) shows the same list.

All sources are public and need no login, data use agreement, or paid key.

| Dataset | Publisher | Used for | Vintage | Link | Terms | Role |
|---|---|---|---|---|---|---|
| SiteLine StartUp pack (location scoring and its data) | SiteLine (kmcalist682336) | The location score: QAP §XI–§XXII points at every 0.25-mile grid point in the 11 ARC counties, used exactly as provided | CSVs pulled July 2026 | [link](https://github.com/kmcalist682336/SitelineLIHTCStartUp) | No license file yet; provided to the team for this project | Drives the score |
| Georgia DCA 9% applicant and award lists, 2013–2025 | Georgia Department of Community Affairs | Past applications and outcomes: the weighting backtest, metric audit, win-probability model, and funding lines | Rounds 2013–2025 | [link](https://dca.georgia.gov/applications-funding-and-funding-cycle-selections) | Georgia public records | Rules and model data |
| Georgia 2024–2025 Qualified Allocation Plan (QAP) | Georgia Department of Community Affairs | The scoring rules: the rubric in the site's Methodology menu and the point values in the deal checklist | Approved November 2023; governs the 2024 and 2025 rounds | [link](https://dca.georgia.gov/affordable-housing/housing-development/housing-tax-credit-program/qualified-allocation-plans-and) | Georgia public document | Rules and model data |
| HUD Qualified Census Tracts, 2025 and 2026 | U.S. Department of Housing and Urban Development | Basis boost (context) and the QCT point under §XIV | 2025 and 2026 designations | [link](https://www.huduser.gov/portal/datasets/qct.html) | Public domain | Context (shown, not scored) |
| HUD Small Area Difficult Development Areas, 2026 | U.S. Department of Housing and Urban Development | Basis boost (context) | 2026 designations | [link](https://www.huduser.gov/portal/datasets/qct.html) | Public domain | Context (shown, not scored) |
| HUD Low-Income Housing Tax Credit database | U.S. Department of Housing and Urban Development | Existing LIHTC units (saturation context) and award history for the backtest | Allocations through about 2019 | [link](https://www.huduser.gov/portal/datasets/lihtc.html) | Public domain | Context (shown, not scored) |
| Census American Community Survey 5-year, 2020–2024 (tables B25002, B25003) | U.S. Census Bureau | Renter households (saturation denominator) and vacancy | 2020–2024 5-year estimates | [link](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/) | Public domain | Context (shown, not scored) |
| FEMA National Flood Hazard Layer | Federal Emergency Management Agency | Flood-zone warning at the exact looked-up spot (queried live) | Current effective flood maps | [link](https://msc.fema.gov/portal/home) | Public domain | Context (shown, not scored) |
| FEMA National Risk Index, census tracts | Federal Emergency Management Agency | Tract inland flood risk (context) | December 2025 release | [link](https://hazards.fema.gov/nri/) | Public domain | Context (shown, not scored) |
| HUD CHAS tract tables, 2018–2022 | U.S. Department of Housing and Urban Development | Renter households by income, for the planned market capture-rate check | 2018–2022 | [link](https://www.huduser.gov/portal/datasets/cp.html) | Public domain | Pulled for a planned check |
| City of Atlanta Tax Allocation Districts | City of Atlanta, via Atlanta BeltLine GIS | Tested as a stand-in for §XIV revitalization plan areas; showed no signal, so not used | Current districts with start dates | [link](https://gis.beltline.org/server/rest/services/COA_Tax_Allocation_Districts_public/FeatureServer/0) | Public map service (no stated license) | Tested, not used |
| OpenStreetMap tiles and Nominatim address search | OpenStreetMap contributors | Basemap and the address lookup | Live | [link](https://www.openstreetmap.org/copyright) | Open Database License (ODbL); attribution required | Map and search service |

**SiteLine StartUp pack (location scoring and its data)** brings in ([data dictionary](https://github.com/kmcalist682336/SitelineLIHTCStartUp/blob/main/data/DATA_DICTIONARY.md)):

- USDA SNAP authorized retailers (groceries, big-box stores)
- Overture Maps places (retail, restaurants, services)
- Trust for Public Land ParkServe (parks)
- Georgia DECAL licensed childcare
- School attendance zones with DCA's CCRPI-based quality tests
- Transit agency GTFS feeds (MARTA, CobbLinc, Ride Gwinnett, and others)
- EPA Toxics Release Inventory and Georgia EPD hazardous sites
- DCA stable-communities tract indicators and HUD CHAS
- HUD LIHTC database and DCA award lists (previous projects)
- National Register of Historic Places districts (NPS)
- Census tracts and places; USDA food desert flags

## Files the project publishes

Served with the website under `fundability_site/`:

| File | Contents |
|---|---|
| `fundability_site/data/tracts.geojson` | All 1,248 ARC census tracts with the location score (best and median per QAP section) and every context value |
| `fundability_site/data/downloads/fundability_tract_features.csv` | The same tract table as a spreadsheet (no geometry) |
| `fundability_site/data/grid.json` | SiteLine's section scores at all 52,369 grid points (0.25 mile) |
| `fundability_site/data/grid_detail.json` | Why each grid point earned its points: amenity categories, transit distances, school grades, awards |
| `fundability_site/data/downloads/applications_9pct.csv` | Every Georgia DCA 9% application 2013–2025 as parsed: scores, outcomes, location, units, credits (contact details removed) |
| `fundability_site/data/downloads/backtest_features.csv` | The 190 ARC applications used in the backtest, with every model input as of its round |
| `fundability_site/data/downloads/metric_audit.csv` | The metric audit: each metric's predictive power against past awards |
| `fundability_site/data/funding_lines.json` | Funding lines by round and pool, and deal points of funded ARC applications |
| `fundability_site/data/win_model.json` | The win-probability model: coefficients, validation, calibration, next-round inputs |
| `fundability_site/data/backtest.json` | The weighting backtest: model, coefficients, held-out results |
| `fundability_site/data/metric_audit.json` | The metric audit as used by the site's Methodology menu |
| `fundability_site/metrics.json` | The metric registry: every metric, its QAP section, data source, and scaling |
| `fundability_site/data/sources.json` | This list of sources, machine-readable |

## Notes

- **SiteLine license.** The SiteLine StartUp repository has no LICENSE file. It was provided to the team for this project; ask the maintainer to add an open license before publishing anything derived from it. Its school layer comes from a scraped GreatSchools directory.
- **Personal data.** DCA's spreadsheets include applicant contact emails and phone numbers; `parse_applications.py` drops them. Owner and developer names are public records and are kept.
- **Avoided.** Sources that need a login, key, or agreement: the Census API (now requires a key; bulk files are used instead), CARTO basemaps (key), NHGIS/IPUMS, LTDB, Eviction Lab, Mapbox.
- Last updated 2026-10-26.
