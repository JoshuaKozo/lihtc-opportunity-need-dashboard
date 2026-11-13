/* LIHTC Opportunity & Need Dashboard: slide between housing need and resident opportunity; see each tract's chance of a 9%
 * award beside it. Reads data/ (written by pipelines/combined/export_site.py). The metric set and its
 * placeholder weights come from pipelines/combined/metric_set.py via data/meta.json. */
"use strict";

const STORE_KEY = "site-finder-v1";
const CLASSES = 5;
const TOP_N = 15;
const state = { meta: null, feats: [], win: null, lines: null, map: null, layer: null, pin: null, selected: null,
                bias: 0.5, deal: null, dealPoints: 48.5, dealStarted: false, railView: "top",
                weights: { need: {}, opportunity: {} }, weightsOff: {}, menuView: null };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const titleCase = (s) => String(s || "").replace(/\b\w/g, (c) => c.toUpperCase());
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
const fmtInt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const tractNo = (n) => String(n || "").replace(/^Census Tract\s*/i, "Tract ");
// ?theme=light or ?theme=dark pins the color scheme (otherwise it follows the system)
{ const th = new URLSearchParams(location.search).get("theme"); if (th === "light" || th === "dark") document.documentElement.dataset.theme = th; }

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let palette = null;
function readPalette() {
  palette = { classes: Array.from({ length: CLASSES }, (_, k) => cssVar(`--c${k + 1}`)), nodata: cssVar("--nodata"),
    ink: cssVar("--text"), line: cssVar("--surface") };
}

/* ── how a tract's values read ── */
const PCT = (v) => `${(100 * v).toFixed(1)}%`;
const FORMAT = {
  unassisted_li_renter_share: PCT,
  median_hh_income: (v) => `$${fmtInt.format(v)}`, assisted_units_per_100: (v) => v.toFixed(1),
  rent_growth: (v) => `${v >= 0 ? "+" : "−"}${Math.abs(100 * v).toFixed(1)}%`,
  oa_kfr_p25: (v) => `${Math.round(100 * v)}th income rank`, oa_jail_p25: PCT,
  school_prof_elem3: (v) => `${v.toFixed(0)}%`, school_prof_middle: (v) => `${v.toFixed(0)}%`,
  school_prof_high: (v) => `${v.toFixed(0)}%`, school_grad_rate_high: (v) => `${v.toFixed(0)}%`,
  hud_env_health: (v) => `${v.toFixed(0)} of 100`, fqhc_dist_mi: (v) => `${v.toFixed(1)} mi`,
  food_low_access_share: (v) => `${v.toFixed(0)}%`, park_dist_mi: (v) => `${v.toFixed(2)} mi`,
  epa_jobs_45min_transit: (v) => fmtInt.format(v), jobs_within_5mi: (v) => fmtInt.format(v),
  epa_jobs_45min_auto: (v) => fmtInt.format(v), jobs_nondegree_5mi: (v) => fmtInt.format(v), jobs_per_nondegree_worker_5mi: (v) => `${v.toFixed(2)} per worker`, jobs_growth_5mi: (v) => `${v >= 0 ? "+" : "−"}${Math.abs(100 * v).toFixed(1)}%`,
  marta_rail_dist_mi: (v) => `${v.toFixed(1)} mi`, marta_stops_half_mi: (v) => fmtInt.format(v),
  epa_walkability: (v) => `${v.toFixed(1)} of 20`, hud_transport_cost: (v) => `${v.toFixed(0)} of 100`,
};
const fmtValue = (k, v) => (!isNum(v) ? "–" : (FORMAT[k] || PCT)(v));

/* ── metric weights: the settings let a user change them, so the scores can be rebuilt here from the metric
 * percentiles, the same way pipelines/combined/build_combined.py builds them ── */
const SCORE_FIELDS = { need: ["ns", "nc", "nm"], opportunity: ["os", "oc", "om"] };
// A metric's default weight as a share of its score: its category's weight split across the category's metrics.
function defaultWeight(cat, m) {
  const sum = cat.metrics.reduce((a, x) => a + x.weight, 0);
  return sum > 0 ? (cat.weight * m.weight) / sum : 0;
}
const metricWeight = (score, cat, m) => state.weights[score][m.key] ?? defaultWeight(cat, m);
const hasCustom = (score) => Object.keys(state.weights[score]).length > 0;
const customWeights = () => hasCustom("need") || hasCustom("opportunity");
const weightTotal = (score) => state.meta.scores[score].reduce((a, c) => a + c.metrics.reduce((b, m) => b + metricWeight(score, c, m), 0), 0);

function rankPct(values) {   // like pandas' rank(pct=True) * 100: ties share a rank, blanks stay blank
  const order = values.map((v, i) => [v, i]).filter(([v]) => v !== null).sort((a, b) => a[0] - b[0]);
  const out = values.map(() => null);
  for (let i = 0; i < order.length;) {
    let j = i;
    while (j + 1 < order.length && order[j + 1][0] === order[i][0]) j++;
    const pct = Math.round((1000 * ((i + j) / 2 + 1)) / order.length) / 10;
    for (let k = i; k <= j; k++) out[order[k][1]] = pct;
    i = j + 1;
  }
  return out;
}
// A category's metrics and a score's categories are weighted means; a missing value's weight goes to the rest.
function rebuildScore(score, feats) {
  const [fScore, fCats, fMetrics] = SCORE_FIELDS[score];
  let first = 0;
  const cats = state.meta.scores[score].map((c) => {
    const edited = c.metrics.some((m) => m.key in state.weights[score]);
    const w = c.metrics.map((m) => (edited ? metricWeight(score, c, m) : m.weight));
    const raw = feats.map((f) => {
      let num = 0, den = 0;
      w.forEach((mw, i) => {
        const pc = f.properties[fMetrics][first + i][0];
        if (mw > 0 && isNum(pc)) { num += pc * mw; den += mw; }
      });
      return den > 0 ? num / den : null;
    });
    first += c.metrics.length;
    return { weight: edited ? w.reduce((a, b) => a + b, 0) : c.weight, raw, ranks: rankPct(raw) };
  });
  const scores = rankPct(feats.map((_, i) => {
    let num = 0, den = 0;
    cats.forEach((c) => { if (c.raw[i] !== null) { num += c.raw[i] * c.weight; den += c.weight; } });
    return den > 0 ? num / den : null;
  }));
  feats.forEach((f, i) => {
    f.properties[fScore] = scores[i];
    f.properties[fCats] = cats.map((c) => c.ranks[i]);
  });
}
function applyWeights() {
  const feats = state.feats.filter((f) => f.published);
  for (const score of Object.keys(SCORE_FIELDS)) {
    const usable = weightTotal(score) > 0;
    state.weightsOff[score] = !usable;
    if (hasCustom(score) && usable) { rebuildScore(score, feats); continue; }
    const [fScore, fCats] = SCORE_FIELDS[score];
    feats.forEach((f) => { f.properties[fScore] = f.published[fScore]; f.properties[fCats] = f.published[fCats]; });
  }
}
function setWeight(score, key, value) {
  const cat = state.meta.scores[score].find((c) => c.metrics.some((m) => m.key === key));
  const m = cat.metrics.find((x) => x.key === key);
  if (Math.abs(value - defaultWeight(cat, m)) < 0.05) delete state.weights[score][key];
  else state.weights[score][key] = value;
  applyWeights();
  update();
}

/* ── scores ── */
// The slider blends Need and Opportunity with a weighted geometric mean of their percentiles, then re-ranks.
// At either end it is one score alone; in between, a tract weak on one side can't hide behind the other.
function computePriority() {
  state.feats.forEach((f) => { f.pr = null; });
  const t = state.bias, scored = state.feats.filter((f) => isNum(f.properties.ns) && isNum(f.properties.os));
  scored.forEach((f) => {
    const p = f.properties;
    f.raw = Math.exp((1 - t) * Math.log(Math.max(p.ns, 0.5) / 100) + t * Math.log(Math.max(p.os, 0.5) / 100));
    f.blend = 100 * f.raw;   // what the tool shows as "priority": 0-100, between the two scores
  });
  scored.sort((a, b) => a.raw - b.raw);
  let i = 0;
  while (i < scored.length) {   // ties share a rank
    let j = i;
    while (j + 1 < scored.length && scored[j + 1].raw === scored[i].raw) j++;
    const pr = (100 * ((i + j) / 2 + 1)) / scored.length;
    for (let k = i; k <= j; k++) scored[k].pr = pr;
    i = j + 1;
  }
  scoreMemo = new Map();
  state.feats.forEach((f) => { f.chance = chanceFor(f.properties); });
}

function winProb(score, pool) {   // the Fundability Map's model (pipelines/fundability/win_probability.py)
  const w = state.win;
  if (!w || !isNum(score)) return null;
  const P = w.next_round.pools[titleCase(pool)];
  if (!P) return null;
  const b = w.coef.margin, sd = w.next_round.drift_sd;
  const eta = w.coef.intercept + b * (score - (P.field_median + w.next_round.drift_mean));
  return 1 / (1 + Math.exp(-eta / Math.sqrt(1 + (Math.PI * b * b * sd * sd) / 8)));
}

/* ── the deal checklist: the QAP's developer criteria, the same rules and stored answers as the Fundability Map ── */
// Shared localStorage key with fundability_site/, so answers filled in either tool carry over (same origin).
const DEAL_KEY = "fundability-deal-v1";
const RO = (label, pts) => [label, pts];
const DEAL = [
  { sec: "VIII", title: "Readiness to Proceed", max: 13, ref: "p. 92", items: [
    { id: "r_site", type: "check", label: "Site control and zoning documents in hand", pts: 5 },
    { id: "r_water", type: "check", label: "Public water / sanitary sewer documentation (full point if not applicable)", pts: 1 },
    { id: "r_util", type: "check", label: "Operating utilities documentation (full point if not applicable)", pts: 1 },
    { id: "r_plan", type: "check", label: "Site information and conceptual site plan", pts: 1 },
    { id: "r_fin", type: "check", label: "Preliminary financing commitments", pts: 1 },
    { id: "r_esa", type: "check", label: "Phase I Environmental Site Assessment", pts: 1 },
    { id: "r_gc", type: "check", label: "General contractor's cost estimate", pts: 1 },
    { id: "r_mkt", type: "check", label: "Market study", pts: 1 },
    { id: "r_pna", type: "check", label: "Physical needs assessment (rehab only; new construction gets the point)", pts: 1 } ] },
  { sec: "VI", title: "Compliance Performance", max: 10, ref: "p. 89", note: "Starts at 10; can go negative.", items: [
    { id: "c_sae", type: "number", label: "Significant Adverse Events with waivers (−5 each)", min: 0, max: 5 },
    { id: "c_ac", type: "number", label: "Unique Adverse Circumstances (−2 each)", min: 0, max: 10 },
    { id: "c_props", type: "radio", label: "LIHTC/HOME properties the team developed and still owns (adds points back, up to 10)",
      options: [RO("Fewer than 7", 0), RO("7", 1), RO("8–10", 2), RO("11–15", 3), RO("16–20", 4), RO("More than 20", 5)] } ] },
  { sec: "III", title: "Extended Affordability Commitment", max: 6, ref: "p. 84", items: [
    { id: "e_qc", type: "radio", label: "Qualified Contract waiver, or resident ownership",
      options: [RO("None", 0), RO("Waive the Qualified Contract option entirely", 5), RO("Waive it for at least 10 years", 3),
        RO("Waive it for at least 5 years", 1), RO("Resident ownership plan instead (single-family only)", 1)] },
    { id: "e_rofr", type: "check", label: "Right of first refusal to a nonprofit or housing authority (only with a waiver)", pts: 1 } ] },
  { sec: "V", title: "Favorable Financing", max: 5, ref: "p. 86", items: [
    { id: "f_amt", type: "radio", label: "New qualifying loans or grants (HOME, CDBG, FHLB AHP, BeltLine, foundation, other government)",
      options: [RO("None", 0), RO("$500K–$1M total, or $10–20K per unit", 1), RO("$1–2M, or $20–30K per unit", 2), RO("$2M or more, or $30K+ per unit", 3)] },
    { id: "f_nhtf", type: "check", label: "That money is National Housing Trust Fund or HOME-ARP (+1)", pts: 1 },
    { id: "f_cost", type: "radio", label: "Property cost reduction",
      options: [RO("None", 0), RO("Private Enterprise Agreement lowering property taxes", 0.5), RO("45+ year ground lease at ≤$100/yr from a PHA, government, or land trust", 1)] } ] },
  { sec: "VII", title: "Integrated Supportive Housing", max: 5, ref: "p. 90", items: [
    { id: "s_ref", type: "radio", label: "Referrals or tenant preference (pick one)",
      options: [RO("None", 0), RO("Supportive-housing referrals for ≥10% of units", 2), RO("Referrals plus project-based rental assistance (30% AMI units)", 3),
        RO("PHA Section 8 PBRA with the Settlement Agreement tenant preference", 1)] },
    { id: "s_pbra", type: "check", label: "No DCA rental-assistance penalty on past properties (DCA assigns these automatically)", pts: 2, def: true } ] },
  { sec: "IX", title: "Deeper Targeting", max: 3, ref: "p. 94", items: [
    { id: "t_deep", type: "radio", label: "Rent and income targeting",
      options: [RO("None", 0), RO("Average income at or below 58% AMI", 2), RO("Project-based rental assistance on ≥20% of units", 3)] } ] },
  { sec: "X", title: "HCV and PHA Notices", max: 2, ref: "p. 95", items: [
    { id: "hcv", type: "check", label: "Register with the voucher administrator and notify the PHA", pts: 2, def: true } ] },
  { sec: "IV", title: "Minority- and Women-Owned Business Engagement", max: 2, ref: "p. 85", items: [
    { id: "mbe", type: "check", label: "Engage qualified MBE/WBE firms (~10% of hard costs), or one is on the team", pts: 2 } ] },
  { sec: "XXIII", title: "Enriched Property Services", max: 2, ref: "p. 118", items: [
    { id: "svc", type: "check", label: "Owner-provided (CORES-certified) or third-party resident services", pts: 2 } ] },
  { sec: "XXI", title: "Mixed Income", max: 1, ref: "p. 116", items: [
    { id: "mix", type: "check", label: "10%+ market-rate units, or the income-averaging election", pts: 1 } ] },
  { sec: "XXIV", title: "DCA Community Initiatives (GICH)", max: 2, ref: "p. 119", items: [
    { id: "gich", type: "check", label: "Support letter from a GICH community team", pts: 2 } ] },
  { sec: "XX", title: "Economic Development Proximity", max: 1, ref: "p. 116", pools: ["other metro"], items: [
    { id: "econ", type: "check", label: "A major job-creating project within commuting distance (Other Metro pool only)", pts: 1 } ] },
  { sec: "XIV", title: "Revitalization / Redevelopment Plans", max: 10, ref: "p. 104", block: true,
    note: "Shares the 10-point block with §XV, §XVI, §XIX: the estimate uses whichever is higher at the site.", items: [
    { id: "v_crp", type: "check", label: "Site is in a qualifying local Community Revitalization Plan area (2; +1 if the tract is a QCT)", pts: 2 },
    { id: "v_bp", type: "radio", label: "Plan best practices (only with a qualifying plan)",
      options: [RO("None", 0), RO("Public input during the plan", 1), RO("Infrastructure assessment and implementation measures", 2),
        RO("Both, or adopted by a PlanFirst / RAS / Rural Zone community", 3)] },
    { id: "v_ct", type: "radio", label: "Community Transformation (quarterback board, partnerships)", options: [RO("None", 0), RO("1", 1), RO("2", 2), RO("3", 3)] },
    { id: "v_ci", type: "radio", label: "Community investment (third-party capital, local fee waivers)", options: [RO("None", 0), RO("1", 1), RO("2", 2), RO("3", 3)] } ] },
  { sec: "XVII", title: "Phased Development", max: 4, ref: "p. 112", swap: true,
    note: "Replaces §XVIII Previous Projects when higher.", items: [
    { id: "phase", type: "check", label: "A later phase of a DCA-funded phased development", pts: 4 } ] },
];

