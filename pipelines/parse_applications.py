"""Normalize DCA's 9% round spreadsheets into one applications table.

    python -m pipelines.parse_applications

Input: the files cached by scrape_dca.py (data/raw/dca/manifest.csv).
Output: data/processed/applications_9pct.parquet (and .csv), one row per application, with
  - attributes from the round's applicant list (self score, address, coordinates, tract, units,
    tenancy, pool, set-aside elections, credit request), and
  - the outcome from the round's selected/award list, which since 2013 lists every application
    with its DCA score and a status code (Select, NS Tiebrk, NS NoTC$, ...).

DCA's layouts change almost every year (multi-row headers, renamed columns, swapped lat/lon), so
parsing is driven by patterns rather than fixed positions:
  1. data rows are the rows holding an application number like "2019-073";
  2. a column's name is all header text stacked above the first data row;
  3. FIELD_PATTERNS maps those names to schema fields (first match wins).
Contact emails and phone numbers are never carried into the output.
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "dca"
OUT_DIR = ROOT / "data" / "processed"
FIRST_YEAR = 2013          # first round whose award list includes non-selected applications

APP_NO = re.compile(r"^\s*(?:19|20)\d\d-\d{3}\s*$")
ARC_COUNTIES = {"Cherokee", "Clayton", "Cobb", "DeKalb", "Douglas", "Fayette", "Forsyth", "Fulton",
                "Gwinnett", "Henry", "Rockdale"}

# (field, regex on the stacked column name). Order matters: first match wins per field, and a
# column is assigned to at most one field.
FIELD_PATTERNS = [
    ("app_number", r"^app_number$"),
    ("status", r"^status|select/non-select"),
    ("dca_score", r"dca score|^new supply 9% score"),
    ("self_score", r"new (supply|affordability).*self|self.?score(?! w/out)"),
    ("self_score_alt", r"preservation.*self|rehab 9% self|^(scoring )?(htc|hud|rad)$"),
    ("project_name", r"(project|development|application|property) name|^name$"),
    ("set_aside_or_pool", r"set aside or pool"),
    ("address", r"site street address|primary street|project address|^address$"),
    ("city", r"^(project )?city$"),
    ("county", r"^(project )?county$"),
    ("zip", r"zip"),
    ("coords", r"geo coordinates$"),
    ("lat", r"latit|lattit"),
    ("lon", r"longit"),
    ("tract", r"census tract"),
    ("qct", r"^qct$"),
    ("dda", r"^dda$"),
    ("within_city_limits", r"w(ith)?/?in city lim|city limits|uninc"),
    ("pool", r"geographic pool|comp-?etitive pool"),
    ("credit_request", r"(lihtc|allocation|credit|tax credit) (amount )?request"),
    ("credit_reserved", r"resrvd|reserved|credits allocated|annual credit allocation"),
    ("units_total", r"^tot(al)?\s*units?$"),
    ("units_li", r"^(li|low.?income) units?$"),
    ("tenancy", r"tenancy|family or senior|target popu"),
    ("construction", r"primary construction"),
    ("activity_type", r"activity type for scoring|construction activity"),
    ("nonprofit_set_aside", r"non.?profit set.?aside|setaside elections (np|nonprofit)|^non-profit$"),
    ("preservation_set_aside", r"preservation set.?aside"),
    ("usda_rural", r"usda rural"),
    ("urban_rural", r"urban or rural"),
    ("developer", r"development team|^developer"),
    ("owner_principal", r"own(er|ership)( entity)? princ"),
    ("tiebreaker", r"tiebreaker"),
]
DROP = re.compile(r"email|phone|fax|cell", re.I)


def _stacked_names(df: pd.DataFrame, first_data_row: int, depth: int = 6) -> list[str]:
    """Join the header text in each column above the first data row."""
    top = df.iloc[max(0, first_data_row - depth):first_data_row]
    names = []
    for c in df.columns:
        parts = [" ".join(str(v).split()) for v in top[c] if pd.notna(v) and str(v).strip()]
        parts = [p for p in parts if not re.match(r"^(this document|selected projects|non-selected)", p, re.I)]
        names.append(" ".join(parts).strip().lower())
    return names


def read_round_sheet(path: Path) -> pd.DataFrame | None:
    """The sheet with the most application-number rows, as a frame with stacked header names."""
    best = None
    for sheet, df in pd.read_excel(path, sheet_name=None, header=None).items():
        is_app = df.apply(lambda col: col.astype(str).str.match(APP_NO))
        if not is_app.values.any():
            continue
        app_col = is_app.sum().idxmax()
        rows = is_app[app_col]
        if best is None or rows.sum() > best[2].sum():
            best = (sheet, df, rows)
    if best is None:
        return None
    sheet, df, rows = best
    first = int(np.flatnonzero(rows.to_numpy())[0])
    names = _stacked_names(df, first)
    names[list(df.columns).index(app_col)] = "app_number"   # found by its values, not its header
    data = df[rows.to_numpy()].copy()
    data.columns = [n or f"col{i}" for i, n in enumerate(names)]
    data = data.loc[:, ~pd.Index(data.columns).str.contains(DROP)]
    data.attrs["sheet"] = sheet
    return data


def map_fields(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    used, out, mapping = set(), {}, {}
    for field, pat in FIELD_PATTERNS:
        for i, col in enumerate(raw.columns):
            if i in used or not re.search(pat, col, re.I):
                continue
            out[field] = raw.iloc[:, i].to_numpy()
            mapping[field] = col
            used.add(i)
            break
    # Award lists sometimes leave the status column unlabeled: find it by its values.
    if "status" not in out:
        for i, col in enumerate(raw.columns):
            s = raw.iloc[:, i].astype(str)
            if i not in used and s.str.match(r"^(Select|Non-?Select|NS|Withdr|Partial|Pending)", case=False).mean() > 0.8:
                out["status"], mapping["status"] = s.to_numpy(), col
                break
    return pd.DataFrame(out), mapping


def _fix_coords(df: pd.DataFrame) -> pd.DataFrame:
    """Coordinates come as one 'lat, lon' text column or two columns in either order."""
    if "coords" in df:
        pairs = df.pop("coords").astype(str).str.findall(r"-?\d+\.\d+")
        has = pairs.map(len) >= 2
        df.loc[has, "lat"] = pairs[has].map(lambda p: p[0])
        df.loc[has, "lon"] = pairs[has].map(lambda p: p[1])
    for c in ("lat", "lon"):   # values like 32.141863N, 81.621278W, '-84.27866
        num = df.get(c).astype(str).str.extract(r"(-?\d+\.\d+)")[0]
        df[c] = pd.to_numeric(num, errors="coerce")
    swap = df["lat"] < 0                      # Georgia: lat 30–35, lon −86 to −80
    df.loc[swap, ["lat", "lon"]] = df.loc[swap, ["lon", "lat"]].to_numpy()
    df.loc[df["lon"] > 0, "lon"] *= -1        # a few rows drop the minus sign
    bad = ~(df["lat"].between(30, 35.1) & df["lon"].between(-86, -80.7))
    df.loc[bad, ["lat", "lon"]] = np.nan
    return df


STATUS_REASONS = [  # (regex on DCA's status code, outcome, reason); first match wins
    (r"^select", "selected", "selected"),
    (r"withdr", "withdrawn", "withdrawn"),
    (r"mkt", "not_selected", "market_study"),
    (r"partial score", "not_selected", "partial_score"),
    (r"notc|norur", "not_selected", "funds_exhausted"),
    (r"geo", "not_selected", "geographic_limit"),
    (r"recapture", "other", "recapture"),
    (r"awdlim", "not_selected", "owner_award_limit"),
    (r"2\+phases", "not_selected", "phasing_rule"),
    (r"home", "not_selected", "home_funds"),
    (r"tie", "not_selected", "tiebreaker"),
    (r"pending", "pending", "pending"),
    (r"non-?select$|^ns$", "not_selected", "score_or_threshold"),
]


def classify_status(s) -> tuple:
    """DCA status code -> (outcome, reason), e.g. 'NS Tiebrk' -> ('not_selected', 'tiebreaker')."""
    s = str(s).strip().lower()
    for pat, outcome, reason in STATUS_REASONS:
        if re.search(pat, s):
            return outcome, reason
    return None, None


def parse_round(year: int, manifest: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    files = manifest[(manifest.credit_type == "9%") & (manifest.year == year)]
    log, frames = [], {}
    for kind in ("applicants", "selected"):
        cand = files[files.kind == kind].sort_values("link_text")
        if cand.empty:
            continue
        r = cand.iloc[-1] if kind == "applicants" else cand.iloc[0]   # prefer latest revision
        raw = read_round_sheet(RAW_DIR / r.filename)
        if raw is None:
            log.append({"year": year, "kind": kind, "file": r.filename, "note": "no application rows"})
            continue
        df, mapping = map_fields(raw)
        df["app_number"] = df["app_number"].astype(str).str.strip()
        df = df.drop_duplicates("app_number")
        frames[kind] = df
        log.append({"year": year, "kind": kind, "file": r.filename, "sheet": raw.attrs["sheet"],
                    "rows": len(df), "fields": ", ".join(f"{k}<-[{v[:40]}]" for k, v in mapping.items())})
    apps, sel = frames.get("applicants"), frames.get("selected")
    if apps is None and sel is None:
        return pd.DataFrame(), log
    if apps is None:
        merged = sel
    elif sel is None:
        merged = apps
    else:
        keep = ["app_number"] + [c for c in sel.columns if c not in apps.columns or c in
                                 ("status", "dca_score", "credit_reserved", "set_aside_or_pool", "tiebreaker")]
        merged = apps.drop(columns=[c for c in keep if c != "app_number" and c in apps.columns]) \
                     .merge(sel[keep], on="app_number", how="outer", indicator="in_lists")
    merged.insert(0, "round_year", year)
    return merged, log


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = _fix_coords(df)
    for c in ("self_score", "dca_score", "credit_request", "credit_reserved", "units_total", "units_li"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("project_name", "address", "city", "county", "pool", "tenancy", "construction",
              "activity_type", "developer", "owner_principal", "status", "set_aside_or_pool"):
        if c in df:
            df[c] = df[c].where(df[c].isna(), df[c].astype(str).str.strip())
    df["county"] = df["county"].str.replace(r"\s+county$", "", case=False, regex=True).str.title() \
                               .str.replace("Dekalb", "DeKalb")
    df["tract"] = df.get("tract").astype(str).str.extract(r"(\d{11})")[0] if "tract" in df else None
    out = df["status"].map(classify_status)
    df["outcome"], df["nonselect_reason"] = zip(*out)
    df["selected"] = df["outcome"].eq("selected")
    df["in_arc"] = df["county"].isin(ARC_COUNTIES)
    if "self_score_alt" in df:   # preservation-track applications report a separate self score
        df["self_score"] = df["self_score"].fillna(pd.to_numeric(df.pop("self_score_alt"), errors="coerce"))
    df["score_gap"] = df["dca_score"] - df["self_score"]          # points DCA took away (≤ 0)
    if "zip" in df:
        df["zip"] = df["zip"].astype(str).str.extract(r"(\d{5})")[0]
    # Remaining mixed-type columns (Yes/1/True, notes) are kept as text.
    for c in df.columns[df.dtypes.eq(object)]:
        df[c] = df[c].map(lambda v: None if pd.isna(v) else str(v).strip())
    df["app_id"] = df["app_number"]
    order = ["app_id", "round_year", "project_name", "status", "outcome", "nonselect_reason",
             "selected", "self_score", "dca_score", "score_gap"]
    return df[order + [c for c in df.columns if c not in order and c != "app_number"]]


def main():
    warnings.filterwarnings("ignore")
    manifest = pd.read_csv(RAW_DIR / "manifest.csv")
    years = sorted(int(y) for y in manifest.query("credit_type == '9%'").year.dropna().unique()
                   if y >= FIRST_YEAR)
    rounds, logs = [], []
    for y in years:
        df, log = parse_round(y, manifest)
        logs += log
        if not df.empty:
            rounds.append(df)
    apps = clean(pd.concat(rounds, ignore_index=True))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    apps.to_parquet(OUT_DIR / "applications_9pct.parquet", index=False)
    apps.to_csv(OUT_DIR / "applications_9pct.csv", index=False)
    pd.DataFrame(logs).to_csv(OUT_DIR / "applications_9pct_parse_log.csv", index=False)
    print(f"{len(apps):,} applications, {apps.round_year.nunique()} rounds "
          f"({apps.round_year.min()}–{apps.round_year.max()}); {apps.in_arc.sum():,} in the ARC region")
    print(apps.groupby("round_year").agg(apps=("app_id", "size"), selected=("selected", "sum"),
                                         dca_score=("dca_score", "count"), self_score=("self_score", "count"),
                                         coords=("lat", "count"), arc=("in_arc", "sum")).to_string())


if __name__ == "__main__":
    main()
