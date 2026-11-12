"""Download Georgia DCA's public Housing Tax Credit application and award lists.

    python -m pipelines.scrape_dca            # download anything not already cached
    python -m pipelines.scrape_dca --list     # only print what would be downloaded

DCA posts one spreadsheet per round on its public "Applications for Funding and Funding
Cycle Selections" page: every 9% applicant (winners and non-winners) and the selected/award
list, back to the early 2000s, plus 4% bond lists. No login is needed. This script reads the
page, classifies each link by year, credit type, and list kind, and caches the files in
data/raw/dca/ with a manifest. Files already on disk are never re-downloaded.

Etiquette: one request at a time, a pause between requests, a descriptive User-Agent, and
only paths that DCA's robots.txt allows.

The 2026 lists are published as Power BI dashboards rather than files; they are recorded in the
manifest with kind "powerbi" and must be exported by hand (see README).
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

LIST_PAGE = "https://dca.georgia.gov/applications-funding-and-funding-cycle-selections"
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "dca"
MANIFEST = RAW_DIR / "manifest.csv"
HEADERS = {"User-Agent": "lihtc_site_selection/0.1 (open-source research; LIHTC award analysis)"}
PAUSE_S = 1.5


def classify(text: str) -> dict:
    """Year, credit type, and list kind from a link's text."""
    t = text.lower()
    years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", text)]
    credit = "4%" if "4%" in t else ("9%" if "9%" in t else None)
    if "non-selected" in t:
        kind = "non_selected"
    elif "selected" in t or "award" in t:
        kind = "selected"
    elif "applica" in t:
        kind = "applicants"
    elif "bonds" in t:
        kind = "bonds"
    else:
        kind = "other"
    return {"year": years[0] if years else None,
            "year_end": years[1] if len(years) > 1 else None,   # e.g. "2016-2020 4% ..."
            "credit_type": credit, "kind": kind}


def find_links(session: requests.Session) -> pd.DataFrame:
    html = session.get(LIST_PAGE, headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    rows = []
    for a in main.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(LIST_PAGE, a["href"])
        is_file = "/download" in href
        is_powerbi = "powerbigov" in href
        if not (is_file or is_powerbi) or not re.search(r"(19|20)\d\d", text):
            continue
        info = classify(text)
        if is_powerbi:
            info["kind"] = "powerbi"
        slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:90]
        rows.append({"link_text": text, "url": href, **info,
                     "filename": None if is_powerbi else f"{slug}.xlsx"})
    return pd.DataFrame(rows).drop_duplicates("url").reset_index(drop=True)


def download(links: pd.DataFrame, session: requests.Session) -> pd.DataFrame:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    status = []
    for r in links.itertuples():
        if r.filename is None:
            status.append("manual_export_needed")
            continue
        path = RAW_DIR / r.filename
        if path.exists() and path.stat().st_size > 0:
            status.append("cached")
            continue
        resp = session.get(r.url, headers=HEADERS, timeout=60)
        if resp.ok:
            # Old files are .xls (BIFF) despite the shared extension; pandas sniffs the format.
            path.write_bytes(resp.content)
            status.append("downloaded")
        else:
            status.append(f"http_{resp.status_code}")
        time.sleep(PAUSE_S)
    out = links.assign(status=status)
    out["retrieved"] = pd.Timestamp.now().strftime("%Y-%m-%d")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="only list the files found")
    args = ap.parse_args()
    with requests.Session() as s:
        links = find_links(s)
        print(f"{len(links)} dated links on {LIST_PAGE}")
        print(links.groupby(["credit_type", "kind"], dropna=False).size().to_string())
        if args.list:
            print(links[["year", "credit_type", "kind", "link_text"]].to_string(index=False))
            return
        manifest = download(links, s)
    manifest.to_csv(MANIFEST, index=False)
    print(manifest["status"].value_counts().to_string())
    print(f"Manifest written to {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
