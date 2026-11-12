/*
 * Opportunity module for the LIHTC site-selection tool.
 *
 * A self-contained widget: map of metro Atlanta census tracts by opportunity, a profile card for
 * any tract or address, weight sliders, a side-by-side site comparison, and a methodology panel.
 * Plain JavaScript; needs Leaflet 1.9.x loaded first (window.L). Styles are injected once and
 * scoped under .opp, so it can sit inside another app.
 *
 *   const opp = OpportunityModule.mount(document.getElementById("opportunity"), {
 *     dataUrl: "data/",          // folder holding tracts.geojson, applications.json, meta.json, places.json
 *     data: null,                // or the same four objects inline: {tracts, applications, meta, places}
 *     weights: null,             // optional starting domain weights, e.g. {schools: 0.4, ...}
 *     tiles: null,               // optional basemap: {url, attribution}; off by default
 *     onChange: (state) => {},   // weights changed: {weights, scores: Map(tract -> {score, tier, index})}
 *     onSelect: (profile) => {}, // a tract was selected: {tract, score, tier, domains, facts}
 *     initialTract: "13121003500", initialPoint: {lat, lon, label},   // optional opening selection
 *     initialCompare: [{tract, point: {lat, lon, label}}],              // optional, up to 4 sites
 *   });
 *   opp.selectTract("13121001600"); opp.setWeights({...}); opp.getScores(); opp.destroy();
 *
 * The same events are dispatched on the mount element as "opportunity:change" and
 * "opportunity:select" (CustomEvent, payload in event.detail) for hosts that prefer listeners.
 */
