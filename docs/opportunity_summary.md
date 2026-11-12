# Opportunity Tool: Summary

**What it is.** The Opportunity tool scores every populated census tract in the 11-county metro Atlanta region (1,245 tracts) from **0 to 100** on how much opportunity living there gives a resident. It's one of three parts of our LIHTC site-selection tool, alongside **Need** (how much the area needs affordable housing) and **Fundability** (how likely a project there is to win DCA funding). All three use the same 2020 census tracts and the same 0–100 scale, so they can be combined.

**What it measures.** Six parts, each built from public data:

- **Mobility (34%):** how kids from low-income families raised there did as adults: their adult income rank and incarceration rate. *Source: Opportunity Atlas (Opportunity Insights and the U.S. Census Bureau, built from Census and IRS tax records).*
- **Schools (27%):** reading and math proficiency at the three nearest elementary schools and the nearest middle and high school, plus the high school graduation rate. *Sources: Georgia Milestones results and graduation rates, 2024–25 (Georgia Governor's Office of Student Achievement); school locations (National Center for Education Statistics).*
- **Environment (12%):** exposure to toxic air pollution. *Source: Environmental Health Index (HUD, built from EPA air-toxics estimates).*
- **Health (11%):** distance to a community health center, the share of adults without health insurance, and the share of residents more than a mile from a grocery store. *Sources: health center sites (HRSA); PLACES 2025 (CDC); SNAP Retailer Access Map 2025 (USDA Economic Research Service).*
- **Jobs (11%):** jobs reachable within 45 minutes by transit or car; jobs within 5 miles that don't require a college degree, and those jobs per non-degree worker competing for them; and job growth 2018–2023. *Sources: LEHD LODES job data (U.S. Census Bureau); Smart Location Database (U.S. EPA).*
- **Transit (5%):** distance to the nearest MARTA rail station, MARTA stops within half a mile, and walkability. *Sources: MARTA schedule data (GTFS); National Walkability Index (U.S. EPA).*

**Geography sources:** 2020 census tracts and population centers, ZIP-code relationship files, and the Census Geocoder for address lookup (U.S. Census Bureau); the 2010-to-2020 tract crosswalk (Opportunity Insights).

**How the score is built.** Each measure is ranked against all metro tracts, so a 70 means "better than 70% of metro Atlanta". The measures combine into the six part scores, and the parts combine into one score using the weights above. Each tract also gets a tier from **Very low** to **Very high**.

**How the weights were set.** Published research set a starting weight and allowed range for each part, drawing on:

- Chetty & Hendren (2018) and Chetty, Hendren & Katz (2016, the Moving to Opportunity study) on how neighborhoods shape children's adult outcomes.
- Laliberté (2021) on schools as the main channel of neighborhood effects.
- Gobillon, Selod & Zenou (2007) on job access, and Blumenberg & Pierce (2014) on cars versus transit.
- Ludwig et al. (2012) on health, and Allcott, Diamond & Dubé on grocery access.
- Isen, Rossin-Slater & Walker (2017) on air pollution.
- The methods behind the Child Opportunity Index and California's LIHTC Opportunity Map.

Then real outcomes for each tract adjusted the weights within those ranges: kids' adult incomes (Opportunity Atlas) and life expectancy (CDC's Small-Area Life Expectancy Estimates). Finally, we tested the weights on counties they weren't fitted to.

**How we know it works:**

- **Predicts real outcomes:** kids' mobility (0.45) and life expectancy (0.52), on counties not used to fit the weights.
- **Agrees with the Child Opportunity Index 3.0** (diversitydatakids.org): 0.81.
- **Holds up:** shifting every weight up or down by half barely changes the rankings.
- **Data verified:** 81 automated checks passed with no data errors. Populations match official Census county totals, and key values were recalculated independently.

**What the tool does:**

- **Map:** every tract colored by tier, with past Georgia DCA 9% applications (2013–2025, funded and not funded) and MARTA stations.
- **Site profile:** click a tract or search an address to see its score, part scores, and the facts behind them.
- **Weights:** adjust the six weights or pick a preset such as "Families with kids" or "Seniors / no car". An advanced panel adds optional measures: parks (OpenStreetMap), broadband (FCC), and HUD's other opportunity indices.
- **Compare:** see up to four sites side by side.
- **Method:** every source with links, plus how the score was checked.
