"""Make a copy of combined_site/ that runs as a published Claude artifact.

    python -m pipelines.combined.build_artifact          # or: make combined-artifact

An artifact can't reach outside services or start its own downloads, so the copy differs from the site:
  - no street map (OpenStreetMap tiles are blocked); county and city labels stand in for it
  - search looks through the tract data (Esri and Nominatim are blocked), so no street addresses or suggestions
  - the CSV goes through the artifact's `downloads` capability; publish with capabilities {downloads: true}
  - links to the Fundability Map become plain text, because that site isn't part of the artifact
  - Leaflet's stylesheet ships with the page, because the artifact can't load one from a CDN

Writes combined_site_artifact/, which git ignores. combined_site/ stays the source: change it, then rebuild.
"""
from __future__ import annotations

import re
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "combined_site"
OUT = ROOT / "combined_site_artifact"
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"


def sub(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"combined_site changed, so this edit needs updating ({text.count(old)} matches): {old[:70]}")
    return text.replace(old, new)


def page() -> str:
    """The page's content only: publishing adds the document skeleton."""
    body = (SRC / "index.html").read_text().split("<body>")[1].split("</body>")[0].strip()
    body = sub(body, '<a class="skip" href="#lookup-input">Skip to address search</a>', '<a class="skip" href="#lookup-input">Skip to search</a>')
    body = sub(body, '<label for="lookup-input" class="visually-hidden">Address or coordinates</label>', '<label for="lookup-input" class="visually-hidden">City, county, tract or coordinates</label>')
    body = sub(body, '''<div class="combo">
          <input id="lookup-input" name="address" type="search" placeholder="1129 Euclid Ave NE, Atlanta…" autocomplete="off" spellcheck="false"
            role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="suggest">
          <ul class="suggest" id="suggest" role="listbox" aria-label="Address suggestions" hidden></ul>
        </div>''', '''<input id="lookup-input" name="address" type="search" placeholder="Decatur, Fulton, Tract 226.02, or 33.75, -84.39" autocomplete="off" spellcheck="false">''')
    body = sub(body, '<button class="btn primary" type="submit">Look up</button>', '<button class="btn primary" type="submit">Find</button>')
    body = sub(body, """Answers stay in this browser and are shared with the
          <a href="../fundability_site/">Fundability Map</a>.""", "Answers stay in this browser.")
    body = sub(body, 'id="download">', 'id="download" hidden>')
    body, n = re.subn(r'src="app\.js\?v=\d+"', 'src="app.js"', body)
    if n != 1:
        raise SystemExit("combined_site changed: the app.js script tag needs a look")
    return ('<title>LIHTC Opportunity & Need Dashboard</title>\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;700&display=swap">\n'
            '<link rel="stylesheet" href="leaflet.min.css">\n<link rel="stylesheet" href="style.css">\n' + body + "\n")


def styles() -> str:
    css = (SRC / "style.css").read_text()
    css = sub(css, "position: sticky; top: 0; z-index: 20; height: var(--bar-h);", "position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20; height: var(--bar-h);")
    return css + """
/* ── place labels: the artifact sandbox blocks map tiles, so these give the map its bearings ── */
.place-label { color: var(--text); font-size: 12px; line-height: 16px; font-weight: 500; text-align: center; white-space: nowrap; text-shadow: 0 0 3px var(--surface), 0 0 3px var(--surface), 0 0 6px var(--surface); }
.place-label.county { font-size: 13px; font-weight: 700; }
"""


