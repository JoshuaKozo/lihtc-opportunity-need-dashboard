# need_site/: the Need score map

A single-page map tool (no build step, no keys) that scores housing need across the 1,248 census tracts in the
11 ARC counties. `index.html` holds the page; `data.js` holds the metrics, percentiles, default weights and
tract shapes, written by `python -m pipelines.need.build_tract_need` (or `make need-data`).

```bash
make need-serve        # http://localhost:8766
```

Methods, default weights and caveats: [`docs/need_project_notes.md`](../docs/need_project_notes.md),
[`docs/need_data_risks.md`](../docs/need_data_risks.md), and the docstring and `CAVEATS` in
`pipelines/need/build_tract_need.py`. Sources: [`docs/need_data_sources.md`](../docs/need_data_sources.md).