// how the rail groups the QAP sections, so a nonprofit can scan it by what each part of the deal is about
const DEAL_GROUPS = [
  ["Readiness and track record", ["VIII", "VI"]],
  ["Financing and affordability", ["V", "III", "IX", "XXI"]],
  ["Residents and services", ["VII", "XXIII", "X"]],
  ["Team and local support", ["IV", "XXIV", "XX"]],
  ["Plans and phasing", ["XIV", "XVII"]],
];

function dealDefaults() {
  const a = {};
  DEAL.forEach((s) => s.items.forEach((it) => { a[it.id] = it.type === "check" ? !!it.def : 0; }));
  return a;
}
// A typical funded deal (48 points, near the 48.5 median of funded ARC applications in 2024-25): one click fills it, so a
// nonprofit can start from what winners claim and uncheck what their project won't have.
function typicalDeal() {
  return { ...dealDefaults(), r_site: true, r_water: true, r_util: true, r_plan: true, r_fin: true, r_esa: true, r_gc: true, r_mkt: true, r_pna: true,
    e_qc: 1, e_rofr: true, f_amt: 3, s_ref: 2, s_pbra: true, t_deep: 1, hcv: true, mbe: true, svc: true, mix: true, gich: true };
}
// The checklist starts blank. Until any answer is saved, the deal points slider sets the points, starting at a typical funded deal.
function loadDeal() {
  try {
    const s = JSON.parse(localStorage.getItem(DEAL_KEY));
    state.dealStarted = !!s;
    return s ? { ...dealDefaults(), ...s } : dealDefaults();
  } catch { state.dealStarted = false; return dealDefaults(); }
}
function saveDeal() { state.dealStarted = true; try { localStorage.setItem(DEAL_KEY, JSON.stringify(state.deal)); } catch { /* fine */ } }
function resetDeal() { state.dealStarted = false; state.deal = dealDefaults(); try { localStorage.removeItem(DEAL_KEY); } catch { /* fine */ } }

// points per section from the answers; pool and QCT change a couple of rules
function dealScore(a, pool = "atlanta metro", qct = false) {
  const ck = (id, pts) => (a[id] ? pts : 0), opt = (id, s) => s.items.find((i) => i.id === id).options[+a[id] || 0][1];
  const by = {};
  for (const s of DEAL) {
    let p = 0;
    if (s.sec === "VIII") p = s.items.reduce((t, it) => t + ck(it.id, it.pts), 0);
    else if (s.sec === "VI") {
      const d = 10 - 5 * (+a.c_sae || 0) - 2 * (+a.c_ac || 0);
      p = d < 10 ? Math.min(10, d + opt("c_props", s)) : 10;
    } else if (s.sec === "III") { const q = +a.e_qc || 0; p = opt("e_qc", s) + (a.e_rofr && q >= 1 && q <= 3 ? 1 : 0); }
    else if (s.sec === "V") { const f = opt("f_amt", s); p = Math.min(5, f + (a.f_nhtf && f > 0 ? 1 : 0) + opt("f_cost", s)); }
    else if (s.sec === "VII") p = opt("s_ref", s) + ck("s_pbra", 2);
    else if (s.sec === "IX") p = opt("t_deep", s);
    else if (s.sec === "XX") p = s.pools.includes(pool) ? ck("econ", 1) : 0;
    else if (s.sec === "XIV") p = Math.min(10, (a.v_crp ? 2 + (qct ? 1 : 0) + opt("v_bp", s) : 0) + opt("v_ct", s) + opt("v_ci", s));
    else p = s.items.reduce((t, it) => t + (it.type === "check" ? ck(it.id, it.pts) : 0), 0);
    by[s.sec] = p;
  }
  const other = DEAL.filter((s) => !s.block && !s.swap).reduce((t, s) => t + by[s.sec], 0);
  const max = DEAL.filter((s) => !s.block && !s.swap && (!s.pools || s.pools.includes(pool))).reduce((t, s) => t + s.max, 0);
  return { by, other, max, xiv: by.XIV, phased: by.XVII };
}
const typicalPoints = () => state.lines?.deal_points_of_funded_arc?.median ?? 48.5;
const usingTypical = () => !state.dealStarted && state.dealPoints === typicalPoints();

// A tract's estimated DCA score at its best spot. Location points come from the export's "lx": the tract's best
// grid point for each §XIV score (0-10) with phasing off and on, because those two answers change location points
// (§XIV shares the 10-point block with §XV/§XVI/§XIX; §XVII phasing replaces §XVIII when higher).
let scoreMemo = new Map();
function chanceParts(p) {
  if (!Array.isArray(p.lx)) return null;
  if (!state.dealStarted) {
    const loc = p.lx[0], deal = state.dealPoints;
    return { loc, deal, total: loc + deal, typical: usingTypical(), lift: 0 };
  }
  const k = `${p.pool}|${p.qct}`;
  if (!scoreMemo.has(k)) scoreMemo.set(k, dealScore(state.deal, p.pool, p.qct === 1));
  const sc = scoreMemo.get(k), loc = p.lx[(sc.phased > 0 ? 11 : 0) + Math.min(10, Math.round(sc.xiv))];
  return { loc, deal: sc.other, total: loc + sc.other, typical: false, lift: loc - p.lx[0], sc };
}
const chanceFor = (p) => { const e = chanceParts(p); return e ? winProb(e.total, p.pool) : null; };

// A looked-up address (or a clicked spot) is scored where it is: its nearest point on the Fundability Map's
// 0.25-mile SiteLine grid (data/grid.json), with the same formula the Fundability Map uses there.
function nearestCell(latlng) {
  if (!state.grid) return null;
  const rows = state.grid.rows, cos = Math.cos((latlng.lat * Math.PI) / 180);
  let best = -1, bd = Infinity;
  for (let i = 0; i < rows.length; i++) {
    const dy = rows[i][0] - latlng.lat, dx = (rows[i][1] - latlng.lng) * cos, d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = i; }
  }
  const miles = Math.sqrt(bd) * 69.09;
  return best >= 0 && miles < 0.3 ? { i: best, miles } : null;
}
function spotParts(p, c = state.spot?.cell) {   // c: a grid cell, by default the selected spot's
  if (!c || !state.grid) return null;
  const [, , base, xviii, block, xii, xiii] = state.grid.rows[c.i], here = base + xviii + block;
  if (!state.dealStarted) { const deal = state.dealPoints; return { loc: here, deal, total: here + deal, typical: usingTypical(), lift: 0, xii, xiii }; }
  const sc = dealScore(state.deal, p.pool, p.qct === 1), loc = base + Math.max(xviii, sc.phased) + Math.max(block, sc.xiv);
  return { loc, deal: sc.other, total: loc + sc.other, typical: false, lift: loc - here, sc, xii, xiii };
}
// the chance shown for the selected tract: at the spot when there is one, otherwise at the tract's best spot
function selectedEstimate() {
  const f = state.selected;
  if (!f) return null;
  const s = spotParts(f.properties);
  if (s) return { ...s, chance: winProb(s.total, f.properties.pool), where: state.spot.label };
  const e = chanceParts(f.properties);
  return e ? { ...e, xii: f.properties.xii, xiii: f.properties.xiii, chance: f.chance, where: "the tract's best spot" } : null;
}
const scored = (f) => isNum(f.pr);
const classOf = (f) => (scored(f) ? Math.min(CLASSES - 1, Math.floor((f.pr / 100) * CLASSES)) : null);
const ranked = () => state.feats.filter(scored).sort((a, b) => b.raw - a.raw);
const latestLine = (pool) => (state.lines?.lines || []).filter((l) => l.pool === titleCase(pool) && isNum(l.line)).sort((a, b) => b.round - a.round)[0];

/* ── map ── */
function styleFor(f) {
  if (!palette) readPalette();
  const k = classOf(f), sel = state.selected === f;
  const zoomedIn = state.map && state.map.getZoom() >= 13;
  return {
    fillColor: k === null ? palette.nodata : palette.classes[k],
    fillOpacity: (k === null ? 0.55 : 0.76) * (zoomedIn ? 0.55 : 1),
    color: sel ? palette.ink : palette.line, weight: sel ? 2.5 : 0.6, opacity: sel ? 1 : 0.7,
  };
}
function restyle() {
  state.layer.eachLayer((l) => l.setStyle(styleFor(l.feature)));
  state.selected?._layer?.bringToFront();
}

function initMap() {
  const map = L.map("map", { preferCanvas: true }).setView([33.82, -84.39], 9);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  state.layer = L.geoJSON({ type: "FeatureCollection", features: state.feats }, {
    style: styleFor,
    onEachFeature: (f, l) => {
      f._layer = l;
      l.bindTooltip(() => {
        const p = f.properties;
        if (!scored(f)) return `<b>${esc(p.lg)}</b><div class="tt-sub">${esc(tractNo(p.n))} · ${esc(p.ex || "Not scored")}</div>`;
        return `<b>${esc(p.lg)}</b><div class="tt-sub">${esc(tractNo(p.n))} · ${esc(p.c)} County</div>
          Priority ${Math.round(f.blend)} · Need ${Math.round(p.ns)} · Opportunity ${Math.round(p.os)}
          <div class="tt-sub">Chance of a 9% award ${isNum(f.chance) ? `${Math.round(100 * f.chance)}%` : "–"}</div>`;
      }, { sticky: true, direction: "top", offset: [0, -8] });
      l.on("click", (e) => { e.originalEvent._handled = true; select(f, e.latlng); });
    },
  }).addTo(map);
  let lastZ = map.getZoom();
  map.on("zoomend", () => { const z = map.getZoom(); if ((z >= 13) !== (lastZ >= 13)) restyle(); lastZ = z; });
  state.map = map;
}

function renderLegend() {
  if (!palette) readPalette();
  const t = Math.round(100 * state.bias);
  $("#legend").innerHTML = `<p class="lg-title">Priority: ${100 - t}% need, ${t}% opportunity</p>
    <p class="lg-sub">Fifths of the ${fmtInt.format(state.feats.filter(scored).length)} scored tracts</p>
    <div class="swatches" aria-hidden="true">${palette.classes.map((c) => `<span class="sw" style="background:${c}"></span>`).join("")}</div>
    <div class="ends"><span>Lower priority</span><span>Higher priority</span></div>
    <div class="lg-you"><i style="background:${palette.nodata};box-shadow:none;border:0"></i>${customWeights() ? "Not scored (no usable metric has weight, or left out)" : "Not scored (no residents or student-heavy)"}</div>`;
}

/* ── the right rail: the chart and the list ── */
function renderRail() {
  const t = Math.round(100 * state.bias), list = ranked();
  $("#rail-sub").textContent = `Ranked by priority (${100 - t}% need, ${t}% opportunity)${customWeights() ? " with your custom metric weights" : ""}. ${dealPhrase(true)}.`;
  $("#toplist").innerHTML = list.slice(0, TOP_N).map((f) => {
    const p = f.properties;
    return `<li><button type="button" data-g="${esc(p.g)}"><span class="nm">${esc(p.lg)}</span>
      <span class="loc">need ${Math.round(p.ns)} · opportunity ${Math.round(p.os)} · ${esc(tractNo(p.n))}</span>
      <span class="sc">${f.blend.toFixed(1)}</span><span class="sc2">${isNum(f.chance) ? `${Math.round(100 * f.chance)}% chance` : "–"}</span></button></li>`;
  }).join("");
  renderScatter(list.slice(0, TOP_N));
}

function renderScatter(top) {
  const svg = $("#scatter"), W = 320, H = 294, m = { l: 30, r: 8, t: 8, b: 26 };
  const x = (v) => m.l + (v / 100) * (W - m.l - m.r), y = (v) => H - m.b - (v / 100) * (H - m.t - m.b);
  const topSet = new Set(top);
  const pts = state.feats.filter(scored);
  const dot = (f, cls) => `<circle class="dot ${cls}" cx="${x(f.properties.ns).toFixed(1)}" cy="${y(f.properties.os).toFixed(1)}" r="${cls === "top" ? 3.2 : 2}"/>`;
  const grid = [25, 50, 75].map((v) => `<line class="grid" x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${H - m.b}"/><line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`).join("");
  const ticks = [0, 50, 100].map((v) => `<text x="${x(v)}" y="${H - m.b + 13}" text-anchor="middle">${v}</text><text x="${m.l - 6}" y="${y(v) + 3}" text-anchor="end">${v}</text>`).join("");
  const sel = state.selected && scored(state.selected) ? `<circle class="sel" cx="${x(state.selected.properties.ns)}" cy="${y(state.selected.properties.os)}" r="6"/>` : "";
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `${grid}<line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/><line class="axis" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${H - m.b}"/>
    ${pts.filter((f) => !topSet.has(f)).map((f) => dot(f, "")).join("")}
    ${top.map((f) => dot(f, "top")).join("")}${sel}${ticks}
    <text x="${W - m.r}" y="${H - 3}" text-anchor="end">Need →</text>
    <text x="10" y="${m.t + 2}" transform="rotate(-90 10 ${m.t + 2})" text-anchor="end">Opportunity →</text>
    <text class="q" x="${W - m.r - 2}" y="${m.t + 10}" text-anchor="end">high on both</text>`;
  const both = pts.filter((f) => f.properties.ns >= 200 / 3 && f.properties.os >= 200 / 3).length;
  $("#scatter-note").textContent = `${both} tracts in the top third of both`;
  svg._pts = pts.map((f) => [x(f.properties.ns), y(f.properties.os), f]);
}

