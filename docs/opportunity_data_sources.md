# Opportunity score: data sources

Every source behind the Opportunity score. Fully public with no login, except two that are **not used in the tool**:

- **Child Opportunity Index 3.0** needs a free account. It is used only to *validate* the score, its files are never committed,
  and nothing in the tool reads it.
- **FCC broadband** is public but the site blocks scripted downloads, so it can't be rebuilt automatically. It is kept
  out of the combined tool; in the Opportunity module it is an optional metric with no default weight.

| Source | Publisher | Access | License / terms | Used for | Status |
|---|---|---|---|---|---|
| [LEHD LODES 8](https://lehd.ces.census.gov/data/) WAC + block crosswalk | U.S. Census Bureau | Public download | Public domain | Jobs within 5 mi and 5-year growth | **In use** (`pull_opportunity.py`) |
| [Smart Location Database v3](https://www.epa.gov/smartgrowth/smart-location-mapping) | U.S. EPA | Public download | Public domain | Transit/auto job access, walkability | **In use** (`pull_opportunity.py`) |
| [Opportunity Atlas](https://opportunityinsights.org/data/) tract outcomes + 2010→2020 crosswalk | Opportunity Insights | Public CSV download | Free public data, cite the authors | Upward mobility; tract crosswalk | **In use** (`pull_opportunity.py`) |
| [2020 tract population centroids; ZCTA↔tract relationship file](https://www.census.gov/geographies/reference-files.html) | U.S. Census Bureau | Public download | Public domain | Distances; ZCTA rollup | **In use** (`pull_opportunity.py`) |
| [PLACES tract estimates (2025 release)](https://data.cdc.gov/resource/yjkw-uj5s) | CDC | Public API, no key | Public domain | Health insurance, health | **In use** (`pull_opportunity.py`) |
| [Food Access Research Atlas: SNAP Retailer Access Map (2025)](https://www.ers.usda.gov/data-products/food-access-research-atlas/download-the-data) | USDA ERS | Public download | Public domain | Food access | **In use** (`pull_opportunity.py`) |
| [Health center service delivery sites](https://data.hrsa.gov/data/download) | HRSA | Public download | Public domain | Distance to FQHC | **In use** (`pull_opportunity.py`) |
| [AFFH opportunity indices](https://hudgis-hud.opendata.arcgis.com/) (AFFHT0006) | HUD | Public ArcGIS API | Public domain | Environmental health; reference indices | **In use** (`pull_opportunity.py`) |
| [MARTA GTFS](https://itsmarta.com/app-developer-resources.aspx) | MARTA | Public download | MARTA developer terms | Rail and stop access | **In use** (`pull_opportunity.py`) |
| [Child Opportunity Index 3.0](https://www.diversitydatakids.org/child-opportunity-index) | diversitydatakids.org (Brandeis) | Free account, manual download | Redistribution terms not yet checked | Benchmark for the opportunity score | **In use locally** (not committed) |
| [Broadband Data Collection: fixed Served/Unserved, Georgia](https://broadbandmap.fcc.gov/data-download) | FCC | Public, but the site blocks scripts: manual download | Public domain | Broadband coverage (reference, not scored) | **In use** (manual file) |
| [OpenStreetMap](https://www.openstreetmap.org/) parks via the Overpass API | OpenStreetMap contributors | Public API, no key | ODbL 1.0: attribute "© OpenStreetMap contributors"; the derived `park_dist_mi` column is shared under the same license | Optional park-access metric | **In use** (`pull_opportunity.py`) |
| [Photon geocoder](https://photon.komoot.io) (komoot), OpenStreetMap data | komoot / OpenStreetMap contributors | Public API, no key, fair use | ODbL (OSM data); results shown, not stored | Address suggestions while typing in the web module | **In use** (web module only) |
| SNAP Retailer Access Map, General Tract Characteristics (poverty, income, SNAP, race) | USDA ERS | Public download | Public domain | Need-overlap and fairness checks only (`verify_opportunity.py`); not scored | **In use** |
| County population estimates base 2020 | Census PEP | Public download | Public domain | Population cross-check only | **In use** |
| Public school locations and characteristics (EDGE / CCD ArcGIS service) | NCES | Public API, no key | Public domain | School points | **In use** (`pull_opportunity.py`) |
| [Georgia Milestones EOG/EOC, graduation rates](https://download.gosa.ga.gov/) | GA Governor's Office of Student Achievement | Public download | Georgia public records | School proficiency | **In use** (`pull_opportunity.py`) |

Planned or to do (from the Opportunity branch): CCRPI and school attendance zones for zoned-school quality.
EPA's EJScreen was taken offline in 2025; HUD's environmental health index stands in for it.