def script() -> str:
    js = (SRC / "app.js").read_text()
    # No street map: draw place labels instead, and keep the fills at full strength.
    js = sub(js, "  const zoomedIn = state.map && state.map.getZoom() >= 13;\n", "")
    js = sub(js, "fillOpacity: (k === null ? 0.55 : 0.76) * (zoomedIn ? 0.55 : 1),", "fillOpacity: k === null ? 0.55 : 0.76,")
    js = sub(js, '''  const map = L.map("map", { preferCanvas: true }).setView([33.82, -84.39], 9);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
''', '''  const map = L.map("map", { preferCanvas: true, minZoom: 8, maxZoom: 16 }).setView([33.82, -84.39], 9);
''')
    js = sub(js, '''  let lastZ = map.getZoom();
  map.on("zoomend", () => { const z = map.getZoom(); if ((z >= 13) !== (lastZ >= 13)) restyle(); lastZ = z; });
  state.map = map;
}
''', '''  state.map = map;
  map.fitBounds(state.layer.getBounds(), { padding: [16, 16] });
  addPlaceLabels(map);
}

// County names when zoomed out, city names when zoomed in. Each sits at the middle of its tracts.
const CITY_ZOOM = 11;
function labelLayer(key, cls) {
  const bounds = new Map();
  state.feats.forEach((f) => {
    const name = key(f.properties);
    if (name) bounds.set(name, (bounds.get(name) || L.latLngBounds([])).extend(f._layer.getBounds()));
  });
  return L.layerGroup([...bounds].map(([name, b]) => L.marker(b.getCenter(), { interactive: false, keyboard: false,
    icon: L.divIcon({ className: `place-label ${cls}`, html: esc(name), iconSize: [160, 16], iconAnchor: [80, 8] }) })));
}
function addPlaceLabels(map) {
  const counties = labelLayer((p) => p.c, "county");
  const cities = labelLayer((p) => (/^unincorporated/i.test(p.lg) ? null : p.lg), "city");
  const sync = () => {
    const near = map.getZoom() >= CITY_ZOOM, show = near ? cities : counties, hide = near ? counties : cities;
    hide.remove();
    if (!map.hasLayer(show)) show.addTo(map);
  };
  map.on("zoomend", sync);
  sync();
}
''')

    # Search the tract data.
    start, end = js.index("/* ── address lookup ──"), js.index("function inRing(x, y, ring)")
    js = js[:start] + r'''/* ── search: the artifact sandbox blocks outside geocoders, so this looks through the tract data ── */
function findTracts(q) {
  const s = q.toLowerCase(), num = s.replace(/^(census\s+)?tract\s+/, "");
  const by = (test) => state.feats.filter((f) => test(f.properties));
  const tracts = by((p) => p.g === s || p.n.toLowerCase().replace(/^census tract\s*/, "") === num);
  if (tracts.length) return tracts;
  const exact = by((p) => p.lg.toLowerCase() === s || p.c.toLowerCase() === s || `${p.c} county`.toLowerCase() === s);
  return exact.length ? exact : by((p) => p.lg.toLowerCase().includes(s));
}
''' + js[end:]
    start, end = js.index("function onLookup(e) {"), js.index("/* ── CSV of every tract at the current settings ── */")
    js = js[:start] + r'''function onLookup(e) {
  e.preventDefault();
  const q = $("#lookup-input").value.trim(), status = $("#lookup-status");
  if (!q) return;
  const ll = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (ll) {
    const latlng = L.latLng(+ll[1], +ll[2]), f = tractAt(latlng), z = Math.max(state.map.getZoom(), 14);
    if (REDUCED.matches) state.map.setView(latlng, z); else state.map.flyTo(latlng, z, { duration: 0.8 });
    status.textContent = f ? "" : "Those coordinates are outside the 11 ARC counties.";
    if (f) select(f, latlng, "these coordinates");
    return;
  }
  const hits = findTracts(q);
  if (!hits.length) { status.textContent = "No match. Try a city, county, tract number, or lat, lon. Street addresses don't work here."; return; }
  const box = hits.reduce((b, f) => b.extend(f._layer.getBounds()), L.latLngBounds([]));
  if (REDUCED.matches) state.map.fitBounds(box, { maxZoom: 14, padding: [40, 40] });
  else state.map.flyToBounds(box, { maxZoom: 14, padding: [40, 40], duration: 0.8 });
  const best = hits.filter(scored).sort((a, b) => b.raw - a.raw)[0] || hits[0];
  status.textContent = hits.length > 1 ? `${fmtInt.format(hits.length)} tracts match. Showing the highest priority.` : "";
  select(best);
}

''' + js[end:]
    js = sub(js, "Type an address, or click anywhere on the map. The tool scores that exact spot.",
             "Search for a city, county, tract number or coordinates, or click anywhere on the map. The tool scores that exact spot.")
    js = sub(js, '''  $("#lookup-input").addEventListener("input", onLookupInput);
  $("#lookup-input").addEventListener("keydown", onLookupKey);
  $("#lookup-input").addEventListener("blur", () => setTimeout(closeSuggest, 120));
  $("#suggest").addEventListener("mousedown", (e) => e.preventDefault());   // keep focus in the box while picking
  $("#suggest").addEventListener("click", (e) => { const li = e.target.closest("li[data-i]"); if (li) pickSuggestion(+li.dataset.i); });
''', "")

    # The CSV goes through the downloads capability.
    js = sub(js, "function downloadCsv() {", "async function downloadCsv() {")
    js = sub(js, '''  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `lihtc_dashboard_tracts_${100 - Math.round(100 * state.bias)}need_${Math.round(100 * state.bias)}opp${customWeights() ? "_custom_weights" : ""}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
''', '''  const filename = `lihtc_dashboard_tracts_${100 - Math.round(100 * state.bias)}need_${Math.round(100 * state.bias)}opp${customWeights() ? "_custom_weights" : ""}.csv`;
  try {
    await state.downloads.save({ filename, data: csv });
  } catch (err) {
    if (err?.code === "declined") return;
    $("#download").textContent = "Couldn't save the file. Try again.";
    console.error(err);
  }
}
async function initDownloads() {
  state.downloads = await window.claude?.use?.("downloads");
  $("#download").hidden = !state.downloads;
  if (state.menuView === "data" && $("#menu").open) openMenu("data");
}
''')
    js = sub(js, '''<p><button class="btn secondary" type="button" data-download>Download every tract at the current settings (CSV)</button></p>''',
             '''${state.downloads ? `<p><button class="btn secondary" type="button" data-download>Download every tract at the current settings (CSV)</button></p>` : ""}''')
    js = sub(js, '''document.addEventListener("click", (e) => { if (e.target.closest("[data-download]")) downloadCsv(); });
''', '''document.addEventListener("click", (e) => { if (e.target.closest("[data-download]")) downloadCsv(); });
  initDownloads();
''')

    # Links to the Fundability Map become plain text.
    js = sub(js, "  const here = state.spot?.latlng || f._layer.getBounds().getCenter();\n", "")
    js = sub(js, '''<a href="../fundability_site/#site=${here.lat.toFixed(5)},${here.lng.toFixed(5)}">See the full breakdown in the Fundability Map</a>.''',
             "The Fundability Map, a separate tool in the project repository, shows the full breakdown.")
    js = sub(js, "Look up an address (or click a spot) and it's", "Enter coordinates (or click a spot) and it's")
    js = sub(js, "a looked-up address is scored where it is. Location\n        points come from a 0.25-mile grid, so an address takes", "coordinates or a clicked spot are scored where they are. Location\n        points come from a 0.25-mile grid, so a spot takes")
    js = sub(js, "the same rules as the Fundability Map, and the two tools share your answers. Until", "the same rules as the Fundability Map. Until")
    js = sub(js, '''<a href="../fundability_site/">Fundability Map</a>''', "Fundability Map")

    # Sources and limits describe this copy.
    js = sub(js, '''  ["World Geocoding Service", "Esri", "https://developers.arcgis.com/rest/geocode/", "Address suggestions and search"],
  ["OpenStreetMap tiles and Nominatim search", "OpenStreetMap contributors", "https://www.openstreetmap.org/copyright", "Map, and address search if Esri doesn't answer"],
''', "")
    js = sub(js, '''      <li>Scores rank tracts within metro Atlanta, not nationally.</li>
''', '''      <li>Scores rank tracts within metro Atlanta, not nationally.</li>
      <li>This copy has no street map and can't search street addresses, because the page can't reach outside map services.
        Search by city, county, tract number or coordinates instead.</li>
''')
    js = sub(js, '''"Couldn't load the data. Serve this folder over http (see combined_site/README.md)."''', '''"The tract data didn't load. Reload the page to try again."''')

    # Open the menu directly: the viewer's frame controls the URL.
    js = sub(js, '''$("#menu-btn").addEventListener("click", () => { location.hash = `#about/${state.menuView || "how"}`; });''',
             '''$("#menu-btn").addEventListener("click", () => openMenu(state.menuView || "how"));
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#about/"]');
    if (a) { e.preventDefault(); openMenu(a.getAttribute("href").slice(7)); }
  });''')

    # Follow the viewer's theme toggle as well as the system setting.
    js = sub(js, '''window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readPalette(); restyle(); renderLegend(); });
''', '''window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readPalette(); restyle(); renderLegend(); });
  new MutationObserver(() => { readPalette(); restyle(); renderLegend(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
''')
    return js


def main() -> None:
    OUT.mkdir(exist_ok=True)
    shutil.copytree(SRC / "data", OUT / "data", dirs_exist_ok=True)
    (OUT / "index.html").write_text(page())
    (OUT / "style.css").write_text(styles())
    (OUT / "app.js").write_text(script())
    with urllib.request.urlopen(LEAFLET_CSS) as r:
        (OUT / "leaflet.min.css").write_bytes(r.read())
    print(f"artifact files -> {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
