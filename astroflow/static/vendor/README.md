# Mitgelieferte Fremddateien der Shell

AstroFlow läuft offline, daher liegen diese Dateien im Repo statt auf einem CDN. Genutzt von der SkyTrace-3D-Ansicht und
dem Tiefen-Tunnel in „Bilder“ (per `import('/vendor/…')`, erst beim Öffnen geladen).

| Datei | Herkunft | Lizenz |
|---|---|---|
| `three.module.min.js` | three.js r160 (`three@0.160.0`, `build/three.module.min.js`, npm/unpkg), unverändert | MIT, siehe `LICENSE-three.txt` |
| `leaflet.js`, `leaflet.css` | Leaflet 1.9.4 (`leaflet@1.9.4/dist`, npm/unpkg), unverändert – SkyTrace „Karte laden“ beim Vorschlag „Unbekannter Ort“, erst beim Klick geladen | BSD-2-Clause, siehe `LICENSE-leaflet.txt` |
| `umrisse-welt.json` | Natural Earth 1:10 Mio. „Admin 0 – Countries“ v5.1.2 (`nvkelso/natural-earth-vector`), nur Außenringe, Douglas-Peucker 0,01° (~1 km), Koordinaten in 1/100°, je Ring Startpunkt + Deltas; Offline-Umrisskarte in SkyTrace | gemeinfrei (Natural Earth) |
| `OrbitControls.js` | three.js r160 `examples/jsm/controls/OrbitControls.js`; einzige Änderung: Import `'three'` → `"./three.module.min.js"` (kein Import-Map/Build-Schritt) | MIT, siehe `LICENSE-three.txt` |

Aktualisieren: gleiche Version beibehalten oder beide three.js-Dateien gemeinsam ersetzen (OrbitControls muss zur Version passen).

Umrisse neu erzeugen: GeoJSON `ne_10m_admin_0_countries.geojson` laden, Außenringe je Polygon mit Douglas-Peucker (Toleranz 0,01°) vereinfachen,
auf 0,01° runden, doppelte Punkte entfernen, Ringe < 4 Punkte weglassen, als `{v, scale: 100, source, rings: [[x0, y0, dx, dy, …]]}` speichern (~1 MB, gzip ~0,3 MB).