/* ── the selected tract ── */
function select(f, latlng = null, label = "the spot you clicked") {
  const prev = state.selected;
  state.selected = f;
  state.spot = latlng ? { latlng, label, cell: nearestCell(latlng) } : null;
  if (prev?._layer) prev._layer.setStyle(styleFor(prev));
  if (f?._layer) { f._layer.setStyle(styleFor(f)); f._layer.bringToFront(); }
  if (state.pin) state.pin.remove();
  if (latlng) state.pin = L.marker(latlng, { icon: L.divIcon({ className: "", html: '<div class="site-pin"></div>', iconSize: [14, 14] }) }).addTo(state.map);
  renderDetail();
  if (f) { showRailView("site"); setRail(true); }
  updateDealSummary(); syncControls();
  renderScatter(ranked().slice(0, TOP_N));
  if (f) history.replaceState(null, "", selectedHash());
}

function catRows(score, values) {
  return state.meta.scores[score].map((c, i) => {
    const v = values[i];
    return `<div class="catrow"><span>${esc(c.label)}</span><span class="v ${isNum(v) ? "" : "na"}">${isNum(v) ? Math.round(v) : "–"}</span>
      <div class="sbar"><i style="width:${isNum(v) ? v : 0}%"></i></div></div>`;
  }).join("");
}
function metricRows(score, values) {
  let i = 0;
  return state.meta.scores[score].map((c) => `<div class="mrow cat"><span>${esc(c.label)}</span><span></span><span></span></div>${c.metrics.map((m) => {
    const [pc, raw] = values[i++] || [null, null];
    return `<div class="mrow"><span>${esc(m.label)}${metricWeight(score, c, m) > 0 ? "" : ' <span class="tag">optional</span>'}</span><span class="raw">${fmtValue(m.key, raw)}</span>
      <span class="pc ${isNum(pc) ? "" : "na"}" title="${isNum(pc) ? "Percentile among scored tracts" : "Missing or not reliable here"}">${isNum(pc) ? Math.round(pc) : "–"}</span></div>`;
  }).join("")}`).join("");
}

function renderDetail() {
  const f = state.selected, box = $("#detail");
  if (!f) { showRailView("top"); return; }
  const p = f.properties;
  const head = `<div class="place"><h2>${esc(p.lg)}</h2><p class="meta">${esc(tractNo(p.n))} · ${esc(p.c)} County${isNum(p.pop) ? ` · ${fmtInt.format(p.pop)} residents` : ""}</p>${saveButton()}</div>`;
  if (!scored(f)) {
    box.innerHTML = `${head}<div class="card"><p>${esc(p.ex || "This tract has no score because none of the metrics you've given weight are usable here. Give another metric some weight to score it.")}</p></div>`;
    return;
  }
  const t = Math.round(100 * state.bias), L = latestLine(p.pool), e = chanceParts(p);
  const spot = spotParts(p), x = selectedEstimate();
  const here = state.spot?.latlng || f._layer.getBounds().getCenter();
  const gap = x && L ? +(x.total - L.line).toFixed(1) : null;
  const plain = x ? `At ${esc(x.where)}, with ${x.typical ? "a typical funded deal" : state.dealStarted ? "your deal checklist" : `${+x.deal.toFixed(1)} deal points`}, the estimated DCA score is
      <b>${x.total.toFixed(1)} of ${scoreMax(p.pool)}</b>${L ? `, ${gap >= 0 ? `${gap} points above` : `${-gap} points below`} the lowest score funded in the ${L.round} ${esc(titleCase(p.pool))} round (${L.line})` : ""}.` : "No location score here.";
  const lvl = (v) => (v >= 80 ? "very high" : v >= 60 ? "high" : v >= 40 ? "middle" : v >= 20 ? "low" : "very low");
  box.innerHTML = `${head}
    <div class="card hero">
      <p class="lbl">Chance of a 9% tax credit award</p>
      <p class="big">${isNum(x?.chance) ? `${Math.round(100 * x.chance)}%` : "–"}</p>
      <p class="plain">${plain}</p>
      ${!state.dealStarted ? `<p class="card-sub"><button class="btn link" type="button" data-open-deal>Fill in your deal checklist</button> to use your project's points.</p>` : ""}
      <details class="about"><summary>How this is scored</summary>
        <dl class="details">
          <dt>Location points at ${esc(x?.where || "the tract's best spot")}</dt><dd>${x ? x.loc.toFixed(1) : "–"} of ${LOCATION_MAX}</dd>
          ${x && x.lift > 0 ? `<dt class="sub-dt">includes your ${[x.sc.xiv ? "§XIV plan" : "", x.sc.phased ? "phasing" : ""].filter(Boolean).join(" and ")}</dt><dd>+${+x.lift.toFixed(1)}</dd>` : ""}
          <dt>${x?.typical ? "Deal points (typical funded deal)" : state.dealStarted ? "Deal points (your checklist)" : "Deal points (the slider)"}</dt><dd>${x ? +x.deal.toFixed(1) : "–"}</dd>
          <dt>Estimated DCA score</dt><dd>${x ? `${x.total.toFixed(1)} of ${scoreMax(p.pool)}` : "–"}</dd>
          ${L ? `<dt>${L.round} ${esc(titleCase(p.pool))} funding line</dt><dd>${L.line}</dd>` : ""}
          <dt>Transit / schools points</dt><dd>${x?.xii ?? "–"} of 6 / ${x?.xiii ?? "–"} of 3</dd>
          ${spot && e ? `<dt>Best spot in this tract</dt><dd>${isNum(f.chance) ? `${Math.round(100 * f.chance)}% · ` : ""}${e.loc.toFixed(1)} pts</dd>` : ""}
          <dt>Funding pool</dt><dd>${esc(titleCase(p.pool))}</dd>
          ${p.qct || p.dda ? `<dt>Basis boost (2026)</dt><dd>${[p.qct ? "QCT" : "", p.dda ? "Small Area DDA" : ""].filter(Boolean).join(", ")}</dd>` : ""}
        </dl>
        ${state.spot && !spot ? `<p class="hint" style="margin-top:6px">No SiteLine grid point within 0.3 miles${state.grid ? "" : " yet (still loading)"}, so this uses the tract's best spot.</p>` : ""}
        <p class="card-sub" style="margin-top:8px">Location points come from SiteLine's reading of Georgia's 2024–2025 QAP.
          <a href="../fundability_site/#site=${here.lat.toFixed(5)},${here.lng.toFixed(5)}">See the full breakdown in the Fundability Map</a>.</p>
      </details>
    </div>
    <div class="card">
      <h3>How it fits your priorities</h3>
      <div class="scores">
        <div><p class="lbl">Need</p><p class="val">${Math.round(p.ns)}</p><p class="sub">${lvl(p.ns)}</p></div>
        <div><p class="lbl">Opportunity</p><p class="val">${Math.round(p.os)}</p><p class="sub">${lvl(p.os)}</p></div>
        <div><p class="lbl">Priority</p><p class="val accent">${f.blend.toFixed(1)}</p><p class="sub">beats ${Math.round(f.pr)}%</p></div>
      </div>
      <p class="card-sub" style="margin-top:8px">Scores run 0–100 against the other metro Atlanta tracts. Priority blends them at
        your ${100 - t}/${t} setting and beats ${Math.round(f.pr)}% of tracts.</p>
    </div>
    <div class="card"><h3>Need: ${Math.round(p.ns)} of 100</h3><p class="card-sub">How much the area needs affordable rental housing.</p>${catRows("need", p.nc)}
      <details class="about"><summary>Every need measure</summary>${metricRows("need", p.nm)}</details></div>
    <div class="card"><h3>Opportunity: ${Math.round(p.os)} of 100</h3><p class="card-sub">What living here gives a resident.</p>${catRows("opportunity", p.oc)}
      <details class="about"><summary>Every opportunity measure</summary>${metricRows("opportunity", p.om)}</details></div>
    <p class="hint" style="margin-top:12px">A dash means the value is missing or not reliable here, so the other measures carry the weight.${customWeights() ? " These scores use your custom metric weights." : ""}</p>`;
}

/* ── shortlist and compare: save sites while exploring, then compare up to four side by side ──
 * A saved site is a tract, or an exact spot in one (a looked-up address or a clicked point). Its numbers are worked out
 * live from the current settings (priority slider, deal points or checklist, metric weights), the same way the site
 * panel works them out. The list is kept in this browser. */
const SL_KEY = "site-finder-shortlist-v1", SL_MAX = 12, CMP_MAX = 4;
const BOOKMARK = '<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 2.5h7v11L8 11l-3.5 2.5z"/></svg>';
state.shortlist = [];
const slFeat = (g) => state.feats.find((f) => f.properties.g === g);
const slId = (g, ll) => (ll ? `${g}@${ll.lat.toFixed(5)},${ll.lng.toFixed(5)}` : g);
function loadShortlist() {
  try {
    const s = JSON.parse(localStorage.getItem(SL_KEY));
    state.shortlist = Array.isArray(s) ? s.filter((i) => i && typeof i.g === "string" && slFeat(i.g)).slice(0, SL_MAX) : [];
  } catch { state.shortlist = []; }
}
function saveShortlist() { try { localStorage.setItem(SL_KEY, JSON.stringify(state.shortlist)); } catch { /* fine */ } }
// the site now open in the panel, as a shortlist entry
function currentItem() {
  const f = state.selected;
  if (!f) return null;
  const p = f.properties, ll = state.spot?.latlng, address = ll && state.spot.label === "this address" ? $("#lookup-input").value.trim() : "";
  return { id: slId(p.g, ll), g: p.g, lat: ll ? +ll.lat.toFixed(5) : null, lng: ll ? +ll.lng.toFixed(5) : null,
    name: address || `${p.lg}, ${tractNo(p.n)}`, kind: ll ? (address ? "address" : "spot") : "tract" };
}
const isSaved = (id) => (state.shortlist || []).some((i) => i.id === id);
function saveButton() {
  const it = currentItem();
  if (!it) return "";
  const saved = isSaved(it.id), full = !saved && state.shortlist.length >= SL_MAX;
  return `<button class="btn ${saved ? "primary" : "secondary"} sl-save" type="button" data-sl-toggle aria-pressed="${saved}" ${full ? "disabled" : ""}>
    ${BOOKMARK}${saved ? "Saved to shortlist" : full ? `Shortlist full (${SL_MAX})` : "Save to shortlist"}</button>`;
}
function toggleSaved() {
  const it = currentItem();
  if (!it) return;
  if (isSaved(it.id)) state.shortlist = state.shortlist.filter((i) => i.id !== it.id);
  else if (state.shortlist.length < SL_MAX) state.shortlist.push({ ...it, cmp: state.shortlist.filter((i) => i.cmp).length < CMP_MAX });
  saveShortlist(); renderShortlist();
  const b = $("#detail [data-sl-toggle]");
  if (b) { b.outerHTML = saveButton(); $("#detail [data-sl-toggle]")?.focus({ preventScroll: true }); }
}
// a saved site's numbers at the current settings: at its exact spot when it has one, otherwise at the tract's best spot
function siteEstimate(item) {
  const f = slFeat(item.g);
  if (!f || !scored(f)) return { f, scoredHere: false };
  const p = f.properties, ll = item.lat != null ? L.latLng(item.lat, item.lng) : null;
  const cell = ll ? nearestCell(ll) : null, s = cell ? spotParts(p, cell) : null, e = s || chanceParts(p);
  return { f, p, scoredHere: true, atSpot: !!s, e, chance: e ? winProb(e.total, p.pool) : null,
    xii: s ? s.xii : p.xii, xiii: s ? s.xiii : p.xiii, line: latestLine(p.pool) };
}
function openItem(item) {
  const f = slFeat(item.g);
  if (!f) return;
  if (item.lat != null) {
    const ll = L.latLng(item.lat, item.lng);
    state.map.setView(ll, Math.max(state.map.getZoom(), 14));
    if (item.kind === "address") $("#lookup-input").value = item.name;
    select(f, ll, item.kind === "address" ? "this address" : "the spot you clicked");
  } else {
    state.map.fitBounds(f._layer.getBounds(), { maxZoom: 14, padding: [40, 40] });
    select(f);
  }
}
function renderShortlist() {
  if (!state.shortlist || !state.meta) return;
  const list = state.shortlist, n = list.length, nCmp = list.filter((i) => i.cmp).length;
  document.querySelectorAll(".sl-count").forEach((el) => { el.textContent = n ? String(n) : ""; });
  // numbered pins on the map
  if (state.map) {
    if (!state.slLayer) state.slLayer = L.layerGroup().addTo(state.map);
    state.slLayer.clearLayers();
    list.forEach((it, k) => {
      const f = slFeat(it.g), ll = it.lat != null ? L.latLng(it.lat, it.lng) : f?._layer?.getBounds().getCenter();
      if (!ll) return;
      L.marker(ll, { title: it.name, keyboard: false, icon: L.divIcon({ className: "", html: `<div class="sl-pin">${k + 1}</div>`, iconSize: [20, 20], iconAnchor: [10, 10] }) })
        .on("click", () => openItem(it)).addTo(state.slLayer);
    });
  }
  const body = $("#sl-body");
  if (!n) {
    body.innerHTML = `<h2 class="sl-h">Your shortlist</h2><div class="card sl-empty"><p>Nothing saved yet.</p>
      <p class="card-sub">Open a site (look one up, click the map, or pick from Top tracts) and press <b>Save to shortlist</b>.
      Saved sites get a numbered pin on the map, and you can compare up to ${CMP_MAX} side by side.</p></div>`;
    return;
  }
  const t = Math.round(100 * state.bias);
  body.innerHTML = `<h2 class="sl-h">Your shortlist <span class="tab-pts">${n} of ${SL_MAX}</span></h2>
    <p class="hint">Numbers use your current settings: ${100 - t}% need, ${t}% opportunity, ${dealPhrase()}${customWeights() ? ", custom metric weights" : ""}.
      Tick up to ${CMP_MAX} to compare.</p>
    <ol class="sl-list">${list.map((it, k) => {
      const x = siteEstimate(it), p = x.p;
      const where = it.kind === "tract" ? "Tract's best spot" : it.kind === "address" ? "This address" : "Clicked spot";
      const stats = x.scoredHere ? `<span><b>${isNum(x.chance) ? `${Math.round(100 * x.chance)}%` : "–"}</b> chance</span>
          <span>Priority <b>${x.f.blend.toFixed(1)}</b></span><span>Need <b>${Math.round(p.ns)}</b></span><span>Opp. <b>${Math.round(p.os)}</b></span>`
        : `<span>Not scored</span>`;
      const off = !it.cmp && nCmp >= CMP_MAX;
      return `<li class="sl-item">
        <span class="sl-num" aria-hidden="true">${k + 1}</span>
        <div class="sl-main">
          <button class="btn link sl-name" type="button" data-sl-go="${esc(it.id)}">${esc(it.name)}</button>
          <span class="sl-sub">${esc(x.f ? `${x.f.properties.c} County · ${tractNo(x.f.properties.n)}` : "")} · ${where}</span>
          <span class="sl-stats">${stats}</span>
        </div>
        <div class="sl-side">
          <label class="sl-cmp" title="${off ? `Compare holds ${CMP_MAX} at a time` : "Include in the comparison"}"><input type="checkbox" data-sl-cmp="${esc(it.id)}" ${it.cmp ? "checked" : ""} ${off ? "disabled" : ""}><span>Compare</span></label>
          <button class="btn icon sl-del" type="button" data-sl-del="${esc(it.id)}" aria-label="Remove ${esc(it.name)} from the shortlist">
            <svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button>
        </div></li>`;
    }).join("")}</ol>
    <div class="sl-actions">
      <button class="btn primary" type="button" data-sl-compare ${nCmp >= 2 ? "" : "disabled"}>Compare ${nCmp >= 2 ? nCmp : ""} sites</button>
      <button class="btn text" type="button" data-sl-clear>Clear all</button>
    </div>
    ${nCmp < 2 ? `<p class="hint">Tick at least two sites to compare.</p>` : ""}`;
}
function onCompareCheck(e) {
  const id = e.target.dataset.slCmp;
  if (!id) return;
  const it = state.shortlist.find((i) => i.id === id);
  if (!it) return;
  if (e.target.checked && state.shortlist.filter((i) => i.cmp).length >= CMP_MAX) { e.target.checked = false; return; }
  it.cmp = e.target.checked;
  saveShortlist(); renderShortlist();
  $(`[data-sl-cmp="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
}
function onShortlistClick(e) {
  const t = e.target;
  if (t.closest("[data-sl-toggle]")) { toggleSaved(); return; }
  if (t.closest("[data-sl-open]")) { renderShortlist(); showRailView("list"); setRail(true, !railOverlay.matches); $("#sl-body").scrollTop = 0; return; }
  const go = t.closest("[data-sl-go]");
  if (go) { const it = state.shortlist.find((i) => i.id === go.dataset.slGo); if (it) openItem(it); return; }
  const del = t.closest("[data-sl-del]");
  if (del) {
    state.shortlist = state.shortlist.filter((i) => i.id !== del.dataset.slDel);
    saveShortlist(); renderShortlist();
    if (state.selected) { const b = $("#detail [data-sl-toggle]"); if (b) b.outerHTML = saveButton(); }
    $("#list-back").focus({ preventScroll: true });
    return;
  }
  const clear = t.closest("[data-sl-clear]");
  if (clear) {   // a second click confirms (pop-up confirms are blocked in some embeds)
    if (clear.dataset.armed !== "1") { clear.dataset.armed = "1"; clear.textContent = `Click again to remove all ${state.shortlist.length}`; return; }
    state.shortlist = []; saveShortlist(); renderShortlist();
    if (state.selected) { const b = $("#detail [data-sl-toggle]"); if (b) b.outerHTML = saveButton(); }
    return;
  }
  if (t.closest("[data-sl-compare]")) { openCompare(); return; }
  const open = t.closest("[data-cmp-open]");
  if (open) { $("#compare").close(); const it = state.shortlist.find((i) => i.id === open.dataset.cmpOpen); if (it) openItem(it); }
}
function openCompare() {
  const items = state.shortlist.filter((i) => i.cmp).slice(0, CMP_MAX);
  if (items.length < 2) return;
  const cols = items.map((it) => ({ it, k: state.shortlist.indexOf(it) + 1, x: siteEstimate(it) }));
  const t = Math.round(100 * state.bias);
  const num = (v, d = 0) => (isNum(v) ? (d ? v.toFixed(d) : String(Math.round(v))) : "–");
  // [label, value(col) for ranking or null, text(col)]; "§" rows are section headings
  const rows = [
    ["§", "Chance of a 9% award"],
    ["Chance of an award", (c) => c.x.chance, (c) => (isNum(c.x.chance) ? `${Math.round(100 * c.x.chance)}%` : "–")],
    ["Estimated DCA score", (c) => c.x.e?.total, (c) => (c.x.e ? `${c.x.e.total.toFixed(1)} <span class="of">of ${scoreMax(c.x.p.pool)}</span>` : "–")],
    ["Location points", (c) => c.x.e?.loc, (c) => (c.x.e ? `${c.x.e.loc.toFixed(1)} <span class="of">of ${LOCATION_MAX}</span>` : "–")],
    ["Deal points", null, (c) => (c.x.e ? num(c.x.e.deal, 1).replace(/\.0$/, "") : "–")],
    ["Latest funding line", null, (c) => (c.x.line ? `${c.x.line.line} <span class="of">${c.x.line.round} ${esc(titleCase(c.x.p.pool))}</span>` : "–")],
    ["Transit / schools points", null, (c) => (c.x.p ? `${c.x.xii ?? "–"} of 6 / ${c.x.xiii ?? "–"} of 3` : "–")],
    ["§", `Your priorities (${100 - t}% need, ${t}% opportunity)`],
    ["Priority", (c) => c.x.f?.blend, (c) => (c.x.p ? `<b class="acc">${c.x.f.blend.toFixed(1)}</b> <span class="of">beats ${Math.round(c.x.f.pr)}%</span>` : "–")],
    ["Need", (c) => c.x.p?.ns, (c) => num(c.x.p?.ns)],
    ["Opportunity", (c) => c.x.p?.os, (c) => num(c.x.p?.os)],
    ["§", "Need, by category"],
    ...state.meta.scores.need.map((cat, i) => [cat.label, (c) => c.x.p?.nc?.[i], (c) => num(c.x.p?.nc?.[i])]),
    ["§", "Opportunity, by category"],
    ...state.meta.scores.opportunity.map((cat, i) => [cat.label, (c) => c.x.p?.oc?.[i], (c) => num(c.x.p?.oc?.[i])]),
    ["§", "The place"],
    ["County", null, (c) => esc(c.x.f?.properties.c || "–")],
    ["Funding pool", null, (c) => esc(c.x.p ? titleCase(c.x.p.pool) : "–")],
    ["Residents", null, (c) => (isNum(c.x.f?.properties.pop) ? fmtInt.format(c.x.f.properties.pop) : "–")],
    ["Basis boost (2026)", null, (c) => (c.x.p ? [c.x.p.qct ? "QCT" : "", c.x.p.dda ? "Small Area DDA" : ""].filter(Boolean).join(", ") || "None" : "–")],
  ];
  const head = `<tr><th scope="col" class="cmp-corner"></th>${cols.map((c) => `<th scope="col">
      <span class="sl-num">${c.k}</span><span class="cmp-name">${esc(c.it.name)}</span>
      <span class="cmp-where">${c.it.kind === "tract" ? "Tract's best spot" : c.x.atSpot ? (c.it.kind === "address" ? "At this address" : "At the clicked spot") : "Tract's best spot (no grid point nearby)"}</span>
      <button class="btn link" type="button" data-cmp-open="${esc(c.it.id)}">Open</button></th>`).join("")}</tr>`;
  const body = rows.map(([label, val, text]) => {
    if (label === "§") return `<tr class="cmp-sec"><th scope="rowgroup" colspan="${cols.length + 1}">${esc(val)}</th></tr>`;
    let best = null;
    if (val) {
      const vs = cols.map(val).filter(isNum);
      if (vs.length >= 2 && Math.max(...vs) > Math.min(...vs)) best = Math.max(...vs);
    }
    return `<tr><th scope="row">${esc(label)}</th>${cols.map((c) => {
      const v = val ? val(c) : null, top = best !== null && isNum(v) && v === best;
      return `<td class="${top ? "best" : ""}">${text(c)}${top ? '<span class="visually-hidden"> (highest)</span>' : ""}</td>`;
    }).join("")}</tr>`;
  }).join("");
  $("#cmp-sub").textContent = `${cols.length} sites at your current settings: ${100 - t}% need, ${t}% opportunity, ${dealPhrase()}${customWeights() ? ", custom metric weights" : ""}. The highest value in each row is marked.`;
  $("#cmp-body").innerHTML = `<div class="cmp-wrap"><table class="cmp-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>
    <p class="hint cmp-foot">Need and opportunity categories are percentiles among metro Atlanta tracts (100 = most). For need, higher means more
      hardship. Chances use the Fundability Map's model; a tract's best spot can score higher than a specific address in it.</p>`;
  const dlg = $("#compare");
  if (!dlg.open) dlg.showModal();
  $("#cmp-body").scrollTop = 0;
}

/* ── the right panel: top tracts, or the selected site ── */
function showRailView(view) {
  state.railView = view === "site" || view === "list" ? view : "top";
  $("#top-view").hidden = state.railView !== "top";
  $("#site-view").hidden = state.railView !== "site";
  $("#list-view").hidden = state.railView !== "list";
  if (state.railView === "site") $("#detail").scrollTop = 0;
}

/* ── controls ── */
function syncControls() {
  const t = Math.round(100 * state.bias);
  $("#bias").value = t;
  $("#bias-out").textContent = t === 0 ? "Need only" : t === 100 ? "Opportunity only" : `${100 - t}% need · ${t}% opportunity`;
  const selP = state.selected?.properties, sc = dealScore(state.deal, selP?.pool, selP?.qct === 1), st = scoreStack(false), slider = $("#deal");
  $("#deal-out").textContent = st.total;
  $("#score-stack").innerHTML = st.html;
  slider.value = state.dealStarted ? sc.other : state.dealPoints;
  slider.disabled = state.dealStarted;   // the checklist's count is more exact, so it wins once it's started
  $("#deal-points-out").textContent = state.dealStarted ? `${+sc.other.toFixed(1)} · from your checklist`
    : `${state.dealPoints}${usingTypical() ? " · typical funded deal" : ""}`;
  $("#deal-note").textContent = state.dealStarted
    ? "Your checklist sets the points now. Reset the checklist to use the slider again."
    : `A quick estimate: funded ARC deals claimed a median of ${typicalPoints()}. For an exact count, use the checklist below.`;
  $("#tab-deal-pts").textContent = state.dealStarted ? `${+sc.other.toFixed(1)} pts` : "";
  $("#weights-out").textContent = customWeights() ? "Custom" : "Team defaults";
}
function saveSettings() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify({ bias: state.bias, dealPoints: state.dealPoints, weights: state.weights })); } catch { /* fine */ }
}
let frame = 0;
function update() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    computePriority(); restyle(); renderLegend(); renderRail(); syncControls(); updateDealSummary(); saveSettings(); renderShortlist();
    if (state.selected) renderDetail();
  });
}

