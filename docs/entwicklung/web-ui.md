# Web-UI, Designsystem, Frontend-Helfer

> Ausgelagert aus `AGENTS.md` am 09.11.2026, Text unverändert übernommen. Regeln, die beim Ändern von Code gelten,
> stehen zusätzlich in Kurzform in `AGENTS.md` → „Wenn du … änderst“. Neue Befunde hier ergänzen, nicht in `AGENTS.md`.

## Modul-Vertrag Frontend (vollständiger Text)

- **Modul-Vertrag Frontend** (`AF.register(id, {nav, routes, summary, recent, discoveries, homeFeatured, settings, palette, status, drop, sitesPanel})`, Kommentar in `app.js`; `sitesPanel(host)` = eigener Abschnitt unter Einstellungen → Standorte;
  `nav[].area` 'work' = Arbeitsbereich, 'library' = Bibliothek, sonst Unterpunkt (optional `parent`, 'home' = unter der Übersicht);
  Kontextpanel rechts per `AF.ctx.open(el, {title, html|render})`, wird beim Ansichtswechsel geleert; Prozessanimationen `vizProcess(host, {kind, …})` aus `static/viz.js`): Helfer (`api`, `send`, `toast`,
  `createViewer`, `folderDialog(start, pick, {title, ok, hint})` (Ordner auf dem Server-Rechner wählen, über `/api/folders` → `core/folders.py`: Laufwerke, Unterordner, FITS-/Bildzählung; genutzt von Hauptordner und SkyTrace; Suchfeld seit 0.31.0: sofort im aktuellen Ordner, entprellt 300 ms + abbrechbar `/api/folders/search?path=&q=` = Breitensuche Tiefe ≤ 4, ≤ 200 Treffer, ≤ 300 000 Einträge/5 s – je Eintrag geprüft, auch mitten in großen Ordnern, Schlüssel `folders.norm`/`folderKey` ohne Leerzeichen/„_“/„-“; bewusst keine Mehrfachauswahl, beide Aufrufer nehmen genau einen Ordner), `picker` (Dropdown mit Suche für Modelle/Stimmen, Listen aus `core/models.list_models`), `jobLine`, `confirmDlg`, `crumbs`, `store` …) sind global. Modul-Skripte lädt die Shell aus `/api/app` mit `?v=<build>`
  (Build-Kennung aus Dateizeiten) – nur `index.html` braucht weiter das manuelle `?v=`.

