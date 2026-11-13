# combined_site/: the LIHTC Opportunity & Need Dashboard

The team's combined tool. A nonprofit slides between **housing need** and **resident opportunity**, sees
each tract's **chance of a 9% LIHTC award** beside it, and gets a ranked list of tracts worth a closer look.
Static HTML/JS/CSS, no build step, no keys.

```bash
make combined-serve      # http://localhost:8768/combined_site/ (serves the repo root so the link to the Fundability Map works)
```

## What's on the screen

| Part | What it does |
|---|---|
| **Left side** | Address search with suggestions and the **priority** slider (need ↔ opportunity), then two links, each showing its live number, to two pages. **Estimated DCA score**: location points at the selected site plus deal points, out of 98, against the funding line, with the chance of an award; it starts from a typical funded deal (48.5), and a deal points slider gives a quick what-if. **Your deal checklist**, which opens as a page of its own over the map (`#checklist`, with Back to the map and the browser's Back button): the QAP's developer criteria grouped by topic, one row per item to check off (choices as dropdowns), details behind each item's arrow, and the running score kept in view; once started it sets the points and the slider locks. Same rules as the Fundability Map, and the two tools share the answers (browser storage key `fundability-deal-v1`). |
| **Map** (center) | Tracts in fifths of priority, in one hue. Tracts not scored are grey. |
| **Right side** (collapsible) | **Top tracts**: a link to the metric weights, the Need × Opportunity chart (every tract; the top 15 highlighted), the ranked list, and a CSV download. Looking up an address, clicking the map or picking a tract opens **the selected site** here instead: the chance of an award in plain words (the scoring details fold away), need and opportunity in plain terms, and each score's categories, with every measure one click deeper. "Top tracts" goes back to the list. |
| **Shortlist and compare** (right side) | **Save to shortlist** on any open site (a tract, a looked-up address or a clicked spot) keeps it in a list with a numbered pin on the map, up to 12, stored in the browser (`site-finder-shortlist-v1`). Each entry shows its chance of an award, priority, need and opportunity at the current settings. **Compare** puts up to 4 side by side: the award estimate, priorities, every Need and Opportunity category, and the place, with the highest value in each row marked. |
| **Settings** (left menu) | **Metric weights**: change how much each metric counts toward the Need and Opportunity scores. Saved in the browser. |
| **How to use** (top bar) | An 11-step guided tour of the screen. It opens on a first visit (not from a shared link) and replays from this button; an example address is pinned so there's something to look at. |
| **Methodology** (left menu) | How it works, both scores' categories and metrics (generated from the metric set, with their calibrated weights), the slider math, the chance of an award, data and sources, limits. |

Addresses: `#tract=<11-digit GEOID>` opens a tract; `#about/<view>` opens the menu at `weights`, `how`, `need`,
`opportunity`, `combining`, `fundability`, `data`, or `limits`; `?theme=light|dark` pins the color scheme.

## How it computes

- **Priority** = need<sup>(1 − t)</sup> × opportunity<sup>t</sup>, a weighted geometric mean of the two 0–100 scores,
  where t is the slider's share for opportunity. It favors tracts that are decent on both over ones strong on one
  side and weak on the other.
- **Chance of an award** = the Fundability Map's win-probability model (`data/win_model.json`, `data/funding_lines.json`)
  at the tract's best SiteLine spot, plus the deal points: the slider's value until the checklist is started, then the
  checklist's count. Two checklist answers change location points too: §XIV revitalization plans (they share the
  10-point block with §XV/§XVI/§XIX) and §XVII phasing (it replaces §XVIII when higher). So each tract carries `lx`, its
  best location points for every §XIV score 0–10 with phasing off and on. The map and the list use each tract's best spot.
- **A looked-up address or a clicked spot is scored where it is**: at its nearest point on the Fundability Map's
  0.25-mile SiteLine grid (`data/grid.json`, a slim copy of `fundability_site/data/grid.json`), with the same formula,
  so it gets the same chance the Fundability Map gives that address. The tract's best spot is shown beside it.
  `#site=<lat>,<lon>` opens a spot.
- **Custom metric weights** rebuild the scores in the browser from each metric's percentile, the same way
  `pipelines/combined/build_combined.py` does. Each metric's weight starts as its share of the score (its category's
  weight split across the category's metrics), and a category's weight becomes the sum of its metrics' weights. With
  no changes the tool shows the published scores. Only those default weights were calibrated and tested.

## Publishing as a Claude artifact

`make combined-artifact` writes `combined_site_artifact/` (ignored by git), a copy that runs inside an artifact's
sandbox: no street map, search by place instead of street address, and the CSV saved through the `downloads`
capability. Change `combined_site/`, then rebuild. See `pipelines/combined/build_artifact.py`.

## Data

`data/` is written by `make combined-site-data` (`pipelines/combined/export_site.py`) from
`data/processed/combined/`, which `make combined-data` builds from `pipelines/combined/metric_set.py`. To change
metrics or weights, edit `metric_set.py` and rerun both. See [`docs/combined_metric_set.md`](../docs/combined_metric_set.md).
