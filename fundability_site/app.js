/* Fundability Map: draft. Reads metrics.json + data/tracts.geojson + data/grid.json.
 * Everything metric-specific lives in metrics.json; this file only knows the field names
 * documented there, so adding a metric needs no change here. */
"use strict";

const STORE_KEY = "fundability-settings-v5";   // v5: SiteLine is the whole score; other layers are context
const CLASSES = 5;   // the map shows rank in five equal classes of one hue (css --c1 … --c5)
const SECTIONS = [  // SiteLine grid columns, in QAP order, for the clicked-site breakdown
  ["XI", "Desirable/Undesirable Activities", 20], ["XII", "Community Transportation Options", 6],
  ["XIII", "Quality Education Areas", 3], ["XV", "Stable Communities", 10, true],
  ["XVI", "Community Designations", 10, true], ["XVIII", "Previous Projects", 5],
  ["XIX", "Housing Needs Characteristics", 10, true], ["XXII", "Historic Preservation", 2],
];

const state = { cfg: null, metrics: [], feats: [], grid: null, detail: null, label: null, audit: null, lines: null, deal: null, win: null, sources: null, colorBy: "fundability",
                layer: null, map: null, selected: null, pin: null };

// ?theme=light or ?theme=dark pins the color scheme (otherwise it follows the system)
{ const th = new URLSearchParams(location.search).get("theme"); if (th === "light" || th === "dark") document.documentElement.dataset.theme = th; }

/* ── motion: everything starts from what's on screen now, and respects reduced motion ── */
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
const motion = { num: {}, raf: {} };

// Numbers glide from their current on-screen value toward the new one (critically damped: no overshoot).
// A new target mid-glide just retargets from wherever the number is right now.
function animateNumbers(root = document) {
  root.querySelectorAll("[data-num]").forEach((el) => {
    const key = el.dataset.num, to = +el.dataset.val, suffix = el.dataset.suffix || "", dec = +el.dataset.dec || 0;
    const show = (v) => `${dec ? +v.toFixed(dec) : Math.round(v)}${suffix}`;
    const from = motion.num[key];
    cancelAnimationFrame(motion.raf[key]);
    if (from === undefined || REDUCED.matches || from === to) { motion.num[key] = to; el.textContent = show(to); return; }
    let v = from, last = performance.now();
    el.textContent = show(v);
    const step = (now) => {
      const dt = (now - last) / 1000; last = now;
      v += (to - v) * (1 - Math.exp(-dt / 0.09));          // ~0.35 s to settle
      if (Math.abs(to - v) < (dec ? 0.05 : 0.4)) v = to;
      motion.num[key] = v;
      el.textContent = show(v);
      if (v !== to) motion.raf[key] = requestAnimationFrame(step);
    };
    motion.raf[key] = requestAnimationFrame(step);
  });
}

// Keep folds open across re-renders (checking a box shouldn't close what you were reading).
function keepOpen(box, render) {
  const open = new Set([...box.querySelectorAll("details[id]")].filter((d) => d.open).map((d) => d.id));
  render();
  open.forEach((id) => { const d = box.querySelector(`#${id}`); if (d) { d.classList.add("instant"); d.open = true; requestAnimationFrame(() => d.classList.remove("instant")); } });
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const titleCase = (s) => String(s || "").replace(/\b\w/g, (c) => c.toUpperCase());

function fmt(m, v) {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) {
    return m.missing === "best" ? "none on record" : "n/a";
  }
  switch (m.format) {
    case "text": return esc(v);
    case "points": return `${+v.toFixed(1)}<small> / ${m.max_points}</small>`;
    case "pct": return `${(v * 100).toFixed(1)}%`;
    case "money": return `$${Math.round(v).toLocaleString()}`;
    case "count": return Math.round(v).toLocaleString();
    case "years": return `${Math.round(v)} yr`;
    case "flag": return v ? "Yes" : "No";
    case "score": return v.toFixed(1);
    default: return (+v.toFixed(2)).toLocaleString();
  }
}

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
let palette = null;   // read from the stylesheet so light and dark stay in one place
function readPalette() {
  palette = { classes: Array.from({ length: CLASSES }, (_, k) => cssVar(`--c${k + 1}`)), nodata: cssVar("--nodata"),
    ink: cssVar("--text"), line: cssVar("--surface") };
}

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
}
function saveSettings() {
  const s = { colorBy: state.colorBy, m: {} };
  state.metrics.forEach((m) => { s.m[m.id] = { on: m.enabled, w: m.weight }; });
  try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch { /* private mode: fine */ }
}

/* ── scoring: every metric -> 0-100 "better for funding", then a weighted mean ── */
function normalizeAll() {
  for (const m of state.metrics) {
    const vals = state.feats.map((f) => f.properties[m.column]).filter(isNum).sort((a, b) => a - b);
    const n = vals.length;
    const lowerCount = (v) => { let lo = 0, hi = n; while (lo < hi) { const mid = (lo + hi) >> 1; if (vals[mid] < v) lo = mid + 1; else hi = mid; } return lo; };
    const upperCount = (v) => { let lo = 0, hi = n; while (lo < hi) { const mid = (lo + hi) >> 1; if (vals[mid] <= v) lo = mid + 1; else hi = mid; } return lo; };
    for (const f of state.feats) {
      const v = f.properties[m.column];
      let s;
      if (!isNum(v)) s = m.missing === "best" ? 100 : m.missing === "worst" ? 0 : null;
      else if (m.scale === "points") s = (100 * v) / m.max_points;
      else if (m.scale === "flag") s = v ? 100 : 0;
      // percentile = share of ARC tracts this one beats, rescaled so the best value scores 100 and the
      // worst 0 (so the many tracts with zero existing LIHTC all get 100 on saturation)
      else if (m.direction === "lower") s = (100 * (n - upperCount(v))) / Math.max(1, n - upperCount(vals[0]));
      else s = (100 * lowerCount(v)) / Math.max(1, lowerCount(vals[n - 1]));
      if (s !== null && m.direction === "lower" && m.scale === "flag") s = 100 - s;
      f.norm = f.norm || {};
      f.norm[m.id] = s;
    }
  }
}

function computeFundability() {
  const on = state.metrics.filter((m) => m.enabled && m.weight > 0);
  for (const f of state.feats) {
    let sw = 0, sv = 0;
    for (const m of on) { const s = f.norm[m.id]; if (s !== null) { sw += m.weight; sv += m.weight * s; } }
    f.fund = sw > 0 ? sv / sw : null;
  }
  const ranked = state.feats.filter((f) => f.fund !== null).sort((a, b) => b.fund - a.fund);
  ranked.forEach((f, i) => { f.rank = i + 1; });
  state.rankedCount = ranked.length;
}

/* ── map ─────────────────────────────────────────────────────────────────── */
function valueForColor(f) {
  return state.colorBy === "fundability" ? f.fund : f.norm[state.colorBy];
}
function colorValue(f) {  // Fundability is colored by rank so the spread is visible; metrics by their 0-100
  if (state.colorBy !== "fundability") return f.norm[state.colorBy];
  return f.rank ? 100 * (1 - (f.rank - 1) / Math.max(1, state.rankedCount - 1)) : null;
}

function classOf(f) {   // 0 (lowest fifth) … 4 (top fifth), by rank among ARC tracts
  const v = colorValue(f);
  return v === null || v === undefined ? null : Math.min(CLASSES - 1, Math.floor((v / 100) * CLASSES));
}

function styleFor(f) {
  if (!palette) readPalette();
  const k = classOf(f), sel = state.selected === f;
  return {
    fillColor: k === null ? palette.nodata : palette.classes[k],
    fillOpacity: (k === null ? 0.5 : 0.74) * (state.map && state.map.getZoom() >= 13 ? 0.55 : 1),
    color: sel ? palette.ink : palette.line, weight: sel ? 2.5 : 0.6, opacity: sel ? 1 : 0.7,
  };
}

function restyle() {
  state.layer.eachLayer((l) => l.setStyle(styleFor(l.feature)));
  if (state.selected) state.selected._layer?.bringToFront();
  renderLegend();
}

function initMap() {
  const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView([33.82, -84.39], 9);
  // OpenStreetMap's own tiles: free, no key. Fine for light use; heavy use should self-host tiles.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  state.layer = L.geoJSON({ type: "FeatureCollection", features: state.feats }, {
    style: styleFor,
    onEachFeature: (f, l) => {
      f._layer = l;
      l.bindTooltip(() => {
        const p = f.properties;
        const pts = isNum(p.total_score_max) ? `${+p.total_score_max.toFixed(1)} of 46 location points` : "No location score";
        return `<b>${esc(p.tract_name)}</b><div class="tt-sub">${esc(p.local_gov)} · ${esc(p.county)} County</div>${pts}${f.rank ? `<div class="tt-sub">#${f.rank} of ${state.rankedCount} tracts</div>` : ""}`;
      }, { sticky: true, direction: "top", offset: [0, -8] });
      l.on("click", (e) => { e.originalEvent._tractHandled = true; selectAt(e.latlng, f); });
    },
  }).addTo(map);
  map.on("click", (e) => { // clicks outside any tract
    if (!e.originalEvent._tractHandled) selectAt(e.latlng, null);
  });
  state.map = map;
  let lastZ = map.getZoom();
  map.on("zoomend", () => { const z = map.getZoom(); if ((z >= 13) !== (lastZ >= 13)) restyle(); lastZ = z; });
}

function renderLegend() {   // five rank classes, each labelled with the location points it actually spans
  if (!palette) readPalette();
  const lo = Array(CLASSES).fill(Infinity), hi = Array(CLASSES).fill(-Infinity);
  state.feats.forEach((f) => {
    const k = classOf(f), v = f.properties.total_score_max;
    if (k === null || !isNum(v)) return;
    lo[k] = Math.min(lo[k], v); hi[k] = Math.max(hi[k], v);
  });
  const r = (v) => (isNum(v) && Number.isFinite(v) ? +v.toFixed(1) : "–");
  $("#legend").innerHTML = `<p class="lg-title">Location points, best spot in each tract</p>
    <p class="lg-sub">SiteLine's QAP score out of 46, in fifths of the ${state.rankedCount.toLocaleString()} ARC tracts</p>
    <div class="swatches" aria-hidden="true">${palette.classes.map((c) => `<span class="sw" style="background:${c}"></span>`).join("")}</div>
    <div class="ticks num">${[...lo, hi[CLASSES - 1]].map((v) => `<span>${r(v)}</span>`).join("")}</div>
    <div class="ends"><span>Fewer points</span><span>More points</span></div>
    ${state.pin ? `<div class="lg-you"><i></i>The spot you scored</div>` : ""}`;
}

/* ── selection + detail panel ────────────────────────────────────────────── */
function nearestCell(latlng) {
  if (!state.grid) return null;
  const { rows } = state.grid;
  const cos = Math.cos((latlng.lat * Math.PI) / 180);
  let best = -1, bd = Infinity;
  for (let i = 0; i < rows.length; i++) {
    const dy = rows[i][0] - latlng.lat, dx = (rows[i][1] - latlng.lng) * cos;
    const d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = i; }
  }
  const miles = Math.sqrt(bd) * 69.09;
  return best >= 0 && miles < 0.3 ? { i: best, miles } : null;
}

function selectAt(latlng, feat, label = null) {
  const prev = state.selected;
  state.selected = feat;
  state.label = label;
  if (prev?._layer) prev._layer.setStyle(styleFor(prev));
  if (feat?._layer) { feat._layer.setStyle(styleFor(feat)); feat._layer.bringToFront(); }
  if (state.pin) state.pin.remove();
  if (latlng) {
    state.pin = L.marker(latlng, { icon: L.divIcon({ className: "", html: '<div class="site-pin"></div>', iconSize: [14, 14] }) }).addTo(state.map);
  }
  renderDetail(latlng, feat, label);
  updateDealSummary();
  renderLegend();
  showView("site");
  if (latlng) history.replaceState(null, "", `#site=${latlng.lat.toFixed(5)},${latlng.lng.toFixed(5)}`);
}

