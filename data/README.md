# data/

| Folder | What | In git? |
|---|---|---|
| `processed/` | Shared: DCA applications 2013–2025, parsed and SiteLine-scored (`applications_9pct*`). | Yes |
| `processed/fundability/` | The Fundability Map's tables: SiteLine grid and tract scores, the tract table, backtest inputs, audit. | Yes |
| `raw/` | Downloads: DCA spreadsheets (`raw/dca/`), HUD, FEMA, Census, and Atlanta files. Re-fetched by `make data` and `make fundability-data`. DCA's spreadsheets include applicant emails and phone numbers, which the processed tables drop. | Only `raw/dca/manifest.csv` |

Columns are described in [`docs/fundability_data_dictionary.md`](../docs/fundability_data_dictionary.md); sources are
indexed in [`DATA_SOURCES.md`](../DATA_SOURCES.md).