/* ── address lookup ──
 * Suggestions as you type, and the lookup itself, come from Esri's World Geocoding Service (no key; results aren't
 * stored), which matches partial street names ("640 mea" → 640 Means Dr NE). If it doesn't answer, a submitted address
 * falls back to OpenStreetMap Nominatim. */
const ARC_VIEWBOX = "-85.05,34.65,-83.55,33.15";
const ESRI = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer";
const ESRI_AREA = "searchExtent=-85.05,33.15,-83.55,34.65&location=-84.39,33.75&countryCode=USA";
const LATLON = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/;
async function esriFind(q, magicKey = null) {
  const url = `${ESRI}/findAddressCandidates?f=json&maxLocations=1&${ESRI_AREA}&SingleLine=${encodeURIComponent(q)}${magicKey ? `&magicKey=${encodeURIComponent(magicKey)}` : ""}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`geocoder returned ${r.status}`);
  const c = ((await r.json()).candidates || [])[0];
  return c && c.score >= 80 ? { lat: c.location.y, lng: c.location.x } : null;
}
async function nominatim(q) {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us&viewbox=${ARC_VIEWBOX}&bounded=1&q=${encodeURIComponent(q)}`;
  const r = await fetch(url, { headers: { "Accept-Language": "en" } });
  if (!r.ok) throw new Error(`geocoder returned ${r.status}`);
  const j = await r.json();
  return j.length ? { lat: +j[0].lat, lng: +j[0].lon } : null;
}
async function geocode(q, magicKey = null) {
  const ll = q.match(LATLON);
  if (ll) return { lat: +ll[1], lng: +ll[2] };
  try { const hit = await esriFind(q, magicKey); if (hit) return hit; } catch (err) { console.warn(err); }
  return nominatim(q);
}
async function suggestAddresses(text, signal) {
  const url = `${ESRI}/suggest?f=json&maxSuggestions=6&${ESRI_AREA}&text=${encodeURIComponent(text)}`;
  const r = await fetch(url, { signal });
  if (!r.ok) return [];
  return ((await r.json()).suggestions || []).filter((s) => !s.isCollection)
    .map((s) => ({ text: s.text.replace(/,\s*USA$/, ""), magicKey: s.magicKey }));
}