const XI_NAMES = { a: "Big-box store", b: "Retail store", c: "Grocery", d: "Restaurant", e: "Hospital",
  f: "Medical provider", g: "Pharmacy", h: "Childcare", i: "Technical college", j: "School",
  l: "Community / rec center", m: "Large park", n: "Small park", o: "Library", p: "Fire / police station",
  q: "Bank", r: "Place of worship", s: "Post office" };
const XI_GROUP1 = new Set("acefghjlmo");
const mi = (v) => (isNum(v) ? `${v.toFixed(2)} mi` : "n/a");

function whyText(sec, d) {  // plain-language reason for a section's points at this spot (d = grid_detail row)
  if (!d) return "Loading details…";
  const g = (k) => d[state.detail.columns.indexOf(k)];
  switch (sec) {
    case "XI": {
      const codes = g("xi_codes"), full = [], part = [], none = [];
      state.detail.xi_categories.forEach((c, k) => {
        const pts = +codes[k] / 2, max = XI_GROUP1.has(c) ? 2.5 : 2;
        if (pts === max) full.push(XI_NAMES[c]);
        else if (pts > 0) part.push(`${XI_NAMES[c]} (${pts})`);
        else none.push(XI_NAMES[c]);
      });
      const ded = (g("xi_undesirables_within_0_25mi") || 0) + (g("xi_food_desert_deduction") || 0);
      return [full.length && `<b>Within 0.5 mi:</b> ${full.join(", ")}.`,
        part.length && `<b>Partial</b> (0.5–1.5 mi away): ${part.join(", ")}.`,
        none.length && `<b>Missed</b> (none within 1.5 mi): ${none.join(", ")}.`,
        ded ? `<b>Deductions:</b> ${ded} × −2 (nuisances/hazards within 0.25 mi or food desert).` : "",
        "Capped at 20, so missing a few categories can still score full."].filter(Boolean).join(" ");
    }
    case "XII": return `Nearest transit hub ${mi(g("xii_hub_mi"))}, nearest stop ${mi(g("xii_stop_mi"))} (route estimates). Hub within 0.25 / 0.5 / 1 mi = 5 / 4.5 / 4 pts; any stop within 0.25 / 0.5 / 1 mi = 3 / 2 / 1. The 6th point needs a transit-agency-owned site.`;
    case "XIII": return `${g("xiii_qualifying_grades") ?? "n/a"} of 13 grades (K–12) are zoned to schools that pass DCA's quality tests. 13 grades = 3 pts, 7–12 = 1.5, 3–6 = 1 (family housing).`;
    case "XV": return `${g("xv_n_above_median") ?? "n/a"} of 5 HUD stability indicators above the pool median${g("xv_basis") === "near" ? ", borrowing from nearby tracts (1 point less)" : ""}. 2 / 3 / 4+ indicators = 6 / 8 / 10 pts.`;
    case "XVI": return `Nearest Choice Neighborhood area ${mi(g("xvi_cni_mi"))}; Purpose Built Community ${mi(g("xvi_pbc_mi"))}. 10 pts only inside one, and only if that group selects the application.`;
    case "XVIII": return `${g("xviii_awards_in_lgb_15yr") ?? 0} LIHTC awards in this city since 2011 (5 pts needs none; 4 if only one, from 2019 or earlier; unincorporated land can't earn these). ${g("xviii_awards_within_1mi") ?? 0} past awards within 1 mile (3 pts if none in the last 6 years, 2 if none in 4; shorter windows near transit hubs).`;
    case "XIX": { const s = g("xix_severe_housing_pct");
      return isNum(s) ? `${(100 * s).toFixed(0)}% of low-income renter households have a severe housing problem. 5 pts needs ≥45% and a non-QCT tract; another 5 if §XV also earns ≥5.`
        : "Not applicable: §XIX counts only in the Other Metro pool."; }
    case "XXII": return `Nearest National Register historic district ${mi(g("xxii_nearest_district_mi"))} (0 = inside). 2 pts only inside one, and the deal must use historic tax credits.`;
    default: return "";
  }
}

function sectionRows(cellIdx) {
  const r = state.grid.rows[cellIdx], cols = state.grid.columns;
  const d = state.detail ? state.detail.rows[cellIdx] : null;
  const win = state.grid.block_winner[cellIdx];
  return SECTIONS.map(([sec, name, max, block]) => {
    const v = r[cols.indexOf(sec)];
    const pct = isNum(v) ? (100 * v) / max : 0;
    const missed = isNum(v) ? max - v : null;
    const blockNote = block ? (win === sec ? '<span class="tag">counts in block</span>' : '<span class="tag">in block</span>') : "";
    const deciding = DECIDING.has(sec);
    return `<div class="srow ${deciding ? "deciding" : ""}"><div class="srow-top"><span class="lbl"><span class="sec">§${sec}</span>${esc(name)}${blockNote}${deciding ? '<span class="tag key">decides awards</span>' : ""}</span>
      <span class="v">${isNum(v) ? +v.toFixed(1) : "–"}<small> / ${max}</small></span></div>
      <div class="sbar"><i style="width:${pct}%"></i></div>
      <div class="srow-sub">${missed ? `<b>${+missed.toFixed(1)} missed.</b> ` : isNum(v) ? "<b>Full points.</b> " : ""}${whyText(sec, d)}</div></div>`;
  }).join("");
}

const qapTag = (sec) => esc(/^(Core|Threshold)/.test(sec) ? sec : "§" + sec);
const DECIDING = new Set(["XII", "XIII"]);   // location sections that separated funded from unfunded (metric audit)

function auditRow(id) { return state.audit?.rows?.find((r) => r.metric_id === id) || null; }
function evidenceText(id) {  // one line from the metric audit, for "About" panels and context rows
  const r = auditRow(id);
  if (!r) return "";
  const ci = r.single_auc_ci ? ` (95% range ${Math.round(100 * r.single_auc_ci[0])}–${Math.round(100 * r.single_auc_ci[1])}%)` : "";
  const lift = isNum(r.lift_over_siteline) ? ` Added to SiteLine, it changed held-out accuracy by ${r.lift_over_siteline >= 0 ? "+" : ""}${Math.round(100 * r.lift_over_siteline)} points.` : "";
  return `In past DCA rounds it ranked the funded application above an unfunded competitor ${Math.round(100 * r.single_auc)}% of the time${ci}; 50% is a coin flip.${lift}`;
}

function breakdownRows(m, p) {  // sub-scores that make up a metric; shown, never scored on their own
  return `<div class="sub">${m.breakdown.map((b) => {
    const v = p[b.column], md = p[b.column_median];
    const pct = isNum(v) ? (100 * v) / b.max_points : 0;
    return `<div class="subrow ${b.deciding ? "deciding" : ""}"><div class="row-top"><span><span class="qap">${qapTag(b.qap_section)}</span>${esc(b.label)}${b.block ? '<span class="tag">block</span>' : ""}${b.deciding ? '<span class="tag key">decides awards</span>' : ""}</span>
      <span class="row-val">${isNum(v) ? +v.toFixed(1) : "n/a"}<small> / ${b.max_points}</small></span></div>
      <div class="row-sub">${esc(b.qap_ref)} · tract median ${isNum(md) ? +md.toFixed(1) : "n/a"}</div>
      <div class="bar thin"><i style="width:${pct}%"></i></div></div>`;
  }).join("")}<div class="row-sub">§XV, §XVI and §XIX share a 10-point block: only the highest counts toward the total.</div></div>`;
}

function detailRows(m, p) {  // supporting values behind a metric
  return `<dl class="details">${m.details.map((d) => `<dt>${esc(d.label)}</dt><dd>${fmt(d, p[d.column])}</dd>`).join("")}</dl>`;
}

function metricRow(m, f) {
  const p = f.properties, s = f.norm[m.id];
  const med = m.column_median ? ` · tract median ${isNum(p[m.column_median]) ? +p[m.column_median].toFixed(1) : "n/a"}` : "";
  const included = m.enabled && m.weight > 0;
  return `<div class="row ${included ? "" : "off"}">
    <div class="row-top"><span class="row-label"><span class="qap">${qapTag(m.qap_section)}</span>${esc(m.label)}${included ? `<span class="tag in">w ${m.weight}</span>` : ""}</span>
      <span class="row-val">${fmt(m, p[m.column])}</span></div>
    <div class="row-sub">${esc(m.qap_title)} · ${esc(m.qap_ref)}${med}${s === null ? "" : ` · ${Math.round(s)}/100`}</div>
    ${s === null ? "" : `<div class="bar"><i style="width:${s}%"></i></div>`}
    ${m.details ? detailRows(m, p) : ""}${m.breakdown ? breakdownRows(m, p) : ""}</div>`;
}

function contextRow(m, f) {
  const p = f.properties, s = f.norm[m.id];
  const rank = m.scale !== "percentile" || s === null || s === undefined ? "" : ` Ranks ${Math.round(s)} of 100 among ARC tracts (100 = best for funding).`;
  return `<div class="srow">
    <div class="srow-top"><span class="lbl">${esc(m.label)}</span><span class="v">${fmt(m, p[m.column])}</span></div>
    <div class="srow-sub">${esc(m.targets || m.qap_title)} <span class="pg-inline">${esc(m.qap_ref)}</span>${rank}</div>
    ${m.details ? detailRows(m, p) : ""}</div>`;
}

function scoreParts(f, spotTotal) {  // each included metric's share of the 100-point Fundability score
  const parts = state.metrics.filter((m) => m.enabled && m.weight > 0).map((m) => ({
    m, s: m.id === "sl_total" && isNum(spotTotal) ? (100 * spotTotal) / m.max_points : f.norm[m.id],
  })).filter((x) => x.s !== null && x.s !== undefined);
  const W = parts.reduce((a, x) => a + x.m.weight, 0);
  parts.forEach((x) => { x.possible = (100 * x.m.weight) / W; x.earned = (x.possible * x.s) / 100; });
  return { parts, total: parts.reduce((a, x) => a + x.earned, 0) };
}


const CHEV = '<svg class="ico chev" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4"/></svg>';

