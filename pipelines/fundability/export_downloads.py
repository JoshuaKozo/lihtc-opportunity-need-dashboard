"""Publish the project's data for download and keep the source list in one place.

    python pipelines/fundability/export_downloads.py

1. Copies the processed tables people are most likely to want into fundability_site/data/downloads/, so the
   website's Data and sources view can offer them (the repository itself may be private).
2. Rewrites docs/fundability_data_sources.md from fundability_site/data/sources.json, the single list of sources that the
   Data and sources view also reads, so the two never disagree.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
SITE = ROOT / "fundability_site"
TABLES = [PROC / "fundability" / "fundability_tract_features.csv", PROC / "applications_9pct.csv",
          PROC / "fundability" / "backtest_features.csv", PROC / "fundability" / "metric_audit.csv"]
STATUS = {"score": "Drives the score", "reference": "Rules and model data", "context": "Context (shown, not scored)",
          "planned": "Pulled for a planned check", "tested": "Tested, not used", "service": "Map and search service"}


def main() -> None:
    out = SITE / "data" / "downloads"
    out.mkdir(parents=True, exist_ok=True)
    for path in TABLES:
        shutil.copy2(path, out / path.name)
        print(f"copied {path.name} ({(out / path.name).stat().st_size / 1e3:,.0f} KB)")

    src = json.loads((SITE / "data" / "sources.json").read_text())
    lines = ["# Data sources: Fundability Map", "",
             "Every dataset behind the Fundability Map. Generated from `fundability_site/data/sources.json` by",
             "`pipelines/export_downloads.py`; edit that file, not this one. The website's Methodology menu (Data and sources) shows the same list.", "",
             "All sources are public and need no login, data use agreement, or paid key.", "",
             "| Dataset | Publisher | Used for | Vintage | Link | Terms | Role |", "|---|---|---|---|---|---|---|"]
    for s in src["sources"]:
        lines.append(f"| {s['name']} | {s['publisher']} | {s['used_for']} | {s['vintage']} | [link]({s['url']}) | "
                     f"{s['terms']} | {STATUS.get(s['status'], s['status'])} |")
    for s in src["sources"]:
        if s.get("includes"):
            lines += ["", f"**{s['name']}** brings in ([data dictionary]({s['details_url']})):", ""]
            lines += [f"- {x}" for x in s["includes"]]
    lines += ["", "## Files the project publishes", "", "Served with the website under `fundability_site/`:", "",
              "| File | Contents |", "|---|---|"]
    lines += [f"| `fundability_site/{d['file']}` | {d['what']} |" for d in src["downloads"]]
    lines += ["", "## Notes", "",
              "- **SiteLine license.** The SiteLine StartUp repository has no LICENSE file. It was provided to the team for this "
              "project; ask the maintainer to add an open license before publishing anything derived from it. Its school layer "
              "comes from a scraped GreatSchools directory.",
              "- **Personal data.** DCA's spreadsheets include applicant contact emails and phone numbers; "
              "`parse_applications.py` drops them. Owner and developer names are public records and are kept.",
              "- **Avoided.** Sources that need a login, key, or agreement: the Census API (now requires a key; bulk files are "
              "used instead), CARTO basemaps (key), NHGIS/IPUMS, LTDB, Eviction Lab, Mapbox.",
              f"- Last updated {src['updated']}.", ""]
    (ROOT / "docs" / "fundability_data_sources.md").write_text("\n".join(lines))
    print("wrote docs/fundability_data_sources.md")


if __name__ == "__main__":
    main()