// the dropdown under the search box: arrow keys move, Enter or a click picks, Escape closes
const sug = { items: [], active: -1, timer: 0, abort: null };
function renderSuggest() {
  const box = $("#suggest"), input = $("#lookup-input"), open = sug.items.length > 0;
  box.hidden = !open;
  input.setAttribute("aria-expanded", String(open));
  if (!open) { input.removeAttribute("aria-activedescendant"); box.innerHTML = ""; return; }
  box.innerHTML = sug.items.map((s, i) => {
    const [main, ...rest] = s.text.split(", ");
    return `<li role="option" id="sug-${i}" data-i="${i}" aria-selected="${i === sug.active}"><span class="s-main">${esc(main)}</span><span class="s-sub">${esc(rest.join(", "))}</span></li>`;
  }).join("") + `<li class="s-credit" role="presentation">Suggestions by Esri</li>`;
  if (sug.active >= 0) { input.setAttribute("aria-activedescendant", `sug-${sug.active}`); $(`#sug-${sug.active}`).scrollIntoView({ block: "nearest" }); }
  else input.removeAttribute("aria-activedescendant");
}
function closeSuggest() { clearTimeout(sug.timer); sug.abort?.abort(); sug.items = []; sug.active = -1; renderSuggest(); }
function onLookupInput() {
  const q = $("#lookup-input").value.trim();
  clearTimeout(sug.timer); sug.abort?.abort();
  if (q.length < 3 || LATLON.test(q)) { sug.items = []; sug.active = -1; renderSuggest(); return; }
  sug.timer = setTimeout(async () => {
    sug.abort = new AbortController();
    try { sug.items = await suggestAddresses(q, sug.abort.signal); } catch { return; }   // aborted or offline: keep typing
    sug.active = -1;
    if (document.activeElement === $("#lookup-input")) renderSuggest();
  }, 180);
}
function onLookupKey(e) {
  if (e.key === "Escape" && sug.items.length) { e.preventDefault(); closeSuggest(); return; }
  if (!sug.items.length) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const n = sug.items.length;
    sug.active = e.key === "ArrowDown" ? (sug.active + 1) % n : (sug.active <= 0 ? n - 1 : sug.active - 1);
    renderSuggest();
  } else if (e.key === "Enter" && sug.active >= 0) { e.preventDefault(); pickSuggestion(sug.active); }
}
function pickSuggestion(i) {
  const s = sug.items[i];
  if (!s) return;
  $("#lookup-input").value = s.text;
  closeSuggest();
  lookUp(s.text, s.magicKey);
}
function inRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function tractAt(latlng) {
  return state.feats.find((f) => {
    if (!f._layer.getBounds().contains(latlng)) return false;
    const g = f.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    return polys.some((r) => inRing(latlng.lng, latlng.lat, r[0]) && !r.slice(1).some((h) => inRing(latlng.lng, latlng.lat, h)));
  }) || null;
}
function onLookup(e) {
  e.preventDefault();
  const q = $("#lookup-input").value.trim();
  closeSuggest();
  if (q) lookUp(q);
}
async function lookUp(q, magicKey = null) {
  const status = $("#lookup-status");
  status.textContent = "Looking up…";
  try {
    const hit = await geocode(q, magicKey);
    if (!hit) { status.textContent = "No match in the Atlanta region. Try adding the city and ZIP, or enter lat, lon."; return; }
    const latlng = L.latLng(hit.lat, hit.lng), f = tractAt(latlng);
    const z = Math.max(state.map.getZoom(), 14);
    if (REDUCED.matches) state.map.setView(latlng, z); else state.map.flyTo(latlng, z, { duration: 0.8 });
    status.textContent = f ? "" : "Found it, but it's outside the 11 ARC counties.";
    if (f) select(f, latlng, "this address");
  } catch (err) {
    status.textContent = "The address search didn't respond. Try again, or enter lat, lon.";
    console.error(err);
  }
}