function renderDetail(latlng, f, label) {
  $("#site-empty").hidden = true;
  const box = $("#site-detail");
  box.hidden = false;
  if (!f) {
    box.innerHTML = `<div class="place"><h2>${label ? esc(label) : "Outside the 11-county region"}</h2></div>
      <div class="card"><p>SiteLine and this tool cover the 11 Atlanta Regional Commission counties only. ${label ? "This address is outside them." : "Click inside a shaded tract."}</p></div>`;
    return;
  }
  const p = f.properties;
  const cell = latlng ? nearestCell(latlng) : null;
  if (cell && !state.detail) loadDetail();
  const c = state.grid?.columns, row = cell ? state.grid.rows[cell.i] : null;
  const spotTotal = row ? row[c.indexOf("total_score")] : null;

  // location points: the sections that decide awards in the summary line, the full breakdown one click away
  const sec = (k) => (row && isNum(row[c.indexOf(k)]) ? +row[c.indexOf(k)].toFixed(1) : "–");
  const locCard = cell ? `<details class="card fold" id="loc-card"><summary><span class="s-title">Location points</span>
      <span class="s-val">${isNum(spotTotal) ? +spotTotal.toFixed(1) : "–"}<span class="hint"> / 46</span></span>${CHEV}
      <span class="s-sub">Transit ${sec("XII")} / 6 · Schools ${sec("XIII")} / 3 · Amenities ${sec("XI")} / 20</span></summary>
      <div class="fold-body"><p class="hint">SiteLine's QAP score at the nearest 0.25-mile grid point. Transit (§XII) and schools (§XIII) are
        the location points that separated funded from unfunded applications; most applicants already max out §XI. §XV, §XVI and §XIX
        share one 10-point block: only the highest counts.</p>
      ${sectionRows(cell.i)}</div></details>`
    : `<div class="card"><p class="hint">${state.grid ? "No SiteLine grid point within 0.3 mi of this spot." : "Loading SiteLine grid…"}</p></div>`;

  // context: one line of the essentials, details on demand
  const ctx = state.metrics.filter((m) => m.group === "context");
  const ctxLine = [p.basis_boost_2026 === 1 ? "Basis boost" : "No basis boost",
    isNum(p.years_since_local_award) ? `last ${p.local_gov} award ${Math.round(p.years_since_local_award)} yr ago` : `no award on record in ${p.local_gov}`]
    .map(esc).join(" · ");
  const ctxCard = ctx.length ? `<details class="card fold" id="ctx-card"><summary><span class="s-title">Context, not scored</span><span></span>${CHEV}
      <span class="s-sub">${ctxLine}</span></summary>
    <div class="fold-body"><p class="hint">${esc(state.cfg.groups.find((g) => g.id === "context")?.note || "")}</p>${ctx.map((m) => contextRow(m, f)).join("")}</div></details>` : "";

  const pills = [[titleCase(p.pool) + " pool", true], [p.local_gov, true], ["QCT 2026", p.qct_2026 === 1], ["Small Area DDA 2026", p.dda_2026 === 1]]
    .filter(([, on]) => on).map(([t]) => `<span class="pill">${esc(t)}</span>`).join("");
  keepOpen(box, () => { box.innerHTML = `<div class="place"><h2>${esc(label || p.tract_name)}</h2>
      <p class="meta">${label ? `${esc(p.tract_name)} · ` : ""}${esc(p.county)} County${f.rank ? ` · #${f.rank} of ${state.rankedCount} tracts for location` : ""}</p>
      <div class="pills">${pills}</div></div>
    ${cell ? estimateCard(cell.i, p) : ""}${latlng ? floodNote(latlng, p) : ""}${locCard}${ctxCard}`; });
  animateNumbers(box);
}

/* ── flood warning: FEMA National Flood Hazard Layer at the exact spot (no key) ── */
const NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query";

function floodHtml(res, p) {
  const tract = p.nri_inland_flood_rating ? ` FEMA rates the tract's overall inland flood risk "${esc(p.nri_inland_flood_rating)}".` : "";
  const foot = `<div class="srow-sub">A warning only, not part of the score. Threshold §VI (p. 44): floodplains and floodways must be addressed in DCA's environmental review.${tract}</div>`;
  if (!res) return `<div class="note"><span class="n-title">Flood map</span> · checking FEMA's flood map at this spot…</div>`;
  if (res.error) return `<div class="note"><span class="n-title">Flood map unavailable.</span> FEMA's flood map didn't respond. Check the spot at <a href="https://msc.fema.gov/portal/search" target="_blank" rel="noopener">msc.fema.gov</a>.${foot}</div>`;
  const z = res.zones[0];
  if (!z) return `<div class="note"><span class="n-title">No flood map data</span> from FEMA at this spot.${foot}</div>`;
  const sub = String(z.ZONE_SUBTY || "");
  if (z.SFHA_TF === "T") {
    const fw = /FLOODWAY/i.test(sub);
    return `<div class="note warn"><span class="n-title">In the 100-year floodplain (FEMA zone ${esc(z.FLD_ZONE)}${fw ? ", regulatory floodway" : ""}).</span>
      ${fw ? "Building in a floodway is heavily restricted." : "Expect flood insurance, elevation requirements, and extra environmental review."}${foot}</div>`;
  }
  if (/0\.2 PCT/i.test(sub)) return `<div class="note caution"><span class="n-title">500-year floodplain</span> (FEMA zone X, moderate risk). Usually workable, but check the site plan.${foot}</div>`;
  return `<div class="note"><span class="n-title">Not in a mapped flood hazard area</span> (FEMA zone ${esc(z.FLD_ZONE)}${sub ? `: ${esc(sub.toLowerCase())}` : ""}).${foot}</div>`;
}

function floodNote(latlng, p) {
  const key = `${latlng.lat.toFixed(5)},${latlng.lng.toFixed(5)}`;
  if (state.flood?.key === key) return `<div id="flood-note">${floodHtml(state.flood.res, p)}</div>`;
  state.flood = { key, res: null };
  const qs = new URLSearchParams({ geometry: `${latlng.lng},${latlng.lat}`, geometryType: "esriGeometryPoint", inSR: "4326",
    spatialRel: "esriSpatialRelIntersects", outFields: "FLD_ZONE,ZONE_SUBTY,SFHA_TF", returnGeometry: "false", f: "json" });
  fetch(`${NFHL}?${qs}`, { signal: AbortSignal.timeout(12000) }).then((r) => r.json()).then((j) => ({ zones: (j.features || []).map((f) => f.attributes) }))
    .catch(() => ({ error: true })).then((res) => {
      if (state.flood?.key !== key) return;           // the user has moved on
      state.flood.res = res;
      const el = document.getElementById("flood-note");
      if (el) el.innerHTML = floodHtml(res, p);
    });
  return `<div id="flood-note">${floodHtml(null, p)}</div>`;
}

let detailLoading = null;
function loadDetail() {
  detailLoading = detailLoading || fetch("data/grid_detail.json", { cache: "no-cache" }).then((r) => r.json()).then((d) => {
    state.detail = d;
    if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label);
  }).catch(() => { detailLoading = null; });
  return detailLoading;
}

/* ── deal readiness: the QAP's developer criteria, answered once and applied to any site ── */
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
// The checklist starts blank. Until it's filled in, estimates assume the typical funded deal; once any answer is saved,
// estimates use the answers and the top-tracts board appears.
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
const dealTouched = () => !!state.dealStarted;

const CHECK = '<svg class="ico ok" viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7"/></svg>';

function itemHtml(it, a) {
  if (it.type === "check") {
    return `<label class="item"><input type="checkbox" data-id="${it.id}" ${a[it.id] ? "checked" : ""}><span>${esc(it.label)}</span><span class="pts">+${it.pts}</span></label>`;
  }
  if (it.type === "number") {
    return `<div class="stepper"><label for="d-${it.id}">${esc(it.label)}</label><span class="ctl">
      <button type="button" data-step="-1" data-for="${it.id}" aria-label="One fewer">−</button>
      <input id="d-${it.id}" type="number" inputmode="numeric" min="${it.min}" max="${it.max}" step="1" data-id="${it.id}" value="${+a[it.id] || 0}">
      <button type="button" data-step="1" data-for="${it.id}" aria-label="One more">+</button></span></div>`;
  }
  return `<fieldset class="choice"><legend>${esc(it.label)}</legend>${it.options.map(([l, p], k) => `<label class="item">
    <input type="radio" name="d-${it.id}" value="${k}" data-id="${it.id}" ${(+a[it.id] || 0) === k ? "checked" : ""}><span>${esc(l)}</span>
    <span class="pts">${p ? `+${p}` : ""}</span></label>`).join("")}</fieldset>`;
}

function renderDeal() {   // builds the form; updateDealSummary() refreshes the numbers without rebuilding it
  const a = state.deal, bySec = Object.fromEntries(DEAL.map((s) => [s.sec, s]));
  state.dealOpen = state.dealOpen || new Set();
  $("#deal-form").innerHTML = DEAL_GROUPS.map(([title, secs]) => `<p class="dgroup">${esc(title)}</p>${secs.map((k) => {
    const s = bySec[k];
    return `<details class="dsec" data-sec="${s.sec}" ${state.dealOpen.has(s.sec) ? "open" : ""}>
      <summary><span><span class="ds-title">${esc(s.title)}</span><span class="ds-ref">§${s.sec} · QAP ${esc(s.ref)}${s.pools ? " · Other Metro pool only" : ""}</span></span>
        <span class="ds-pts num" id="dpts-${s.sec}"></span>${CHEV}</summary>
      <div class="ds-body">${s.note ? `<p class="ds-note">${esc(s.note)}</p>` : ""}${s.items.map((it) => itemHtml(it, a)).join("")}</div></details>`;
  }).join("")}`).join("");
  $("#deal-form").querySelectorAll("details.dsec").forEach((d) => d.addEventListener("toggle", () => {
    if (d.open) state.dealOpen.add(d.dataset.sec); else state.dealOpen.delete(d.dataset.sec);
    syncExpandLabel();
  }));
  syncExpandLabel();
  updateDealSummary();
}

function syncExpandLabel() {
  const all = [...document.querySelectorAll("#deal-form details.dsec")];
  $("#deal-expand").textContent = all.length && all.every((d) => d.open) ? "Collapse all" : "Expand all";
}

function updateDealSummary() {
  const selP = state.selected?.properties;
  const sc = dealScore(state.deal, selP?.pool, selP?.qct_2026 === 1);
  const dp = state.lines?.deal_points_of_funded_arc;
  DEAL.forEach((s) => {
    const el = document.getElementById(`dpts-${s.sec}`);
    if (!el) return;
    el.innerHTML = `<span><b>${+sc.by[s.sec].toFixed(1)}</b> / ${s.max}</span>${CHECK}`;
    el.closest("details")?.classList.toggle("full", sc.by[s.sec] >= s.max);
  });
  // the chance at the site being scored, so it stays in view while the checklist scrolls
  const sel = state.selected, cell = sel && state.pin && state.grid ? nearestCell(state.pin.getLatLng()) : null;
  let live = "";
  if (cell && state.lines && state.win) {
    const e = estimateParts(cell.i, sel.properties, state.deal, !dealTouched()), pr = winProb(e.total, sel.properties.pool);
    if (pr !== null) {
      live = `<p class="live"><b data-num="live" data-val="${100 * pr}" data-suffix="%">${Math.round(100 * pr)}%</b>
        <span>chance at ${esc(state.label ? state.label.split(",")[0] : sel.properties.tract_name)}${dealTouched() ? "" : ", typical deal"}</span></p>`;
    }
  }
  const pts = +sc.other.toFixed(1), med = dp?.median;
  $("#deal-total").innerHTML = `<div class="dt-row"><span class="dt-val" data-num="dealpts" data-dec="1" data-val="${pts}">${pts}</span>
      <span class="dt-of">of ${sc.max} deal points${sc.xiv ? ` · §XIV ${sc.xiv}` : ""}${sc.phased ? " · phased +4" : ""}</span></div>
    <div class="dmeter" style="--p:${Math.min(1, pts / sc.max)};--m:${med ? med / sc.max : 0}" role="img"
      aria-label="${pts} of ${sc.max} deal points${med ? `; funded applications claimed a median of ${med}` : ""}"><i></i>${med ? `<b></b><span>Funded median ${med}</span>` : ""}</div>${live}`;
  $("#rail-btn-pts").textContent = dealTouched() ? `${pts} pts` : "";
  animateNumbers($("#deal-total"));
  renderBoard();
}

function onDealInput(e) {
  const id = e.target.dataset.id;
  if (!id) return;
  if (e.target.type === "number") {
    const v = Math.max(+e.target.min, Math.min(+e.target.max, Math.round(+e.target.value || 0)));
    state.deal[id] = v;
  } else state.deal[id] = e.target.type === "checkbox" ? e.target.checked : +e.target.value;
  saveDeal(); updateDealSummary();
  if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label);
  if (!$("#top-view").hidden) renderTop();
}

function onDealStep(e) {
  const b = e.target.closest("[data-step]");
  if (!b) return;
  const inp = document.getElementById(`d-${b.dataset.for}`);
  inp.value = Math.max(+inp.min, Math.min(+inp.max, (+inp.value || 0) + +b.dataset.step));
  inp.dispatchEvent(new Event("input", { bubbles: true }));
}