(function (global) {
  "use strict";

  const DOMAINS = ["mobility", "schools", "jobs", "transit", "health", "environment"];
  const TIER_CLASS = ["t0", "t1", "t2", "t3", "t4"];
  const MAX_COMPARE = 4;
  const SLOT_LETTERS = ["A", "B", "C", "D"];

  // Profile-card facts, grouped by domain: [column, label, format]
  const FACTS = {
    mobility: [
      ["oa_kfr_p25", "Adult income rank of kids from low-income families", "rank"],
      ["oa_jail_p25", "Of those kids, share incarcerated as adults", "share"],
    ],
    schools: [
      ["school_prof_elem3", "Proficient, 3 nearest elementary schools", "pct"],
      ["school_prof_middle", "Proficient, nearest middle school", "pct"],
      ["school_prof_high", "Proficient, nearest high school", "pct"],
      ["school_grad_rate_high", "Graduation rate, nearest high school", "pct"],
    ],
    jobs: [
      ["epa_jobs_45min_transit", "Jobs within 45 min by transit", "int"],
      ["epa_jobs_45min_auto", "Jobs within 45 min by car", "int"],
      ["jobs_nondegree_5mi", "Jobs for workers without a degree, within 5 miles", "int"],
      ["jobs_per_nondegree_worker_5mi", "Those jobs per non-degree worker nearby", "ratio"],
      ["jobs_growth_5mi", "Job growth within 5 miles, 2018-23", "growth"],
    ],
    transit: [
      ["marta_rail_dist_mi", "Distance to MARTA rail", "mi"],
      ["marta_stops_half_mi", "MARTA stops within half a mile", "int"],
      ["epa_walkability", "Walkability", "walk"],
    ],
    health: [
      ["food_low_access_share", "People over 1 mile from a grocery store", "pct"],
      ["fqhc_dist_mi", "Distance to a community health center", "mi"],
      ["places_uninsured", "Adults without health insurance", "pct"],
    ],
    environment: [["hud_env_health", "Environmental health index", "index"]],
  };
  // Facts shown in the comparison table: [column, label, format, higherIsBetter]
  const COMPARE_FACTS = [
    ["school_prof_elem3", "Elementary proficiency", "pct", true],
    ["epa_jobs_45min_transit", "Jobs by transit, 45 min", "int", true],
    ["marta_rail_dist_mi", "To MARTA rail", "mi", false],
    ["fqhc_dist_mi", "To health center", "mi", false],
  ];

  // ------------------------------------------------------------------ formatting

  const nf = new Intl.NumberFormat("en-US");
  function fmt(v, kind) {
    if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
    switch (kind) {
      case "int": return nf.format(Math.round(v));
      case "pct": return Math.round(v) + "%";
      case "share": return (v * 100).toFixed(1) + "%";
      case "rank": return ordinal(Math.round(v * 100)) + " percentile";
      case "growth": return (v >= 0 ? "+" : "") + Math.round(v * 100) + "%";
      case "mi": return v < 10 ? v.toFixed(1) + " mi" : Math.round(v) + " mi";
      case "walk": return v.toFixed(1) + " of 20";
      case "index": return Math.round(v) + " of 100";
      case "ratio": return v.toFixed(2) + " per worker";
      default: return String(v);
    }
  }
  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function tractLabel(id) {
    const t = id.slice(5);
    const n = String(parseInt(t.slice(0, 4), 10)) + (t.slice(4) === "00" ? "" : "." + t.slice(4));
    return "Tract " + n;
  }

  // ------------------------------------------------------------------ scoring
  // Mirrors pipelines/opportunity/build_opportunity.py: weighted mean of domain scores (renormalized over the
  // domains a tract has), re-ranked to a 0-100 percentile (ties get the average rank), then
  // quintile tiers with right-closed bins like pandas.cut(include_lowest=True).

  function normalize(weights) {
    const total = DOMAINS.reduce((s, d) => s + Math.max(0, weights[d] || 0), 0);
    const out = {};
    DOMAINS.forEach((d) => (out[d] = total > 0 ? Math.max(0, weights[d] || 0) / total : 1 / 6));
    return out;
  }

  function computeScores(tracts, weights) {
    const w = normalize(weights);
    const rows = [];
    for (const t of tracts) {
      let num = 0, den = 0;
      for (const d of DOMAINS) {
        const v = t.scores[d];
        if (v !== null && v !== undefined) { num += w[d] * v; den += w[d]; }
      }
      if (den > 0) rows.push({ id: t.tract, index: num / den });
    }
    rows.sort((a, b) => a.index - b.index);
    const n = rows.length, out = new Map();
    for (let i = 0; i < n;) {
      let j = i;
      while (j + 1 < n && rows[j + 1].index === rows[i].index) j++;
      const pct = (((i + 1) + (j + 1)) / 2 / n) * 100;
      for (let k = i; k <= j; k++) {
        const tier = pct <= 20 ? 0 : pct <= 40 ? 1 : pct <= 60 ? 2 : pct <= 80 ? 3 : 4;
        out.set(rows[k].id, { index: rows[k].index, score: pct, tier });
      }
      i = j + 1;
    }
    return out;
  }

  // ------------------------------------------------------------------ geometry

  function bboxOf(geom) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
    for (const poly of polys) for (const [x, y] of poly[0]) {
      if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y;
    }
    return [x0, y0, x1, y1];
  }
  function inRing(x, y, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function inGeom(x, y, geom) {
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
    return polys.some((p) => inRing(x, y, p[0]) && !p.slice(1).some((h) => inRing(x, y, h)));
  }

  // ------------------------------------------------------------------ address lookup
  // The Census Geocoder has no CORS headers but supports JSONP, so it is called with a script tag.
  // This is the same geocoder the pipeline used, so typed addresses land in the same tracts.

  let jsonpSeq = 0;
  function geocodeAddress(address) {
    return new Promise((resolve, reject) => {
      const cb = "__oppGeocode" + ++jsonpSeq;
      const s = document.createElement("script");
      const timer = setTimeout(() => done(new Error("timeout")), 10000);
      function done(err, data) {
        clearTimeout(timer); delete global[cb]; s.remove();
        err ? reject(err) : resolve(data);
      }
      global[cb] = (data) => {
        const m = data && data.result && data.result.addressMatches;
        if (!m || !m.length) return done(new Error("nomatch"));
        done(null, { lat: m[0].coordinates.y, lon: m[0].coordinates.x, label: m[0].matchedAddress });
      };
      s.onerror = () => done(new Error("blocked"));
      s.src = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?benchmark=Public_AR_Current" +
        "&format=jsonp&callback=" + cb + "&address=" + encodeURIComponent(address);
      document.head.appendChild(s);
    });
  }

  // Search-as-you-type suggestions from Photon (komoot), an open geocoder built on OpenStreetMap
  // data that allows autocomplete use and sends CORS headers. Limited to the metro Atlanta box.
  // A picked suggestion is placed at its own point. (Re-geocoding its text with the Census service
  // can match a different street, e.g. West Peachtree for Peachtree.) Addresses typed in full
  // still go to the Census geocoder, the same one the pipeline used.
  const PHOTON = "https://photon.komoot.io/api/";
  const ARC_BBOX = "-84.95,33.2,-83.75,34.45";
  async function suggestAddresses(text, signal) {
    const url = PHOTON + "?limit=8&lang=en&bbox=" + ARC_BBOX + "&q=" + encodeURIComponent(text);
    const r = await fetch(url, { signal });
    if (!r.ok) return [];
    const seen = new Set(), out = [];
    for (const f of (await r.json()).features || []) {
      const p = f.properties || {};
      if (p.countrycode && p.countrycode !== "US") continue;
      const street = [p.housenumber, p.street || (p.housenumber ? null : p.name)].filter(Boolean).join(" ");
      if (!street) continue;
      const label = [street, p.city || p.county, ["GA", p.postcode].filter(Boolean).join(" ")].filter(Boolean).join(", ");
      if (seen.has(label)) continue;
      seen.add(label);
      out.push({ label, num: p.housenumber || null, place: p.housenumber && p.name && p.name !== p.street ? p.name : null,
        lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0] });
      if (out.length >= 5) break;
    }
    return out;
  }

  // ------------------------------------------------------------------ styles

  // Leaflet 1.9.4 core CSS (BSD-2-Clause), inlined so hosts need only the script. Image URLs removed.
  const LEAFLET_CSS = ".leaflet-image-layer,.leaflet-layer,.leaflet-marker-icon,.leaflet-marker-shadow,.leaflet-pane,.leaflet-pane>canvas,.leaflet-pane>svg,.leaflet-tile,.leaflet-tile-container,.leaflet-zoom-box{position:absolute;left:0;top:0}.leaflet-container{overflow:hidden}.leaflet-marker-icon,.leaflet-marker-shadow,.leaflet-tile{-webkit-user-select:none;-moz-user-select:none;user-select:none;-webkit-user-drag:none}.leaflet-tile::selection{background:0 0}.leaflet-safari .leaflet-tile{image-rendering:-webkit-optimize-contrast}.leaflet-safari .leaflet-tile-container{width:1600px;height:1600px;-webkit-transform-origin:0 0}.leaflet-marker-icon,.leaflet-marker-shadow{display:block}.leaflet-container .leaflet-overlay-pane svg{max-width:none!important;max-height:none!important}.leaflet-container .leaflet-marker-pane img,.leaflet-container .leaflet-shadow-pane img,.leaflet-container .leaflet-tile,.leaflet-container .leaflet-tile-pane img,.leaflet-container img.leaflet-image-layer{max-width:none!important;max-height:none!important;width:auto;padding:0}.leaflet-container img.leaflet-tile{mix-blend-mode:plus-lighter}.leaflet-container.leaflet-touch-zoom{-ms-touch-action:pan-x pan-y;touch-action:pan-x pan-y}.leaflet-container.leaflet-touch-drag{-ms-touch-action:pinch-zoom;touch-action:none;touch-action:pinch-zoom}.leaflet-container.leaflet-touch-drag.leaflet-touch-zoom{-ms-touch-action:none;touch-action:none}.leaflet-container{-webkit-tap-highlight-color:transparent}.leaflet-container a{-webkit-tap-highlight-color:rgba(51,181,229,.4)}.leaflet-tile{filter:inherit;visibility:hidden}.leaflet-tile-loaded{visibility:inherit}.leaflet-zoom-box{width:0;height:0;-moz-box-sizing:border-box;box-sizing:border-box;z-index:800}.leaflet-overlay-pane svg{-moz-user-select:none}.leaflet-pane{z-index:400}.leaflet-tile-pane{z-index:200}.leaflet-overlay-pane{z-index:400}.leaflet-shadow-pane{z-index:500}.leaflet-marker-pane{z-index:600}.leaflet-tooltip-pane{z-index:650}.leaflet-popup-pane{z-index:700}.leaflet-map-pane canvas{z-index:100}.leaflet-map-pane svg{z-index:200}.leaflet-vml-shape{width:1px;height:1px}.lvml{behavior:none;display:inline-block;position:absolute}.leaflet-control{position:relative;z-index:800;pointer-events:visiblePainted;pointer-events:auto}.leaflet-bottom,.leaflet-top{position:absolute;z-index:1000;pointer-events:none}.leaflet-top{top:0}.leaflet-right{right:0}.leaflet-bottom{bottom:0}.leaflet-left{left:0}.leaflet-control{float:left;clear:both}.leaflet-right .leaflet-control{float:right}.leaflet-top .leaflet-control{margin-top:10px}.leaflet-bottom .leaflet-control{margin-bottom:10px}.leaflet-left .leaflet-control{margin-left:10px}.leaflet-right .leaflet-control{margin-right:10px}.leaflet-fade-anim .leaflet-popup{opacity:0;-webkit-transition:opacity .2s linear;-moz-transition:opacity .2s linear;transition:opacity .2s linear}.leaflet-fade-anim .leaflet-map-pane .leaflet-popup{opacity:1}.leaflet-zoom-animated{-webkit-transform-origin:0 0;-ms-transform-origin:0 0;transform-origin:0 0}svg.leaflet-zoom-animated{will-change:transform}.leaflet-zoom-anim .leaflet-zoom-animated{-webkit-transition:-webkit-transform .25s cubic-bezier(0,0,.25,1);-moz-transition:-moz-transform .25s cubic-bezier(0,0,.25,1);transition:transform .25s cubic-bezier(0,0,.25,1)}.leaflet-pan-anim .leaflet-tile,.leaflet-zoom-anim .leaflet-tile{-webkit-transition:none;-moz-transition:none;transition:none}.leaflet-zoom-anim .leaflet-zoom-hide{visibility:hidden}.leaflet-interactive{cursor:pointer}.leaflet-grab{cursor:-webkit-grab;cursor:-moz-grab;cursor:grab}.leaflet-crosshair,.leaflet-crosshair .leaflet-interactive{cursor:crosshair}.leaflet-control,.leaflet-popup-pane{cursor:auto}.leaflet-dragging .leaflet-grab,.leaflet-dragging .leaflet-grab .leaflet-interactive,.leaflet-dragging .leaflet-marker-draggable{cursor:move;cursor:-webkit-grabbing;cursor:-moz-grabbing;cursor:grabbing}.leaflet-image-layer,.leaflet-marker-icon,.leaflet-marker-shadow,.leaflet-pane>svg path,.leaflet-tile-container{pointer-events:none}.leaflet-image-layer.leaflet-interactive,.leaflet-marker-icon.leaflet-interactive,.leaflet-pane>svg path.leaflet-interactive,svg.leaflet-image-layer.leaflet-interactive path{pointer-events:visiblePainted;pointer-events:auto}.leaflet-container{background:#ddd;outline-offset:1px}.leaflet-container a{color:#0078a8}.leaflet-zoom-box{border:2px dotted #38f;background:rgba(255,255,255,.5)}.leaflet-container{font-family:\"Helvetica Neue\",Arial,Helvetica,sans-serif;font-size:12px;font-size:.75rem;line-height:1.5}.leaflet-bar{box-shadow:0 1px 5px rgba(0,0,0,.65);border-radius:4px}.leaflet-bar a{background-color:#fff;border-bottom:1px solid #ccc;width:26px;height:26px;line-height:26px;display:block;text-align:center;text-decoration:none;color:#000}.leaflet-bar a,.leaflet-control-layers-toggle{background-position:50% 50%;background-repeat:no-repeat;display:block}.leaflet-bar a:focus,.leaflet-bar a:hover{background-color:#f4f4f4}.leaflet-bar a:first-child{border-top-left-radius:4px;border-top-right-radius:4px}.leaflet-bar a:last-child{border-bottom-left-radius:4px;border-bottom-right-radius:4px;border-bottom:none}.leaflet-bar a.leaflet-disabled{cursor:default;background-color:#f4f4f4;color:#bbb}.leaflet-touch .leaflet-bar a{width:30px;height:30px;line-height:30px}.leaflet-touch .leaflet-bar a:first-child{border-top-left-radius:2px;border-top-right-radius:2px}.leaflet-touch .leaflet-bar a:last-child{border-bottom-left-radius:2px;border-bottom-right-radius:2px}.leaflet-control-zoom-in,.leaflet-control-zoom-out{font:bold 18px 'Lucida Console',Monaco,monospace;text-indent:1px}.leaflet-touch .leaflet-control-zoom-in,.leaflet-touch .leaflet-control-zoom-out{font-size:22px}.leaflet-control-layers{box-shadow:0 1px 5px rgba(0,0,0,.4);background:#fff;border-radius:5px}.leaflet-control-layers-toggle{background-image:none;width:36px;height:36px}.leaflet-retina .leaflet-control-layers-toggle{background-image:none;background-size:26px 26px}.leaflet-touch .leaflet-control-layers-toggle{width:44px;height:44px}.leaflet-control-layers .leaflet-control-layers-list,.leaflet-control-layers-expanded .leaflet-control-layers-toggle{display:none}.leaflet-control-layers-expanded .leaflet-control-layers-list{display:block;position:relative}.leaflet-control-layers-expanded{padding:6px 10px 6px 6px;color:#333;background:#fff}.leaflet-control-layers-scrollbar{overflow-y:scroll;overflow-x:hidden;padding-right:5px}.leaflet-control-layers-selector{margin-top:2px;position:relative;top:1px}.leaflet-control-layers label{display:block;font-size:13px;font-size:1.08333em}.leaflet-control-layers-separator{height:0;border-top:1px solid #ddd;margin:5px -10px 5px -6px}.leaflet-default-icon-path{background-image:none}.leaflet-container .leaflet-control-attribution{background:#fff;background:rgba(255,255,255,.8);margin:0}.leaflet-control-attribution,.leaflet-control-scale-line{padding:0 5px;color:#333;line-height:1.4}.leaflet-control-attribution a{text-decoration:none}.leaflet-control-attribution a:focus,.leaflet-control-attribution a:hover{text-decoration:underline}.leaflet-attribution-flag{display:inline!important;vertical-align:baseline!important;width:1em;height:.6669em}.leaflet-left .leaflet-control-scale{margin-left:5px}.leaflet-bottom .leaflet-control-scale{margin-bottom:5px}.leaflet-control-scale-line{border:2px solid #777;border-top:none;line-height:1.1;padding:2px 5px 1px;white-space:nowrap;-moz-box-sizing:border-box;box-sizing:border-box;background:rgba(255,255,255,.8);text-shadow:1px 1px #fff}.leaflet-control-scale-line:not(:first-child){border-top:2px solid #777;border-bottom:none;margin-top:-2px}.leaflet-control-scale-line:not(:first-child):not(:last-child){border-bottom:2px solid #777}.leaflet-touch .leaflet-bar,.leaflet-touch .leaflet-control-attribution,.leaflet-touch .leaflet-control-layers{box-shadow:none}.leaflet-touch .leaflet-bar,.leaflet-touch .leaflet-control-layers{border:2px solid rgba(0,0,0,.2);background-clip:padding-box}.leaflet-popup{position:absolute;text-align:center;margin-bottom:20px}.leaflet-popup-content-wrapper{padding:1px;text-align:left;border-radius:12px}.leaflet-popup-content{margin:13px 24px 13px 20px;line-height:1.3;font-size:13px;font-size:1.08333em;min-height:1px}.leaflet-popup-content p{margin:17px 0;margin:1.3em 0}.leaflet-popup-tip-container{width:40px;height:20px;position:absolute;left:50%;margin-top:-1px;margin-left:-20px;overflow:hidden;pointer-events:none}.leaflet-popup-tip{width:17px;height:17px;padding:1px;margin:-10px auto 0;pointer-events:auto;-webkit-transform:rotate(45deg);-moz-transform:rotate(45deg);-ms-transform:rotate(45deg);transform:rotate(45deg)}.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#fff;color:#333;box-shadow:0 3px 14px rgba(0,0,0,.4)}.leaflet-container a.leaflet-popup-close-button{position:absolute;top:0;right:0;border:none;text-align:center;width:24px;height:24px;font:16px/24px Tahoma,Verdana,sans-serif;color:#757575;text-decoration:none;background:0 0}.leaflet-container a.leaflet-popup-close-button:focus,.leaflet-container a.leaflet-popup-close-button:hover{color:#585858}.leaflet-popup-scrolled{overflow:auto}.leaflet-oldie .leaflet-popup-content-wrapper{-ms-zoom:1}.leaflet-oldie .leaflet-popup-tip{width:24px;margin:0 auto}.leaflet-oldie .leaflet-control-layers,.leaflet-oldie .leaflet-control-zoom,.leaflet-oldie .leaflet-popup-content-wrapper,.leaflet-oldie .leaflet-popup-tip{border:1px solid #999}.leaflet-div-icon{background:#fff;border:1px solid #666}.leaflet-tooltip{position:absolute;padding:6px;background-color:#fff;border:1px solid #fff;border-radius:3px;color:#222;white-space:nowrap;-webkit-user-select:none;-moz-user-select:none;-ms-user-select:none;user-select:none;pointer-events:none;box-shadow:0 1px 3px rgba(0,0,0,.4)}.leaflet-tooltip.leaflet-interactive{cursor:pointer;pointer-events:auto}.leaflet-tooltip-bottom:before,.leaflet-tooltip-left:before,.leaflet-tooltip-right:before,.leaflet-tooltip-top:before{position:absolute;pointer-events:none;border:6px solid transparent;background:0 0;content:\"\"}.leaflet-tooltip-bottom{margin-top:6px}.leaflet-tooltip-top{margin-top:-6px}.leaflet-tooltip-bottom:before,.leaflet-tooltip-top:before{left:50%;margin-left:-6px}.leaflet-tooltip-top:before{bottom:0;margin-bottom:-12px;border-top-color:#fff}.leaflet-tooltip-bottom:before{top:0;margin-top:-12px;margin-left:-6px;border-bottom-color:#fff}.leaflet-tooltip-left{margin-left:-6px}.leaflet-tooltip-right{margin-left:6px}.leaflet-tooltip-left:before,.leaflet-tooltip-right:before{top:50%;margin-top:-6px}.leaflet-tooltip-left:before{right:0;margin-right:-12px;border-left-color:#fff}.leaflet-tooltip-right:before{left:0;margin-left:-12px;border-right-color:#fff}@media print{.leaflet-control{-webkit-print-color-adjust:exact;print-color-adjust:exact}}";

  const CSS = `
.opp {
  /* Layout: one working screen. Map fills the left; a tabbed panel holds profile, weights,
     compare and methodology. Stacks to map-over-panel under 960px. */
  --opp-bg: #f3f5f8;
  --opp-surface: #ffffff;
  --opp-ink: #131820;
  --opp-ink-2: #465061;
  --opp-muted: #6f7888;
  --opp-line: #d9dee6;
  --opp-accent: #1c5cab;
  --opp-accent-soft: #e4eefb;
  --opp-funded: #eb6834;
  --opp-nodata: #e6e8ec;
  --opp-t0: #86b6ef; --opp-t1: #5598e7; --opp-t2: #2a78d6; --opp-t3: #1c5cab; --opp-t4: #104281;
  --opp-font: "Overpass", "Public Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --opp-mono: "Overpass Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  font-family: var(--opp-font);
  color: var(--opp-ink);
  background: var(--opp-bg);
  font-size: 14px;
  line-height: 1.45;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100%;
  min-height: 560px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .opp {
    --opp-bg: #101318; --opp-surface: #181c22; --opp-ink: #eef1f5; --opp-ink-2: #bcc3ce;
    --opp-muted: #8d95a2; --opp-line: #2b313a; --opp-accent: #6da7ec; --opp-accent-soft: #1b2a3e;
    --opp-funded: #d95926; --opp-nodata: #262a31;
    --opp-t0: #184f95; --opp-t1: #256abf; --opp-t2: #3987e5; --opp-t3: #6da7ec; --opp-t4: #b7d3f6;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] .opp {
  --opp-bg: #101318; --opp-surface: #181c22; --opp-ink: #eef1f5; --opp-ink-2: #bcc3ce;
  --opp-muted: #8d95a2; --opp-line: #2b313a; --opp-accent: #6da7ec; --opp-accent-soft: #1b2a3e;
  --opp-funded: #d95926; --opp-nodata: #262a31;
  --opp-t0: #184f95; --opp-t1: #256abf; --opp-t2: #3987e5; --opp-t3: #6da7ec; --opp-t4: #b7d3f6;
  color-scheme: dark;
}
.opp *, .opp *::before, .opp *::after { box-sizing: border-box; }
.opp button, .opp input, .opp select { font: inherit; color: inherit; }
.opp :focus-visible { outline: 2px solid var(--opp-accent); outline-offset: 2px; }

.opp-bar {
  grid-column: 1 / -1;
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px;
  padding-block: 12px;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  border-bottom: 1px solid var(--opp-line);
  background: var(--opp-surface);
}
.opp-brand { display: flex; flex-direction: column; line-height: 1.1; margin-right: 6px; }
.opp-brand b { font-size: 19px; font-weight: 800; letter-spacing: -0.01em; }
.opp-brand span { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--opp-muted); }
.opp-search { position: relative; flex: 1 1 280px; max-width: 460px; min-width: 0; }
.opp-search input {
  width: 100%; padding: 9px 12px 7px; border: 1px solid var(--opp-line); border-radius: 6px;
  background: var(--opp-bg);
}
.opp-results {
  position: absolute; z-index: 1000; left: 0; right: 0; top: calc(100% + 4px);
  background: var(--opp-surface); border: 1px solid var(--opp-line); border-radius: 6px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.14); max-height: 320px; overflow-y: auto;
}
.opp-results button {
  display: block; width: 100%; text-align: left; padding: 8px 12px 6px; border: 0;
  background: none; cursor: pointer;
}
.opp-results button:hover, .opp-results button[aria-selected="true"] { background: var(--opp-accent-soft); }
.opp-results small { display: block; color: var(--opp-muted); font-size: 12px; }
.opp-note { padding: 10px 12px; color: var(--opp-ink-2); font-size: 13px; }
.opp-credit { padding: 6px 12px 5px; color: var(--opp-muted); font-size: 11px; border-top: 1px solid var(--opp-line); }
.opp-ctl { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--opp-ink-2); }
.opp-ctl select { padding: 6px 8px 4px; border: 1px solid var(--opp-line); border-radius: 6px; background: var(--opp-bg); }
.opp-ctl input[type="checkbox"] { accent-color: var(--opp-accent); width: 16px; height: 16px; }

.opp-mapwrap { position: relative; min-height: 0; min-width: 0; background: var(--opp-bg); }
.opp-map { position: absolute; inset: 0; background: var(--opp-bg); }
.opp-legend {
  position: absolute; z-index: 500; left: 12px; bottom: 12px;
  background: var(--opp-surface); border: 1px solid var(--opp-line); border-radius: 8px;
  padding: 10px 12px 8px; font-size: 12px; display: grid; gap: 6px; max-width: calc(100% - 24px);
}
.opp-legend h4 { margin: 0; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--opp-muted); font-weight: 700; }
.opp-ramp { display: flex; gap: 2px; }
.opp-ramp span { flex: 1; height: 10px; min-width: 34px; }
.opp-ramp span:first-child { border-radius: 3px 0 0 3px; }
.opp-ramp span:last-child { border-radius: 0 3px 3px 0; }
.opp-ramp-labels { display: flex; justify-content: space-between; color: var(--opp-ink-2); gap: 12px; }
.opp-legend-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; color: var(--opp-ink-2); }
.opp-key { display: inline-flex; align-items: center; gap: 5px; }
.opp-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.opp-dot.funded { background: var(--opp-funded); }
.opp-dot.unfunded { border: 2px solid var(--opp-ink-2); background: var(--opp-surface); }
.opp-dot.station { width: 8px; height: 8px; border-radius: 1px; background: var(--opp-ink); transform: rotate(45deg); }

.opp-panel {
  border-left: 1px solid var(--opp-line); background: var(--opp-surface);
  display: flex; flex-direction: column; min-height: 0; min-width: 0;
}
.opp-tabs { display: flex; border-bottom: 1px solid var(--opp-line); padding: 0 8px; gap: 2px; overflow-x: auto; }
.opp-tabs button {
  border: 0; background: none; padding: 12px 10px 9px; cursor: pointer; color: var(--opp-muted);
  font-weight: 600; border-bottom: 2px solid transparent; white-space: nowrap;
}
.opp-tabs button[aria-selected="true"] { color: var(--opp-ink); border-bottom-color: var(--opp-accent); }
.opp-tabs .opp-count {
  font-family: var(--opp-mono); font-size: 11px; background: var(--opp-accent-soft); color: var(--opp-accent);
  border-radius: 9px; padding: 1px 6px; margin-left: 4px;
}
.opp-body { overflow-y: auto; padding: 18px 18px 28px; min-height: 0; flex: 1; }
.opp-body h3 { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--opp-muted); margin: 22px 0 10px; font-weight: 700; }
.opp-body p { margin: 0 0 10px; color: var(--opp-ink-2); max-width: 62ch; }

.opp-head { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; align-items: end; }
.opp-head h2 { margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.01em; text-wrap: balance; }
.opp-head .sub { color: var(--opp-muted); font-size: 13px; grid-column: 1; }
.opp-big { grid-row: 1 / span 2; grid-column: 2; text-align: right; }
.opp-big b { display: block; font-family: var(--opp-mono); font-size: 40px; line-height: 1; font-weight: 600; font-variant-numeric: tabular-nums; }
.opp-big small { color: var(--opp-muted); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; }
.opp-tier {
  display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700;
  padding: 3px 9px 1px; border-radius: 999px; border: 1px solid var(--opp-line); white-space: nowrap;
}
.opp-tier i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.opp-sw-t0 { background: var(--opp-t0); } .opp-sw-t1 { background: var(--opp-t1); }
.opp-sw-t2 { background: var(--opp-t2); } .opp-sw-t3 { background: var(--opp-t3); }
.opp-sw-t4 { background: var(--opp-t4); }

.opp-domains { display: grid; gap: 7px; margin-top: 16px; }
.opp-drow { display: grid; grid-template-columns: 96px minmax(0, 1fr) 34px; gap: 10px; align-items: center; }
.opp-drow .lab { font-size: 13px; }
.opp-drow .lab small { color: var(--opp-muted); font-family: var(--opp-mono); font-size: 11px; margin-left: 4px; }
.opp-drow .val { font-family: var(--opp-mono); text-align: right; font-variant-numeric: tabular-nums; font-size: 13px; }
.opp-track { position: relative; height: 10px; background: var(--opp-bg); border-radius: 0 4px 4px 0; }
.opp-fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--opp-accent); border-radius: 0 4px 4px 0; }
.opp-mid { position: absolute; top: -3px; bottom: -3px; left: 50%; width: 1px; background: var(--opp-muted); opacity: 0.5; }
.opp-why { margin-top: 12px; font-size: 13px; color: var(--opp-ink-2); }
.opp-why b { color: var(--opp-ink); }

.opp-facts { display: grid; gap: 14px; }
.opp-fgroup h4 { margin: 0 0 6px; font-size: 13px; font-weight: 700; }
.opp-fact { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 12px; padding: 5px 0 4px; border-top: 1px solid var(--opp-line); font-size: 13px; }
.opp-fact .v { font-family: var(--opp-mono); font-variant-numeric: tabular-nums; text-align: right; }
.opp-fact .p { grid-column: 1 / -1; display: flex; align-items: center; gap: 8px; color: var(--opp-muted); font-size: 11px; }
.opp-fact .p .opp-track { flex: 1; height: 4px; }
.opp-fact .p .opp-fill { background: var(--opp-muted); }
.opp-apps { display: grid; gap: 6px; }
.opp-app { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.opp-app .y { font-family: var(--opp-mono); color: var(--opp-muted); }

.opp-btn {
  border: 1px solid var(--opp-line); background: var(--opp-surface); border-radius: 6px;
  padding: 7px 12px 5px; cursor: pointer; font-weight: 600; font-size: 13px;
}
.opp-btn:hover { border-color: var(--opp-accent); color: var(--opp-accent); }
.opp-btn.primary { background: var(--opp-accent); border-color: var(--opp-accent); color: var(--opp-surface); }
.opp-btn.primary:hover { color: var(--opp-surface); filter: brightness(1.08); }
.opp-btn:disabled { opacity: 0.5; cursor: default; }
.opp-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }

.opp-presets { display: flex; flex-wrap: wrap; gap: 6px; }
.opp-chip { border: 1px solid var(--opp-line); background: var(--opp-surface); border-radius: 999px; padding: 5px 12px 3px; cursor: pointer; font-size: 13px; }
.opp-chip[aria-pressed="true"] { background: var(--opp-accent-soft); border-color: var(--opp-accent); color: var(--opp-accent); font-weight: 700; }
.opp-slider { display: grid; grid-template-columns: 100px minmax(0, 1fr) 44px; gap: 10px; align-items: center; padding: 6px 0; }
.opp-slider input { width: 100%; accent-color: var(--opp-accent); }
.opp-slider output { font-family: var(--opp-mono); text-align: right; font-variant-numeric: tabular-nums; }
.opp-stab { margin-top: 14px; padding: 12px 14px 10px; background: var(--opp-bg); border-radius: 8px; display: grid; gap: 6px; font-size: 13px; }
.opp-stab div { display: flex; justify-content: space-between; gap: 12px; }
.opp-stab b { font-family: var(--opp-mono); font-variant-numeric: tabular-nums; }

.opp-adv { margin-top: 18px; border-top: 1px solid var(--opp-line); padding-top: 12px; }
.opp-adv summary { cursor: pointer; font-weight: 700; padding: 4px 0; }
.opp-adv > p { margin-top: 8px; }
.opp-mgroup h4 { margin: 14px 0 4px; font-size: 13px; }
.opp-metric { display: grid; grid-template-columns: 18px minmax(0, 1fr) 90px 52px; gap: 8px; align-items: center; padding: 5px 0; border-top: 1px solid var(--opp-line); font-size: 13px; }
.opp-metric input[type="checkbox"] { accent-color: var(--opp-accent); width: 16px; height: 16px; margin: 0; }
.opp-metric input[type="range"] { width: 100%; accent-color: var(--opp-accent); }
.opp-metric input[type="range"]:disabled { opacity: 0.35; }
.opp-metric output { font-family: var(--opp-mono); text-align: right; font-variant-numeric: tabular-nums; color: var(--opp-ink-2); }
.opp-tag { font-size: 10px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--opp-muted); border: 1px solid var(--opp-line); border-radius: 4px; padding: 1px 4px 0; font-weight: 600; white-space: nowrap; }
.opp-fact.off .v { color: var(--opp-muted); }
.opp-cmp-wrap { overflow-x: auto; }
.opp-cmp { border-collapse: collapse; width: 100%; font-size: 13px; }
.opp-cmp th, .opp-cmp td { padding: 7px 6px 5px; border-top: 1px solid var(--opp-line); text-align: right; font-variant-numeric: tabular-nums; vertical-align: top; }
.opp-cmp th:first-child, .opp-cmp td:first-child { text-align: left; color: var(--opp-ink-2); font-weight: 400; }
.opp-cmp thead th { border-top: 0; vertical-align: bottom; }
.opp-cmp td { font-family: var(--opp-mono); }
.opp-cmp td.best { font-weight: 700; color: var(--opp-accent); }
.opp-cmp .slot { font-family: var(--opp-mono); font-weight: 700; }
.opp-cmp .rm { border: 0; background: none; color: var(--opp-muted); cursor: pointer; font-size: 12px; padding: 0; text-decoration: underline; }
.opp-pin {
  width: 22px; height: 22px; border-radius: 50%; background: var(--opp-ink); color: var(--opp-surface);
  font: 700 12px/22px var(--opp-mono); text-align: center; border: 2px solid var(--opp-surface);
  box-shadow: 0 1px 4px rgb(0 0 0 / 0.35);
}
.opp-empty { padding: 18px; border: 1px dashed var(--opp-line); border-radius: 8px; color: var(--opp-ink-2); }

.opp-method dl { margin: 0; display: grid; gap: 14px; }
.opp-method dt { font-weight: 700; display: flex; justify-content: space-between; gap: 12px; }
.opp-method dt span { font-family: var(--opp-mono); color: var(--opp-accent); font-weight: 600; }
.opp-method dd { margin: 4px 0 0; color: var(--opp-ink-2); }
.opp-method dd small { display: block; color: var(--opp-muted); margin-top: 4px; }
.opp-method ul { margin: 0; padding-left: 18px; color: var(--opp-ink-2); display: grid; gap: 6px; }
.opp-src { margin: 0; padding-left: 20px; display: grid; gap: 8px; font-size: 13px; }
.opp-src a { color: var(--opp-accent); text-underline-offset: 2px; }
.opp-src span { display: block; color: var(--opp-ink-2); font-size: 12px; }
.opp-verified { margin-top: 10px; font-size: 12px; color: var(--opp-muted); }
.opp-iw { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 6px; }
.opp-iw td { padding: 3px 0; border-top: 1px solid var(--opp-line); color: var(--opp-ink-2); }
.opp-iw td:last-child { text-align: right; font-family: var(--opp-mono); }

.opp .leaflet-container { background: var(--opp-bg); font-family: var(--opp-font); }
.opp .leaflet-tooltip {
  background: var(--opp-surface); color: var(--opp-ink); border: 1px solid var(--opp-line);
  border-radius: 6px; box-shadow: 0 4px 14px rgb(0 0 0 / 0.16); padding: 6px 9px 4px; font-size: 12px;
}
.opp .leaflet-tooltip-top::before { border-top-color: var(--opp-line); }
.opp .leaflet-control-zoom a { background: var(--opp-surface); color: var(--opp-ink); border-color: var(--opp-line); }
.opp .leaflet-control-attribution { background: var(--opp-surface); color: var(--opp-muted); }
.opp .leaflet-control-attribution a { color: var(--opp-accent); }
.opp-label { font-size: 12px; font-weight: 800; letter-spacing: 0.1em; text-transform: uppercase; color: var(--opp-ink); white-space: nowrap; text-shadow: 0 0 3px var(--opp-surface), 0 0 3px var(--opp-surface), 0 0 6px var(--opp-surface); pointer-events: none; }

@media (max-width: 960px) {
  .opp { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto 58vh auto; height: auto; }
  .opp-mapwrap { min-height: 360px; }
  .opp-panel { border-left: 0; border-top: 1px solid var(--opp-line); }
  .opp-body { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
}
@media (max-width: 600px) {
  .opp-legend { left: 8px; bottom: 8px; padding: 7px 9px 5px; gap: 4px; font-size: 11px; }
  .opp-ramp span { min-width: 22px; height: 8px; }
}
@media (prefers-reduced-motion: reduce) { .opp * { transition: none !important; } }
`;

  function injectStyles() {
    if (document.getElementById("opp-styles")) return;
    const s = document.createElement("style");
    s.id = "opp-styles";
    const hasLeafletCss = [...document.styleSheets].some((ss) => (ss.href || "").includes("leaflet"));
    s.textContent = (hasLeafletCss ? "" : LEAFLET_CSS) + CSS;
    document.head.appendChild(s);
  }

  // ------------------------------------------------------------------ module

  async function mount(el, opts = {}) {
    if (!global.L) throw new Error("OpportunityModule needs Leaflet (window.L) loaded first.");
    injectStyles();
    // Data comes either inline (opts.data, used by the single-file standalone.html) or as four files
    // fetched from opts.dataUrl.
    const base = (opts.dataUrl || "data/").replace(/\/?$/, "/");
    const [geo, apps, meta, places] = opts.data
      ? [opts.data.tracts, opts.data.applications, opts.data.meta, opts.data.places]
      : await Promise.all(["tracts.geojson", "applications.json", "meta.json", "places.json"].map((f) =>
          fetch(base + f).then((r) => { if (!r.ok) throw new Error(f + ": HTTP " + r.status); return r.json(); })));

    const tracts = geo.features.map((f) => ({ ...f.properties, geometry: f.geometry, bbox: bboxOf(f.geometry) }));
    const byId = new Map(tracts.map((t) => [t.tract, t]));
    const appsByTract = new Map();
    for (const a of apps) (appsByTract.get(a.tract) || appsByTract.set(a.tract, []).get(a.tract)).push(a);

    // Metrics ("indicators"). Each tract carries every metric's metro percentile (higher = more
    // opportunity); a part's score is the weighted mean of its switched-on metrics, as in
    // build_opportunity.py. Optional metrics start at weight 0 (off).
    const IND = meta.indicators;
    const defaultInd = Object.fromEntries(Object.entries(IND).map(([c, i]) => [c, i.weight]));
    function recomputeDomains(ind) {
      for (const t of tracts) {
        const s = {};
        for (const d of DOMAINS) {
          let num = 0, den = 0;
          for (const c in IND) {
            const w = ind[c] || 0, p = t.pctl[c];
            if (IND[c].domain !== d || w <= 0 || p === null || p === undefined) continue;
            num += w * p; den += w;
          }
          s[d] = den > 0 ? num / den : null;
        }
        t.scores = s;
      }
    }
    recomputeDomains(defaultInd);
    const defaultScores = computeScores(tracts, meta.weights);
    const startWeights = { ...(opts.weights || meta.weights) };
    const startInd = { ...defaultInd, ...(opts.indicators || {}) };
    recomputeDomains(startInd);
    const state = {
      weights: startWeights,
      ind: startInd,
      advOpen: false,
      scores: computeScores(tracts, startWeights),
      colorBy: "overall",
      showApps: true,
      selected: null,     // {tract, point?: {lat, lon, label}}
      compare: [],        // [{tract, label}]
      tab: "profile",
    };
    const listeners = { change: opts.onChange, select: opts.onSelect };

    // ---- skeleton
    el.classList.add("opp");
    el.innerHTML = `
      <div class="opp-bar">
        <div class="opp-brand"><b>Opportunity</b><span>Metro Atlanta · 2020 tracts</span></div>
        <div class="opp-search">
          <input id="opp-q" type="search" autocomplete="off" spellcheck="false"
            placeholder="Address, tract number, or past project name" aria-label="Find a site">
          <div class="opp-results" hidden></div>
        </div>
        <label class="opp-ctl" for="opp-colorby">Color by
          <select id="opp-colorby">
            <option value="overall">Opportunity score</option>
            ${DOMAINS.map((d) => `<option value="${d}">${esc(meta.domains[d].label)}</option>`).join("")}
          </select>
        </label>
        <label class="opp-ctl" for="opp-showapps"><input id="opp-showapps" type="checkbox" checked> Past DCA applications</label>
      </div>
      <div class="opp-mapwrap">
        <div class="opp-map" role="region" aria-label="Map of opportunity by census tract"></div>
        <div class="opp-legend"></div>
      </div>
      <div class="opp-panel">
        <div class="opp-tabs" role="tablist">
          <button role="tab" data-tab="profile">Profile</button>
          <button role="tab" data-tab="weights">Weights</button>
          <button role="tab" data-tab="compare">Compare<span class="opp-count"></span></button>
          <button role="tab" data-tab="method">Method</button>
        </div>
        <div class="opp-body" role="tabpanel"></div>
      </div>`;
    const $ = (sel) => el.querySelector(sel);
    const body = $(".opp-body");

    // ---- map
    const map = L.map($(".opp-map"), { zoomSnap: 0.25, preferCanvas: false, attributionControl: true });
    map.attributionControl.setPrefix(false);
    if (opts.tiles && opts.tiles.url) L.tileLayer(opts.tiles.url, { attribution: opts.tiles.attribution || "" }).addTo(map);
    map.attributionControl.addAttribution("Tracts: U.S. Census Bureau");
    if (IND.park_dist_mi) map.attributionControl.addAttribution('Parks: © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors');

    let colors = {};
    function readColors() {
      const c = getComputedStyle(el);
      colors = { tiers: TIER_CLASS.map((_, i) => c.getPropertyValue("--opp-t" + i).trim()),
        nodata: c.getPropertyValue("--opp-nodata").trim(), surface: c.getPropertyValue("--opp-surface").trim(),
        ink: c.getPropertyValue("--opp-ink").trim(), ink2: c.getPropertyValue("--opp-ink-2").trim(),
        funded: c.getPropertyValue("--opp-funded").trim() };
    }
    readColors();

    // Domain coloring uses quintiles of that domain's score, so the same five-step ramp applies.
    const domainQuintiles = {};
    function recomputeQuintiles() {
      DOMAINS.forEach((d) => {
        const vals = tracts.map((t) => t.scores[d]).filter((v) => v !== null).sort((a, b) => a - b);
        domainQuintiles[d] = vals.length ? [0.2, 0.4, 0.6, 0.8].map((q) => vals[Math.floor(q * (vals.length - 1))]) : [0, 0, 0, 0];
      });
    }
    recomputeQuintiles();
    function tierFor(t) {
      if (state.colorBy === "overall") { const s = state.scores.get(t.tract); return s ? s.tier : null; }
      const v = t.scores[state.colorBy];
      if (v === null || v === undefined) return null;
      const q = domainQuintiles[state.colorBy];
      return v <= q[0] ? 0 : v <= q[1] ? 1 : v <= q[2] ? 2 : v <= q[3] ? 3 : 4;
    }
    function styleFor(t) {
      const tier = tierFor(t);
      const sel = state.selected && state.selected.tract === t.tract;
      return { fillColor: tier === null ? colors.nodata : colors.tiers[tier], fillOpacity: 1,
        color: sel ? colors.ink : colors.surface, weight: sel ? 2.5 : 0.6, opacity: 1 };
    }

    const layers = new Map();
    map.createPane("opp-hover").style.zIndex = 450;   // above tracts, below markers and tooltips
    map.getPane("opp-hover").style.pointerEvents = "none";
    const hoverOutline = L.polygon([], { pane: "opp-hover", interactive: false, fill: false,
      weight: 1.6, opacity: 0 }).addTo(map);
    let hovered = null;
    const tip = L.tooltip({ direction: "top", offset: [0, -8], interactive: false });
    function clearHover() {
      hovered = null;
      hoverOutline.setStyle({ opacity: 0 });
      if (map.hasLayer(tip)) map.removeLayer(tip);
    }
    map.on("mouseout", clearHover);
    // Markers (stations, application dots, comparison pins) share the same tooltip.
    function hoverTip(marker, html) {
      marker.on("mouseover", () => {
        hovered = marker;
        hoverOutline.setStyle({ opacity: 0 });
        tip.setContent(html()).setLatLng(marker.getLatLng());
        if (!map.hasLayer(tip)) tip.addTo(map);
      });
      marker.on("mouseout", () => { if (hovered === marker) clearHover(); });
      return marker;
    }
    const tractLayer = L.geoJSON(geo, {
      style: (f) => styleFor(byId.get(f.properties.tract)),
      onEachFeature: (f, layer) => {
        const t = byId.get(f.properties.tract);
        layers.set(t.tract, layer);
        // One shared tooltip for every tract, so two can never be open at once, even if the
        // browser drops a mouseout. Hover also never reorders the tract shapes (moving an SVG path
        // under the pointer is what makes browsers drop mouseout); the outline is drawn on a
        // separate non-interactive layer instead.
        layer.on("mouseover", (e) => {
          hovered = t;
          hoverOutline.setLatLngs(layer.getLatLngs()).setStyle({ color: colors.ink, opacity: 1 });
          tip.setContent(tooltipHtml(t)).setLatLng(e.latlng);
          if (!map.hasLayer(tip)) tip.addTo(map);
        });
        layer.on("mousemove", (e) => { if (hovered === t) tip.setLatLng(e.latlng); });
        layer.on("mouseout", () => { if (hovered === t) clearHover(); });
        layer.on("click", (e) => select(t.tract, { lat: e.latlng.lat, lon: e.latlng.lng, label: null }, { fly: false }));
      },
    }).addTo(map);
    const front = () => { stationLayer.bringToFront(); appLayer.bringToFront(); };
    const isSel = (t) => state.selected && state.selected.tract === t.tract;
    map.fitBounds(tractLayer.getBounds(), { padding: [12, 12] });

    function tooltipHtml(t) {
      const s = state.scores.get(t.tract);
      const head = `<b>${esc(tractLabel(t.tract))}</b>, ${esc(t.county)}`;
      if (!s) return head + "<br>No residents, so no score";
      const extra = state.colorBy === "overall" ? "" :
        `<br>${esc(meta.domains[state.colorBy].label)}: ${Math.round(t.scores[state.colorBy] ?? NaN) || "n/a"}`;
      return `${head}<br>${esc(meta.tiers[s.tier])} · score ${Math.round(s.score)}${extra}`;
    }

    // Orientation without a basemap: county names and MARTA rail stations.
    const labelLayer = L.layerGroup(places.counties.map((c) =>
      L.marker([c.lat, c.lon], { interactive: false, keyboard: false,
        icon: L.divIcon({ className: "opp-label", html: esc(c.name), iconSize: null, iconAnchor: [0, 0] }) }))).addTo(map);
    const stationLayer = L.featureGroup(places.stations.map((s) =>
      hoverTip(L.circleMarker([s.lat, s.lon], { radius: 3, color: colors.surface, weight: 1, fillColor: colors.ink, fillOpacity: 1 }),
        () => "MARTA " + esc(s.name)))).addTo(map);
    void labelLayer;

    const appLayer = L.featureGroup().addTo(map);
    function drawApps() {
      appLayer.clearLayers();
      if (!state.showApps) return;
      for (const a of apps) {
        const m = L.circleMarker([a.lat, a.lon], a.selected
          ? { radius: 5, color: colors.surface, weight: 2, fillColor: colors.funded, fillOpacity: 1 }
          : { radius: 4.5, color: colors.ink2, weight: 2, fillColor: colors.surface, fillOpacity: 1 });
        hoverTip(m, () => `<b>${esc(a.name)}</b><br>${a.year} · ${a.selected ? "Funded" : "Not funded"}` +
          `${a.units ? " · " + a.units + " units" : ""}${a.exact ? "" : "<br><i>Placed at tract center</i>"}`);
        m.on("click", () => select(a.tract, { lat: a.lat, lon: a.lon, label: a.name }, { fly: false }));
        appLayer.addLayer(m);
      }
    }

    const pinLayer = L.layerGroup().addTo(map);
    function drawPins() {
      pinLayer.clearLayers();
      state.compare.forEach((c, i) => {
        const t = byId.get(c.tract);
        const ll = c.point ? [c.point.lat, c.point.lon] : centroid(t);
        const pin = L.marker(ll, { icon: L.divIcon({ className: "", html: `<div class="opp-pin">${SLOT_LETTERS[i]}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }),
          keyboard: false }).addTo(pinLayer);
        hoverTip(pin, () => esc(c.label));
      });
    }
    function centroid(t) { const b = t.bbox; return [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]; }

    function restyle() { readColors(); for (const [id, layer] of layers) layer.setStyle(styleFor(byId.get(id))); drawApps(); stationLayer.eachLayer((m) => m.setStyle({ color: colors.surface, fillColor: colors.ink })); }
    const mq = global.matchMedia ? global.matchMedia("(prefers-color-scheme: dark)") : null;
    const onTheme = () => restyle();
    mq && mq.addEventListener && mq.addEventListener("change", onTheme);
    const themeObs = new MutationObserver(onTheme);
    themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    // ---- legend
    function isCustom() {
      const w = normalize(state.weights), dw = normalize(meta.weights);
      return DOMAINS.some((d) => Math.abs(w[d] - dw[d]) > 0.005) ||
        Object.keys(IND).some((c) => Math.abs((state.ind[c] || 0) - defaultInd[c]) > 1e-9);
    }
    function drawLegend() {
      const name = (state.colorBy === "overall" ? "Opportunity tier" : meta.domains[state.colorBy].label + " (quintiles)") +
        (isCustom() ? " · custom" : "");
      $(".opp-legend").innerHTML = `
        <h4>${esc(name)}</h4>
        <div class="opp-ramp">${TIER_CLASS.map((c, i) => `<span class="opp-sw-${c}" title="${esc(meta.tiers[i])}"></span>`).join("")}</div>
        <div class="opp-ramp-labels"><span>Very low</span><span>Very high</span></div>
        <div class="opp-legend-row">
          ${state.showApps ? `<span class="opp-key"><i class="opp-dot funded"></i>Funded</span>
          <span class="opp-key"><i class="opp-dot unfunded"></i>Not funded</span>` : ""}
          <span class="opp-key"><i class="opp-dot station"></i>MARTA rail</span>
        </div>`;
    }

    // ---- scoring + events
    function rescore(emit = true) {
      state.scores = computeScores(tracts, state.weights);
      restyle();
      drawLegend();
      if (emit) fire("change", { weights: normalize(state.weights), indicators: { ...state.ind },
        isDefault: !isCustom(), scores: state.scores });
    }
    function setIndicators(ind) {
      state.ind = { ...ind };
      recomputeDomains(state.ind);
      recomputeQuintiles();
      rescore();
    }
    function fire(kind, detail) {
      try { listeners[kind] && listeners[kind](detail); } catch (e) { console.error(e); }
      el.dispatchEvent(new CustomEvent("opportunity:" + kind, { detail }));
    }

    // ---- selection
    function select(tractId, point, { fly = true, tab = "profile" } = {}) {
      const t = byId.get(tractId);
      if (!t) return false;
      const prev = state.selected && layers.get(state.selected.tract);
      state.selected = { tract: tractId, point: point || null };
      prev && prev.setStyle(styleFor(byId.get(prev.feature.properties.tract)));
      const layer = layers.get(tractId);
      layer.setStyle(styleFor(t)); layer.bringToFront(); front();
      if (fly) map.flyToBounds(layer.getBounds(), { maxZoom: 13, duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 0.6 });
      setTab(tab);
      fire("select", profile(tractId));
      return true;
    }
    function profile(id) {
      const t = byId.get(id), s = state.scores.get(id);
      return { tract: id, county: t.county, score: s ? s.score : null, tier: s ? meta.tiers[s.tier] : null,
        domains: { ...t.scores }, facts: { ...t.facts } };
    }

    // ---- panel rendering
    function setTab(tab) {
      state.tab = tab;
      el.querySelectorAll(".opp-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
      render();
    }
    function render() {
      $(".opp-count").textContent = state.compare.length ? String(state.compare.length) : "";
      ({ profile: renderProfile, weights: renderWeights, compare: renderCompare, method: renderMethod })[state.tab]();
    }

    function domainBars(t) {
      const w = normalize(state.weights);
      return `<div class="opp-domains">${DOMAINS.map((d) => {
        const v = t.scores[d];
        return `<div class="opp-drow"><span class="lab">${esc(meta.domains[d].label)}<small>${Math.round(w[d] * 100)}%</small></span>
          <span class="opp-track" title="Metro percentile"><span class="opp-fill" style="width:${v ?? 0}%"></span><span class="opp-mid"></span></span>
          <span class="val">${v === null ? "n/a" : Math.round(v)}</span></div>`;
      }).join("")}</div>`;
    }
    function whyLine(t) {
      const ds = DOMAINS.filter((d) => t.scores[d] !== null).sort((a, b) => t.scores[b] - t.scores[a]);
      if (ds.length < 2) return "";
      const hi = ds.slice(0, 2).map((d) => meta.domains[d].label.toLowerCase());
      const lo = ds.slice(-2).reverse().map((d) => meta.domains[d].label.toLowerCase());
      return `<p class="opp-why">Strongest on <b>${hi.join("</b> and <b>")}</b>; weakest on <b>${lo.join("</b> and <b>")}</b>.</p>`;
    }
    function tierPill(s) {
      return s ? `<span class="opp-tier"><i class="opp-sw-${TIER_CLASS[s.tier]}"></i>${esc(meta.tiers[s.tier])}</span>` : "";
    }

    // A part's facts: its core metrics, then any optional metric this dataset has.
    function factsFor(d) {
      const extra = Object.entries(IND).filter(([c, i]) => i.optional && i.domain === d)
        .map(([c, i]) => [c, i.label, meta.facts[c] || "index"]);
      return [...FACTS[d], ...extra];
    }
    function renderProfile() {
      if (!state.selected) {
        body.innerHTML = `<div class="opp-empty">Click a tract on the map, or search an address, tract number or past project.</div>`;
        return;
      }
      const t = byId.get(state.selected.tract), s = state.scores.get(t.tract);
      const label = state.selected.point && state.selected.point.label;
      const inCompare = state.compare.some((c) => c.tract === t.tract && (c.label === (label || tractLabel(t.tract))));
      const tApps = (appsByTract.get(t.tract) || []).slice().sort((a, b) => b.year - a.year);
      body.innerHTML = `
        <div class="opp-head">
          <h2>${esc(label || tractLabel(t.tract))}</h2>
          <div class="opp-big"><b>${s ? Math.round(s.score) : "n/a"}</b><small>of 100</small></div>
          <div class="sub">${label ? esc(tractLabel(t.tract)) + " · " : ""}${esc(t.county)} County · ${nf.format(t.pop)} residents</div>
        </div>
        <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${tierPill(s)}${isCustom()
          ? `<span class="opp-tier" title="Weights or metrics differ from the calibrated default">Custom weights</span>` : ""}</div>
        ${s ? domainBars(t) + whyLine(t) : `<p class="opp-why">This tract has no residents, so it isn't scored.</p>`}
        <div class="opp-actions">
          <button class="opp-btn primary" data-act="compare" ${inCompare || state.compare.length >= MAX_COMPARE ? "disabled" : ""}>
            ${inCompare ? "In comparison" : state.compare.length >= MAX_COMPARE ? "Comparison full" : "Add to compare"}</button>
          <button class="opp-btn" data-act="copy">Copy summary</button>
        </div>
        <h3>What's behind the score</h3>
        <div class="opp-facts">${DOMAINS.map((d) => `
          <div class="opp-fgroup"><h4>${esc(meta.domains[d].label)}</h4>
          ${factsFor(d).map(([c, lab, kind]) => {
            const v = t.facts[c], p = t.pctl[c], off = !(state.ind[c] > 0);
            return `<div class="opp-fact${off ? " off" : ""}"><span>${esc(lab)}${off ? ` <small class="opp-tag">not counted</small>` : ""}</span><span class="v">${esc(fmt(v, kind))}</span>
              ${p === null || p === undefined ? "" : `<span class="p"><span class="opp-track"><span class="opp-fill" style="width:${p}%"></span></span>better than ${Math.round(p)}% of metro tracts</span>`}</div>`;
          }).join("")}
          ${d === "schools" && t.facts.nearest_high_school ? `<div class="opp-fact"><span>Nearest high school</span><span class="v">${esc(t.facts.nearest_high_school)}</span></div>` : ""}
          </div>`).join("")}
        </div>
        <h3>Past 9% applications in this tract</h3>
        ${tApps.length ? `<div class="opp-apps">${tApps.map((a) => `<div class="opp-app"><span class="y">${a.year}</span>
          <span class="opp-dot ${a.selected ? "funded" : "unfunded"}"></span><span>${esc(a.name)}${a.selected ? "" : " <span style=\"color:var(--opp-muted)\">(not funded)</span>"}</span></div>`).join("")}</div>`
          : `<p>None since 2013.</p>`}`;
      body.querySelector('[data-act="compare"]').onclick = () => addCompare(t.tract, state.selected.point);
      body.querySelector('[data-act="copy"]').onclick = (e) => copySummary(t, s, label, e.currentTarget);
    }

    function copySummary(t, s, label, btn) {
      const lines = [`${label || tractLabel(t.tract)} (${tractLabel(t.tract)}, ${t.county} County)`,
        s ? `Opportunity score ${Math.round(s.score)} of 100 (${meta.tiers[s.tier]}) among metro Atlanta tracts` : "Not scored",
        ...DOMAINS.map((d) => `${meta.domains[d].label}: ${t.scores[d] === null ? "n/a" : Math.round(t.scores[d])}`),
        ...DOMAINS.flatMap((d) => FACTS[d].map(([c, lab, kind]) => `- ${lab}: ${fmt(t.facts[c], kind)}`))];
      const text = lines.join("\n");
      const ok = () => { btn.textContent = "Copied"; setTimeout(() => (btn.textContent = "Copy summary"), 1600); };
      const fallback = () => {
        const ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", "");
        ta.style.cssText = "width:100%;height:140px;margin-top:10px;font:12px var(--opp-mono)";
        btn.closest(".opp-actions").after(ta); ta.select(); btn.textContent = "Select and copy below";
      };
      try { navigator.clipboard.writeText(text).then(ok, fallback); } catch (e) { fallback(); }
    }

    // ---- weights tab
    function renderWeights() {
      const w = normalize(state.weights);
      const active = Object.entries(meta.presets).find(([, p]) => DOMAINS.every((d) => Math.abs(normalize(p)[d] - w[d]) < 0.005));
      body.innerHTML = `
        <p>Set how much each part counts. Scores and the map update as you move a slider. Weights are shares of the total, so they always add up to 100%.</p>
        <h3>Start from</h3>
        <div class="opp-presets">${Object.keys(meta.presets).map((name) =>
          `<button class="opp-chip" data-preset="${esc(name)}" aria-pressed="${active && active[0] === name}">${esc(name)}</button>`).join("")}</div>
        <h3>Weights</h3>
        ${DOMAINS.map((d) => `<div class="opp-slider">
          <label for="opp-w-${d}">${esc(meta.domains[d].label)}</label>
          <input id="opp-w-${d}" type="range" min="0" max="100" step="1" value="${Math.round((state.weights[d] || 0) * 100)}">
          <output id="opp-wo-${d}">${Math.round(w[d] * 100)}%</output></div>`).join("")}
        <div class="opp-stab" aria-live="polite"></div>
        <details class="opp-adv" ${state.advOpen ? "open" : ""}>
          <summary>Advanced: choose metrics</summary>
          <p>Switch metrics on or off and set how much each counts inside its part. Optional metrics start switched off. Any change here marks the score as custom.</p>
          ${DOMAINS.map((d) => {
            const rows = Object.entries(IND).filter(([, i]) => i.domain === d);
            const tot = rows.reduce((s, [c]) => s + (state.ind[c] || 0), 0);
            return `<div class="opp-mgroup"><h4>${esc(meta.domains[d].label)}${tot > 0 ? "" : ` <small class="opp-tag">no metrics on</small>`}</h4>
              ${rows.map(([c, i]) => {
                const on = (state.ind[c] || 0) > 0;
                return `<div class="opp-metric">
                  <input type="checkbox" id="opp-mc-${c}" data-metric="${c}" ${on ? "checked" : ""}>
                  <label for="opp-mc-${c}">${esc(i.label)}${i.optional ? ` <small class="opp-tag">optional</small>` : ""}</label>
                  <input type="range" id="opp-mw-${c}" data-metricw="${c}" min="1" max="100" step="1" value="${Math.round((on ? state.ind[c] : i.on_weight) * 100)}" ${on ? "" : "disabled"} aria-label="${esc(i.label)} weight">
                  <output id="opp-mo-${c}">${on ? Math.round((state.ind[c] / tot) * 100) + "%" : "off"}</output>
                </div>`;
              }).join("")}</div>`;
          }).join("")}
          <div class="opp-actions"><button class="opp-btn" data-act="reset-metrics" ${Object.keys(IND).every((c) => (state.ind[c] || 0) === defaultInd[c]) ? "disabled" : ""}>Reset metrics to default</button></div>
        </details>`;
      body.querySelector(".opp-adv").ontoggle = (e) => (state.advOpen = e.target.open);
      body.querySelectorAll("[data-metric]").forEach((cb) => (cb.onchange = () => {
        const c = cb.dataset.metric;
        setIndicators({ ...state.ind, [c]: cb.checked ? (IND[c].weight || IND[c].on_weight) : 0 });
        renderWeights();
      }));
      body.querySelectorAll("[data-metricw]").forEach((sl) => (sl.oninput = () => {
        const c = sl.dataset.metricw;
        setIndicators({ ...state.ind, [c]: Number(sl.value) / 100 });
        const d = IND[c].domain;
        const rows = Object.keys(IND).filter((k) => IND[k].domain === d);
        const tot = rows.reduce((s, k) => s + (state.ind[k] || 0), 0);
        rows.forEach((k) => (body.querySelector("#opp-mo-" + k).textContent = state.ind[k] > 0 ? Math.round((state.ind[k] / tot) * 100) + "%" : "off"));
        renderStability();
      }));
      body.querySelector('[data-act="reset-metrics"]').onclick = () => { setIndicators(defaultInd); renderWeights(); };
      body.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => {
        state.weights = { ...meta.presets[b.dataset.preset] }; rescore(); renderWeights();
      }));
      DOMAINS.forEach((d) => {
        body.querySelector("#opp-w-" + d).oninput = (e) => {
          state.weights[d] = Number(e.target.value) / 100;
          rescore();
          const nw = normalize(state.weights);
          DOMAINS.forEach((k) => (body.querySelector("#opp-wo-" + k).textContent = Math.round(nw[k] * 100) + "%"));
          body.querySelectorAll("[data-preset]").forEach((b) => b.setAttribute("aria-pressed",
            String(DOMAINS.every((k) => Math.abs(normalize(meta.presets[b.dataset.preset])[k] - nw[k]) < 0.005))));
          renderStability();
        };
      });
      renderStability();
    }
    function renderStability() {
      let changed = 0, top = 0, topKept = 0, n = 0;
      for (const [id, s] of state.scores) {
        const d = defaultScores.get(id); if (!d) continue;
        n++; if (d.tier !== s.tier) changed++;
        if (d.tier === 4) { top++; if (s.tier === 4) topKept++; }
      }
      const sel = state.selected && state.scores.get(state.selected.tract);
      const box = body.querySelector(".opp-stab");
      if (!box) return;
      box.innerHTML = `
        <div><span>Tracts in a different tier than with the default</span><b>${Math.round((changed / n) * 100)}%</b></div>
        <div><span>Default "Very high" tracts still Very high</span><b>${Math.round((topKept / top) * 100)}%</b></div>
        ${sel ? `<div><span>Selected site's score</span><b>${Math.round(sel.score)} · ${esc(meta.tiers[sel.tier])}</b></div>` : ""}`;
    }

    // ---- compare tab
    function addCompare(tractId, point) {
      if (state.compare.length >= MAX_COMPARE) return;
      const label = (point && point.label) || tractLabel(tractId);
      if (state.compare.some((c) => c.tract === tractId && c.label === label)) return;
      state.compare.push({ tract: tractId, label, point: point && point.lat ? point : null });
      drawPins(); render();
    }
    function renderCompare() {
      if (!state.compare.length) {
        body.innerHTML = `<div class="opp-empty">Add up to four sites from their profile to compare them side by side.</div>`;
        return;
      }
      const cols = state.compare.map((c) => ({ ...c, t: byId.get(c.tract), s: state.scores.get(c.tract) }));
      const best = (vals, higher) => {
        const ok = vals.filter((v) => v !== null && v !== undefined);
        if (ok.length < 2) return null;
        return higher ? Math.max(...ok) : Math.min(...ok);
      };
      const row = (label, vals, show, higher = true) => {
        const b = best(vals, higher);
        return `<tr><th scope="row">${esc(label)}</th>${vals.map((v) =>
          `<td class="${b !== null && v === b ? "best" : ""}">${esc(show(v))}</td>`).join("")}</tr>`;
      };
      body.innerHTML = `
        <p>Best value in each row is highlighted. Scores use the current weights.</p>
        <div class="opp-cmp-wrap"><table class="opp-cmp">
          <thead><tr><th></th>${cols.map((c, i) => `<th scope="col"><div class="slot">${SLOT_LETTERS[i]}</div>
            <div style="font-weight:600;font-family:var(--opp-font)">${esc(c.label)}</div>
            <div style="color:var(--opp-muted);font-weight:400;font-size:12px">${esc(c.t.county)}</div>
            <button class="rm" data-rm="${i}">Remove</button></th>`).join("")}</tr></thead>
          <tbody>
            ${row("Opportunity score", cols.map((c) => c.s ? Math.round(c.s.score) : null), (v) => v ?? "n/a")}
            <tr><th scope="row">Tier</th>${cols.map((c) => `<td>${c.s ? tierPill(c.s) : "n/a"}</td>`).join("")}</tr>
            ${DOMAINS.map((d) => row(meta.domains[d].label, cols.map((c) => c.t.scores[d] === null ? null : Math.round(c.t.scores[d])), (v) => v ?? "n/a")).join("")}
            ${COMPARE_FACTS.map(([col, lab, kind, hi]) => row(lab, cols.map((c) => c.t.facts[col]), (v) => fmt(v, kind), hi)).join("")}
          </tbody></table></div>
        <div class="opp-actions"><button class="opp-btn" data-act="clear">Clear all</button></div>`;
      body.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => {
        state.compare.splice(Number(b.dataset.rm), 1); drawPins(); render();
      }));
      body.querySelector('[data-act="clear"]').onclick = () => { state.compare = []; drawPins(); render(); };
    }

    // ---- methodology tab
    function renderMethod() {
      const w = normalize(state.weights);
      body.innerHTML = `<div class="opp-method">
        <p>Each measure is ranked against every populated census tract in the 11 Atlanta Regional Commission counties, so 0 is the lowest in the metro and 100 the highest. Each part below combines its measures, and the opportunity score combines the parts using the weights, then re-ranks the result from 0 to 100.</p>
        <h3>The six parts</h3>
        <dl>${DOMAINS.map((d) => {
          const ind = Object.entries(meta.indicators).filter(([, i]) => i.domain === d);
          return `<div><dt>${esc(meta.domains[d].label)}<span>${Math.round(w[d] * 100)}%</span></dt>
            <dd>${esc(meta.domains[d].what)}<small>${esc(meta.domains[d].source)}</small>
            <table class="opp-iw">${ind.map(([c, i]) => {
              const tot = ind.reduce((s, [k]) => s + (state.ind[k] || 0), 0);
              const v = state.ind[c] > 0 ? Math.round((state.ind[c] / tot) * 100) + "%" : i.optional ? "optional, off" : "off";
              return `<tr><td>${esc(i.label)}${i.direction < 0 ? " (lower is better)" : ""}</td><td>${v}</td></tr>`;
            }).join("")}</table></dd></div>`;
        }).join("")}</dl>
        ${meta.verification ? `
        <h3>How we checked it</h3>
        <ul>${meta.verification.items.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        <h3>Things to weigh</h3>
        <ul>${meta.verification.notes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        <p class="opp-verified">${meta.verification.summary.PASS || 0} automated checks passed with no data errors
          (last run ${esc(meta.verification.date)}). Full methodology: docs/opportunity_methodology.md.</p>` : ""}
        <h3>Keep in mind</h3>
        <ul>${meta.caveats.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        ${meta.sources ? `
        <h3>Data sources</h3>
        <ol class="opp-src">${meta.sources.map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>
          <span>${esc(s.publisher)}. ${esc(s.use)}${s.period ? `. ${esc(s.period)}` : ""}.</span></li>`).join("")}</ol>` : ""}
        ${meta.references ? `
        <h3>Research behind the weights</h3>
        <ol class="opp-src">${meta.references.map((r) => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.cite)}</a></li>`).join("")}</ol>` : ""}
        <p class="opp-verified">Every source is public. Scores rank the 1,245 populated census tracts (2020) in the 11 Atlanta Regional Commission counties.</p>
      </div>`;
    }

    // ---- search
    const q = $("#opp-q"), results = $(".opp-results");
    let hits = [], active = -1;
    function showResults(html) { results.innerHTML = html; results.hidden = !html; }
    function search(text) {
      const raw = text.trim(), s = raw.toLowerCase();
      if (!s) { showResults(""); return; }
      hits = [];
      // A street address starts with a house number and a word ("1600 Peachtree St"). Its lookup
      // goes first, so Enter never picks a tract whose number happens to match the house number.
      const looksLikeAddress = /^\d+[a-z]?\s+[a-z]/.test(s);
      const address = { kind: "address", query: raw, title: `Look up "${raw}" as typed`, sub: "U.S. Census Bureau geocoder" };
      if (looksLikeAddress) hits.push(address);
      // Tract search only for tract-style input: "35", "35.01", "tract 35.01" or a GEOID.
      const tq = s.replace(/^tract\s*/, "");
      if (!looksLikeAddress && /^[\d.]+$/.test(tq)) {
        for (const t of tracts) {
          const label = tractLabel(t.tract).toLowerCase();
          if (label === "tract " + tq || label.startsWith("tract " + tq + ".") || (tq.length >= 6 && t.tract.includes(tq)))
            hits.push({ kind: "tract", id: t.tract, title: tractLabel(t.tract), sub: t.county + " County · " + t.tract });
          if (hits.length >= 8) break;
        }
      }
      if (s.length >= 2) {
        for (const a of apps) if (a.name.toLowerCase().includes(s)) {
          hits.push({ kind: "app", id: a.tract, title: a.name, sub: `${a.year} application · ${a.city || ""} · ${a.selected ? "funded" : "not funded"}`, point: { lat: a.lat, lon: a.lon, label: a.name } });
          if (hits.length >= 10) break;
        }
      }
      if (!looksLikeAddress && /[a-z]/.test(s) && s.length >= 6) hits.push(address);
      active = hits.length ? 0 : -1;
      renderHits(hits.length ? "" : `<div class="opp-note">No match yet. Keep typing a street address, or try a tract number such as 35 or a project name.</div>`);
      // Address suggestions arrive a moment later; they go above the "as typed" lookup.
      clearTimeout(suggestTimer);
      if (suggestAbort) suggestAbort.abort();
      if (/[a-z]/.test(s) && s.length >= 3 && !/^(tract\s*)?[\d.]+$/.test(s)) {
        suggestTimer = setTimeout(async () => {
          suggestAbort = new AbortController();
          let sug = [];
          try { sug = await suggestAddresses(raw, suggestAbort.signal); } catch (e) { return; }
          if (q.value.trim() !== raw || !sug.length) return;
          const extra = sug.map((g) => ({ kind: "suggest", num: g.num, title: g.label,
            sub: g.place ? g.place + " · address suggestion" : "Address suggestion", point: { lat: g.lat, lon: g.lon, label: g.label } }));
          // A suggestion goes above the "as typed" lookup only if its house number matches what was
          // typed, so Enter on a full address never jumps to a different place.
          const typedNum = (s.match(/^(\d+)/) || [])[1];
          const same = extra.filter((e) => typedNum && e.num === typedNum), rest = extra.filter((e) => !same.includes(e));
          const at = hits.findIndex((h) => h.kind === "address");
          hits = looksLikeAddress ? [...same, ...hits, ...rest]
            : at >= 0 ? [...hits.slice(0, at), ...extra, ...hits.slice(at)] : [...hits, ...extra];
          active = 0;
          renderHits("", true);
        }, 250);
      }
    }
    let suggestTimer = null, suggestAbort = null;
    function renderHits(empty, credit = false) {
      showResults(hits.length ? hits.map((h, i) => `<button type="button" data-i="${i}" aria-selected="${i === active}">${esc(h.title)}<small>${esc(h.sub)}</small></button>`).join("") +
        (credit ? `<div class="opp-credit">Suggestions: Photon · © OpenStreetMap contributors</div>` : "") : empty);
    }
    async function choose(i) {
      const h = hits[i]; if (!h) return;
      if (h.kind !== "address" && h.kind !== "suggest") { showResults(""); q.value = h.title; select(h.id, h.point || null); return; }
      clearTimeout(suggestTimer);
      if (suggestAbort) suggestAbort.abort();
      showResults(`<div class="opp-note">Looking up the address...</div>`);
      try {
        let g;
        if (h.kind === "suggest") {
          g = { lat: h.point.lat, lon: h.point.lon, label: h.title };
        } else {
          g = await geocodeAddress(h.query);
        }
        const t = tracts.find((t) => g.lon >= t.bbox[0] && g.lon <= t.bbox[2] && g.lat >= t.bbox[1] && g.lat <= t.bbox[3] && inGeom(g.lon, g.lat, t.geometry));
        if (!t) { showResults(`<div class="opp-note">${esc(g.label)} is outside the 11 metro Atlanta counties this tool covers.</div>`); return; }
        showResults(""); q.value = g.label;
        select(t.tract, { lat: g.lat, lon: g.lon, label: g.label.replace(/,\s*GA.*$/i, "") });
      } catch (e) {
        showResults(`<div class="opp-note">${e.message === "nomatch"
          ? "The Census geocoder didn't find that address. Check the spelling, or click the site on the map."
          : "Address lookup can't reach the U.S. Census geocoder from this page (the claude.ai preview blocks it; it works when the app runs on its own). Click the site on the map, or search a tract number or project name."}</div>`);
      }
    }
    q.addEventListener("input", () => search(q.value));
    q.addEventListener("keydown", (e) => {
      const btns = results.querySelectorAll("button");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); if (!btns.length) return;
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length;
        btns.forEach((b, i) => b.setAttribute("aria-selected", String(i === active)));
      } else if (e.key === "Enter") {
        e.preventDefault(); choose(active >= 0 ? active : 0);
      } else if (e.key === "Escape") showResults("");
    });
    results.addEventListener("click", (e) => { const b = e.target.closest("button[data-i]"); if (b) choose(Number(b.dataset.i)); });
    const onDocClick = (e) => { if (!el.querySelector(".opp-search").contains(e.target)) showResults(""); };
    document.addEventListener("click", onDocClick);

    // ---- controls
    $("#opp-colorby").onchange = (e) => { state.colorBy = e.target.value; restyle(); drawLegend(); };
    $("#opp-showapps").onchange = (e) => { state.showApps = e.target.checked; drawApps(); drawLegend(); };
    el.querySelectorAll(".opp-tabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
    const ro = global.ResizeObserver ? new ResizeObserver(() => map.invalidateSize()) : null;
    ro && ro.observe($(".opp-map"));

    // ---- start
    rescore(false);
    drawLegend();
    drawPins();
    if (opts.initialTract) select(opts.initialTract, opts.initialPoint || null, { fly: false });
    (opts.initialCompare || []).forEach((c) => addCompare(c.tract, c.point || { label: c.label }));
    if (!state.selected) setTab("profile");
    else render();

    return {
      selectTract: (id) => select(id, null),
      setWeights: (w) => { state.weights = { ...w }; rescore(); render(); },
      getWeights: () => normalize(state.weights),
      setIndicators: (ind) => { setIndicators({ ...defaultInd, ...ind }); render(); },
      getIndicators: () => ({ ...state.ind }),
      getScores: () => state.scores,
      getProfile: (id) => (byId.has(id) ? profile(id) : null),
      addToCompare: (id, label) => addCompare(id, label ? { label } : null),
      map,
      destroy: () => {
        document.removeEventListener("click", onDocClick);
        mq && mq.removeEventListener && mq.removeEventListener("change", onTheme);
        themeObs.disconnect(); ro && ro.disconnect(); map.remove();
        el.innerHTML = ""; el.classList.remove("opp");
      },
    };
  }

  global.OpportunityModule = { mount, computeScores, normalize };
})(typeof window !== "undefined" ? window : globalThis);
