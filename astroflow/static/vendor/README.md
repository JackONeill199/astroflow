# Mitgelieferte Fremddateien der Shell

AstroFlow läuft offline, daher liegen diese Dateien im Repo statt auf einem CDN. Genutzt von der SkyTrace-3D-Ansicht und
dem Tiefen-Tunnel in „Bilder“ (per `import('/vendor/…')`, erst beim Öffnen geladen).

| Datei | Herkunft | Lizenz |
|---|---|---|
| `three.module.min.js` | three.js r160 (`three@0.160.0`, `build/three.module.min.js`, npm/unpkg), unverändert | MIT, siehe `LICENSE-three.txt` |
| `OrbitControls.js` | three.js r160 `examples/jsm/controls/OrbitControls.js`; einzige Änderung: Import `'three'` → `"./three.module.min.js"` (kein Import-Map/Build-Schritt) | MIT, siehe `LICENSE-three.txt` |

Aktualisieren: gleiche Version beibehalten oder beide three.js-Dateien gemeinsam ersetzen (OrbitControls muss zur Version passen).