/* ── top tracts for the deal: each tract's best grid point, with the checklist's points, as a chance of an award ── */
let cellTract = null;   // grid row -> index into state.feats (-1 = outside every tract)
function mapCellsToTracts() {
  if (cellTract || !state.grid) return cellTract;
  const rows = state.grid.rows, B = 0.05, bins = new Map(), key = (i, j) => i * 100000 + j;
  const bounds = state.feats.map((f) => f._layer.getBounds());
  const polys = state.feats.map((f) => (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates));
  bounds.forEach((b, t) => {
    for (let i = Math.floor(b.getSouth() / B); i <= Math.floor(b.getNorth() / B); i++) {
      for (let j = Math.floor(b.getWest() / B); j <= Math.floor(b.getEast() / B); j++) {
        const k = key(i, j);
        if (!bins.has(k)) bins.set(k, []);
        bins.get(k).push(t);
      }
    }
  });
  const out = new Int16Array(rows.length).fill(-1);
  for (let c = 0; c < rows.length; c++) {
    const y = rows[c][0], x = rows[c][1];
    for (const t of bins.get(key(Math.floor(y / B), Math.floor(x / B))) || []) {
      const b = bounds[t];
      if (y < b.getSouth() || y > b.getNorth() || x < b.getWest() || x > b.getEast()) continue;
      if (polys[t].some((rings) => inRing(x, y, rings[0]) && !rings.slice(1).some((h) => inRing(x, y, h)))) { out[c] = t; break; }
    }
  }
  cellTract = out;
  return out;
}

let dealRankCache = { key: null, list: null };
function rankedForDeal() {   // same arithmetic as estimateParts(), at every grid point at once
  const key = JSON.stringify(state.deal);
  if (dealRankCache.key === key) return dealRankCache.list;
  const map = mapCellsToTracts();
  if (!map || !state.win) return [];
  const rows = state.grid.rows, col = Object.fromEntries(state.grid.columns.map((k, i) => [k, i]));
  const memo = {}, best = new Map();
  for (let c = 0; c < rows.length; c++) {
    const t = map[c];
    if (t < 0) continue;
    const p = state.feats[t].properties, q = p.qct_2026 === 1, mk = `${p.pool}|${q}`;
    const sc = memo[mk] || (memo[mk] = dealScore(state.deal, p.pool, q));
    const r = rows[c], g = (k) => +r[col[k]] || 0;
    const loc = g("XI") + g("XII") + g("XIII") + g("XXII") + Math.max(g("XVIII"), sc.phased) + Math.max(g("block_score"), sc.xiv);
    const b = best.get(t);
    if (!b || loc > b.loc) best.set(t, { t, cell: c, loc, total: loc + sc.other });
  }
  const list = [...best.values()].map((b) => ({ ...b, f: state.feats[b.t], prob: winProb(b.total, state.feats[b.t].properties.pool) }))
    .filter((b) => b.prob !== null).sort((a, b) => b.prob - a.prob || b.loc - a.loc);
  dealRankCache = { key, list };
  return list;
}

const tractNo = (p) => String(p.tract_name || "").replace(/^Census Tract\s*/i, "Tract ");
function rankRow(b, byDeal, compact = false) {
  const p = b.f.properties;
  if (compact) {
    return `<li><button type="button" data-t="${b.t}" data-cell="${b.cell ?? ""}"><span class="nm">${esc(p.local_gov)} <span class="loc">${esc(tractNo(p))}</span></span>
      <span class="sc">${Math.round(100 * b.prob)}%</span></button></li>`;
  }
  const right = byDeal ? `<span class="sc">${Math.round(100 * b.prob)}%</span><span class="sc2">score ${b.total.toFixed(1)}</span>`
    : `<span class="sc">${+b.loc.toFixed(1)}</span><span class="sc2">of 46</span>`;
  return `<li><button type="button" data-t="${b.t}" data-cell="${b.cell ?? ""}"><span class="nm">${esc(p.local_gov)}</span>
    <span class="loc">${esc(tractNo(p))} · ${esc(p.county)} County</span>${right}</button></li>`;
}

function renderBoard() {
  const board = $("#board"), ready = dealTouched() && state.grid && state.win && state.lines;
  board.hidden = !ready;
  if (!ready) return;
  const list = rankedForDeal();
  $("#board-list").innerHTML = list.slice(0, 5).map((b) => rankRow(b, true, true)).join("");
  $("#board-sub").textContent = `Chance of a 9% award at each tract's best spot, with your ${+dealScore(state.deal).other.toFixed(1)} deal points.`;
}

// ── win probability (pipelines/fundability/win_probability.py): logistic curve on the margin over the pool's field ──
function winProb(score, pool) {
  const w = state.win;
  if (!w) return null;
  const P = w.next_round.pools[titleCase(pool)];
  if (!P) return null;
  const b = w.coef.margin, sd = w.next_round.drift_sd;
  const eta = w.coef.intercept + b * (score - (P.field_median + w.next_round.drift_mean));
  return 1 / (1 + Math.exp(-eta / Math.sqrt(1 + (Math.PI * b * b * sd * sd) / 8)));   // widened for next round's unknown field
}
const pointValue = (p) => (state.win ? state.win.coef.margin * p * (1 - p) : null);   // win-chance per extra point

// location + deal at a spot, with the block (§XIV) and §XVIII/§XVII swaps
function estimateParts(cellIdx, p, answers, useTypical) {
  const r = state.grid.rows[cellIdx], c = state.grid.columns, g = (k) => +r[c.indexOf(k)] || 0;
  const sc = dealScore(answers, p.pool, p.qct_2026 === 1);
  const dp = state.lines?.deal_points_of_funded_arc;
  const deal = useTypical ? dp?.median ?? 0 : sc.other;
  const blockSite = g("block_score"), block = Math.max(blockSite, useTypical ? 0 : sc.xiv);
  const prev = Math.max(g("XVIII"), useTypical ? 0 : sc.phased);
  const loc = g("XI") + g("XII") + g("XIII") + g("XXII") + prev + block;
  return { loc, deal, total: loc + deal, blockLift: block > blockSite, phaseLift: prev > g("XVIII") };
}

// "what to prioritize": each unclaimed deal item, ranked by how much it would raise the chance of an award here
function dealGains(cellIdx, p) {
  const base = estimateParts(cellIdx, p, state.deal, false), p0 = winProb(base.total, p.pool);
  const out = [];
  for (const s of DEAL) {
    if ((s.pools && !s.pools.includes(p.pool)) || s.swap) continue;   // phasing only applies to a real later phase
    for (const it of s.items) {
      const trial = { ...state.deal };
      if (it.type === "check") { if (state.deal[it.id]) continue; trial[it.id] = true; }
      else if (it.type === "number") { if (!+state.deal[it.id]) continue; trial[it.id] = 0; }
      else {
        let best = +state.deal[it.id] || 0;
        it.options.forEach((o, k) => { const tt = { ...state.deal, [it.id]: k }; if (estimateParts(cellIdx, p, tt, false).total > estimateParts(cellIdx, p, { ...state.deal, [it.id]: best }, false).total) best = k; });
        if (best === (+state.deal[it.id] || 0)) continue;
        trial[it.id] = best;
      }
      const e = estimateParts(cellIdx, p, trial, false), dpts = e.total - base.total;
      if (dpts <= 0) continue;
      const label = it.type === "radio" ? `${it.label}: ${it.options[trial[it.id]][0]}` : it.type === "number" ? `Clear: ${it.label}` : it.label;
      out.push({ sec: s.sec, label, dpts, dprob: p0 === null ? null : winProb(e.total, p.pool) - p0 });
    }
  }
  return out.sort((x, y) => y.dpts - x.dpts || (y.dprob ?? 0) - (x.dprob ?? 0));
}

// everything still unclaimed, claimed at once (items interact through caps and the block)
function allGains(cellIdx, p) {
  const trial = { ...state.deal };
  for (const s of DEAL) {
    if (s.pools && !s.pools.includes(p.pool)) continue;
    for (const it of s.items) {
      if (it.type === "check") trial[it.id] = true;
      else if (it.type === "number") trial[it.id] = 0;
      else { let best = 0; it.options.forEach((o, k) => { if (estimateParts(cellIdx, p, { ...trial, [it.id]: k }, false).total > estimateParts(cellIdx, p, { ...trial, [it.id]: best }, false).total) best = k; }); trial[it.id] = best; }
    }
  }
  delete trial.phase;                         // phasing only applies to a real later phase; don't assume it
  trial.phase = !!state.deal.phase;
  const e = estimateParts(cellIdx, p, trial, false);
  return { total: e.total, prob: winProb(e.total, p.pool) };
}

function gainsList(gains, n) {
  if (!gains.length) return `<p class="hint">Nothing left to claim: every deal point is already in.</p>`;
  return `<ol class="gains">${gains.slice(0, n).map((x) => {
    const d = x.dprob === null ? null : Math.round(100 * x.dprob);
    return `<li><span><span class="g-sec">§${x.sec}</span> ${esc(x.label)}</span><span class="g-pts">+${+x.dpts.toFixed(1)} pts</span>
      ${d === null ? "" : `<span class="g-chance ${d > 0 ? "" : "flat"}">${d > 0 ? `+${d}% chance` : "under 1% on its own"}</span>`}</li>`;
  }).join("")}</ol>`;
}

const latestLine = (poolName) => (state.lines?.lines || []).filter((l) => l.pool === poolName && isNum(l.line)).sort((x, y) => y.round - x.round)[0];
function typicalApplicant(pool) {   // next round's expected median claimed score in the pool (win_model.json)
  const P = state.win?.next_round.pools[titleCase(pool)];
  return P ? P.field_median + state.win.next_round.drift_mean : null;
}

// The signature strip: the estimated score as a bar, against the latest funding line and next round's typical applicant.
function lineStrip(score, L, typical) {
  const vals = [score, L?.line, typical].filter(isNum);
  const lo = Math.floor(Math.min(...vals) / 5) * 5 - 5, hi = Math.ceil(Math.max(...vals) / 5) * 5 + 5;
  const x = (v) => Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const edge = (v) => (x(v) < 0.14 ? "edge-l" : x(v) > 0.86 ? "edge-r" : "");
  const near = (a, b) => isNum(a) && isNum(b) && Math.abs(x(a) - x(b)) < 0.3;
  const youUp = !near(score, L?.line), youDown = !youUp && !near(score, typical);
  return `<div class="linestrip" role="img" aria-label="Estimated score ${score.toFixed(1)}${L ? `; ${L.round} funding line ${L.line}` : ""}${isNum(typical) ? `; typical applicant next round ${typical.toFixed(1)}` : ""}">
    <span class="track"></span><span class="fill" style="--s:${x(score)}"></span>
    ${L ? `<span class="tick" style="--x:${x(L.line)}"></span><span class="tlabel up ${edge(L.line)}" style="--x:${x(L.line)}">${L.round} line ${L.line}</span>` : ""}
    ${isNum(typical) ? `<span class="tick median" style="--x:${x(typical)}"></span><span class="tlabel down ${edge(typical)}" style="--x:${x(typical)}">Typical applicant ${typical.toFixed(1)}</span>` : ""}
    ${youUp || youDown ? `<span class="tlabel you ${youUp ? "up" : "down"} ${edge(score)}" style="--x:${x(score)}">You ${score.toFixed(1)}</span>` : ""}
  </div>`;
}

