# Need score: data sources

Every source behind the Need score. All are public and need no login, data use agreement, or key.
Rebuild with `python -m pipelines.need.build_tract_need` (`--offline` reuses cached downloads in `data/raw/`).

| Source | Publisher | Access | License / terms | Used for |
|---|---|---|---|---|
| [ACS 2020–2024 5-year table-based summary files](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/) (tables B01003, B19013, B19113, C17002, B25070, B25003, B23025, B23022, B22003, B25014, B25044, B25106, B19001, B25064, B25004, B17010, B17020, C18130, C16002, B25034, B25048, B25052, B14007) | U.S. Census Bureau | Public download, no key | Public domain | Income, poverty, cost burden, rental market, vulnerable households, work, housing quality, student share |
| ACS 2017–2021 5-year B25064 | U.S. Census Bureau | Public download, no key | Public domain | Rent growth |
| [LIHTC properties, public housing buildings, HUD multifamily assisted properties, Housing Choice Vouchers by tract, 2026 QCTs](https://hudgis-hud.opendata.arcgis.com/) | HUD | Public ArcGIS services, no key | Public domain | Affordable supply; QCT shown for reference |
| [TIGERweb 2020 tract boundaries](https://tigerweb.geo.census.gov/) | U.S. Census Bureau | Public ArcGIS service, no key | Public domain | Map shapes |
| [2020 ZCTA-to-county relationship file](https://www.census.gov/geographies/reference-files.html) | U.S. Census Bureau | Public download | Public domain | ZIP search and the 137-ZCTA working set |

Caveats (margins of error, student tracts, overlapping assisted programs) are in
[`need_data_risks.md`](need_data_risks.md) and in `CAVEATS` in `pipelines/need/build_tract_need.py`.
