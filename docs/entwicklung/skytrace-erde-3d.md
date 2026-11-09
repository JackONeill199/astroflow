# SkyTrace: 3D-Erde, Karten, Heatmap, Lauf-Ansicht in 3D, Satellit anklicken

> Ausgelagert aus `astroflow/modules/skytrace/AGENTS.md` am 09.11.2026 (Aufräumen nach dem Vorbild von PR #18). Befunde und Regeln
> unverändert, Nutzerzitate gekürzt, private Angaben entfernt. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## 3D-Ansicht (`orbit3d.py`, `static/skytrace-3d.js|css`, 03.11.2026, Bahn muss stimmen)
`GET /api/skytrace/orbit3d?run=<Lauf>&key=<Ordner>/<K-Nr>` → `Controller.orbit3d` → `orbit3d.payload_for` (Kandidat,
WCS aus `<Beobachtung>/platesolve`, Bildecken der Trefferaufnahme über `context_image.reference_to_current`) → `build`.
Fehlt etwas (Standort, Zuordnung, Zeit, Plate-Solving): `{ok:false, reason}` statt Fehler. `run_data` setzt je Kandidat
`view3d` (`orbit3d.available`); nur dann zeigt das Detail den Knopf. Rechnung: numpy + erfa (aus astropy) + `sgp4`;
fehlt `sgp4` im Server, läuft `orbit3d.py` als Skript im Engine-Interpreter (JSON stdin/stdout).
- **Bezugssystem D** = erdfest (ECEF) zu t0, danach raumfest (TEME um GMST(t0) gedreht). Erde dreht sich darin mit
  `earth_rate`·(t − t0); Bahnen geschlossen; das nachgeführte Blickfeld zeigt fest (ICRS → TEME per `pnm06a` + `eqeq94`),
  sein Ursprung wandert mit dem Standort. Erdfestes (Standort, Meteore, Flugzeuge) kommt als ECEF. Frontend: three = (x, z, −y)/R.
  UT1 ≈ UTC, keine Polbewegung. Geprüft gegen astropy: Satellit 9 m, Zenitrichtung 16″ (Aberration), Sonne 0,006°.
- t0 = Belichtungsbeginn (`satellites.exposure_start`, sonst wie `motion_plausibility.exposure_start`) + Mittel von
  `passage_seconds` des besten Treffers. Je Satellit (≤ 5 Treffer): Umlauf (360 Punkte), ±10 min alle 2 s
  (Position, Höhe, Geschwindigkeit, Entfernung), Durchstoßstücke im Blickfeld (±2 min, 0,05 s, Punkt-im-Viereck in
  gnomonischer Projektion, über dem Horizont).
- **Bahnelemente:** neue Abgleiche speichern `matches[].tle` (`sgp4.exporter.export_tle`), Meteore `trajectory`
  (GMN Anfang/Ende + Zeit), Flugzeuge `track` ([t, lat, lon, Höhe m]). Ältere Läufe: `archive_satrec` sucht ohne Netz in
  `catalog_cache/archives/orbits-<Datum>.json` (wie `satellite_archive.model_from_record`) und `eth-<hash>.json`
  (Schlüssel wie `eth_tles`); sonst `missing[].reason` → Hinweis in der Ansicht.
- **Seit 0.35.0** zeichnet die Lauf-Ebene auf dem gemeinsamen Globus der 3D-Erde (s. „3D-Erde – eine Ansicht …“ weiter unten in dieser Datei); die Punkte zur Darstellung gelten weiter, Einbindung und Seitenleiste dort.
- Frontend (seit 0.21.0 kein Dialog mehr, zentrales Feature statt Popup): `SkyTrace3D.mount(host, run, {focus, session, onCandidate})`
  zeichnet Bühne (Kamera-Wahl, „Schema“, Vollbild), Zeitleiste und Objektliste in einen Behälter (`host._st3d` = Szene, `state()` für Tests) und
  gibt `{dispose, fullscreen}` zurück. Genutzt im Lauf-Reiter `3d` (`tab3d`, nur mit Bildfolge und nicht während einer Analyse; Fokus-Kandidat über
  `focus3d`, da der Router keine `?`-Parameter kennt) und in `#/skytrace/3d` (`sky3dView`: Analysenliste `.st3d-runs` oben in der Seitenleiste – nach jedem `mount` wieder eingesetzt –,
  Filter „Mit Kandidaten“ `skytrace.3dhits`, zuletzt gewählter Lauf in `skytrace.3drun`). Liste der Läufe standardmäßig als Karten (`skytrace.view2`).
  Eine gemeinsame Szene über alle Läufe gibt es nicht (jede Beobachtung eigene Zeit/Standort). Taste F = Vollbild. three.js r160 +
  OrbitControls (Import auf `./three.module.min.js` umgeschrieben) seit 0.25.0 in der Shell (`astroflow/static/vendor/`, auch für den
  Tiefen-Tunnel in Bilder), Erdtextur unter `static/vendor/` (Lizenzen in den jeweiligen `README.md`), erst beim Öffnen per `import()` geladen. Zeichnet nur bei Änderung; Kamerafahrten entfallen bei
  `prefers-reduced-motion`, kein Auto-Drehen. Kegellänge = 1,35 × Entfernung des Satelliten im Blickfeld (echte Öffnung,
  daher erst in „Blickfeld“ sichtbar). Atmosphärensaum blendet sich nahe der Erde aus. `#st3dHost._st3d.state()` für Tests.
- **Lauf-Ansicht (0.17.0, zeigt auch ohne erkanntes Objekt, wohin das Teleskop geschaut hat):**
  `GET /api/skytrace/run3d?run=&session=&focus=` → `run3d.build_run`. Je Beobachtung: Blickrichtung je Aufnahme aus
  Referenz-WCS + `frames.jsonl.reference_to_current` (≤ `MAX_SAMPLES` 360 Stichproben, Ecken → Bildlage/Positionswinkel),
  Az/Alt zur Belichtungsmitte (Stellina: Zeitstempel = Ende), parallaktischer Winkel (= Feldrotation einer Alt-Az-Montierung),
  Lücken (> max(120 s, 5 × Median, 3 × Belichtung)), ausgelassene Aufnahmen aus `ausgeschlossen.jsonl`. Ohne WCS:
  Zielkoordinate über `catalog_resolve` (Namensteile von lang nach kurz) und Bildfeld aus FOCAL/PIXSZ/NAXIS, `source='katalog'`.
  Ziel: `catalog_resolve` (Typ) + Sage-Dienst `object_facts` (Entfernung, nur Gespeichertes). Standort: Kandidat → `resolve_site`
  (Ausnahme, FITS, Ortsliste der Nacht, sonst Einstellungen); fehlt er → `{ok:false, need_site}`. Treffer: `orbit3d.build` je Kandidat im gemeinsamen
  System (`anchor` = erste Aufnahme), ±5 min, Umlauf 180 Punkte, bester Satellit je Kandidat (Fokus-Kandidat alle).
  Ergebnis per `lru_cache` nach Dateizeiten (echter Lauf mit 8 Treffern: 0,3 s, dann 2 ms; 240 KB).
- Frontend `SkyTrace3D.mount(host, run, {focus, session, onCandidate})`; Kandidaten-Knopf setzt `focus3d` und wechselt zum Reiter „3D“.
  Kuppel (`DOME` 0,045 R) und Sternkugel (`SKY` 60 R) sind erdfest bzw. raumfest und nicht maßstäblich. Ansichten Erde/Teleskop/
  Durchgang/Folgen, „Ziel“ beim Antippen des Ziels; Teleskop und Ziel mit lokaler Senkrechten als „oben“ (OrbitControls
  liest `camera.up` nur beim Anlegen → `makeControls` legt sie neu an) und folgen dem Standort, wenn sich die Erde dreht.
  „Kandidat öffnen“ setzt `focusNext` in `skytrace.js` und wechselt zum Reiter Kandidaten.
- Tests: `test_skytrace_3d.py` (gegen astropy, alte Läufe, Hinweise), `test_skytrace_3d_run.py` (Teleskopweg gegen astropy,
  Lücke, Bildlage, ohne Treffer/Standort/Plate-Solving), `test_skytrace_3d_ui.py` (Playwright, 375/1440 px).
  Offen: Flugzeug- und Meteordarstellung nur mit synthetischen Daten geprüft. Uhrversatz der Nacht: `skytrace-satelliten-uhr.md`.

## Karte und Heatmap je Nacht (0.34.0, 06.11.2026, die Karte erschien nur bei `kind='global'`; Wunsch nach einer Heatmap)
- **Daten** (`global_site.heat_map`): grob je 20-km-Zelle (gleiches Gitter wie die erste Stufe, Sinusprojektion um das Mittel der Orte) die größte Zahl
  verschiedener Spuren bei einem Versatz – nur der belegte Ausschnitt, uint8 Base64 (Zeilen Süd → Nord); fein aus `local` (1 km, ± 30 km, je Punkt bester
  Versatz) **um das Endergebnis nach `refine`** neu gerechnet (der grobe Block lag in der Probe 38 km daneben). Am Ergebnis bilden Spuren über den Bildrand
  ein Plateau längs der Bahn (synthetisch 95 km² gleich hoch) – das ist echte Unbestimmtheit, kein Fehler.
- **Ablage** `<SKYTRACE_HOME>/standortsuche-karten/<Nacht>.json` (`rematch_sites.save_heat`; `coarse`, `fine`, `clock_s`, `region_km`, `computed`, `result` =
  Kurzfassung mit Vorschlag/Häufungen). **Nicht** in `standortsuche-naechte.json` (gepollt; `_heat` wird vor dem Speichern entfernt). Echte Nacht: ~40 KB.
  Koordinaten bleiben lokal (Datenordner), nichts davon ins Repo.
- **Berechnen** (Entscheidung): Lief die Suche ohne Vorwissen nicht, weil ein Listenort passte, gibt es Knopf **„Heatmap berechnen“** statt sie immer zu rechnen
  (normale Nächte bleiben schnell) und statt nur einer `sitefit`-Ellipse (die zeigt nichts über andere mögliche Orte). Engine `rematch_sites.py heatmap
  <gruppen> <Nacht> [--global-km] [--no-clock]` → `global_site.search_night` für diese Nacht, ändert keinen Vorschlag; Server `SiteJobs.start_heatmap`
  (Auftrag `kind='heatmap'`, sperrt keine Läufe, nur Nächte aus `standortsuche-gruppen.json`), `POST /sitesearch/nights/heatmap {night, global_km, clock}`,
  `GET /sitesearch/nights/heatmap?night=` (`heat`, `job`), `GET /sitesearch/nights` → `maps` (Nächte mit Datei). Echte Kontrollnacht: 10 s.
- **UI** (`openNightMap` in `skytrace.js`): Knopf „Karte“ in jeder Nachtzeile (bei `global` gleich offen), Ansicht „Übersicht“ (Orte + heiße Zellen ≥ 60 % des
  Maximums) / „Nah“, Heatmap als Canvas-Bild im SVG über den Umrissen (Natural Earth, `outlines`), Farbskala dunkel → hell (`heatColor`), Orte grau,
  bisheriger Ort als Ring, Vorschlag blau mit Ellipse (`ellipse_km` von `sitefit`, 1σ, Winkel von Nord über Ost; sonst Kreis mit `accuracy_m`;
  bei `list` Koordinaten aus der Ortsliste). „Straßenkarte“/„Gelände“ = Leaflet (`leafletMap`, Zellen als Rechtecke, Canvas-Renderer, ≤ 4 000) – erst auf
  Knopfdruck; „Karten immer laden“ = `af.skytrace.mapsOnline`. Offene Karten bleiben beim Neuzeichnen offen (`opened`, `mapState`). Kopf: Leiste
  „n Vorschläge noch nicht übernommen“ mit „Alle übernehmen“ (`.stn-open`).
- Tests: `test_skytrace_global.py` (Heatmap-Datei: hellste grobe Zelle < 40 km, Mitte des feinen Plateaus < 5 km am wahren Ort, nicht in der Ergebnisdatei;
  „berechnen“ bei passendem Listenort; Server: Prüfungen, Datei, `maps`), `test_ui_layout.py::test_night_map_heatmap` (synthetische Heatmap, Umrisse, Nah,
  Nacht ohne Heatmap mit Knopf, keine Kachelanfragen) und Fixture `_heat_fixture`.

## 3D-Erde – eine Ansicht für Nächte, Standorte und Analysen (0.35.0, 06.11.2026, Karte und 3D-Ansicht zu einer zentralen Komponente vereint)
- **Eine Komponente, ein Globus:** `static/skytrace-erde.js|css` (Präfix `ste-`) = Globus-Engine `globe()` + Seitenleiste; `static/skytrace-3d.js` ist nur noch die
  **Lauf-Ebene** `SkyTrace3D.runLayer(G, data, {interactive})` auf dieser Szene (kein eigener Renderer). Navigation **„3D-Erde“** (ein Eintrag), Seite
  `#/skytrace/erde[/<Nacht>]` = Kontext **„Nächte & Standorte“**, `#/skytrace/3d` = Kontext **„Analysen“** (alter Link bleibt gültig; der Umschalter oben in der
  Seitenleiste ändert die Adresse per `replaceState`). Lauf-Reiter „3D“ (`tab3d`) **bettet dieselbe Komponente ein** (`SkyTraceErde.embed`, Lauf fest, ohne Lauf-/Nachtliste,
  mit allen Ebenen) – Entscheidung: im Lauf springt man zwischen Kandidaten, Vorschau und 3D; ein Sprung in die zentrale Ansicht würde den Lauf mit seinen Reitern verlassen.
  Kandidatenknopf „3D-Ansicht“ wechselt wie bisher zum Reiter mit Fokus (`focus3d`). `SkyTrace3D.mount` = Alias auf `embed`.
- **Helfer** aus `skytrace.js` über `window.SkyTraceHelpers` (Heatmap-Zellen, `nightMarks`, `ellipsePts`, `outlines`, `applyNights`, Texte, `runVariants`, `runDate`, `media`,
  `openCandidate`). Datenweg wie 0.34.0 (`standortsuche-naechte.json`, `standortsuche-karten/<Nacht>.json`, Ortsliste, `run3d`); neu nur `GET /sitesearch/nights/heatmaps?nights=a,b`
  (leer = alle, ≤ 120 je Anfrage; das Frontend lädt in Paketen zu 100 und holt eine fehlende Nacht beim Wählen einzeln nach). `nightsView` hat je Nacht „Auf der Erde zeigen“.
- **Szene:** Erde = Gruppe `earth` (WGS84-Ellipsoid: Kugelgeometrie per `toEllipsoid` umgerechnet, Breite = geodätisch – sonst lagen Listenort und Teleskopstandort bei 47° ~20 km
  auseinander). Alles Erdfeste (Textur, Grenzen, Heatmap, Orte, Kacheln, Kuppel/Standort des Laufs) hängt daran. **Analysen:** die Zeitleiste dreht `earth` um
  `earth_rate`·(t − anchor) (System D wie bisher, Raumfestes direkt in der Szene). **Nächte:** Drehung 0 = erdfest; die Ebene „Satellitenbahnen und Teleskop“ zeigt dieselbe
  Lauf-Ebene **statisch zur Ankerzeit** (bei Drehung 0 ist D = ECEF des Ankers, also ohne Umrechnung und ohne zweite Darstellung): Kuppel, Teleskopweg, Blickfeld der ersten
  Aufnahme, Satelliten an ihrem Durchgang, ohne Zielstrahl. Kontextwechsel: Lauf entfernen, Erde in 0,9 s zurückdrehen, Kamera fährt zur Nacht; Laufwahl = Kamerafahrt der Lauf-Ebene.
- **Schnittstelle G** (für `runLayer`): `THREE, sc, earth, camera, controls(), makeControls(up)` (OrbitControls liest `camera.up` nur beim Anlegen), `zoomMode('globe'|'orbit')`
  (Globus: eigenes Zoomen über die Höhe; Teleskop/Durchgang/Folgen: OrbitControls zum Blickpunkt), `setFly`, `dirty`, `addMat(layer, m, base)`/`dropMats` (Deckkraft der Ebene),
  `layerK`, `hook('draw'|'frame'|'resize'|'pick', fn)` → Abmelden, `cardEl` (eine Infokarte für Nächte und Lauf-Objekte), `setSun`, `onRunPick`.
- **Ebenen** (`LAYERS`, localStorage `skytrace.erde.layers`; im Analysen-Kontext ist „Satellitenbahnen“ eingeschaltet): Orte der Liste, Ort je Nacht (Ring), Heatmap, Vorschläge
  (Punkt + 1σ-Ellipse; übernommene Nächte ohne), Satellitenbahnen und Teleskop, Grenzen/Küsten (ein `LineSegments`, ~190 000 Punkte), Tag/Nacht-Grenze. Deckkraft `k` je Ebene,
  450 ms weich (`applyK`). Im Analysen-Kontext gelten Ort/Vorschlag/Heatmap der **Nacht des Laufs** (`run3d` liefert `night` nach `core/sites.night_of`; Rückfall Anker − 12 h).
- **Heatmap:** je Nacht eine Kappe über dem belegten Ausschnitt mit Wert-Textur (r = Spuren/Höchstwert, 3 px je Zelle, ≤ 1024²), Farbe per Shader aus der Skala von `heatColor`;
  Zellen mit 1–2 Stimmen fast durchsichtig (Rauschen einzelner Spuren; die Streifen im ersten Screenshot waren Zufallswerte der Test-Fixture). Grob (20 km) immer, fein (1 km, ± 30 km)
  blendet zwischen 1,55 und 1,2 R ein. „Aufbau“: `reveal` 0 → 1 in 1,4 s, hellste Zellen zuerst. „Alle Nächte“ = alle Heatmaps, je Nacht relativ.
- **Nachtsicht** (`EARTH_FS`, Uniform `nv`, 600 ms): Nachtseite → gedämpftes Blaugrün, Umrisse hell-türkis; Auto (`skytrace.erde.nv`) = an, wenn eine Datenebene an ist und der
  Blickpunkt auf der Nachtseite liegt (Skalarprodukt mit der Sonne < 0,12); An/Aus übersteuert. **Sonne:** Analysen = `run3d.sun` (raumfest – die Grenze wandert mit der Zeit);
  Nächte = vereinfachte Formel (`sunLatLon`, ~0,01°) zur Ankerzeit des ersten Laufs der Nacht, sonst Mitternacht Ortszeit an der Region.
- **Kamera:** Globus-Fahrt = Slerp + Abstand mit Bogen (1,5 s), Ausschnitt aus Heatmap, Vorschlag, bisherigem Ort und Bahnen, mindestens 1,09 R; Lauf-Ansichten Erde/Teleskop/Durchgang/
  Folgen/Ziel wie bisher (1,1 s). `prefers-reduced-motion`: alles sofort.
- **Kacheln:** „Karte laden“ erst unter 1,08 R im Globus-Zoom, 4 × 4 OSM-Kacheln als Textur der Erdgruppe, entfernt sich ab 1,5 R. Sonst nie Netz.
- **Leistung** (06.11.2026, 1440 × 900, Playwright-Chromium, mittlerer Bildabstand): mit Hardware-GPU 16,7 ms bei Nachtwechsel, Kontextwechsel, Teleskop-Ansicht
  und Abspielen (einzelne 50 ms beim Neuaufbau der Marken); ohne GPU (SwiftShader) 20–28 ms. Zeichnen < 0,2 ms CPU.
- Tests: `host._ste.state()` (Kontext, Nacht, Lauf, `earthRot`, `nv`, `layers`, `reveal`, `runs`, Bildzeiten), `host._st3d.state()` = Lauf (wie bisher). `test_skytrace_erde_ui.py`
  (Ebenen, Nachtsicht, Karte, Regler, keine Kacheln; Bahnen-Ebene statisch; Kontextwechsel mit Erddrehung und Rückdrehung, alter Link; Sammel-Endpunkt),
  `test_skytrace_3d_ui.py` (Lauf-Reiter eingebettet, zentrale Ansicht ohne Standort), `test_ui_layout.py` `ROUTES`.

### Flache Karte (0.37.0, 06.11.2026, Ziel: wie ein Kartendienst – reingezoomt normale Karte, rausgezoomt Kugel, mit Heatmap)
- **`static/skytrace-karte.js`** (`SkyTraceKarte.FlatMap(stage, deps)`): Canvas 2D in Web-Mercator über der WebGL-Bühne (Klasse `ste-flat`), Weltkoordinaten x, y ∈ [0, 1],
  Maßstab `Z` px je Welteinheit (px/km = Z / (40 075 · cos φ)). Zeichnet nur bei Änderung; ist sie ganz eingeblendet, zeichnet der Globus nicht (`flat.tick` → `flatFull`).
  Daten kommen über Getter aus `globe()` (Deckkraft der Ebenen, `nv`, `shade`, Sonne im erdfesten System, sichtbare Heatmaps, `M.flatMarks` aus `rebuildMarks`, Teleskopstandorte).
- **Übergang:** `zoomBy` (Mausrad/Pinch zum Zeiger, Punkt unter dem Zeiger per Quaternion nachgeführt) ruft unter `FLAT_IN` 1,07 R (≈ 450 km) `enterFlat(sx, sy)`:
  Sehstrahl → Erdpunkt, Maßstab = Bildschirmhöhe/2 / (tan(fov/2) · Abstand Kamera–Punkt) → `flat.enter(lat, lon, px/km, sx, sy)` → derselbe Ort am selben Pixel, 220 ms
  Überblendung (reduzierte Bewegung: sofort). Zurück, wenn der Kartenmaßstab unter den von `FLAT_OUT` 1,085 R fällt (Hysterese): `leaveFlat` setzt die Kamera über die Kartenmitte
  in passender Höhe und dreht zweimal so nach, dass der Ort unter dem Zeiger bleibt. Knopf „Karte/Globus“; Kamerafahrten (`flyTo`, andere Nacht, Übersicht) verlassen die Karte zuerst;
  Antippen derselben Nacht in der Karte zeigt nur ihre Detailkarte (`M.lastNight`). Lauf-Ansichten Teleskop/Durchgang/Folgen (`zoomMode('orbit')`) verlassen die Karte.
- **Bedienung:** Karte – Ziehen hält den Weltpunkt unter dem Zeiger fest, Rad/Pinch um den Zeiger, keine Trägheit, Zoom bis ~2,5 m/px. Globus – OrbitControls drehen nur noch in den
  Lauf-Ansichten; im Globus-Modus **Greif-Drehen** (Sehstrahl-Treffer beim Anfassen bleibt unter dem Zeiger, Quaternion je Bewegung, Pole begrenzt, daneben im All langsam nach Höhe).
  Mausrad auf der ganzen Bühne (vorher schluckten Schilder es).
- **Ebenen in der Karte:** Erdbild in drei vorab berechneten Fassungen (Tag, Nacht, Nachtsicht – Formeln wie `EARTH_FS`), in 48 Streifen von der Plattkarte nach Mercator; Nachtmaske
  72 × 48 aus der Sonnenrichtung (`destination-in`). Grenzen als Pfade (Ringe nach Umriss-Rechteck verworfen), Heatmap als scharfe Zellen (Farbe/Durchsicht wie `HEAT_FS`; fein ab 1,5 px/km
  eingeblendet, voll ab 6 px/km, grob dann 40 %), Orte, Ort je Nacht (Ring), Vorschläge mit Ellipse, Teleskopstandorte (Quadrat). **Satellitenbahnen nicht** – Bahnen im Raum
  (Hunderte km hoch, ein Umlauf um die ganze Erde) ergäben auf einer ebenen Karte nur verzerrte Linien ohne Bezug zum Bodenort; gezeigt wird, wo das Teleskop stand.
  Kacheln (OSM/OpenTopoMap) nur nach Klick auf „Straßenkarte“/„Gelände“; die alte Kugelkappe mit Kacheln (0.35, eine grobe Stufe als Textur gestreckt – Befund
  „Auflösung schlecht“) entfällt. **Zoomstufe** z = round(log2(Z · devicePixelRatio / 256)) → 1 Kachelpixel ≈ 1 Gerätepixel (Faktor 0,7–1,4); in Gerätepixeln gezeichnet,
  auf ganze Pixel gerundet, deckend. Nur sichtbare Kacheln, höchstens `MAX_TILES` 96 je Ansicht (sonst eine Stufe gröber – 48 drückten bei dpr 2 die Stufe), LRU-Cache 400
  im Speicher, Ladevorgänge außer Sicht abgebrochen, keine Vorab-Downloads, Quellenangabe sichtbar (Nutzungsregeln OSM/OpenTopoMap; einen eigenen User-Agent kann der Browser
  nicht setzen). Bis feine Kacheln da sind, steht der nächste geladene Vorfahr (≤ 4 Stufen) als Platzhalter, dann 200 ms Überblendung (`S.again` hält das Zeichnen am Laufen).
  Globus-Textur: Anisotropie = Maximum der Karte, trilineare Mipmaps.
- **Panel in der Karte** (Optionen liegen rechts in der Karte, damit diese mehr Platz hat): ab 861 px hängt `component` die Seitenleiste
  `.ste-side` in die Bühne (`.ste-over`, Glas, rechts, einklappbar über `.ste-paneltg`, localStorage `skytrace.erde.panel`); Detailkarte und Maßstab weichen nach links aus
  (`.panel-open`). Darunter (`matchMedia`-Wechsel) zurück unter die Karte.
- **Anzeige:** `.ste-readout` = Maßstabsleiste (1–2–5-Stufen, 60–150 px) + Koordinaten unter dem Zeiger auf 0,01° (auch auf dem Globus unter 1,6 R).
- **Leistung** (06.11.2026, 1440 × 900): Karte zeichnen 0,9 ms (GPU) bzw. 5 ms (SwiftShader), Verschieben/Zoomen 60 fps in beiden; Zoom bis zur Karte inkl. Überblendung 17–19 ms je Bild.
- Tests: `test_skytrace_erde_ui.py::test_kartenmodus_kugel_und_flache_karte` (375 mit reduzierter Bewegung, 1440: Übergang am Zeiger ≤ 0,05°, 1:1-Verschieben exakt, kein Nachlauf,
  Maßstab/Koordinaten, Antippen → Karte der Nacht, Kacheln erst nach Klick, zurück zur Kugel), `test_kartenkacheln_passend_zur_aufloesung` (dpr 1/2, Kachel-Attrappe mit z/x/y: Stufe passend,
  ≤ 96 Kacheln, nichts doppelt, Quellenangabe). `host._ste.state().flat`, `_ste.flat.at/screenOf`.

## Satellit anklicken – Infokarte und Bildausschnitt (0.45.0, 07.11.2026, Satellit anklicken: Infos plus Ausschnitt der eigenen Aufnahme)
- **Antippen im Bildschirmraum** (`skytrace-3d.js` `near`, Hook `near` in `globe()`): je Treffer Stützpunkte `polys` (Bahn ±5 min jede 3., Umlauf jede 2., Durchstoß, Punkt
  selbst), projiziert, Abstand zu Punkt/Segment; hinter der Erde (`behind`) zählt nicht. Radius Maus 14 px, Stift 18, Finger 26 (`pointerType`). Ein Treffer → `select`,
  mehrere → Auswahl `.ste-pick` (≤ 8, Pfeiltasten/Enter/Esc, Fokus per `setTimeout` nach dem Klick). Erst danach die alten Raycast-`pick`-Hooks (Teleskop, Ziel, Orte).
  Überfahren (nur Maus, einmal je Bild): `setHover` → `emph` (Linien-Basisdeckkraft ×2,4, Punkt ×1,6 über `G.refresh` = `applyK`), Namensschild `.ste-tip`, Zeiger `.ste-hot`.
  Die Bühne darf nie scrollen (`scroll`-Wächter) – sonst verschob ein Fokus/`scrollIntoView` im Menü die ganze Karte.
- **Statisch (Nächte & Standorte)** öffnet ein Satellit jetzt seine Karte statt der Nachtkarte; Teleskop/Ziel weiter `onRunPick`. Kandidat öffnen über
  `onCandidate` bzw. `SkyTraceHelpers.openCandidate(data.run, key)` – dafür liefert `run3d` jetzt `run` (Laufname).
- **Flache Karte:** `orbit3d.build` gibt je Satellit `ground` (geodätisch, `erfa.gc2gd`, jede `GROUND_EVERY` = 5. Stützstelle → 10 s, 61 Punkte ≈ 1 KB) und `ground_step`.
  `runLayer().ground()` → `deps.satellites()`: gestrichelte Bodenspur (Länge stetig über die Datumsgrenze, **verankert am Punkt beim Durchgang** in der Weltkopie nächst der Mitte wie alle Marken – an `pts[0]` verankert lag er bei Kartenmitte nahe dem Gegenpunkt genau eine Weltbreite daneben, CI-Befund), Raute = Punkt unter dem Satelliten beim Durchgang, Schild nur bei
  Hervorhebung oder ≤ 3 Satelliten. Abstand auch zur Spur (`distTo`), Satelliten vor Orten/Nächten, mehrere → `{kind:'multi'}` → dieselbe Auswahl. (0.37 zeigte bewusst keine
  Bahnen im Raum auf der Karte – das bleibt so; die Bodenspur ist eindeutig ein Erdort.)
- **Karte** (`card` in `runLayer`): Grundzeilen sofort aus `run3d` (Durchgang, Höhe/Entfernung/Geschwindigkeit beim Durchgang bzw. zur Zeitleiste, Umlauf, Neigung,
  Bahnelemente, Blickfeld, Abgleich); gleiche Auswahl beim Abspielen erneuert nur `.st3d-rows` (Ausschnitt flackert nicht). Details erst beim Anklicken:
  `GET /api/skytrace/satellite?run=&key=<Ordner>/<K>&norad=` (`satdetail.details`, Antwort ~2 KB, im Browser je Satellit gemerkt, solange die Lauf-Ebene lebt – neue Ansicht/Laufwahl lädt neu, nach einem Neuabgleich also nichts Veraltetes; Review PR #85) → Ausschnitt + Zuordnung oben, Katalogzeilen unten.
- **`satdetail.py` (nur Gespeichertes, kein Netz):** Kandidat über `videos.json` (`find`, K-Nummer `^[A-Za-z0-9_-]{1,40}$`, Ordner muss eine Beobachtung sein);
  SATCAT aus `catalog_cache/archives/satcat.csv` (eigener kleiner Leser, `lru_cache` je Dateistand; Spalten Name/COSPAR/Art/Betreiber/Start/Wiedereintritt/Bahn), sonst
  `satcat-names.json`, sonst COSPAR aus TLE-Zeile 1 (`intl_from_tle`). Art: Konstellation aus dem Namen (`kind_of`), sonst SATCAT-Typ. Winkelgeschwindigkeit = Spurlänge ÷
  Durchgangszeit der Bahn (`passage_seconds`), sonst Untergrenze aus `evidence`/`plausibility` („≥“). Helligkeit exakt wie die Statistik (`satellites.phot_of` des
  angeklickten Treffers, `pick_zp` mit Nacht/Gerät aus `stats._run`, Dienst `night_zero_points`). **Zuordnung** `certainty`: `alternative` (nicht der beste Treffer),
  `unsicher` (`site_widened`), `mehrdeutig` (Zweiter wie `sitefit._sure` fast gleich gut, Namen in `others`), `eindeutig` (≤ 60″, Abdeckung ≥ 80 %, ≤ 2 s außerhalb), sonst
  `wahrscheinlich`; dazu Fakten (Abstand, Abdeckung, Richtung, Zeit). Externe Links CelesTrak/N2YO nur als `<a rel="noopener noreferrer">`.
- **Ausschnitt:** Quelle = Ausschnitt-PNG des Trefferbilds (`frames[hit].image` + `overlay.points`, bei fehlender Linie `context_image.points` − `crop`), sonst Vollbild
  (`full_image`/`context_image`). `crop_box`: Spur + Rand (≥ 24 px bzw. 12 %), ≥ 160 px, Seitenverhältnis 1,4, im Bild; ≤ 560 px, Streckung Perzentil 1/99,8, γ 0,85, JPEG 82
  (~30–60 KB). `GET /api/skytrace/satellite/crop?run=&key=` liefert ihn; Zwischenspeicher **`kandidaten/<K>/satellit-ausschnitt.jpg`** (fester Name, Kennung `v<CROP_VERSION>-<mtime>-<box>`
  als Kommentar hinter dem JPEG-Ende, atomar ersetzt; schreibgeschützter Lauf → nur ausliefern). Die Karte legt zwei gestrichelte Linien (±9 px) beidseits der Spur
  (SVG über dem Bild); Klick öffnet den Kandidaten im Lauf. Fehlt alles → `crop: null` + `crop_note` (Hinweis auf „Zeitreihen aktualisieren“).
- Prüfwerte: `host._st3d.screen()` (je Treffer ein sichtbarer Bildschirmpunkt), `_st3d.sel()`, `_ste.satScreen()`, `_ste.satSel()`, `_ste.chooser()`, `_ste.state().flat.sats`.
- Tests: `test_skytrace_satellit.py` (Details, Stufen, ohne SATCAT, Pfadprüfungen, Zwischenspeicher, Bodenspur gegen astropy), `test_skytrace_erde_ui.py::test_satellit_anklicken`,
  `::test_satellit_in_der_flachen_karte`, `::test_satellit_antippen_handy` (zwei Satelliten auf derselben Bahn → Auswahl; Details lazy; Ausschnitt → Kandidat).

- **Heatmap in der 3D-Erde (0.43.5):** Deckkraft = Regler unter Ebenen (`localStorage af.skytrace.erde.heatop`, Standard 0,4 = Deckkraft des heißesten Bereichs; Alpha ∝ 0,08 + 0,92·v^1,3, Shader `HEAT_FS` und `drawHeat` in `skytrace-karte.js` gleich). Aus: Häkchen, Knopf „Ausblenden“ an der Legende, „Heatmap ausblenden“ in der Nachtkarte (`setLayer`). Legende nur sichtbar, solange die Ebene an ist.