function estimateCard(cellIdx, p) {
  if (cellIdx == null || !state.grid) return "";
  const started = dealTouched();
  const e = estimateParts(cellIdx, p, state.deal, !started);
  const dp = state.lines?.deal_points_of_funded_arc;
  const poolName = titleCase(p.pool), L = latestLine(poolName), typical = typicalApplicant(p.pool);
  const pr = winProb(e.total, p.pool);
  const pv = pr === null ? null : pointValue(pr);
  const gap = L ? e.total - L.line : null;
  const chance = pr === null ? "–" : `<span data-num="hero" data-val="${100 * pr}" data-suffix="%">${Math.round(100 * pr)}%</span>`;
  const est = `<div class="card estimate" id="est-card">
    <div class="kpi-row"><div class="kpi"><p class="lbl">Chance of a 9% award</p><p class="val ${pr === null ? "" : "accent"}">${chance}</p></div>
      <p class="kpi-side">Estimated DCA score<br><b>${e.total.toFixed(1)}</b></p></div>
    ${lineStrip(e.total, L, typical)}
    <dl class="ledger">
      <dt>Location points${e.blockLift ? ", with your §XIV" : ""}${e.phaseLift ? ", with §XVII phasing" : ""}</dt><dd>${e.loc.toFixed(1)}</dd>
      <dt>Deal points${started ? ", from your checklist" : ", typical funded deal"}</dt><dd>${e.deal.toFixed(1)}</dd>
      <dt class="total">Estimated DCA score</dt><dd class="total">${e.total.toFixed(1)}</dd>
      ${L ? `<dt>${L.round} ${esc(poolName)} funding line</dt><dd>${L.line}<span class="gap ${gap >= 0 ? "pos" : "neg"}">${gap >= 0 ? "+" : "−"}${Math.abs(gap).toFixed(1)}</span></dd>` : ""}
    </dl>
    ${started ? "" : `<p class="assume">Assumes a typical funded deal: ${dp?.median ?? "–"} deal points, the median claimed by funded ARC applications in
      ${dp ? dp.rounds.join("–") : "recent rounds"}. <button class="btn link" type="button" data-goto="deal">Fill in your deal</button> to use your own.</p>`}
    ${pv !== null ? `<p class="hint pv">At this score, each extra point is worth about <b>${Math.round(100 * pv)} points of chance</b>.
      <a href="#about/chance">How the chance is calculated</a></p>` : ""}
  </div>`;
  if (!started) return est;
  const all = allGains(cellIdx, p);
  return `${est}<div class="card" id="gains-card"><h3>Worth the most here</h3>
    <p class="card-sub">Unchecked items, by the points and chance each would add on its own.</p>${gainsList(dealGains(cellIdx, p), 5)}
    ${all.total > e.total + 0.01 ? `<p class="gains-all">Everything still open, together (except phasing): score <b>${all.total.toFixed(1)}</b>,
      chance <b>${all.prob === null ? "–" : `${Math.round(100 * all.prob)}%`}</b>.</p>` : ""}</div>`;
}

/* ── address lookup ──────────────────────────────────────────────────────── */
const ARC_VIEWBOX = "-85.05,34.65,-83.55,33.15";   // west,north,east,south around the 11 counties

async function geocode(q) {
  const ll = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (ll) return { lat: +ll[1], lng: +ll[2], label: `${(+ll[1]).toFixed(5)}, ${(+ll[2]).toFixed(5)}` };
  // OpenStreetMap Nominatim: free, no key. Policy: at most 1 request a second, on submit only.
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us&viewbox=${ARC_VIEWBOX}&bounded=1&q=${encodeURIComponent(q)}`;
  const r = await fetch(url, { headers: { "Accept-Language": "en" } });
  if (!r.ok) throw new Error(`geocoder returned ${r.status}`);
  const j = await r.json();
  return j.length ? { lat: +j[0].lat, lng: +j[0].lon, label: j[0].display_name.replace(/, United States$/, "") } : null;
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
  const x = latlng.lng, y = latlng.lat;
  return state.feats.find((f) => {
    if (!f._layer.getBounds().contains(latlng)) return false;
    const g = f.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    return polys.some((rings) => inRing(x, y, rings[0]) && !rings.slice(1).some((h) => inRing(x, y, h)));
  }) || null;
}

function tractNear(latlng) {  // exact tract, else one within ~250 m (water and simplified edges leave small gaps)
  const hit = tractAt(latlng);
  if (hit) return { f: hit, nudged: false };
  for (const d of [0.001, 0.0025]) {
    for (const [dy, dx] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const g = tractAt(L.latLng(latlng.lat + dy * d, latlng.lng + dx * d));
      if (g) return { f: g, nudged: true };
    }
  }
  return { f: null, nudged: false };
}

let lastLookup = 0;
async function onLookup(e) {
  e.preventDefault();
  const q = $("#lookup-input").value.trim();
  if (!q) return;
  const status = $("#lookup-status");
  if (Date.now() - lastLookup < 1100) { status.textContent = "One moment…"; await new Promise((r) => setTimeout(r, 1100)); }
  lastLookup = Date.now();
  status.textContent = "Looking up…";
  try {
    const hit = await geocode(q);
    if (!hit) { status.textContent = "No match in the Atlanta region. Try adding the city and ZIP, or enter lat, lon."; return; }
    const latlng = L.latLng(hit.lat, hit.lng);
    const { f, nudged } = tractNear(latlng);
    const z = Math.max(state.map.getZoom(), 14);
    if (REDUCED.matches) state.map.setView(latlng, z); else state.map.flyTo(latlng, z, { duration: 0.8 });
    status.textContent = !f ? "Found it, but it's outside the 11 ARC counties."
      : nudged ? "This spot sits on a tract edge or water; using the nearest tract." : "";
    selectAt(latlng, f, hit.label);
  } catch (err) {
    status.textContent = "The address search didn't respond. Try again, or enter lat, lon.";
    console.error(err);
  }
}

/* ── metrics tab ─────────────────────────────────────────────────────────── */
function renderMetricsPanel() {
  if (!$("#metric-list")) return;   // the Metrics tab was removed; kept for a future multi-metric score
  const about = (m) => `<details class="about"><summary>About this metric</summary>
          <p><b>What it measures.</b> ${esc(m.measures || m.description)}</p>
          <p><b>What it targets in DCA's scoring.</b> ${esc(m.targets || m.qap_title)} <span class="pg-inline">${esc(m.qap_ref)}</span></p>
          ${evidenceText(m.id) ? `<p><b>Evidence.</b> ${esc(evidenceText(m.id))}</p>` : ""}
          <p class="src">Data: ${esc(m.source)} · ${m.direction === "lower" ? "lower is better" : "higher is better"}${m.breakdown ? ` · made up of ${m.breakdown.map((b) => "§" + esc(b.qap_section)).join(", ")}, shown on the Score a site tab` : ""}</p>
        </details>`;
  const html = state.cfg.groups.map((g) => {
    const ms = state.metrics.filter((m) => m.group === g.id);
    const scored = g.id !== "context";
    return `<div class="mgroup"><h3>${esc(g.title)}</h3>${scored ? "" : `<p class="hint">${esc(g.note)}</p>`}${ms.map((m) => `
      <div class="metric ${scored ? "" : "ctx"}" data-id="${esc(m.id)}">
        <div class="metric-top">
          ${scored ? `<label class="inc"><input type="checkbox" ${m.enabled ? "checked" : ""} aria-label="Include ${esc(m.label)}">
            <span><span class="qap">${qapTag(m.qap_section)}</span>${esc(m.label)}</span></label>`
            : `<span class="inc"><span><span class="qap">${qapTag(m.qap_section)}</span>${esc(m.label)}</span></span>`}
          <label class="mapradio"><input type="radio" name="colorby" value="${esc(m.id)}" ${state.colorBy === m.id ? "checked" : ""}> Map</label>
        </div>
        ${scored ? `<div class="w"><span>Weight</span><input type="range" min="0" max="100" step="5" value="${m.weight}" ${m.enabled ? "" : "disabled"} aria-label="Weight for ${esc(m.label)}"><output>${m.weight}</output></div>` : ""}
        ${about(m)}
      </div>`).join("")}</div>`;
  }).join("");
  $("#metric-list").innerHTML = html;
  document.querySelectorAll('input[name="colorby"]').forEach((r) => { r.checked = r.value === state.colorBy; });
}

function onMetricsInput(e) {
  const card = e.target.closest(".metric");
  if (e.target.name === "colorby") { state.colorBy = e.target.value; saveSettings(); restyle(); return; }
  if (!card) return;
  const m = state.metrics.find((x) => x.id === card.dataset.id);
  if (e.target.type === "checkbox") {
    m.enabled = e.target.checked;
    if (m.enabled && m.weight === 0) m.weight = 10;
    card.querySelector('input[type="range"]').disabled = !m.enabled;
    card.querySelector('input[type="range"]').value = m.weight;
    card.querySelector("output").textContent = m.weight;
  } else if (e.target.type === "range") {
    m.weight = +e.target.value;
    card.querySelector("output").textContent = m.weight;
  }
  update();
}

let pending = 0;
function update() {
  cancelAnimationFrame(pending);
  pending = requestAnimationFrame(() => {
    computeFundability(); restyle(); saveSettings(); renderTop();
    if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label);
  });
}

/* ── top tracts: by chance with the deal once the checklist is filled in, by location points before ── */
const rankByDeal = () => dealTouched() && !!state.grid && !!state.win;

function filteredRanked() {
  const pool = $("#pool-filter").value, county = $("#county-filter").value;
  const keep = (f) => (!pool || f.properties.pool === pool) && (!county || f.properties.county === county);
  if (rankByDeal()) return rankedForDeal().filter((b) => keep(b.f));
  return state.feats.filter((f) => f.fund !== null && keep(f)).sort((a, b) => b.fund - a.fund)
    .map((f) => ({ f, t: state.feats.indexOf(f), loc: f.properties.total_score_max }));
}

function renderTop() {
  const list = filteredRanked(), byDeal = rankByDeal();
  $("#top-h").textContent = byDeal ? "Top tracts for your deal" : "Top tracts for location";
  $("#top-sub").textContent = byDeal
    ? `${list.length.toLocaleString()} tracts, ranked by the chance of a 9% award at each tract's best spot with your ${+dealScore(state.deal).other.toFixed(1)} deal points. Showing the top 50.`
    : `${list.length.toLocaleString()} tracts, ranked by SiteLine's location points at each tract's best spot. Fill in the deal checklist to rank by the chance of an award.`;
  $("#toplist").innerHTML = list.slice(0, 50).map((b) => rankRow(b, byDeal)).join("");
}

function downloadCsv() {
  const cols = [...new Set(state.metrics.flatMap((m) => [m.column, ...(m.breakdown || []).map((b) => b.column),
    ...(m.details || []).map((d) => d.column)]))];
  const byDeal = rankByDeal();
  const head = ["geoid", "tract_name", "county", "pool", "local_gov", "location_rank", ...(byDeal ? ["est_score_with_deal", "chance_of_award"] : []), ...cols];
  const rows = filteredRanked().map((b) => {
    const p = b.f.properties;
    return [p.geoid, p.tract_name, p.county, p.pool, p.local_gov, b.f.rank, ...(byDeal ? [b.total.toFixed(1), b.prob.toFixed(3)] : []), ...cols.map((c) => p[c])];
  });
  const csv = [head, ...rows].map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = byDeal ? "fundability_tracts_for_your_deal.csv" : "fundability_tracts.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function bestCellIn(t) {   // the grid point with the most location points inside tract t
  const map = mapCellsToTracts();
  if (!map) return null;
  const tot = state.grid.columns.indexOf("total_score");
  let best = -1;
  for (let c = 0; c < map.length; c++) if (map[c] === t && (best < 0 || state.grid.rows[c][tot] > state.grid.rows[best][tot])) best = c;
  return best < 0 ? null : best;
}