/* ── CSV of every tract at the current settings ── */
function downloadCsv() {
  const needM = state.meta.scores.need.flatMap((c) => c.metrics), oppM = state.meta.scores.opportunity.flatMap((c) => c.metrics);
  const head = ["geoid", "tract", "local_gov", "county", "pool", "priority", "need_score", "opportunity_score", "chance_of_award",
    "location_points", "deal_points", ...state.meta.scores.need.map((c) => `need_${c.key}`), ...state.meta.scores.opportunity.map((c) => `opportunity_${c.key}`),
    ...needM.map((m) => m.key), ...oppM.map((m) => m.key)];
  const rows = state.feats.filter(scored).sort((a, b) => b.raw - a.raw).map((f) => {
    const p = f.properties;
    return [p.g, p.n, p.lg, p.c, p.pool, f.blend.toFixed(1), p.ns, p.os, isNum(f.chance) ? f.chance.toFixed(3) : "", ...(() => { const e = chanceParts(p); return e ? [e.loc, +e.deal.toFixed(1)] : ["", ""]; })(),
      ...p.nc, ...p.oc, ...p.nm.map((x) => x[1]), ...p.om.map((x) => x[1])];
  });
  const csv = [head, ...rows].map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `lihtc_dashboard_tracts_${100 - Math.round(100 * state.bias)}need_${Math.round(100 * state.bias)}opp${customWeights() ? "_custom_weights" : ""}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── the right rail: a column on wide screens, a sheet on narrow ones ── */
const railOverlay = window.matchMedia("(max-width: 1179px)");
const RAIL_KEY = "site-finder-rail-open";
function railPref() { try { return localStorage.getItem(RAIL_KEY) !== "0"; } catch { return true; } }
function setRail(open, save = false) {
  const app = $("#app"), overlay = railOverlay.matches, scrim = $("#scrim");
  app.classList.toggle("rail-closed", !open);
  app.classList.toggle("rail-open", open);
  $("#rail-btn").setAttribute("aria-expanded", String(open));
  $("#rail").inert = !open;
  if (overlay && open) { scrim.hidden = false; requestAnimationFrame(() => scrim.classList.add("on")); }
  else { scrim.classList.remove("on"); scrim.hidden = true; }
  if (save && !overlay) { try { localStorage.setItem(RAIL_KEY, open ? "1" : "0"); } catch { /* fine */ } }
  if (!overlay) setTimeout(() => state.map?.invalidateSize({ pan: false }), REDUCED.matches ? 0 : 320);
}


// The QAP's full 9% scale: location sections (§XI 20, §XII 6, §XIII 3, the larger of §XVIII 5 / §XVII 4, §XXII 2, the
// 10-point block) = 47, plus the deal sections, 51 (52 in Other Metro, with §XX): 98 / 99. SiteLine scores 46 of the 47;
// §XII's sixth point needs a transit-agency-owned site, which neither SiteLine nor the checklist models.
const LOCATION_MAX = 46, UNMODELED = 1;
const scoreMax = (pool) => LOCATION_MAX + UNMODELED + dealScore(dealDefaults(), pool).max;
// The estimated DCA score, layered: the site's SiteLine location points (at the looked-up address or clicked spot,
// else the tract's best spot) plus the deal checklist's points, against the pool's latest funding line.
function scoreStack(withTotal = true) {
  const f = state.selected, p = f && scored(f) ? f.properties : null, x = p ? selectedEstimate() : null;
  const sc = dealScore(state.deal, p?.pool, p?.qct === 1);
  const deal = x ? x.deal : state.dealStarted ? sc.other : state.dealPoints;
  const dealLbl = state.dealStarted ? "your checklist" : usingTypical() ? "typical funded deal" : "the slider";
  const MAX = scoreMax(p?.pool), L = p ? latestLine(p.pool) : null, W = (v) => `${Math.max(0, Math.min(100, (100 * v) / MAX))}%`;
  const loc = x?.loc, total = isNum(loc) ? +(loc + deal).toFixed(1) : null;
  const where = x ? x.where : null;
  const html = `<div class="stack-bar" role="img" aria-label="${isNum(loc) ? `${loc} location points plus ${+deal.toFixed(1)} deal points` : `${+deal.toFixed(1)} deal points; no site picked`}${L ? `; funding line ${L.line}` : ""}">
      ${isNum(loc) ? `<i class="seg-loc" style="width:${W(loc)}"></i>` : ""}<i class="seg-deal" style="width:${W(deal)}"></i>${L ? `<b style="left:${W(L.line)}"></b>` : ""}</div>
    <dl class="stack-rows">
      <dt><i class="sw loc"></i>Location points <span>${where ? `at ${esc(where)}` : "pick a site"}</span></dt><dd>${isNum(loc) ? loc.toFixed(1) : "–"} <span class="of">/ ${LOCATION_MAX}</span></dd>
      ${x && x.lift > 0 ? `<dt class="sub"><span>includes your ${[x.sc.xiv ? "§XIV plan" : "", x.sc.phased ? "phasing" : ""].filter(Boolean).join(" and ")}</span></dt><dd>+${+x.lift.toFixed(1)}</dd>` : ""}
      <dt><i class="sw deal"></i>Deal points <span>${dealLbl}</span></dt><dd>+ ${+deal.toFixed(1)} <span class="of">/ ${MAX - LOCATION_MAX - UNMODELED}</span></dd>
      ${withTotal ? `<dt class="tot">Estimated DCA score</dt><dd class="tot">${isNum(total) ? `${total} of ${MAX}` : "–"}</dd>` : ""}
    </dl>
    ${L || isNum(x?.chance) ? `<p class="stack-foot">${L ? `${L.round} ${esc(titleCase(p.pool))} funding line ${L.line}` : ""}${L && isNum(x?.chance) ? " · " : ""}${isNum(x?.chance) ? `<b>${Math.round(100 * x.chance)}% chance</b>` : ""}</p>` : ""}`;
  return { html, total: isNum(total) ? `${total} of ${MAX}` : `${+deal.toFixed(1)} + location, of ${MAX}` };
}

/* ── the settings: metric weights ── */
function weightRow(score, cat, m) {
  const v = +metricWeight(score, cat, m).toFixed(1), byDefault = defaultWeight(cat, m) > 0;
  return `<div class="wt-row">
    <label for="wt-${m.key}">${esc(m.label)}${byDefault ? "" : ' <span class="tag">off by default</span>'}${m.coverage < 0.95 ? `<span class="pg-inline">usable in ${Math.round(100 * m.coverage)}% of tracts</span>` : ""}</label>
    <input type="range" id="wt-${m.key}" data-score="${score}" data-metric="${m.key}" min="0" max="50" step="0.1" value="${v}">
    <input type="number" id="wtn-${m.key}" aria-label="${esc(m.label)}: weight" data-score="${score}" data-metric="${m.key}" min="0" max="100" step="0.1" value="${v}">
    <output data-share="${m.key}" title="Share of the ${titleCase(score)} score"></output>
  </div>`;
}
function weightSection(score) {
  const label = titleCase(score);
  return `<section class="wt-score"><div class="wt-head"><h3>${label}</h3>
      <button class="btn secondary" type="button" data-reset-weights="${score}">Reset ${label}</button></div>
    <p class="note warn" data-weights-off="${score}" hidden>Every ${label} weight is zero, so ${label} is using the team's defaults. Give at least one metric a weight.</p>
    ${state.meta.scores[score].map((c) => `<div class="wt-cat"><div class="wt-cat-head"><span>${esc(c.label)}</span><output data-share="${c.key}"></output></div>
      ${c.metrics.map((m) => weightRow(score, c, m)).join("")}</div>`).join("")}</section>`;
}
function syncWeightShares() {
  for (const score of Object.keys(SCORE_FIELDS)) {
    const total = weightTotal(score) || 1, share = (w) => `${((100 * w) / total).toFixed(1)}%`;
    for (const c of state.meta.scores[score]) {
      let sum = 0;
      for (const m of c.metrics) {
        const w = metricWeight(score, c, m);
        sum += w;
        $(`[data-share="${m.key}"]`).textContent = share(w);
      }
      $(`[data-share="${c.key}"]`).textContent = `${share(sum)} of ${titleCase(score)}`;
    }
    $(`[data-weights-off="${score}"]`).hidden = !state.weightsOff[score];
  }
}
function onWeightInput(e) {
  const el = e.target.closest("[data-metric]");
  if (!el || el.value === "" || !Number.isFinite(+el.value)) return;
  const value = Math.min(100, Math.max(0, +el.value));
  document.querySelectorAll(`[data-metric="${el.dataset.metric}"]`).forEach((x) => { if (x !== el) x.value = value; });
  setWeight(el.dataset.score, el.dataset.metric, value);
  syncWeightShares();
}
function onWeightReset(e) {
  const b = e.target.closest("[data-reset-weights]");
  if (!b) return;
  state.weights[b.dataset.resetWeights] = {};
  applyWeights();
  update();
  const body = $("#menu-body"), top = body.scrollTop;
  openMenu("weights");
  body.scrollTop = top;
}

const dealPhrase = (long = false) => {
  if (usingTypical()) return long ? `Chances assume a typical funded deal (${typicalPoints()} deal points)` : `typical funded deal (${typicalPoints()} pts)`;
  if (!state.dealStarted) return long ? `Chances assume ${state.dealPoints} deal points` : `${state.dealPoints} deal points`;
  const pts = +dealScore(state.deal).other.toFixed(1);
  return long ? `Chances use your deal checklist (${pts} deal points)` : `your deal (${pts} pts)`;
};

const CHEV = '<svg class="ico chev" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>';
const CHECK = '<svg class="ico ok" viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7"/></svg>';

// Short labels for scanning, and the detail behind each item's arrow. Rules and ids are the Fundability Map's (DEAL above).
const ITEM_TEXT = {
  r_site: ["Site control and zoning", "Documents showing you control the site and that its zoning allows the project."],
  r_water: ["Water and sewer documentation", "Public water and sanitary sewer documentation. You get the full point if it doesn't apply."],
  r_util: ["Operating utilities documentation", "You get the full point if it doesn't apply."],
  r_plan: ["Site information and site plan", "Site information and a conceptual site plan."],
  r_fin: ["Preliminary financing commitments", ""],
  r_esa: ["Phase I environmental assessment", "A Phase I Environmental Site Assessment."],
  r_gc: ["Contractor's cost estimate", "The general contractor's cost estimate."],
  r_mkt: ["Market study", ""],
  r_pna: ["Physical needs assessment", "Rehab only. New construction gets the point."],
  c_sae: ["Significant adverse events", "Significant Adverse Events with waivers on the team's past properties: −5 points each."],
  c_ac: ["Unique adverse circumstances", "−2 points each."],
  c_props: ["Properties the team still owns", "LIHTC or HOME properties the team developed and still owns. After deductions, these add points back, up to 10."],
  e_qc: ["Qualified Contract waiver", "Waiving the Qualified Contract option keeps the property affordable longer: entirely (5), for at least 10 years (3) or 5 years (1). A resident ownership plan (single-family only) earns 1."],
  e_rofr: ["Right of first refusal", "To a nonprofit or housing authority. Counts only with a Qualified Contract waiver."],
  f_amt: ["New loans or grants", "Qualifying sources: HOME, CDBG, FHLB AHP, BeltLine, foundations and other government programs. Points by total, or by amount per unit."],
  f_nhtf: ["Housing Trust Fund or HOME-ARP money", "+1 if that money is National Housing Trust Fund or HOME-ARP."],
  f_cost: ["Property cost reduction", "A Private Enterprise Agreement lowering property taxes (0.5), or a 45+ year ground lease at $100 a year or less from a housing authority, government or land trust (1)."],
  s_ref: ["Referrals or tenant preference", "Pick one: supportive-housing referrals for at least 10% of units, referrals plus project-based rental assistance on 30% AMI units, or a housing authority's Section 8 PBRA with the Settlement Agreement tenant preference."],
  s_pbra: ["No rental-assistance penalty", "No DCA rental-assistance penalty on the team's past properties. DCA assigns these automatically."],
  t_deep: ["Rent and income targeting", "An average income at or below 58% AMI (2), or project-based rental assistance on at least 20% of units (3)."],
  hcv: ["Voucher registration and PHA notice", "Register with the voucher administrator and notify the public housing authority."],
  mbe: ["Minority- and women-owned firms", "Engage qualified MBE/WBE firms for about 10% of hard costs, or have one on the development team."],
  svc: ["Resident services", "Owner-provided (CORES-certified) or third-party services for residents."],
  mix: ["Mixed income", "10%+ market-rate units, or the income-averaging election."],
  gich: ["GICH support letter", "A support letter from a Georgia Initiative for Community Housing team."],
  econ: ["Major job-creating project nearby", "A major job-creating project within commuting distance. Other Metro pool only."],
  v_crp: ["In a revitalization plan area", "The site is in a qualifying local Community Revitalization Plan area: 2 points, +1 if the tract is a QCT."],
  v_bp: ["Plan best practices", "Only with a qualifying plan: public input during the plan (1), an infrastructure assessment with implementation measures (2), or both, or adoption by a PlanFirst, RAS or Rural Zone community (3)."],
  v_ct: ["Community Transformation", "A quarterback board and partnerships carrying out the plan."],
  v_ci: ["Community investment", "Third-party capital and local fee waivers."],
  phase: ["Later phase of a phased development", "A later phase of a DCA-funded phased development."],
};
state.descOpen = new Set();

function moreBtn(id, short) {
  if (!ITEM_TEXT[id]?.[1]) return "<span></span>";
  const open = state.descOpen.has(id);
  return `<button type="button" class="ck-more" data-more="${id}" aria-expanded="${open}" aria-controls="desc-${id}" aria-label="More about ${esc(short)}">${CHEV}</button>`;
}
function descHtml(id) {
  const d = ITEM_TEXT[id]?.[1];
  return d ? `<p class="ck-desc" id="desc-${id}" ${state.descOpen.has(id) ? "" : "hidden"}>${esc(d)}</p>` : "";
}
function itemHtml(it, a) {
  const [short] = ITEM_TEXT[it.id] || [it.label];
  if (it.type === "check") {
    return `<div class="ck"><label class="ck-main"><input type="checkbox" data-id="${it.id}" ${a[it.id] ? "checked" : ""}>
      <span class="ck-label">${esc(short)}</span><span class="pts">+${it.pts}</span></label>${moreBtn(it.id, short)}${descHtml(it.id)}</div>`;
  }
  if (it.type === "number") {
    return `<div class="ck"><div class="ck-main"><label class="ck-label" for="d-${it.id}">${esc(short)}</label><span class="ctl">
      <button type="button" data-step="-1" data-for="${it.id}" aria-label="One fewer">−</button>
      <input id="d-${it.id}" type="number" inputmode="numeric" min="${it.min}" max="${it.max}" step="1" data-id="${it.id}" value="${+a[it.id] || 0}">
      <button type="button" data-step="1" data-for="${it.id}" aria-label="One more">+</button></span></div>${moreBtn(it.id, short)}${descHtml(it.id)}</div>`;
  }
  const k = +a[it.id] || 0;
  return `<div class="ck pick"><div class="ck-main"><label class="ck-label" for="d-${it.id}">${esc(short)}</label>
      <span class="pts" id="pp-${it.id}">${it.options[k][1] ? `+${it.options[k][1]}` : ""}</span></div>${moreBtn(it.id, short)}
    <select id="d-${it.id}" data-id="${it.id}">${it.options.map(([l, pt], j) => `<option value="${j}" ${j === k ? "selected" : ""}>${esc(l)}${pt ? ` (+${pt})` : ""}</option>`).join("")}</select>
    ${descHtml(it.id)}</div>`;
}

function renderDeal() {   // builds the form; updateDealSummary() refreshes the numbers without rebuilding it
  const a = state.deal, bySec = Object.fromEntries(DEAL.map((s) => [s.sec, s]));
  $("#deal-form").innerHTML = DEAL_GROUPS.map(([title, secs]) => `<section class="dgrp"><h3 class="dgroup">${esc(title)}</h3>${secs.map((k) => {
    const s = bySec[k];
    return `<div class="dsec" data-sec="${s.sec}">
      <div class="ds-head"><span class="ds-title">${esc(s.title)} <span class="ds-ref">§${s.sec}${s.pools ? " · Other Metro only" : ""}</span></span>
        <span class="ds-pts num" id="dpts-${s.sec}"></span></div>
      ${s.note ? `<p class="ds-note">${esc(s.note)}</p>` : ""}${s.items.map((it) => itemHtml(it, a)).join("")}</div>`;
  }).join("")}</section>`).join("");
  updateDealSummary();
}
function updateDealSummary() {
  const selP = state.selected?.properties;
  const sc = dealScore(state.deal, selP?.pool, selP?.qct === 1), med = typicalPoints();
  DEAL.forEach((s) => {
    const el = document.getElementById(`dpts-${s.sec}`);
    if (!el) return;
    el.innerHTML = `${CHECK}<span><b>${+sc.by[s.sec].toFixed(1)}</b> / ${s.max}</span>`;
    el.closest(".dsec")?.classList.toggle("full", sc.by[s.sec] >= s.max);
    s.items.forEach((it) => {
      const pp = it.type === "radio" && document.getElementById(`pp-${it.id}`);
      if (pp) { const v = it.options[+state.deal[it.id] || 0][1]; pp.textContent = v ? `+${v}` : ""; }
    });
  });
  const pts = +sc.other.toFixed(1);
  $("#deal-total").innerHTML = `<div class="dt-row"><span class="dt-val">${pts}</span>
      <span class="dt-of">of ${sc.max} deal points${sc.xiv ? ` · §XIV ${sc.xiv}` : ""}${sc.phased ? " · phased +4" : ""}</span></div>
    <div class="dmeter" style="--p:${Math.min(1, pts / sc.max)};--m:${med / sc.max}" role="img"
      aria-label="${pts} of ${sc.max} deal points; funded applications claimed a median of ${med}"><i></i><b></b><span>Funded median ${med}</span></div>
    ${state.dealStarted ? "" : `<p class="ds-note">Not started: until you check or change an item, the score uses the deal points slider above.</p>`}`;
}
function onDealInput(e) {
  const id = e.target.dataset.id;
  if (!id) return;
  if (e.target.type === "number") state.deal[id] = Math.max(+e.target.min, Math.min(+e.target.max, Math.round(+e.target.value || 0)));
  else state.deal[id] = e.target.type === "checkbox" ? e.target.checked : +e.target.value;
  saveDeal(); update();
}
function onDealMore(e) {
  const b = e.target.closest("[data-more]");
  if (!b) return false;
  const id = b.dataset.more, open = b.getAttribute("aria-expanded") !== "true";
  if (open) state.descOpen.add(id); else state.descOpen.delete(id);
  b.setAttribute("aria-expanded", String(open));
  document.getElementById(`desc-${id}`).hidden = !open;
  return true;
}
function onDealStep(e) {
  if (onDealMore(e)) return;
  const b = e.target.closest("[data-step]");
  if (!b) return;
  const inp = document.getElementById(`d-${b.dataset.for}`);
  inp.value = Math.max(+inp.min, Math.min(+inp.max, (+inp.value || 0) + +b.dataset.step));
  inp.dispatchEvent(new Event("input", { bubbles: true }));
}

/* ── the methodology menu, generated from the metric set ── */
function categoryTable(score) {
  return `<div class="tablewrap"><table class="rubric">
    <thead><tr><th>Category</th><th class="num">Weight</th><th>What it answers</th><th>Data that goes into it</th></tr></thead>
    <tbody>${state.meta.scores[score].map((c) => `<tr><td>${esc(c.label)}</td><td class="num">${c.weight}%</td><td>${esc(c.question)}</td>
      <td>${c.metrics.map((m) => `${esc(m.label)}${m.weight > 0 ? "" : " (optional, no weight)"}${score === "need" ? ` <span class="pg-inline">usable in ${Math.round(100 * m.coverage)}%</span>` : ""}`).join("<br>")}</td></tr>`).join("")}</tbody>
  </table></div>`;
}
const weightsNote = (score) => {
  const s = String(state.meta.weights_status?.[score] ?? state.meta.weights_status), final = /^final/i.test(s);
  const text = s.replace(/^(placeholder|final):\s*/i, "").replace(/^./, (c) => c.toUpperCase());
  return `<p class="note ${final ? "" : "caution"}"><span class="n-title">${final ? "Weights are final." : "Weights are placeholders."}</span> ${esc(text)}.</p>`;
};
function needWeightsSection() {
  const w = state.meta.need_weights, v = state.meta.need_validation;
  if (!w || !w.ranges) return "";
  const labels = Object.fromEntries(state.meta.scores.need.map((x) => [x.key, x.label]));
  const p = (x) => `${Math.round(100 * x)}%`;
  const rows = Object.keys(w.final_percent).map((c) => `<tr><td>${esc(labels[c] || c)}</td>
    <td class="num">${p(w.ranges[c].low)}–${p(w.ranges[c].high)}</td><td class="num">${p(w.ranges[c].start)}</td>
    <td class="num">${p(w.data_weights[c])}</td><td class="num"><b>${w.final_percent[c]}%</b></td></tr>`).join("");
  const OUT = { ...w.fit_outcomes, ...w.held_out_outcomes };
  const mv = Object.entries(w.metro_vs_start).map(([k, r]) => `<tr><td>${esc(OUT[k] || k)}${w.fit_outcomes[k] ? " (fitted)" : " (held out)"}</td>
    <td class="num">${r.rho.toFixed(2)}</td><td class="num">${r.diff_vs_start >= 0 ? "+" : "−"}${Math.abs(r.diff_vs_start).toFixed(3)}</td>
    <td class="num">${r.diff_ci[0].toFixed(3)} to ${r.diff_ci[1].toFixed(3)}</td><td>${r.diff_ci[0] > 0 ? "Better" : r.diff_ci[1] < 0 ? "Worse" : "No clear difference"}</td></tr>`).join("");
  const TARGET = { evictions_2023_2025: 0.30, evictions_2026: 0.30, housing_insecurity: 0.50, utility_shutoff: 0.50, cdc_svi: 0.50 };
  const val = v ? v.results.filter((r) => r.version === "chas").map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${r.rho.toFixed(2)}</td>
    <td class="num">${r.ci_lo.toFixed(2)}–${r.ci_hi.toFixed(2)}</td><td class="num">${TARGET[r.outcome].toFixed(2)}</td>
    <td>${r.ci_lo >= TARGET[r.outcome] ? "Met" : r.rho >= TARGET[r.outcome] ? "Point estimate only" : "Not met"}</td></tr>`).join("") : "";
  return `<h3>How the weights were set</h3>
    <p>With the Opportunity team's method, on the Need team's backtest design. Federal need definitions and research set a range
      for each category around the Need team's weights. The category scores are rebuilt from data that ends in 2021, and three
      outcomes that came later, one for each kind of hardship, move each weight inside its range: eviction filings, low-income
      renters leaving a tract, and housing insecurity that adults report (CDC). The final weight is half the data's suggestion and
      half the starting point.</p>
    <div class="tablewrap"><table class="rubric"><thead><tr><th>Category</th><th class="num">Range</th><th class="num">Starting point</th><th class="num">Data suggests</th><th class="num">Final</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="hint">Starting point: the Need team's group weights, which beat HUD's worst-case-needs weighting, equal weights and
      CHAS-based versions in its backtests. Ranges: income is the gate in every federal need definition but is capped so the score
      stays about housing; severe rent burden drives HUD's worst case needs; worst case needs counts only unassisted renters;
      job loss predicts eviction (Desmond &amp; Gershenson 2017).</p>
    <p>Compared with the starting point, with 95% intervals from a spatial bootstrap (neighboring tracts resampled together):</p>
    <div class="tablewrap"><table class="rubric"><thead><tr><th>Outcome</th><th class="num">ρ</th><th class="num">Change</th><th class="num">95% interval</th><th>Result</th></tr></thead>
      <tbody>${mv}</tbody></table></div>
    <p class="hint">Income alone predicts evictions and reported hardship well but is the worst of every weighting at predicting
      low-income renters leaving, which is why the ranges cap income at 35% and keep the other categories in.</p>
    ${val ? `<h3>How well today's score tracks hardship</h3>
    <p class="hint">Rank correlation with outcomes the score never uses. Targets are the Need team's, set before testing; a target
      is met only if the bottom of the interval clears it.</p>
    <div class="tablewrap"><table class="rubric"><thead><tr><th>Outcome</th><th class="num">ρ</th><th class="num">95% interval</th><th class="num">Target</th><th>Result</th></tr></thead>
      <tbody>${val}</tbody></table></div>
    <p class="hint">Swapping the low-income rent-burden metric from the ACS to HUD CHAS was tested the same way: never worse, and
      significantly better on housing insecurity, utility shutoffs and the CDC index. The supply gap is usable in about a quarter
      of tracts, so its weight applies only there.</p>` : ""}`;
}
function calibrationTable() {
  const c = state.meta.opportunity_calibration;
  if (!c) return "";
  const labels = Object.fromEntries(state.meta.scores.opportunity.map((x) => [x.key, x.label]));
  const p = (v) => `${(100 * v).toFixed(1).replace(/\.0$/, "")}%`;
  return `<h3>How the weights were set</h3>
    <p>With the Opportunity team's method. Published research sets a range for each category; two tract outcomes,
      the adult income of children from low-income families (Opportunity Atlas) and life expectancy (CDC), move each
      weight inside its range; the final weight is half the data's suggestion and half the research starting point.</p>
    <div class="tablewrap"><table class="rubric">
      <thead><tr><th>Category</th><th class="num">Research range</th><th class="num">Research start</th><th class="num">Data suggests</th><th class="num">Final</th></tr></thead>
      <tbody>${Object.keys(c.final_percent).map((d) => `<tr><td>${esc(labels[d] || d)}</td>
        <td class="num">${p(c.ranges[d].low)}–${p(c.ranges[d].high)}</td><td class="num">${p(c.ranges[d].start)}</td>
        <td class="num">${p(c.data_weights[d])}</td><td class="num"><b>${c.final_percent[d]}%</b></td></tr>`).join("")}</tbody>
    </table></div>
    <p class="hint">Tested on counties left out of the fit: the score ranks tracts in line with children's upward mobility
      (${c.validity_final.mobility.toFixed(2)}) and life expectancy (${c.validity_final.life_expectancy.toFixed(2)}).
      Moving every weight by up to ±50% leaves the ranking almost unchanged (${c.stability_final.median_rank_corr.toFixed(2)}).
      Health falls to the bottom of its range because, without the uninsured rate (which tracks income and now belongs
      to Need), its metrics don't predict either outcome.</p>`;
}
const SOURCES = [
  ["American Community Survey 2020–2024, 5-year", "U.S. Census Bureau", "https://www.census.gov/programs-surveys/acs", "Need: income, rent burden, renters, work, housing age"],
  ["CHAS 2018–2022 (Table 8)", "HUD", "https://www.huduser.gov/portal/datasets/cp.html", "Need: low-income renters paying 30%+"],
  ["LIHTC, public housing and multifamily assisted properties", "HUD", "https://hudgis-hud.opendata.arcgis.com/", "Need: affordable supply"],
  ["Opportunity Atlas", "Opportunity Insights", "https://opportunityinsights.org/data/", "Opportunity: upward mobility"],
  ["Georgia Milestones and graduation rates; school locations", "GOSA; NCES", "https://download.gosa.ga.gov/", "Opportunity: schools"],
  ["LODES jobs; Smart Location Database", "Census LEHD; EPA", "https://lehd.ces.census.gov/data/", "Opportunity: jobs access, walkability"],
  ["MARTA GTFS", "MARTA", "https://itsmarta.com/app-developer-resources.aspx", "Opportunity: transit"],
  ["Health center sites; grocery access", "HRSA; USDA ERS", "https://data.hrsa.gov/data/download", "Opportunity: health and daily needs"],
  ["AFFH environmental health index", "HUD", "https://hudgis-hud.opendata.arcgis.com/", "Opportunity: environment"],
  ["SiteLine QAP location scoring; DCA applicant and award lists", "SiteLine; Georgia DCA", "https://github.com/kmcalist682336/SitelineLIHTCStartUp", "Chance of an award"],
  ["World Geocoding Service", "Esri", "https://developers.arcgis.com/rest/geocode/", "Address suggestions and search"],
  ["OpenStreetMap tiles and Nominatim search", "OpenStreetMap contributors", "https://www.openstreetmap.org/copyright", "Map, and address search if Esri doesn't answer"],
];
const customNote = (score) => (hasCustom(score) ? `<p class="note caution"><span class="n-title">You're using custom weights.</span>
    This page describes the team's defaults, and the results below were measured with them. <a href="#about/weights">Change your weights</a>.</p>` : "");