## Web-UI (`astroflow/static/` + `modules/*/static/`)
Hash-Routing: `#/` Übersicht, `#/nacht`, `#/himmel` (Himmelsqualität), `#/analyse` (Analyse-Dashboard), `#/bilder` (Sessions), `#/galerie` (fertige Bilder), `#/p/<id>/<bild|analyse|aufnahmen|versionen|optionen>`, `#/import`,
`#/skytrace[/neu|/3d (= 3D-Erde, Analysen)|/erde[/<Nacht>]|/statistik|/<lauf>/<vorschau|kandidaten|beobachtungen|protokoll|3d>]`, `#/sage[/wiki|/entdeckungen|/sammlung|/<eintrag>]`, `#/activity` (Aufträge), `#/settings/<modul|standorte|module>`.
**Designsystem (seit 0.21.0, Nutzerauftrag „professionelles, konsistentes Designsystem wie eine native Desktop-App“):** Tokens in `:root` von `style.css`
(`--bg/--sidebar/--surface/--surface-2/--border/--fg/--mut/--dim/--acc/--ok/--warn/--bad`, Maße, `--t` 160 ms); Datenfarben `--data-2/--data-3`, Rahmen `--border-soft`;
die alten Aliase (`--panel`, `--line`, `--grad`, `--acc2/3` …) sind seit 0.49.1 entfernt – neue Stile nutzen nur die Tokens. **Inhaltsbreiten:** der Router gibt jedem Ansichtsbehälter die Klasse `page` (zentriert, `--page-max` 80rem), `page wide`
(Arbeitsflächen: `PAGE_WIDE` in `app.js` – Galerie-Betrachter, SkyTrace-Lauf und 3D; die Objektseite `#/p/…` ist eine normale Seite) bzw. `page narrow` (`--page-narrow` 60rem, nur Willkommen-Assistent; Einstellungen seit 0.45.5 normale `page`, Abschnitte `#sbody .split` ab 60rem Containerbreite zweispaltig; Ein/Aus-Optionen immer als `label.modrow` mit `input[type=checkbox]` + `span.switch`, nie nacktes Kästchen);
Lesetext mit `.readable` / `--read` 42rem. Keine Karte in der Karte (`.panel .panel` ohne Fläche). Kontextpanel startet geschlossen
(`AF.ctx.shown`, je Ansicht neu, nicht gespeichert), Knöpfe mit `data-ctx-toggle` öffnen es. Objektseite, Reiter Bild: Details als
Spalte `.imgwrap > #imgSide` neben dem Bild (Standard sichtbar, `bild.details`); der „Details“-Knopf schaltet dort die Spalte (`data-side`).
Die Spalte zeigt nur noch Kernzahlen (`analysisSummary`: Bildqualität, Grenzgröße, Bortle, Seeing → Link in die Analyse); importierte Bilder behalten die Bildprüfung.
**Reiter Analyse** (seit 0.27.0, `static/analysis.js|css`, Nutzerwunsch „zu gequetscht in der Detailspalte“): `analysisTab(t, {p, r, solve, viewerFor})` = kompakter
Betrachter (gleiche Overlays) + Abschnittsliste, darunter breite `.an-sec`: `depthPanel(host, r, viewer, {wide, onFocus, onSolve})` (Kennzahlen `data-count`,
Fakten mit `data-x/y` → `viewer.zoomTo`; `opts.on3d`/`onData` für den 3D-Knopf), `comparePanel` mit
`compareViewer` links (`.an-cmp.has-blink`), `AFsky.analysis(host, pid)` (Bortle-Skala `bortleScale`, Kurven `sqm`/`seeing`, Windrose aus `/stacking/sky`),
`tutorPanel`. `anReveal` blendet Abschnitte per IntersectionObserver ein (`.in`), `anCountUp` zählt die erste Zahl eines Texts hoch (deutsches Format);
CSS-Animationen hängen an `.an-sec.in` und entfallen bei `prefers-reduced-motion`.
**3D im Betrachter** (seit 0.27.1, Nutzerwunsch „kein 2. Betrachterfeld, aus dem Bild oben hervorgehen“): Knopf `[data-v3d]` in der `.vbar` →
`depthTunnel(d, r, viewer)` legt `.tunnel.in-viewer` über das Foto (vor die `.vbar`, Ziehen/Mausrad gehen nicht an den flachen Betrachter) und gibt
`{close(animiert)}` zurück. Startbild = Foto als Textur (gleiche Datei, sRGB, deckend), Kamera im Ursprung mit `fitFov` = Einpassen (contain);
Punkte liegen auf den Sichtstrahlen ihrer Bildposition (`dirOf × Abstand`) – von vorn decken sie sich daher mit dem Foto. Übergang: `k` schiebt sie
von der Bildebene (`tF` ≈ 30 % des Trichters) in ihre Entfernung, `fade` (Blickwinkel zur Bildebene) blendet Punkte/Rahmen ein und das Foto auf
75 % ab. Ohne Betrachter bleibt der Vollbild-Dialog. Galerie = Betrachter `#/galerie/<id>`
(`pictureView`, Vor/Zurück aus `galleryOrder`), Bilder = Verarbeitung `#/p/<id>`; `objectKnowledge(host, name)` holt Sages Wissenskarte
(`/api/sage/kb/card`, legt bei Bedarf einen Grundeintrag an) für Betrachter und Details-Panel. **Alle Maße in `rem`** (1rem = 16px; nur 1–2px-Rahmen und Media-Queries in px) – `html` wird ab 1800/2300/3200 px Breite größer
(17,5/19,5/23 px) bzw. per Einstellung „Darstellung“ (`localStorage af.ui.scale`, `applyUiScale`); neue Stile daher in `rem` schreiben. Nur dunkel. Keine Marketing-Überschriften. **Atmosphäre (seit 0.24.0, Nutzerwunsch „Farbverläufe und bisschen glassy look wie früher“):**
`static/look.css` über `<html data-look="dezent|nebula">` (ohne Attribut = „Schlicht“, das Anthrazit von 0.21; `index.html` setzt es schon im `<head>`,
`applyLook`/`UI_LOOKS` in `app.js`, `localStorage af.ui.look`, Standard „dezent“). Tokens `--bg-grad`, `--glass`, `--glass-bar`, `--glass-border`, `--glass-hi`,
`--grad-acc`, `--grad-soft`, `--glow`. Verlauf nur im Seitenhintergrund (am `<html>`, `fixed`), auf Primärknöpfen, aktiver Navigation, Reiterstrich;
Glühen nur auf Primärknöpfen. `backdrop-filter` nur auf Werkzeugleiste, Seitenleiste, Kontextpanel; Karten/Panels nur halbtransparent (Glasregeln per
`:where(:root[data-look])` mit niedriger Spezifität, damit flache Varianten wie `.img-side .panel` gewinnen). Menüs, Dialoge, Eingaben deckend.
Eingaben nur `background-color` setzen (Suchfeld trägt sein Symbol als `background-image`). `prefers-reduced-transparency` → deckend ohne Unschärfe.
Sternschnuppen: `static/meteors.js` (`window.meteors`, Ebene `#meteors`, `z-index: -1` hinter allem; Nutzerwunsch „selten, Algorithmus nach
echten Daten“): `sky(date, lat, lon)` rechnet die sichtbare Rate – `SHOWERS` (IAU-MDC: λ☉max, ZHR, Jenniskens-Profil 10^(−B·Δλ), Radiant, v, r),
HR = ZHR · sin(Radiantenhöhe) · r^(Grenzgröße − 6,5), sporadisch 2 + 10·sin(Apexhöhe), Sonne über −6° → 0, Mondphase senkt die Grenzgröße.
Ort aus `/api/sites/resolve` (sonst 50° N, Länge aus der Zeitzone). Alle 15 s Poisson-Ziehung mit Rate HR/5; Strom-Meteore fliegen vom Radianten weg
(Blick Süd), Tempo/Länge/Farbe aus v. `info()` liefert den Text unter Darstellung. `localStorage af.ui.meteors`, aus bei reduzierter Bewegung und
ohne Stil. Test: `test_meteors.py` (Geminiden-/Perseidenmaximum, Tag, Apex morgens > abends, Radiant unter dem Horizont).
**Kreisel** (seit 0.46.2, Nutzerbefund: vier verschieden schnelle auf der Objektseite): alle über das Token `--spin` (`animation: var(--spin)`),
keine doppelten im Inhalt. Objektseite bei laufendem Auftrag: Kreisel nur in der Auftragsanzeige oben (`showJobPill`, bleibt immer – Nutzerwunsch)
und `.sl-pulse` in der Statuszeile; `.sl-orbit` ist ein ruhiger Platzhalter, „Läuft …“ ohne `.busy`. Test `test_spinners_on_running_object_page`.
**Rückmeldung** (seit 0.24.0, Nutzerwunsch „responsiver“, Ende von `style.css`, gilt für alle Stile): Drücken = `scale(.94–.985)` in 120 ms, nur
transform/opacity/Farben. `api()` merkt den zuletzt gedrückten Knopf (`lastPress`, ≤ 0,6 s) und setzt bei schreibenden Anfragen `.pending`
(gesperrt, Kreisel nach 150 ms über `::before/::after`) – `.btn` darf daher keine eigenen `::before/::after` bekommen. Aufwendige Animationen nur für echte Prozesse (`viz.js`, als „Schema“
gekennzeichnet, mit echten Zahlen aus Job/Engine), sonst 150–250 ms. AppShell: Grid `top / side main ctx`; Seite scrollt als Ganzes (Layout-Test braucht das),
Sidebar/Panel `sticky`. Breiten: ≥ 1280 Panel im Raster, darunter Schublade (`.shell.ctx-shown`); 861–1100 Sidebar nur Symbole; ≤ 860 Leiste unten + `#subnav`.
Navigation wird einmal gebaut (`buildNav`), `navState` setzt nur Klassen. Bilder-Objektseite: keine Schrittleiste im Kopf (Nutzerwunsch) – der Fortschritt steht in der Bildfläche (`live.js`); `pipeStep` bildet `jobs.stage` auf Schritte ab (für die Schema-Animation in den übrigen Reitern) (Kalibrierung und
Registrierung laufen innerhalb „Stacking“ und werden nicht getrennt gemeldet).
**Seitenwechsel** (`route`): neue Ansicht als `.page.pending` (unsichtbar) aufbauen, die alte bleibt als `.page-ghost` (fest positioniert,
IDs entfernt, nicht klickbar) stehen; fertig → Geist sofort weg + `.swap` (kurze Einblendung von 70 %); dauert es > `SKEL_AFTER` 350 ms,
Geist ausblenden und Skelett zeigen. Keine Kreuzblende (Text lag doppelt übereinander). Messen: Layout-Shift-Observer + Zwischenbilder.
Ladebalken `loadbar` (unter der Werkzeugleiste, erst nach `LOADBAR_AFTER` 150 ms). **Langsame Ansichten nicht als Ganzes abwarten**
(seit 0.21.1, Nutzerbefund „Übersicht ploppt auf einmal auf“): Gerüst sofort zurückgeben, Abschnitte einzeln füllen – Übersicht: `load()`
startet je Abschnitt eigene Anfragen (`paintCard` je Modul, `paintFeatured` je homeFeatured), Platzhalter `.skel-*` in fester Höhe, erste Füllung `.fill`.
Prüfen mit künstlich verzögerter API (Playwright async `page.route` + `asyncio.sleep` – synchron blockiert `time.sleep` alles).
Polling alle 2,5 s (`/api/status`), Ansichten zeichnen nur bei geänderter Signatur neu (Formulare/Zoom bleiben erhalten). `route()` verwirft Ansichten, die erst
nach einem weiteren Klick fertig werden (`routeTicket`), und gibt jeder Ansicht einen eigenen Behälter in `#view` (sonst schrieb eine
verworfene, noch ladende Ansicht über die aktuelle). Ansichten, die nach `await` global per `$('#…')` suchen, vorher `el.isConnected` prüfen. Bildbetrachter `createViewer(host, src, cmpSrc, labels, ph, {overlay})` mit Zoom/Pan/Pinch, Vergleich
und optionalem SVG-Overlay in Bildpixeln (Sage-Objektmarken). Achtung: in `createViewer` keine lokale Variable `esc` (TDZ-Fehler). Nach `opts.slowAfter` (Standard 20 s) ohne `load` bzw. bei `error` zeigt `.vload` einen Hinweis mit „Erneut versuchen“ (lädt mit `?neu=` neu; Test `test_viewer_slow.py`).
Globale Auftragsanzeige oben (`showJobPill` aus `/api/activity`, alle Module) + Toast beim Fertigwerden. Dateien überall ablegen: zentraler Bilder-Import für FITS und JPG/PNG/TIFF.
Fertige Bilder: `modules/stacking/picture_import.py`, Projektoption `input_type='picture'`, Original + Vorschauen als Ergebnis ohne Rohframes/Stack;
SHA256 gegen doppelte Uploads, eigenes Ereignis `stacking.imported` → Sage (kein SkyTrace). Keine erfundenen Belichtungs-/Frame- oder Qualitätswerte.
Stellina-Verlaufsbilder: EXIF Make/Model/ImageDescription/DateTime + `img-…-output` → `imported.series`, ein Projekt je Objekt/Gerät/Nacht mit geordneten Versionen.
`picture_import.attach_previews` ordnet diese bei passenden Rohdaten per `options.gallery_parent` zu (nach Upload und Scan); Hauptgalerie/Statistik/Sage-Auswahl zeigen nur eigenständige Projekte.
Begleitbilder bleiben über den Tab `teleskopbilder` erreichbar; beim Löschen des Elternprojekts wird die Zuordnung aufgehoben, keine Begleitdatei mitgelöscht.
Zusätzlicher Schutz (`picture_import.skipped_pictures`, Server + Frontend): > 10 FITS im Upload → nur FITS (auch kein TIFF, Nutzerwunsch 31.10.);
sonst > 10 TIFF/FITS → alle JPG/PNG übersprungen
(am 30.10.2026 landeten 233 Stellina-Vorschauen in der Galerie – der Server lief noch ohne die Verlaufsbild-Erkennung).
Sage zeigt einen Verweis zum zentralen Import; eigener Upload nur als Rückfall bei deaktiviertem Bilder-Modul. API `/sage/analyze` bleibt kompatibel.
Befehlspalette Strg+K: Einträge aller Module (`palette()`) + Aktionen der aktuellen Ansicht (`actions()`).
Tastenkürzel: `/` Suche in Listen, `1–4` Tabs (Objekt, SkyTrace-Lauf), `F` Vollbild, `B` Vorher/Nachher. Server schickt `Cache-Control: no-cache` für `/`.
**Nach Änderungen an `index.html`/`app.js`/`style.css` `?v=` in `index.html` hochzählen**; Modul-Dateien bekommen die Build-Kennung automatisch.
**Diagramm-Motive** (`modules/stacking/static/chartdeco.js` + `chartdeco.css`, Nutzerwunsch 09.11.2026 „visuell gleich verstehen, nicht zu viel Text“):
`AFdeco.draw('sky'|'seeing'|'depth', {L,T,PW,PH,W,R,Y,lo,hi,sharpTop})` zeichnet einen dezenten stilisierten Hintergrund (Himmel dunkel→Dunst/Mond,
scharfer/aufgeblähter Stern, schwache/helle Sterne) mit Randsymbolen im rechten Rand (`AFdeco.R`); `AFdeco.legend([{cls,r,text,title}])` = einzeilige
Legende mit echten Diagrammpunkten, Erklärung im Tooltip. Je Grafik höchstens ein kurzer Satz (was oben/unten bedeutet). Genutzt von Dashboard
(`scatter(…, {deco})`), Himmelsqualität (`nightsChart`, `curveSvg`), Aufnahmen-Diagramm (`chart` in `stacking.js`); HR-Diagramm hat ein eigenes Farbband aus `starColor`. Bewusst ohne Motiv:
Heute Nacht (zeigt Dämmerung und Mond schon), Gerätediagnose, Balken und Listen.
**Aufnahmen-Diagramm** „Qualität je Aufnahme“ (`stacking.js` → `chart(frames, breite)`, Stil `frames.css` `.fq*`): zwei Spuren übereinander, beide
oben = besser (Himmel bzw. Sterne, Schärfe), in echter Pixelbreite gezeichnet (ResizeObserver, kein `preserveAspectRatio="none"`). Linie nur durch
verwendete Aufnahmen (ab ~720 als gleitender Median, Einzelwerte blass), verworfene als rote Striche unter der unteren Spur (Nutzerwunsch: keine Punkte), Skala 1.–99. Perzentil der verwendeten mit
Mindestspanne 30 % des Medians; Nächte als abwechselnde Bänder, Datum ausgedünnt (Tooltip mit Anzahl); Stand der Nacht ohne Beschriftung
als Verlauf je Nachtband nach Sonnenhöhe (`sun` vom Server, `sites.sun_alt`; Dämmerung hell-bläulich `--data-2`, Nacht ohne Tönung), Uhrzeit nur im Tooltip; Klick auf eine Aufnahme → `framePopup`.