function onRankClick(e) {
  const btn = e.target.closest("button[data-t]");
  if (!btn) return;
  const t = +btn.dataset.t, f = state.feats[t], b = f._layer.getBounds();
  const c = btn.dataset.cell !== "" ? +btn.dataset.cell : bestCellIn(t);
  const ll = c !== null && state.grid ? L.latLng(state.grid.rows[c][0], state.grid.rows[c][1]) : b.getCenter();
  if (REDUCED.matches) state.map.fitBounds(b, { maxZoom: 14, padding: [40, 40] });
  else state.map.flyToBounds(b, { maxZoom: 14, padding: [40, 40], duration: 0.8 });
  selectAt(ll, f);
  if (railOverlay.matches) setRail(false);
}

/* ── methodology tab ─────────────────────────────────────────────────────── */
function scaleText(m) {
  const dir = m.direction === "lower" ? "lower is better" : "higher is better";
  if (m.scale === "points") return `points ÷ ${m.max_points}`;
  if (m.scale === "flag") return "yes = 100, no = 0";
  return `rank among ARC tracts, 0 (worst) to 100 (best); ${dir}`;
}
const BLANK_TEXT = { best: "counts as best", worst: "counts as worst", skip: "skipped (weights rescale)" };

function renderMethodMetrics() {
  $("#method-metrics").innerHTML = `<p class="hint">Generated from <code>metrics.json</code>, so it always matches the site.
    </p>
  ${state.metrics.map((m) => {
    const def = state.cfg.metrics.find((x) => x.id === m.id);
    return `<div class="mcard">
      <div class="mcard-head"><span class="qap">${qapTag(m.qap_section)}</span><b>${esc(m.label)}</b>
        <span class="chip ${m.group === "context" ? "" : "on"}">${m.group === "context" ? "context: shown, not scored" : `scored, weight ${def.weight}`}</span></div>
      <p>${esc(m.description)}</p>
      ${m.breakdown ? `<p class="hint">Breakdown shown but not weighted separately: ${m.breakdown.map((b) => "§" + esc(b.qap_section)).join(", ")}.</p>` : ""}
      <dl class="facts">
        <dt>QAP rule</dt><dd>${esc(m.qap_title)} <span class="pg-inline">${esc(m.qap_ref)}</span></dd>
        <dt>Data</dt><dd>${esc(m.source)}</dd>
        <dt>Scaled as</dt><dd>${esc(scaleText(m))}</dd>
        <dt>If blank</dt><dd>${esc(BLANK_TEXT[m.missing] || m.missing)}</dd>
      </dl></div>`;
  }).join("")}`;
}


/* ── weighting tab: the backtest write-up, numbers read from data/backtest.json ── */
const pct = (v) => (isNum(v) ? `${Math.round(100 * v)}%` : "–");
const ciTxt = (ci) => (ci && isNum(ci[0]) ? `${Math.round(100 * ci[0])}–${Math.round(100 * ci[1])}%` : "–");

function weightShare(wmap, id) {  // share of the variance of map scores contributed by one metric, under weights wmap
  const ids = Object.keys(wmap).filter((k) => wmap[k] > 0);
  const rows = state.feats.map((f) => ids.map((k) => wmap[k] * (f.norm[k] ?? 50)));
  const tot = rows.map((r) => r.reduce((a, b) => a + b, 0));
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const mt = mean(tot), col = rows.map((r) => r[ids.indexOf(id)]), mc = mean(col);
  const cov = mean(col.map((c, n) => (c - mc) * (tot[n] - mt))), vt = mean(tot.map((x) => (x - mt) ** 2));
  return vt > 0 ? cov / vt : 0;
}