const VIEWS = {
  weights: () => `<h2>Metric weights</h2>
    <p class="lead">Choose how much each metric counts toward the Need and Opportunity scores. The map, the chart and the list
      update as you go, and your weights are saved in this browser.</p>
    <p class="note caution"><span class="n-title">Custom weights aren't validated.</span> The team calibrated and tested the default
      weights against later outcomes. Treat scores from other weights as a what-if.</p>
    <p class="hint">Weights are relative: a metric's share is its weight divided by the total of every weight in that score, shown at
      the end of each row. A value that's missing or unreliable in a tract is skipped, and the tract's other metrics carry its weight,
      so a metric usable in few tracts moves fewer scores.</p>
    ${weightSection("need")}${weightSection("opportunity")}`,
  how: () => `<h2>How it works</h2>
    <p class="lead">The dashboard helps a nonprofit decide where to look for a LIHTC site in the 11 Atlanta Regional Commission
      counties. It brings together three tools the team built.</p>
    <ol class="steps">
      <li><b>Need</b> measures how much a tract needs affordable rental housing: low incomes, rent burden, little assisted
        housing, renter pressure, and barriers to work.</li>
      <li><b>Opportunity</b> measures what living there gives a resident: how children from low-income families do as adults,
        schools, jobs, transit, health care, groceries and clean air.</li>
      <li><b>The priority slider</b> blends the two. Toward need favors places with the most hardship; toward opportunity favors
        places that give the most. The map, the chart and the list update as you move it.</li>
      <li><b>Chance of a 9% award</b> comes from the Fundability Map: SiteLine's location points at the tract's best spot, plus
        your deal points, compared with past DCA rounds. It's shown beside the priority, not blended into it, so you can see
        whether a high-priority tract is also fundable.</li>
    </ol>
    <p>Scores are percentiles among the ${fmtInt.format(state.meta.tracts)} scored tracts: 80 means higher than 80% of them. Tracts with no
      residents (${state.meta.excluded.no_residents}) and student-heavy tracts (${state.meta.excluded.student_tracts}), where college students inflate poverty, are left out.</p>`,
  need: () => `<h2>Need score</h2>
    <p class="lead">How much a tract needs affordable rental housing. Each metric becomes a percentile (100 = most need), each
      category averages its metrics, and the score averages the categories with the weights below, re-ranked so 90 means the top tenth.</p>
    ${customNote("need")}${weightsNote("need")}
    ${categoryTable("need")}
    ${needWeightsSection()}
    <p class="hint">"Usable in" is the share of tracts where the Census estimate is precise enough to trust (its margin of error is
      under 40% of the value, 30% for income, and the value is clearly above zero). Elsewhere the value is skipped and the tract's
      other metrics carry the weight. Rent burden has no usable value in about a quarter of tracts, the supply gap in about three quarters.</p>`,
  opportunity: () => `<h2>Opportunity score</h2>
    <p class="lead">What living in a tract gives a resident. Each metric becomes a percentile (100 = most opportunity), each
      category averages its metrics, and the score averages the categories with the weights below.</p>
    ${customNote("opportunity")}${weightsNote("opportunity")}
    ${categoryTable("opportunity")}
    ${calibrationTable()}
    <p class="hint">Opportunity's metrics come from administrative and modeled data, so they're usable in every tract. School
      proficiency and upward mobility partly reflect residents' income; the Opportunity team's fairness review recommends
      composition-adjusted versions.</p>`,
  combining: () => `<h2>The priority slider</h2>
    <p class="lead">The slider blends the Need and Opportunity scores (each 0–100) with a <b>weighted geometric mean</b>.</p>
    <div class="formula">priority = need<sup>(1 − t)</sup> × opportunity<sup>t</sup>, &nbsp; where t is the slider's share for opportunity</div>
    <p>Priority stays on the 0–100 scale, between the two scores. The map shows it in fifths of the scored tracts, and a tract's
      detail says where it ranks ("top 5%").</p>
    <ul>
      <li><b>At either end</b> it is one score alone: all the way left is Need only, all the way right is Opportunity only.</li>
      <li><b>In between</b> it rewards balance. With a straight average, a tract at 95 need and 5 opportunity ties one at 50 and 50.
        With the geometric mean, the balanced tract ranks higher, because a very low score on one side can't be hidden by the other.</li>
      <li><b>Why that matters here:</b> need and opportunity run opposite across metro Atlanta (rank correlation
        ${state.meta.need_vs_opportunity_rho}). Only a few tracts are in the top third of both; the middle of the slider finds them.</li>
      <li><b>Fundability isn't in the blend.</b> The chance of an award is shown beside it.</li>
    </ul>`,
  fundability: () => { const dp = state.lines?.deal_points_of_funded_arc; return `<h2>Chance of an award</h2>
    <p class="lead">From the <a href="../fundability_site/">Fundability Map</a>. DCA scores each 9% application; the chance of an award
      depends on how far that score sits above or below the pool's typical applicant.</p>
    <ul>
      <li><b>Location points</b> are SiteLine's score under the 2024–2025 QAP at the site's 0.25-mile grid point (out of 46).</li>
      <li><b>The full scale is 98 points</b> (99 in the Other Metro pool): 47 for the location sections and 51 (52) for the deal
        sections. SiteLine covers 46 of the 47; the last is §XII's point for a site owned by a transit agency.</li>
      <li><b>Deal points</b> are the developer criteria: readiness, compliance record, financing, affordability commitments,
        supportive housing, services and the rest. The <b>deal checklist</b> scores them section by section with the QAP's own
        point values and caps, the same rules as the Fundability Map, and the two tools share your answers. Until you fill it in,
        the deal points slider sets the points, starting at a typical funded deal${dp ? `: funded ARC applications in ${dp.rounds.join("–")} claimed a median of ${dp.median}` : ""}.</li>
      <li><b>Two answers change location points.</b> §XIV revitalization plan points share a 10-point block with §XV, §XVI and §XIX,
        so the block takes whichever is higher at the site; §XVII phasing replaces §XVIII previous projects when higher. For each tract
        the tool keeps its best spot for every combination of those two answers.</li>
      <li><b>The model</b> is a logistic regression fit on past statewide 9% applications and tested on rounds it never saw. Details
        are in the Fundability Map's methodology.</li>
    </ul>
    <p class="hint">On the map and in the list, a tract's chance uses its best spot. Look up an address (or click a spot) and it's
      scored at that spot, at its nearest point on the same 0.25-mile grid the Fundability Map uses, with the same result.</p>`; },
  data: () => `<h2>Data and sources</h2>
    <p class="lead">Every source is public and needs no login, data use agreement, or key.</p>
    <div class="tablewrap"><table class="rubric"><thead><tr><th>Dataset</th><th>Publisher</th><th>Used for</th></tr></thead>
      <tbody>${SOURCES.map(([n, p, u, w]) => `<tr><td><a href="${u}" target="_blank" rel="noopener">${esc(n)}</a></td><td>${esc(p)}</td><td>${esc(w)}</td></tr>`).join("")}</tbody></table></div>
    <p><button class="btn secondary" type="button" data-download>Download every tract at the current settings (CSV)</button></p>
    <p class="hint">Validation only, never used in the tool: the Child Opportunity Index 3.0. Rebuild everything with
      <code>make combined-data</code> and <code>make combined-site-data</code> in the project repository.</p>`,
  limits: () => `<h2>Limits</h2>
    <ul>
      <li>The Need score tracks housing insecurity and social vulnerability well (about 0.7) but eviction filings only modestly
        (0.35), and its exact ranks are uncertain: survey error alone moves a typical tract a few hundred places. Read ranks as tiers.</li>
      <li>The Opportunity weights are calibrated, but rest on outcome data for children born around 1980.</li>
      <li>Census tract estimates are noisy. Many need metrics are only usable in some tracts; rent burden is missing in about a quarter.</li>
      <li>Upward mobility describes children born around 1980, and school proficiency reflects family income as well as school quality.</li>
      <li>Distances are straight-line from the tract's population center; schools are the nearest ones, not the zoned ones.</li>
      <li>The map and the list use each tract's best spot; a looked-up address is scored where it is. Location
        points come from a 0.25-mile grid, so an address takes its nearest grid point's score.</li>
      <li>Scores rank tracts within metro Atlanta, not nationally.</li>
      <li>Only the default weights were calibrated and tested. Custom metric weights are a what-if.</li>
    </ul>`,
};
function openMenu(view) {
  const dlg = $("#menu");
  if (!VIEWS[view]) view = "how";
  if (!dlg.open) dlg.showModal();
  state.menuView = view;
  $("#menu-body").innerHTML = `<article class="doc">${VIEWS[view]()}</article>`;
  document.querySelectorAll(".menu-nav a").forEach((a) => { if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  document.querySelectorAll(".doc table.rubric").forEach((tbl) => {
    const heads = [...tbl.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    tbl.querySelectorAll("tbody tr").forEach((tr) => [...tr.children].forEach((td, i) => { td.dataset.label = heads[i] || ""; }));
  });
  if (view === "weights") syncWeightShares();
  $("#menu-body").scrollTop = 0;
  $("#menu-body").focus({ preventScroll: true });
}
const selectedHash = () => (state.spot ? `#site=${state.spot.latlng.lat.toFixed(5)},${state.spot.latlng.lng.toFixed(5)}`
  : state.selected ? `#tract=${state.selected.properties.g}` : location.pathname + location.search);
function route() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("about/")) { openMenu(h.slice(6)); return; }
  if ($("#menu").open) $("#menu").close();
  const site = h.match(/^site=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/);
  if (site && state.map) {
    const latlng = L.latLng(+site[1], +site[2]), f = tractAt(latlng);
    if (f && !(state.spot && state.spot.latlng.equals(latlng))) { state.map.setView(latlng, Math.max(state.map.getZoom(), 14)); select(f, latlng, "this spot"); }
    return;
  }
  const m = h.match(/^tract=(\d{11})$/);
  if (m && state.map) {
    const f = state.feats.find((x) => x.properties.g === m[1]);
    if (f && f !== state.selected) { state.map.fitBounds(f._layer.getBounds(), { maxZoom: 14, padding: [40, 40] }); select(f); }
  }
}

/* ── the guided tour: a first-visit walkthrough of the screen, replayable from "How to use" ── */
const TOUR_KEY = "site-finder-tour-v1";
const EXAMPLE = { lat: 33.76443, lng: -84.35012, label: "1129 Euclid Ave NE, Atlanta" };
const narrow = () => window.matchMedia("(max-width: 759px)").matches;
function exampleSite() {   // a pin to explain, unless the visitor has already picked a place
  if (state.selected) return;
  const ll = L.latLng(EXAMPLE.lat, EXAMPLE.lng), f = tractAt(ll);
  if (!f) return;
  state.map.setView(ll, 14, { animate: false });
  $("#lookup-input").value = EXAMPLE.label;
  select(f, ll, "this address");
  if (railOverlay.matches) setRail(false);
}
const TOUR = [
  { title: "Welcome to the LIHTC Opportunity & Need Dashboard",
    body: `A free tool for nonprofits looking for places in metro Atlanta to build affordable rental housing with 9% Low-Income
      Housing Tax Credits. It weighs three things: how much a place needs affordable housing, what living there offers residents,
      and how likely a project there is to win the tax credits. This tour takes about a minute.` },
  { el: () => $("#lookup"), title: "Start with a place",
    body: "Type an address, or click anywhere on the map. The tool scores that exact spot." },
  { el: () => $(".mapwrap"), title: "Read the map", before: exampleSite,
    body: `Each shape is a census tract, a neighborhood of a few thousand people. Brighter blue means a higher priority for your goals.
      We've dropped a pin on an example address, ${EXAMPLE.label.split(",")[0]}, so you can follow along.` },
  { el: () => $("#bias").closest(".control"), title: "Choose what matters more",
    body: `Slide toward <b>need</b> to favor places with the most housing hardship, or toward <b>opportunity</b> to favor places with
      strong schools, jobs and health. The map and the rankings update as you slide.` },
  { el: () => $("#score-stack").closest(".control"), title: "Your estimated score",
    body: `Georgia's housing agency (DCA) scores every 9% application out of 98 points. The <b>blue</b> part comes from the site's
      location; the <b>grey</b> part comes from your project, your "deal". The tick marks the lowest score funded last year, and the
      chance of an award is underneath.` },
  { el: () => $(".deal-slider"), title: "Your deal points",
    body: `Not sure yet? Leave this at a typical funded deal (${typicalPoints()} points), or slide it for a quick what-if.` },
  { el: () => $("#deal-form"), title: "The deal checklist", noScroll: true,
    before: () => { const h = $(".deal-h"); h.style.scrollMarginTop = narrow() ? "64px" : "8px"; h.scrollIntoView({ block: "start" }); },
    body: `For an exact count, check off what your project has. Click an item's arrow to see what it means. Your answers stay in
      your browser, and the slider steps aside once you start.` },
  { el: () => $("#rail"), title: "Details for your place", before: () => { if (state.selected) showRailView("site"); setRail(true); },
    body: `The place you picked opens here: the chance of an award in plain words, and why the area scores the way it does.
      <b>Top tracts</b> goes back to the best-ranked places for your settings, with a download of every tract.` },
  { el: () => [...document.querySelectorAll("#rail .sl-save"), ...document.querySelectorAll("#rail .sl-open")].find((b) => b.offsetParent), title: "Save places to compare",
    before: () => { if (state.selected) showRailView("site"); setRail(true); },
    body: `Like a place? Press <b>Save to shortlist</b>. Your <b>Shortlist</b> keeps up to 12 places with their chances and scores,
      and puts up to 4 side by side, with the best value in each row marked.` },
  { el: () => $("#menu-btn"), title: "Settings and methodology", before: () => { if (railOverlay.matches) setRail(false); },
    body: "Change how much each measure counts, or read how every score is built and where the data comes from." },
  { title: "You're ready",
    body: `Look up an address to get started. You can replay this tour any time from <b>How to use</b> at the top of the page.` },
];
const tour = { i: -1, nodes: null, lastFocus: null };
function tourNodes() {
  if (tour.nodes) return tour.nodes;
  const block = document.createElement("div"), spot = document.createElement("div"), pop = document.createElement("div");
  block.className = "tour-block"; spot.className = "tour-spot"; pop.className = "tour-pop";
  pop.setAttribute("role", "dialog"); pop.setAttribute("aria-modal", "true"); pop.setAttribute("aria-labelledby", "tour-title");
  pop.tabIndex = -1;
  document.body.append(block, spot, pop);
  // The tour never traps anyone: a click anywhere outside its card ends the tour and goes on to what was clicked
  // (the highlighted button, a menu, the map...).
  block.addEventListener("click", (e) => {
    const x = e.clientX, y = e.clientY;
    tourEnd();
    document.elementFromPoint(x, y)?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
  });
  pop.addEventListener("click", (e) => {
    const a = e.target.closest("[data-tour]")?.dataset.tour;
    if (a === "next") tourGo(tour.i + 1); else if (a === "back") tourGo(tour.i - 1); else if (a === "close") tourEnd();
  });
  tour.nodes = { block, spot, pop };
  return tour.nodes;
}
function tourStart() {
  tour.lastFocus = document.activeElement;
  if ($("#menu").open) $("#menu").close();
  const { block, spot, pop } = tourNodes();
  [block, spot, pop].forEach((n) => { n.hidden = false; });
  document.addEventListener("keydown", tourKey, true);
  window.addEventListener("resize", tourPlace);
  document.addEventListener("scroll", tourPlace, true);
  tourGo(0);
}
function tourEnd() {
  const { block, spot, pop } = tourNodes();
  [block, spot, pop].forEach((n) => { n.hidden = true; });
  document.removeEventListener("keydown", tourKey, true);
  window.removeEventListener("resize", tourPlace);
  document.removeEventListener("scroll", tourPlace, true);
  tour.i = -1;
  try { localStorage.setItem(TOUR_KEY, "done"); } catch { /* fine */ }
  (tour.lastFocus?.isConnected ? tour.lastFocus : $("#tour-btn")).focus({ preventScroll: true });
}
function tourKey(e) {
  if (e.key === "Escape") { e.preventDefault(); tourEnd(); }
  else if (e.key === "ArrowRight" && !e.target.matches("input, select")) { e.preventDefault(); tourGo(tour.i + 1); }
  else if (e.key === "ArrowLeft" && !e.target.matches("input, select")) { e.preventDefault(); tourGo(tour.i - 1); }
  else if (e.key === "Tab") {   // keep focus in the tour card
    const f = [...tourNodes().pop.querySelectorAll("button")], first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
}
function tourGo(i) {
  if (i < 0) return;
  if (i >= TOUR.length) { tourEnd(); return; }
  tour.i = i;
  const step = TOUR[i], { pop } = tourNodes(), last = i === TOUR.length - 1;
  step.before?.();
  pop.innerHTML = `<div class="t-top"><span class="t-step">${i + 1} of ${TOUR.length}</span>
      <button class="btn icon" type="button" data-tour="close" aria-label="Close the tour">
        <svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button></div>
    <div class="t-bar" aria-hidden="true"><i style="width:${(100 * (i + 1)) / TOUR.length}%"></i></div>
    <h2 id="tour-title">${esc(step.title)}</h2><p>${step.body}</p>
    <div class="t-actions">${i > 0 ? `<button class="btn text" type="button" data-tour="back">Back</button>` : `<button class="btn text" type="button" data-tour="close">Skip the tour</button>`}
      <button class="btn primary" type="button" data-tour="next">${last ? "Get started" : i === 0 ? "Show me around" : "Next"}</button></div>`;
  const el = step.el?.();
  if (el && !step.noScroll) {
    if (narrow()) { el.style.scrollMarginTop = "64px"; el.scrollIntoView({ block: "start" }); }
    else el.scrollIntoView({ block: "nearest" });
  }
  requestAnimationFrame(() => { tourPlace(); pop.querySelector('[data-tour="next"]').focus({ preventScroll: true }); });
  setTimeout(tourPlace, 340);   // after the side panel finishes sliding
}
function visibleRect(el) {   // the element's box, clipped to the viewport and to any scrolling panel it sits in
  let r = el.getBoundingClientRect(), top = r.top, left = r.left, right = r.right, bottom = r.bottom;
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    const cs = getComputedStyle(a);
    if (/(auto|scroll|hidden)/.test(cs.overflowY + cs.overflowX)) {
      const ar = a.getBoundingClientRect();
      top = Math.max(top, ar.top); left = Math.max(left, ar.left); right = Math.min(right, ar.right); bottom = Math.min(bottom, ar.bottom);
    }
  }
  top = Math.max(top, 0); left = Math.max(left, 0); right = Math.min(right, innerWidth); bottom = Math.min(bottom, innerHeight);
  return { top, left, right, bottom, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}
function tourPlace() {
  if (tour.i < 0) return;
  const step = TOUR[tour.i], { block, spot, pop } = tourNodes(), el = step.el?.(), pad = 6, gap = 14, m = 12;
  const r = el ? visibleRect(el) : null, has = r && r.width > 0 && r.height > 0;
  block.classList.toggle("dim", !has);
  spot.hidden = !has;
  if (has) Object.assign(spot.style, { top: `${r.top - pad}px`, left: `${r.left - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px` });
  pop.classList.toggle("docked", narrow());
  if (narrow()) { pop.style.top = pop.style.left = ""; return; }
  const w = pop.offsetWidth, h = pop.offsetHeight, W = innerWidth, H = innerHeight;
  const clampY = (y) => Math.max(m, Math.min(H - h - m, y)), clampX = (x) => Math.max(m, Math.min(W - w - m, x));
  let x = (W - w) / 2, y = (H - h) / 2;
  if (has) {
    const fits = {
      right: W - (r.right + pad + gap) >= w + m, left: r.left - pad - gap >= w + m,
      below: H - (r.bottom + pad + gap) >= h + m, above: r.top - pad - gap >= h + m,
    };
    if (r.width > W * 0.5 && r.height > H * 0.5) { x = r.left + (r.width - w) / 2; y = r.bottom - h - 24; }   // a big area: sit inside it
    else if (fits.right) { x = r.right + pad + gap; y = clampY(r.top); }
    else if (fits.left) { x = r.left - pad - gap - w; y = clampY(r.top); }
    else if (fits.below) { x = clampX(r.left); y = r.bottom + pad + gap; }
    else if (fits.above) { x = clampX(r.left); y = r.top - pad - gap - h; }
  }
  pop.style.left = `${clampX(x)}px`; pop.style.top = `${clampY(y)}px`;
}

/* ── boot ── */
async function init() {
  const get = (u) => fetch(u, { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(u); return r.json(); });
  const [meta, gj, win, lines] = await Promise.all([get("data/meta.json"), get("data/tracts.geojson"), get("data/win_model.json"), get("data/funding_lines.json")]);
  Object.assign(state, { meta, feats: gj.features, win, lines });
  state.deal = loadDeal();
  state.dealPoints = typicalPoints();
  state.feats.forEach((f) => {
    const p = f.properties;
    if (p.nm) f.published = { ns: p.ns, os: p.os, nc: p.nc, oc: p.oc };
  });
  try {   // deal answers live under the shared checklist key
    const saved = JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    if (isNum(saved.bias)) state.bias = saved.bias;
    if (isNum(saved.dealPoints)) state.dealPoints = Math.min(+$("#deal").max, Math.max(+$("#deal").min, saved.dealPoints));
    for (const score of Object.keys(SCORE_FIELDS)) {   // only metrics still in the data
      state.meta.scores[score].flatMap((c) => c.metrics).forEach((m) => {
        const w = saved.weights?.[score]?.[m.key];
        if (isNum(w) && w >= 0) state.weights[score][m.key] = w;
      });
    }
  } catch { /* fine */ }
  applyWeights();
  computePriority();
  readPalette();
  initMap();
  $("#loading").hidden = true;
  get("data/grid.json").then((g) => {   // 2 MB; until it arrives, a spot is scored at the tract's best spot
    state.grid = g;
    renderShortlist();   // saved spots can now be scored where they are
    if (state.spot) { state.spot.cell = nearestCell(state.spot.latlng); renderDetail(); updateDealSummary(); syncControls(); }
  }).catch((err) => console.error(err));
  update();

  $("#bias").addEventListener("input", (e) => { state.bias = +e.target.value / 100; update(); });
  $("#deal").addEventListener("input", (e) => { state.dealPoints = +e.target.value; update(); });
  renderDeal();
  $("#deal-form").addEventListener("input", onDealInput);
  $("#deal-form").addEventListener("click", onDealStep);
  $("#deal-form").addEventListener("submit", (e) => e.preventDefault());
  $("#deal-typical").addEventListener("click", () => { state.deal = typicalDeal(); saveDeal(); renderDeal(); update(); });
  $("#deal-clear").addEventListener("click", () => { resetDeal(); renderDeal(); update(); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-open-deal]")) return;
    if (railOverlay.matches) setRail(false);
    const dv = $("#deal-view");
    setTimeout(() => {   // after the sheet starts closing, so the scroll isn't cut short
      dv.scrollIntoView({ behavior: REDUCED.matches ? "auto" : "smooth", block: "start" });
      dv.querySelector("input, select")?.focus({ preventScroll: true });
    }, 60);
  });
  $("#site-back").addEventListener("click", () => showRailView("top"));
  loadShortlist(); renderShortlist();
  $("#list-back").addEventListener("click", () => showRailView("top"));
  $("#list-close").addEventListener("click", () => { setRail(false, true); $("#rail-btn").focus({ preventScroll: true }); });
  document.addEventListener("click", onShortlistClick);
  $("#sl-body").addEventListener("change", onCompareCheck);
  $("#cmp-close").addEventListener("click", () => $("#compare").close());
  $("#compare").addEventListener("click", (e) => { if (e.target === $("#compare")) $("#compare").close(); });
  $("#site-close").addEventListener("click", () => { setRail(false, true); $("#rail-btn").focus({ preventScroll: true }); });
  $("#lookup").addEventListener("submit", onLookup);
  $("#lookup-input").addEventListener("input", onLookupInput);
  $("#lookup-input").addEventListener("keydown", onLookupKey);
  $("#lookup-input").addEventListener("blur", () => setTimeout(closeSuggest, 120));
  $("#suggest").addEventListener("mousedown", (e) => e.preventDefault());   // keep focus in the box while picking
  $("#suggest").addEventListener("click", (e) => { const li = e.target.closest("li[data-i]"); if (li) pickSuggestion(+li.dataset.i); });
  $("#toplist").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-g]");
    if (!b) return;
    const f = state.feats.find((x) => x.properties.g === b.dataset.g), bounds = f._layer.getBounds();
    if (REDUCED.matches) state.map.fitBounds(bounds, { maxZoom: 14, padding: [40, 40] });
    else state.map.flyToBounds(bounds, { maxZoom: 14, padding: [40, 40], duration: 0.8 });
    select(f);
  });
  $("#scatter").addEventListener("click", (e) => {
    const svg = e.currentTarget, pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    let best = null, bd = 64;
    for (const [x, y, f] of svg._pts || []) { const d = (x - p.x) ** 2 + (y - p.y) ** 2; if (d < bd) { bd = d; best = f; } }
    if (best) { state.map.fitBounds(best._layer.getBounds(), { maxZoom: 14, padding: [40, 40] }); select(best); }
  });
  $("#download").addEventListener("click", downloadCsv);
  document.addEventListener("click", (e) => { if (e.target.closest("[data-download]")) downloadCsv(); });

  setRail(railOverlay.matches ? false : railPref());
  railOverlay.addEventListener("change", () => setRail(railOverlay.matches ? false : railPref()));
  $("#rail-btn").addEventListener("click", () => {
    const open = $("#rail-btn").getAttribute("aria-expanded") === "true";
    if (open && state.railView === "site") { showRailView("top"); return; }   // showing a site: switch to the list
    showRailView("top"); setRail(!open, true);
  });
  $("#rail-close").addEventListener("click", () => { setRail(false, true); $("#rail-btn").focus({ preventScroll: true }); });
  $("#scrim").addEventListener("click", () => setRail(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && railOverlay.matches && $("#app").classList.contains("rail-open")) setRail(false); });

  const menu = $("#menu");
  $("#menu-btn").addEventListener("click", () => { location.hash = `#about/${state.menuView || "how"}`; });
  $("#menu-close").addEventListener("click", () => menu.close());
  menu.addEventListener("click", (e) => { if (e.target === menu) menu.close(); });
  $("#menu-body").addEventListener("input", onWeightInput);
  $("#menu-body").addEventListener("click", onWeightReset);
  menu.addEventListener("close", () => { if (location.hash.startsWith("#about/")) history.replaceState(null, "", selectedHash()); });
  window.addEventListener("hashchange", route);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readPalette(); restyle(); renderLegend(); });
  route();
  $("#tour-btn").addEventListener("click", tourStart);
  let seen = false;
  try { seen = !!localStorage.getItem(TOUR_KEY); } catch { /* fine */ }
  if (!seen && !location.hash) setTimeout(tourStart, 500);   // first visit, not a shared link
}

init().catch((err) => {
  $("#loading").textContent = "Couldn't load the data. Serve this folder over http (see combined_site/README.md).";
  console.error(err);
});
