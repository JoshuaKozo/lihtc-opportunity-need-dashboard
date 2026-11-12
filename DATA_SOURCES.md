# Data sources

Each tool keeps its own list of sources. Every source must be public and need no login, data use agreement,
or paid key, so a nonprofit can clone this repository and use it right away.

| Tool | Source list |
|---|---|
| **Fundability Map** | [`docs/fundability_data_sources.md`](docs/fundability_data_sources.md) (generated from `fundability_site/data/sources.json`; also in the site's Methodology menu, under Data and sources) |
| **Need** | [`docs/need_data_sources.md`](docs/need_data_sources.md) (Census ACS summary files, HUD, TIGERweb; no keys) |
| **Opportunity** | [`docs/opportunity_data_sources.md`](docs/opportunity_data_sources.md) (Opportunity Atlas, LODES, EPA, GOSA, CDC, USDA, HUD, MARTA) |

## Shared data

| Source | Publisher | Used for | Link |
|---|---|---|---|
| Georgia DCA 9% applicant and award lists, 2013–2025 | Georgia Department of Community Affairs | `data/processed/applications_9pct*` (shared by all tools) | [link](https://dca.georgia.gov/applications-funding-and-funding-cycle-selections) |
| SiteLine StartUp pack | SiteLine (kmcalist682336) | QAP location scoring | [link](https://github.com/kmcalist682336/SitelineLIHTCStartUp) |
| 2020 ARC tract list (`data/processed/arc_tracts_2020.csv`), ZCTA↔county file (`metro_atlanta_zctas.csv`) | U.S. Census Bureau (Gazetteer, relationship files) | The 1,248 tracts every tool scores; ZIP search | [link](https://www.census.gov/geographies/reference-files.html) |
| Census Geocoder (`pipelines/assign_tracts.py`) | U.S. Census Bureau | Puts each DCA application on a 2020 tract | [link](https://geocoding.geo.census.gov/) |

## Notes

- **Personal data.** DCA's spreadsheets include applicant contact emails and phone numbers;
  `pipelines/parse_applications.py` drops them. Owner and developer names are public records and are kept.
- **SiteLine license.** SiteLine has no LICENSE file yet; it was provided to the team for this project.
  Ask its maintainer before redistributing data derived from it.
- **Validation only, never in the tools, never committed:** Child Opportunity Index 3.0 (free account) and Eviction Lab's
  tract eviction filings (download form). Only summary results of those checks are in the repo. FCC broadband (public, but not
  scriptable) stays out of the combined tool.
- **Avoided** (need a login, key, or agreement): the Census API (bulk files are used instead), CARTO basemaps,
  NHGIS/IPUMS, LTDB, Eviction Lab, Mapbox.