function renderWeighting() {
  const bt = state.backtest, au = state.audit, box = $("#weighting-body");
  if (!bt) {
    box.innerHTML = `<h2>Why these weights</h2><p>The backtest hasn't run yet. Run <code>python pipelines/fundability/backtest.py</code> to fill in this page.</p>`;
    $("#chance-body").innerHTML = `<h2>Chance of an award</h2><p>The win-probability model hasn't run yet.</p>`;
    return;
  }
  const v = bt.validation, d = bt.data, F = bt.factors;
  const loc = F.filter((f) => f.kind === "location"), deal = F.filter((f) => f.kind !== "location");
  const models = v.models || [];
  const sl = models.find((m) => m.label.startsWith("SiteLine"));
  const ref = models.find((m) => m.kind === "reference");
  const raw = Object.fromEntries(loc.filter((f) => f.weight > 0).map((f) => [f.metric_id, f.weight]));
  const rawTxt = loc.filter((f) => f.weight > 0).map((f) => `${esc(f.label.replace(/ \(.*\)$/, ""))} ${Math.round(f.weight)}`).join(", ");
  const bb = auditRow("basis_boost");
  const bbShare = raw.basis_boost ? weightShare(raw, "basis_boost") : null;
  const rounds = Object.entries(v.auc_by_round || {});
  const decision = (r) => {
    if (r.metric_id === "sl_total") return '<span class="tag in">scored</span>';
    if (["XII", "XIII"].includes(r.metric_id)) return '<span class="tag key">inside SiteLine · decides awards</span>';
    if (r.group === "SiteLine section") return '<span class="tag">inside SiteLine</span>';
    if (["basis_boost", "years_since_local_award", "lihtc_saturation", "flood_risk"].includes(r.metric_id)) return '<span class="tag">context</span>';
    if (r.group === "deal feature") return '<span class="tag">deal readiness (planned)</span>';
    if (r.group === "reference") return '<span class="tag">reference</span>';
    return '<span class="tag">tested, not used</span>';
  };
  const pctR = (x) => (isNum(x) ? `${Math.round(100 * x)}%` : "–");
  const rangeR = (c) => (c ? `${Math.round(100 * c[0])}–${Math.round(100 * c[1])}%` : "");
  const lift = (r) => (isNum(r.lift_over_siteline) ? `${r.lift_over_siteline >= 0 ? "+" : "−"}${Math.abs(Math.round(100 * r.lift_over_siteline))} pts <span class="pg-inline">(${r.lift_ci.map((x) => `${x >= 0 ? "+" : "−"}${Math.abs(Math.round(100 * x))}`).join(" to ")})</span>` : "–");
  const auditRows = (au?.rows || []).map((r) => `<tr><td>${esc(r.metric)}<div class="desc">${esc(r.qap_link)}</div></td>
      <td>${pctR(r.single_auc)} <span class="pg-inline">${rangeR(r.single_auc_ci)}</span></td><td>${lift(r)}</td>
      <td>${pctR(r.modal_share)}</td><td>${decision(r)}</td></tr>`).join("");

  box.innerHTML = `
  <h2>Why these weights</h2>
  <p class="lead"><b>Recommended score: SiteLine's location points alone (weight 100).</b> Backtested on ${d.applications} DCA 9% applications in the
    ${esc(d.region.split(",")[0])} (${esc(d.rounds)}; ${d.funded} funded, ${d.competitions} round-and-pool competitions), SiteLine ranked the
    funded application above an unfunded competitor <b>${pct(sl?.auc)}</b> of the time on held-out rounds (95% CI ${ciTxt(sl?.auc_ci)}; 50% is a coin flip).
    No other metric improved on it.</p>
  <p>The model's raw weights were <b>${rawTxt || "none"}</b>. The metric audit (section 6, below) showed the basis boost adds nothing on held-out
    rounds${bb && isNum(bb.lift_over_siteline) ? ` (${bb.lift_over_siteline < 0 ? "−" : "+"}${Math.abs(Math.round(100 * bb.lift_over_siteline))} points)` : ""}, yet as a yes/no it would drive
    ${bbShare !== null ? `<b>${Math.round(100 * bbShare)}%</b> of` : "much of"} the differences between map scores. So it is shown as context instead of scored.</p>
  <p class="hint">Backtest run ${esc(bt.run_date)}${au ? ` · audit run ${esc(au.run_date)}` : ""}.</p>

  <h4 id="w-method">1. The method: conditional logistic regression</h4>
  <p><b>Logistic regression</b> predicts a yes/no outcome (was the application funded?) from a set of inputs, and estimates how
    much each input moves the odds. Its coefficients are what we turn into weights.</p>
  <p>The <b>conditional</b> version fits how DCA actually decides. DCA doesn't judge applications one at a time: it ranks
    everyone competing in the same round and pool and funds from the top until the credits run out. So the model only
    compares each funded application with the applications it competed against. Anything shared by a whole competition
    (that year's budget, the number of applicants, that year's QAP) cancels out.</p>
  <div class="formula">
    <div>score of application <i>i</i> &nbsp;=&nbsp; <i>s<sub>i</sub></i> = β<sub>1</sub>·x<sub>1i</sub> + β<sub>2</sub>·x<sub>2i</sub> + … + β<sub>k</sub>·x<sub>ki</sub></div>
    <div>P(<i>i</i> is funded | its competition) &nbsp;=&nbsp; e<sup><i>s<sub>i</sub></i></sup> ⁄ Σ<sub><i>j</i> in the same competition</sub> e<sup><i>s<sub>j</sub></i></sup></div>
  </div>
  <p class="hint">With several winners in one competition, the same idea gives the probability of that exact set of winners
    among all sets of the same size. The β's are chosen to make the real winners as likely as possible (maximum likelihood).
    A small ridge (L2) penalty pulls them toward zero so a small dataset isn't overfit. ${esc(bt.method?.penalty || "")}.</p>

  <h4 id="w-inputs">2. The inputs, rebuilt as of each round</h4>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>Input</th><th>Built for each past application from</th><th>Role</th></tr></thead>
    <tbody>
      <tr><td>SiteLine location score</td><td>SiteLine's score at the application's address, with LIHTC awards made after that round removed</td><td>Scored</td></tr>
      <tr><td>Basis boost (QCT or DDA)</td><td>The QCT / DDA status DCA recorded on the application</td><td>Context</td></tr>
      <tr><td>Years since last local award</td><td>Awards in the same city or county before that round</td><td>Context</td></tr>
      <tr><td>LIHTC saturation</td><td>LIHTC units in the tract before that round, per 100 renter households</td><td>Context</td></tr>
      <tr><td>Flood risk (tract)</td><td>FEMA's current tract score (no history exists)</td><td>Context (spot warning)</td></tr>
      <tr><td>Credits per low-income unit · owner's prior award · nonprofit set-aside</td><td>DCA's applicant lists</td><td>Control: keeps the deal's strength from being credited to the location</td></tr>
    </tbody></table></div>
  <p class="hint">Every map metric is put on the same 0–100 scale the website uses before fitting. DCA's own final score is
    never an input: it is the decision itself.</p>

  <h4 id="w-weights">3. From coefficients to weights</h4>
  <p>Because the inputs are on the website's 0–100 scale, each coefficient β is the change in the log-odds of being funded
    per point of that metric. The website's Fundability is a weighted average of the same 0–100 values, so setting each
    weight in proportion to its coefficient makes the map rank sites the way the model does:</p>
  <div class="formula"><div>weight<sub><i>i</i></sub> &nbsp;=&nbsp; 100 · β<sub><i>i</i></sub> ⁄ Σ β<sub><i>j</i></sub></div></div>
  <p>Only coefficients that point the expected way (better metric, better odds) and keep that sign in at least 75% of the
    held-out fits get a weight; the rest are set to 0 and flagged, rather than trusted. A weight that survives this step still has
    to earn its place in the audit (section 6): it must improve held-out predictions, not just fit past ones.</p>

  <h4 id="w-test">4. How it's tested</h4>
  <ul>
    <li><b>Hold out one round at a time:</b> fit on every other round, then score the round that was left out. Every result
      below comes from rounds the model never saw.</li>
    <li><b>AUC within competition:</b> of all (funded, unfunded) pairs in the same competition, the share the score puts in the
      right order. 50% is a coin flip, 100% is perfect.</li>
    <li><b>Top-k hit rate:</b> in a competition with <i>k</i> winners, the share of winners in the score's top <i>k</i>
      (chance is about ${pct(v.chance_topk)} here, because many applicants win).</li>
    <li><b>95% ranges:</b> from resampling whole competitions 2,000 times.</li>
  </ul>

  <h4 id="w-results">5. Model results</h4>
  <h5>Which score predicts funding best (held-out rounds)</h5>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>Score</th><th>AUC</th><th>95% CI</th><th>Top-k hit rate</th></tr></thead>
    <tbody>${models.map((m) => `<tr class="${m === sl ? "hl" : ""}"><td>${esc(m.label)}</td><td>${pct(m.auc)}</td><td>${ciTxt(m.auc_ci)}</td><td>${pct(m.topk_hit_rate)}</td></tr>`).join("")}</tbody>
  </table></div>

  <h5>What each input does in the fitted model</h5>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>Input</th><th>Effect on odds of funding</th><th>95% CI (coef.)</th><th>Same sign in held-out fits</th><th>Raw weight</th></tr></thead>
    <tbody>${[...loc, ...deal].map((f) => `<tr><td>${esc(f.label)}<div class="desc">${f.kind === "location" ? "map metric" : "deal control"}</div></td>
      <td>${f.kind === "location" ? `×${f.odds_ratio_per_10pts.toFixed(2)} per 10 points` : `×${f.odds_ratio_per_10pts.toFixed(2)} per ${f.column === "d_cost" ? "1 SD cheaper" : "yes vs. no"}`}</td>
      <td>${f.ci_low.toFixed(3)} to ${f.ci_high.toFixed(3)}</td><td>${pct(f.sign_stable_share)}</td>
      <td>${f.kind === "location" ? `${Math.round(f.weight)}${f.flag ? ` <span class="tag">${esc(f.flag)}</span>` : ""}` : "–"}</td></tr>`).join("")}</tbody>
  </table></div>

  <h5>AUC by held-out round (full model)</h5>
  <div class="bars" role="img" aria-label="AUC by held-out round">${rounds.map(([yr, a]) => `
    <div class="barrow"><span class="yr">${esc(yr)}</span><span class="track"><i style="width:${100 * a}%"></i><b class="half"></b></span><span class="val">${pct(a)}</span></div>`).join("")}
    <div class="barnote">The line marks 50% (coin flip). Single rounds have only a handful of applications, so they swing widely.</div></div>

  <h4 id="w-audit">6. Metric audit: is each metric doing anything?</h4>
  <p>Every metric, each SiteLine section, and the candidate replacements we tried, scored on the same applications.
    <b>Alone</b>: how often the metric by itself ranks the funded application above an unfunded competitor (no fitting, so nothing to
    overfit); if its range includes 50%, there is no reliable signal. <b>Adds to SiteLine</b>: the change in held-out accuracy when the
    metric is added to SiteLine; negative means it adds noise. <b>Applicants identical</b>: the share of applicants with the most common
    value; a metric nearly everyone shares can't separate them.</p>
  ${au ? `<div class="tablewrap"><table class="rubric">
    <thead><tr><th>Metric</th><th>Alone</th><th>Adds to SiteLine</th><th>Applicants identical</th><th>Decision</th></tr></thead>
    <tbody>${auditRows}</tbody></table></div>` : `<p class="hint">Run <code>python pipelines/fundability/metric_audit.py</code> to fill in this table.</p>`}

  <h4 id="w-meaning">7. What it means</h4>
  <ul>
    <li><b>SiteLine carries the location signal, and transit and schools carry SiteLine.</b> §XII transit and §XIII schools are the
      sections whose ranges stay above 50%. Most applicants already max out §XI amenities, so it can't separate them.</li>
    <li><b>Basis boost, award history, saturation and flood risk are context.</b> They matter for whether a deal pencils and how ties
      break, but none of them predicted past awards, and saturation and flood ran the wrong way (both are higher in urban
      Qualified Census Tracts, where awards cluster). They're shown beside the score, not inside it.</li>
    <li><b>The replacements we tried didn't work either:</b> QCT or DDA alone, how crowded a city is with applications, a city's past
      win rate, and Atlanta's Tax Allocation Districts as a stand-in for §XIV revitalization plans.</li>
    <li><b>The deal matters more than the site.</b> The applicant's own claimed score, mostly developer points (readiness,
      financing, compliance, services), predicts ${pct(ref?.auc)} of pairs. That's why the tool estimates the full application score
      and turns it into a <a href="#about/chance">chance of an award</a>.</li>
    <li><b>Treat all of this as provisional.</b> With ${d.applications} applications the ranges are wide; “no detectable signal” is not
      proof a rule never matters. Re-run after each round.</li>
  </ul>

  <h4 id="w-run">8. Re-run it</h4>
  <p><code>python pipelines/fundability/backtest.py</code> refits the model and rewrites <code>fundability_site/data/backtest.json</code>;
    <code>python pipelines/fundability/metric_audit.py</code> rewrites <code>fundability_site/data/metric_audit.json</code>;
    <code>python pipelines/fundability/win_probability.py</code> rewrites <code>fundability_site/data/win_model.json</code>. They read the same per-round inputs
    (saved in <code>data/processed/fundability/backtest_features.csv</code>), and every backtest run is appended to
    <code>data/processed/fundability/backtest_runs.csv</code> so weight changes are traceable.</p>`;

  $("#chance-body").innerHTML = (() => { const w = state.win, dp = state.lines?.deal_points_of_funded_arc;
    if (!w) return `<h2>Chance of an award</h2><p>The win-probability model hasn't loaded yet.</p>`;
    const h = w.validation.held_out[w.validation.chosen], P = w.next_round.pools;
    const pv50 = w.coef.margin * 0.25;
    return `<h2>Chance of an award</h2>
  <p class="lead">Location says where a project <i>could</i> score; the full application score decides whether it's funded. The deal checklist turns the
    non-location criteria into points with the QAP's own point values, adds SiteLine's location points, and this model turns that
    estimated score into a <b>chance of a 9% award</b>.</p>
  <div class="formula">
    <div>margin &nbsp;=&nbsp; estimated score − median claimed score of the applications in the same round and pool</div>
    <div>P(funded) &nbsp;=&nbsp; 1 ⁄ (1 + e<sup>−(${w.coef.intercept < 0 ? "−" : ""}${Math.abs(w.coef.intercept).toFixed(2)} + ${w.coef.margin.toFixed(2)} · margin)</sup>)</div>
  </div>
  <ul>
    <li><b>Why a margin, not the raw score:</b> each QAP changes the scale (typical claimed scores were about 55 in 2013–2020, 71 in 2022–23,
      and 82 in 2024–25), so what matters is how a score compares with the field it competes against.</li>
    <li><b>Data:</b> ${w.data.applications} statewide 9% new-affordability applications (${esc(w.data.rounds)}, ${w.data.funded} funded,
      ${w.data.competitions} round-and-pool competitions), using applicants' own claimed scores, the same kind of number the checklist
      estimates. Preservation applications are scored on a separate scale and excluded.</li>
    <li><b>Tested on rounds it never saw</b> (one round held out at a time): it ranked funded above unfunded <b>${pct(h.auc)}</b> of the time
      overall and <b>${pct(h.within_competition_auc)}</b> within the same competition. Brier score ${h.brier.toFixed(3)} vs.
      ${w.validation.base_rate_brier.toFixed(3)} for guessing the base rate (lower is better). Adding the nonprofit set-aside didn't
      improve it, so it isn't used.</li>
    <li><b>For the next round</b> the field is unknown, so the site uses the ${w.next_round.based_on} median for the pool
      (${Object.entries(P).map(([k, v]) => `${esc(k)} ${v.field_median}`).join(", ")}) plus the typical year-to-year rise within one QAP
      (+${w.next_round.drift_mean.toFixed(1)} ± ${w.next_round.drift_sd.toFixed(1)} points), and widens the curve for that uncertainty.
      A new QAP for 2026 could shift the scale.</li>
  </ul>
  <h3>Calibration: does a predicted chance come true?</h3>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>Predicted chance (held out)</th><th>Applications</th><th>Average predicted</th><th>Actually funded</th></tr></thead>
    <tbody>${w.validation.calibration.map((c) => `<tr><td>${c.bin.replace(/[(\]\[]/g, "").split(",").map((x) => `${Math.max(0, Math.round(100 * +x))}%`).join("–")}</td><td>${c.n}</td><td>${pct(c.predicted)}</td><td>${pct(c.observed)}</td></tr>`).join("")}</tbody>
  </table></div>

  <h3 id="w-priorities">What nonprofits should prioritize</h3>
  <p><b>DCA's weights are the QAP's point values.</b> DCA publishes only each application's total score, not its points by section, so the
    data can't re-learn separate weights for each deal item. What the data does show is how much a point is worth: near the middle of
    the field, <b>one point moves the chance of an award by about ${Math.round(100 * pv50)} percentage points</b>. Winners leave almost
    nothing on the table${dp ? `: funded ARC applications in ${dp.rounds.join("–")} claimed a median of ${dp.median} deal points (middle half ${dp.p25}–${dp.p75}) out of about 51` : ""}.</p>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>Deal criterion</th><th>Max points</th><th>Why it matters</th></tr></thead>
    <tbody>${DEAL.filter((s) => !s.pools).sort((x, y) => y.max - x.max).map((s) => `<tr><td>§${esc(s.sec)} ${esc(s.title)}</td><td>${s.max}</td>
      <td>${s.block ? "Competes with §XV/§XVI/§XIX in the 10-point block: only helps where SiteLine's block is low" : s.swap ? "Replaces §XVIII Previous Projects when higher" : (s.max >= 5 ? `Large: missing it usually drops an application below the field (the middle half of each field spans only a few points)` : `Small, but near the line a single point is worth about ${Math.round(100 * pv50)} points of chance`)}</td></tr>`).join("")}</tbody>
  </table></div>
  <p>For a specific site, the result ranks every checklist item still unclaimed by its points and by how much it would
    raise the chance of an award there, and shows what claiming everything open would do together.</p>`; })();
  labelTableCells();
}
// On narrow screens the rubric tables stack into cards; each cell shows its column name.
function labelTableCells() {
  document.querySelectorAll(".doc table.rubric").forEach((tbl) => {
    const heads = [...tbl.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    tbl.querySelectorAll("tbody tr").forEach((tr) => [...tr.children].forEach((td, i) => {
      td.dataset.label = heads[i] || "";
      if (/^(max|auc|top-k|weight)/i.test(heads[i] || "")) td.classList.add("num");
    }));
  });
}

/* ── data tab: every source and every file, from data/sources.json ── */
const ROLE = { score: ["Drives the score", "on"], reference: ["Rules and model data", "on"], context: ["Context, not scored", ""],
  planned: ["Pulled for a planned check", ""], tested: ["Tested, not used", ""], service: ["Map and search", ""] };

function sourcesText(src) {   // plain text for pasting into a "sources of data" doc
  return src.sources.map((s, i) => `${i + 1}. ${s.name}. ${s.publisher}. ${s.vintage}. Used for: ${s.used_for}. ${s.url}` +
    (s.includes ? `\n   Includes: ${s.includes.join("; ")}. Details: ${s.details_url}` : "")).join("\n");
}

async function renderData() {
  const box = $("#data-body");
  if (!state.sources) {
    try { state.sources = await fetch("data/sources.json", { cache: "no-cache" }).then((r) => r.json()); }
    catch { box.innerHTML = `<h2>Data and sources</h2><p>Couldn't load the source list. Reload the page to try again.</p>`; return; }
  }
  const src = state.sources;
  box.innerHTML = `
  <h2>Data and sources</h2>
  <p class="lead">Everything behind the map is public and needs no login, agreement, or key. Below are the ${src.sources.length} sources
    (what each is used for and where to get it) and every file the site uses, ready to download.</p>
  <p><button class="btn secondary" type="button" id="copy-sources">Copy the source list</button> <span class="hint" id="copy-status">Plain text, numbered, with links: ready to paste into a document.</span></p>

  <h3 id="d-sources">Sources</h3>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>Dataset</th><th>Publisher</th><th>Used for</th><th>Vintage</th><th>Terms</th><th>Role</th></tr></thead>
    <tbody>${src.sources.map((s) => `<tr>
      <td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a><div class="desc">${esc(s.access)}</div>
        ${s.includes ? `<details class="about"><summary>Includes ${s.includes.length} datasets</summary><ul class="inc">${s.includes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
          <p><a href="${esc(s.details_url)}" target="_blank" rel="noopener">SiteLine data dictionary</a></p></details>` : ""}</td>
      <td>${esc(s.publisher)}</td><td>${esc(s.used_for)}</td><td>${esc(s.vintage)}</td><td>${esc(s.terms)}</td>
      <td><span class="tag ${ROLE[s.status]?.[1] === "on" ? "in" : ""}">${esc(ROLE[s.status]?.[0] || s.status)}</span></td></tr>`).join("")}</tbody>
  </table></div>

  <h3 id="d-downloads">Download the project's data</h3>
  <div class="tablewrap"><table class="rubric">
    <thead><tr><th>File</th><th>Contents</th></tr></thead>
    <tbody>${src.downloads.map((d) => `<tr><td><a href="${esc(d.file)}" download>${esc(d.file.split("/").pop())}</a></td><td>${esc(d.what)}</td></tr>`).join("")}</tbody>
  </table></div>
  <p class="hint">Everything is rebuilt by the scripts in the project's <code>pipelines/</code> folder (see <code>docs/fundability_data_sources.md</code> and
    <code>fundability_site/README.md</code>). Last updated ${esc(src.updated)}.</p>

  <details class="about"><summary>Show the source list as text</summary><pre class="srctext">${esc(sourcesText(src))}</pre></details>`;
  labelTableCells();
  $("#copy-sources").addEventListener("click", async () => {
    const text = sourcesText(src);
    try { await navigator.clipboard.writeText(text); $("#copy-status").textContent = "Copied. Paste it into your document."; }
    catch { $("#copy-status").textContent = "Your browser blocked copying: open “Show the source list as text” below and copy it from there."; }
  });
}

/* ── the results column: home (nothing scored yet), a site, or the full top-tracts list ── */
function showView(v) {
  $("#site-empty").hidden = v !== "home";
  $("#site-detail").hidden = v !== "site";
  $("#top-view").hidden = v !== "top";
  if (v === "top") { renderTop(); $("#results").scrollTop = 0; }
}

// Before anything is scored, the column shows how to start and the figures every site is compared against.
function renderEmpty() {
  const dp = state.lines?.deal_points_of_funded_arc, round = latestLine("Atlanta Metro")?.round;
  const rows = ["Atlanta Metro", "Other Metro"].map((pn) => {
    const L = latestLine(pn), t = typicalApplicant(pn);
    return `<tr><td>${pn}</td><td class="num">${L ? L.line : "–"}</td><td class="num">${isNum(t) ? t.toFixed(1) : "–"}</td></tr>`;
  }).join("");
  $("#site-empty").innerHTML = `<div class="card">
      <h2>Find a site's chance of a 9% award</h2>
      <ol class="steps-mini">
        <li><span>Look up an address above, or click anywhere on the map.</span></li>
        <li><span>Check off what your project already has in the <button class="btn link" type="button" data-goto="deal">deal checklist</button>.
          Until then, estimates assume a typical funded deal.</span></li>
        <li><span>See which items would raise the chance most, and the tracts where your deal is strongest.</span></li>
      </ol></div>
    <div class="card"><h3>What every site is compared against</h3>
      <p class="card-sub">DCA application scores. The funding line is the lowest score funded${round ? ` in ${round}` : ""}; the typical applicant is next round's expected median.</p>
      <table class="reftable"><thead><tr><th>Pool</th><th class="num">Funding line</th><th class="num">Typical applicant</th></tr></thead><tbody>${rows}</tbody></table>
      ${dp ? `<p class="card-sub ref-foot">Funded ARC applications in ${dp.rounds.join("–")} claimed a median of <b>${dp.median}</b> deal points
        (middle half ${dp.p25}–${dp.p75}), on top of up to 46 location points.</p>` : ""}</div>
    <div class="card"><h3>Where the numbers come from</h3>
      <p class="card-sub">Location points are SiteLine's scoring of the 2024–2025 QAP. The chance of an award comes from a model fit on
        Georgia DCA's 9% applications and awards, 2013–2025, and tested on rounds it never saw. All data is public.</p>
      <p class="ref-foot"><a href="#about/how">Read the methodology</a></p></div>`;
}

/* ── the checklist rail: a column on wide screens, a sheet over the map on narrow ones ── */
const railOverlay = window.matchMedia("(max-width: 1179px)");
const RAIL_KEY = "fundability-rail-open";
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
  if (open && overlay) $("#rail-close").focus({ preventScroll: true, focusVisible: false });
}

/* ── the methodology menu: a sheet from the left, one view per topic, each with its own address ── */
const MENU_VIEWS = ["how", "scoring", "metrics", "weights", "chance", "data", "limits"];
function openMenu(view) {
  const dlg = $("#menu");
  if (!dlg.open) dlg.showModal();
  if (!MENU_VIEWS.includes(view)) view = "how";
  state.menuView = view;
  document.querySelectorAll("#menu-body > .doc").forEach((a) => { a.hidden = a.dataset.view !== view; });
  document.querySelectorAll(".menu-nav a").forEach((a) => {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (view === "metrics") renderMethodMetrics();
  if (view === "weights" || view === "chance") renderWeighting();
  if (view === "data") renderData();
  labelTableCells();
  $("#menu-body").scrollTop = 0;
  $("#menu-body").focus({ preventScroll: true });   // start keyboard and screen-reader focus on the text, not the close button
  document.querySelector(".menu-nav a[aria-current]")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}
const siteHash = () => (state.pin ? `#site=${state.pin.getLatLng().lat.toFixed(5)},${state.pin.getLatLng().lng.toFixed(5)}` : location.pathname + location.search);

// #about/<view> opens the menu there; #site=lat,lon scores that spot (so a result can be shared as a link)
function route() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("about/")) { openMenu(h.slice(6)); return; }
  if ($("#menu").open) $("#menu").close();
  const m = h.match(/^site=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/);
  if (m && state.map) {
    const ll = L.latLng(+m[1], +m[2]);
    if (state.pin && state.pin.getLatLng().distanceTo(ll) < 5) return;
    state.map.setView(ll, Math.max(state.map.getZoom(), 14));
    selectAt(ll, tractNear(ll).f);
  }
}

/* ── boot ────────────────────────────────────────────────────────────────── */
async function init() {
  const [cfg, gj] = await Promise.all([
    fetch("metrics.json", { cache: "no-cache" }).then((r) => r.json()),
    fetch("data/tracts.geojson", { cache: "no-cache" }).then((r) => r.json()),
  ]);
  const saved = loadSettings();
  state.deal = loadDeal();
  state.cfg = cfg;
  state.colorBy = "fundability";   // the map always shows the score (metric-by-metric coloring was dropped with the Metrics tab)
  state.metrics = cfg.metrics.map((m) => {
    const s = saved.m?.[m.id];
    if (m.group === "context") return { ...m, enabled: false, weight: 0 };   // shown, never scored
    return { ...m, enabled: s ? s.on : !!m.in_fundability, weight: s ? s.w : (m.weight || 0) };
  });
  state.feats = gj.features;
  normalizeAll();
  computeFundability();
  readPalette();
  initMap();
  renderLegend();
  renderEmpty();
  const counties = [...new Set(state.feats.map((f) => f.properties.county))].sort();
  $("#county-filter").innerHTML += counties.map((c) => `<option>${esc(c)}</option>`).join("");
  $("#loading").textContent = "Loading SiteLine grid…";
  fetch("data/grid.json", { cache: "no-cache" }).then((r) => r.json()).then((g) => {
    state.grid = g; $("#loading").hidden = true;
    if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label);
    updateDealSummary();
  }).catch(() => { $("#loading").textContent = "Couldn't load the SiteLine grid. Reload the page to try again."; });

  // results column and map
  $("#lookup").addEventListener("submit", onLookup);
  $("#top-back").addEventListener("click", () => showView(state.selected || state.label ? "site" : "home"));
  $("#toplist").addEventListener("click", onRankClick);
  $("#pool-filter").addEventListener("change", renderTop);
  $("#county-filter").addEventListener("change", renderTop);
  $("#download").addEventListener("click", downloadCsv);

  // checklist rail
  setRail(railOverlay.matches ? false : railPref());
  railOverlay.addEventListener("change", () => setRail(railOverlay.matches ? false : railPref()));
  $("#rail-btn").addEventListener("click", () => setRail($("#rail-btn").getAttribute("aria-expanded") !== "true", true));
  $("#rail-close").addEventListener("click", () => { setRail(false, true); $("#rail-btn").focus({ preventScroll: true }); });
  $("#scrim").addEventListener("click", () => setRail(false));
  renderDeal();
  $("#deal-form").addEventListener("input", onDealInput);
  $("#deal-form").addEventListener("click", onDealStep);
  $("#deal-form").addEventListener("submit", (e) => e.preventDefault());
  const afterDealChange = () => {
    renderDeal();
    if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label);
    if (!$("#top-view").hidden) renderTop();
  };
  $("#deal-typical").addEventListener("click", () => { state.deal = typicalDeal(); saveDeal(); afterDealChange(); });
  $("#deal-clear").addEventListener("click", () => { resetDeal(); afterDealChange(); });
  $("#deal-expand").addEventListener("click", () => {
    const all = [...document.querySelectorAll("#deal-form details.dsec")], open = !all.every((d) => d.open);
    all.forEach((d) => { d.open = open; });
    syncExpandLabel();
  });
  $("#board-list").addEventListener("click", onRankClick);
  $("#board-all").addEventListener("click", () => { showView("top"); if (railOverlay.matches) setRail(false); });

  // methodology menu
  const menu = $("#menu");
  $("#menu-btn").addEventListener("click", () => { location.hash = `#about/${state.menuView || "how"}`; });
  $("#menu-close").addEventListener("click", () => menu.close());
  menu.addEventListener("click", (e) => { if (e.target === menu) menu.close(); });   // a click on the backdrop
  menu.addEventListener("close", () => { if (location.hash.startsWith("#about/")) history.replaceState(null, "", siteHash()); $("#menu-btn").focus({ preventScroll: true }); });
  window.addEventListener("hashchange", route);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && railOverlay.matches && $("#app").classList.contains("rail-open")) setRail(false); });
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-goto]");
    if (!a) return;
    e.preventDefault();
    if (a.dataset.goto === "deal") {
      if (menu.open) menu.close();
      setRail(true, true);
      document.querySelector("#deal-form details.dsec")?.querySelector("summary")?.focus({ preventScroll: true });
    }
  });

  // light and dark: the map's classes are read from the stylesheet
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readPalette(); restyle(); });

  fetch("data/win_model.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
    .then((w) => { state.win = w; renderEmpty(); updateDealSummary(); if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label); }).catch(() => {});
  fetch("data/funding_lines.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
    .then((fl) => { state.lines = fl; renderEmpty(); updateDealSummary(); if (state.selected) renderDetail(state.pin?.getLatLng(), state.selected, state.label); }).catch(() => {});
  fetch("data/metric_audit.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
    .then((au) => { state.audit = au; }).catch(() => {});
  fetch("data/backtest.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).then((bt) => { state.backtest = bt && bt.status === "complete" ? bt : null; }).catch(() => {});
  route();
}

init().catch((err) => {
  $("#loading").textContent = "Couldn't load the map data. Serve this folder over http (see fundability_site/README.md).";
  console.error(err);
});
