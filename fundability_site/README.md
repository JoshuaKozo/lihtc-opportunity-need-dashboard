# fundability_site/: the Fundability Map

The website, as published. Plain HTML/JS/CSS with no build step and no keys. Serve this folder over http
(browsers block `fetch` from `file://`):

```bash
python3 -m http.server 8765 --directory fundability_site     # or: make fundability-serve
```

Then open http://localhost:8765. It works as-is on GitHub Pages with this folder as the site root.

## The screen

| Part | What it does |
|---|---|
| **Score a site** (left column) | Look up an address (OpenStreetMap Nominatim, or type `lat, lon`) or click the map. Shows the chance of a 9% award, the estimated score (location + deal) against the latest funding line and next round's typical applicant, the unchecked deal items worth the most there, the FEMA flood zone at the spot, SiteLine's location points by QAP section with reasons, and context. Before anything is scored it shows the funding lines every site is compared against. |
| **Map** (center) | Tracts in five classes of one hue by SiteLine's location points (best spot in each tract). |
| **Deal checklist** (right rail, collapsible) | The QAP's developer criteria, grouped by what they're about, as checkboxes, choices and counters with their points. It starts blank; until it's filled in, estimates assume a typical funded deal (one click fills one in). Answers stay in the browser. Once it's filled in, **Top tracts for your deal** appears under it: each tract's chance at its best spot with your deal points; **View all** lists 50 with pool and county filters and a CSV download. |
| **Methodology** (left menu) | How it works, the QAP scoring systems, each metric, why these weights (backtest and metric audit), the chance-of-award model, data and sources (with downloads), and limits. |

Addresses: `#site=lat,lon` scores a spot (share a result as a link); `#about/<view>` opens the methodology at
`how`, `scoring`, `metrics`, `weights`, `chance`, `data`, or `limits`; `?theme=light` or `?theme=dark` pins the color scheme.

A lookup uses SiteLine's nearest 0.25-mile grid point (within about 0.18 mi), not an exact rescoring of the
address; for an exact location score, run SiteLine's `score_site(lat, lon)`.

## Files

| File | What it is |
|---|---|
| `index.html`, `style.css`, `app.js` | The page. Deal-checklist point rules (QAP developer criteria) are in `app.js` (`DEAL`) |
| `metrics.json` | **The metric registry.** Label, column, QAP section and page, source, direction, scaling, and whether a metric is scored or context |
| `data/tracts.geojson` | 1,248 ARC tracts with every metric column (`pipelines/fundability/build_fund_features.py`) |
| `data/grid.json` | SiteLine section scores at 52,369 grid points (same script) |
| `data/grid_detail.json` | The reasons behind each grid point's points; loaded on the first lookup |
| `data/win_model.json` | Win-probability model (`pipelines/fundability/win_probability.py`) |
| `data/funding_lines.json` | Funding lines by round and pool, and deal points of funded ARC applications (`pipelines/fundability/funding_lines.py`) |
| `data/backtest.json` | Weighting backtest (`pipelines/fundability/backtest.py`) |
| `data/metric_audit.json` | Metric audit (`pipelines/fundability/metric_audit.py`) |
| `data/sources.json` | Every data source; Methodology → Data and sources and `docs/fundability_data_sources.md` are built from it |
| `data/downloads/` | CSV copies of the processed tables (`pipelines/fundability/export_downloads.py`) |

Column definitions are in [`docs/fundability_data_dictionary.md`](../docs/fundability_data_dictionary.md).

## Add a metric

1. Compute a column per tract (keyed on `geoid`) in `pipelines/fundability/build_fund_features.py`, and re-run it.
2. Add an entry to `metrics.json` (copy an existing one). Key fields: `column`, `group` (`siteline` = scored,
   `context` = shown only), `qap_section`, `qap_ref`, `direction`, `scale` (`points`, `percentile`, or
   `flag`), `missing`, `in_fundability`, `weight`, `measures`, `targets`.
3. Test it against past awards with `pipelines/fundability/metric_audit.py` before scoring it.
4. Reload the page.

## Rebuild the data

`make fundability-data` runs every step (about five minutes; downloads are cached). The basemap is OpenStreetMap's
standard tiles (no key; fine for light use, self-host for heavy use).

## Design

A quiet working screen: ink and grey chrome, flat surfaces separated by 1px lines, Geist type, and one blue kept for the
data (the map's tracts, the chance of an award, the estimate against the funding line). No gradients, glass, or resting
shadows; only what floats over the map (legend, tooltips) and the menu cast one. Light and dark follow the system.
Motion is short and interruptible (press feedback, numbers that glide from their current value, the map flying to a
looked-up address, sheets that slide from their own edge) and honors `prefers-reduced-motion` and `prefers-contrast`.
