# Mitgelieferte Fremddateien (3D-Ansicht)

AstroFlow läuft offline, daher liegen diese Dateien im Repo statt auf einem CDN.

| Datei | Herkunft | Lizenz |
|---|---|---|
| `three.module.min.js` | three.js r160 (`three@0.160.0`, `build/three.module.min.js`, npm/unpkg), unverändert | MIT, siehe `LICENSE-three.txt` |
| `OrbitControls.js` | three.js r160 `examples/jsm/controls/OrbitControls.js`; einzige Änderung: Import `'three'` → `"./three.module.min.js"` (kein Import-Map/Build-Schritt) | MIT, siehe `LICENSE-three.txt` |
| `earth-2048.jpg` | NASA Visible Earth, Blue Marble „Land Surface, Shallow Water, and Shaded Topography“ (`land_shallow_topo`, Reto Stöckli, NASA Goddard Space Flight Center), auf 2048×1024 verkleinert | gemeinfrei (NASA) |

Aktualisieren: gleiche Version beibehalten oder beide three.js-Dateien gemeinsam ersetzen (OrbitControls muss zur Version passen).
