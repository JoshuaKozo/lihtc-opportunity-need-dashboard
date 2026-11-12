# Data risks: housing-need score geography

Notes from scoping which Census geographies the need score can use (October 2026). Each risk is
followed by what it means for us.

## 1. No public income data below the block group

**Risk.** ACS income, rent burden and employment stop at the block group (about 600–3,000 people).
The 2020 Census goes down to the block, but it has no income or employment data. Street-level
income would have to be modeled (e.g., spreading block-group estimates onto residential parcels).

**What it means for us.**
- Block group is the finest grain for the need score.
- Any parcel- or street-level income shown in the app must be labeled as an estimate.
- LEHD LODES (low-wage jobs by home block) is the one employment source finer than the block group.

## 2. Block-group estimates are noisy

**Risk.** ACS 5-year margins of error at the block group can be as large as the estimate.

**What it means for us.**
- Compute the coefficient of variation (MOE / 1.645 / estimate) for every input.
- Flag or merge block groups above roughly 30–40% CV on key variables so the score doesn't rank noise.

## 3. Inputs come at different geographies

**Risk.** ACS is block group, CHAS / QCT / eviction data are tract, Small Area DDAs and ALICE are
ZCTA, and LODES is block. Mixing them carelessly double-counts or misplaces need.

**What it means for us.**
- Pick one scoring grain (block group recommended) and document how each source is assigned to it
  (aggregate up from blocks; assign tract values down to the block groups inside each tract).
- Make the join to the suitability tool at that same grain.

## 4. ZIP codes are not Census geographies

**Risk.** The Census publishes ZCTAs, which approximate USPS ZIPs. Most residential ZIPs match a
ZCTA with the same five digits, but:
- PO box and single-organization ZIPs (e.g., 30301) have no ZCTA and no data.
- ZIPs created or redrawn after 2020 don't exist as ZCTAs (ZCTAs are fixed to the 2020 Census).
- Boundaries don't line up exactly, so addresses near edges can land in a neighboring ZCTA.

**What it means for us.**
- ZIP input: match directly to the ZCTA list, fall back to the UDS Mapper ZIP-to-ZCTA crosswalk,
  and tell the user when a ZIP has no Census data instead of returning a blank score.
- Address or site input: geocode to lat/lon and look up the ZCTA, tract and block group directly.
  This is more accurate and fits the existing `lat` / `lon` columns.

## 5. ZCTAs are coarse and limited

**Risk.** An Atlanta ZCTA often holds 30,000–50,000 people (10–20 tracts). ZCTA data exists only in
the ACS 5-year, not in CHAS or QCT designations.

**What it means for us.**
- Use ZIP / ZCTA as a filter and summary layer in the app, not as the scoring unit.

## 6. Many ZCTAs cross county lines

**Risk.** 155 ZCTAs touch the 11 ARC counties, but only 137 are mostly inside them; 77 span more
than one county. (29-county MSA: 237 touch, 216 mostly inside.) Source:
`data/processed/metro_atlanta_zctas.csv`, built from the Census 2020 ZCTA-to-county file.

**What it means for us.**
- Use the 137 majority-inside set as the working list and state the rule.
- The split is by **land area, not population**. A few rural edge ZCTAs may be misassigned; redo
  the split with population once we have Census API access.

## 7. Census API now requires a key

**Risk.** api.census.gov redirects requests without a key, so we haven't yet confirmed which ZCTAs
have published ACS values. Some tiny or non-residential ZCTAs will come back blank.

**What it means for us.**
- Get a free key (api.census.gov/data/key_signup.html) and check coverage for all 237 ZCTAs.

## 8. Data quality: ZIP 31206 in the ARC set

**Risk.** One application in `applications_9pct.csv` flagged `in_arc` has ZIP 31206, which is a
Macon ZIP. Either the ZIP or the `in_arc` flag is wrong.

**What it means for us.**
- Check that row against the source application before it feeds any model.

## 9. College students inflate need in campus ZCTAs

**Risk.** Students living off campus count as low-income, part-time households in the ACS. In
`zcta_need_metrics_acs2024.csv`, campus ZCTAs top the poverty and part-time lists: 30303 and 30313
(Georgia State), 30332 (Georgia Tech), 30322 (Emory), 30118 (West Georgia). 30303 shows 60% poverty
and suppressed median income.

**What it means for us.**
- Flag campus ZCTAs, or adjust them using the share of the population enrolled in college (ACS B14007),
  so they don't outrank areas with long-term family housing need.

## 10. Overcrowding is unreliable at the ZCTA level

**Risk.** Overcrowding is rare, so its estimates are noisy: 44 of the 137 ARC ZCTAs have a CV above 40%.

**What it means for us.**
- Give overcrowding a low weight or use it only as a tract-level input.
