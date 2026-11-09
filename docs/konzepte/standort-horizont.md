# Konzept: Standort & Horizont

> **Status:** Konzept vom 10.11.2026, noch keine Umsetzung. Entscheidungsfragen für den Nutzer am Ende (Abschnitt 7).
> Grundlage: Code-Stand `main` v0.54.0.

**Nutzerwunsch (10.11.2026, wörtlich):** „Anhand von Geländedaten und den spezifischen Teleskopdaten (erstmal vaonis und
seestar modelle) entweder anhand des selbst ausgewählten Ortes optimale Belichtungsobjekte wählen (wie jetzt auch bloß in
berücksichtung von Sichtbarkeit anhand des geländes und umliegenden häußern etc.) sowie die automatische Suche perfekter
Standorte anhand kriterien wie erreichbarkeit und entfernung, in einem neuen passenden Reiter“

Zwei Funktionen in einem neuen Reiter **„Standort & Horizont“**:

- **A – Objektwahl mit echtem Horizont:** Für einen Standort den lokalen Horizont aus Gelände und Nahbereich (Häuser, Bäume)
  bestimmen; „Heute Nacht“ zählt nur Zeiten, in denen ein Objekt über diesem Horizont *und* über der Mindesthöhe des Geräts steht.
- **B – Standortsuche:** Im Umkreis Orte vorschlagen, die dunkel, horizontfrei, erreichbar und nicht zu weit weg sind.

**Kernaussage vorab:** A lässt sich vollständig offline und ohne Datenschutzabstrich bauen; die Frage ist nur, *wie* die Geländedaten
auf den Rechner kommen (Abschnitt 2). Für flaches Land bringt der Nahbereich (Haus, Baumreihe) viel mehr als das Gelände –
ein 10 m hohes Dach in 30 m Abstand verdeckt alles unter ~17°, genau den Bereich 20–40°, in dem Smart-Teleskope oft arbeiten.
B ist deutlich größer (Lichtatlas, Wegenetz, Bewertung) und sollte erst nach A kommen.

---

## 0. Ausgangslage im Code

| Baustein | Stand | Bedeutung für das Konzept |
|---|---|---|
| `stacking/tonight.py` | Höhe analytisch aus Ortssternzeit, feste Mindesthöhe `MIN_ALT = 30°`, Zenitabschlag > 80° bei Alt-Az, Mond, Wolken, Saison, Nachtplan | Horizontfilter = eine zusätzliche Maske `alt ≥ h(az)` – Azimut muss dazu analytisch mitgerechnet werden (bisher nur Höhe) |
| `stacking/deepsky.py` | Messier/Caldwell/ASTAP, Filter nach `MAG_LIMITS` je Gerät und Größe zum Bildfeld | Geräteprofile erweitern (Abschnitt 4) |
| `stacking/tutor.DEVICES` | Stellina, Vespera, Seestar, Dwarf, eVscope, Kamera: Öffnung, Brennweite, `alt_az`, Böen; Bildfeld nur aus Plate-Solving bzw. Stellina-Rückfall | Bildfeld/Sensor je Modell fehlt; Vespera II/Pro und Seestar S30/S50 nur als Namenszusatz |
| `core/sites.py` | Ortsliste mit `lat, lon, alt_m, accuracy_m`, Zeiträume, nur Standardbibliothek | Horizont je Ort **nicht** in `sites.py` rechnen (numpy), nur Verweis/ID; Ablage getrennt |
| `server.py` `/api/sites/elevation` | **sendet die genauen Koordinaten (5 Nachkommastellen ≈ 1 m) an Open-Meteo** (auf Knopfdruck) | Befund: weicht von „Standort verlässt den Rechner nie“ ab; mit lokalem Geländemodell ersetzbar (Frage 8) |
| `tonight._forecast` | Wetter mit auf 0,1° (~10 km) gerundetem Ort | Vorbild für „grob gerundet“ |
| SkyTrace `skytrace.js` | Leaflet (`static/vendor/leaflet.js`), OSM-/OpenTopoMap-Kacheln **auf Knopfdruck** | wiederverwendbar für die Karte in B; Kacheln verraten den Kartenausschnitt (Abschnitt 2) |
| SkyTrace `skytrace-erde.js` | three.js-Globus mit Heatmaps | für B nicht nötig; eher später für eine 3D-Geländeansicht |
| `skyquality.py` | gemessene Himmelshelligkeit (mag/″²), Lichtrichtung je Ort | eigene Messungen eichen den Lichtatlas in B und bestätigen Horizontsektoren mit Lichtglocken |

---

## 1. Datenquellen

Lizenzangaben nach Stand der Recherche vom 10.11.2026 – **vor dem Einbau jeweils an der Originalquelle prüfen und in `NOTICE` eintragen**.
Größen bei Copernicus selbst gemessen (HEAD-Anfrage auf öffentliche Beispielkacheln, kein Download): 30-m-Kachel 1°×1° = 31–42 MB,
90-m-Kachel = 3,7–5,2 MB (Frankfurt-, Alpen-, Hamburg-Kachel).

### 1.1 Gelände (DEM)

| Quelle | Auflösung | Lizenz | Größe (Beispiel) | Offline | Bemerkung |
|---|---|---|---|---|---|
| **Copernicus DEM GLO-90** | 90 m (3″) | Copernicus-DEM-Lizenz: frei, auch kommerziell, Quellenangabe Pflicht | ~4–5 MB je 1°-Kachel; Deutschland (~65 Kacheln) ≈ **0,3 GB** | ja (Kacheln als COG, AWS-Bucket ohne Konto) | weltweit, für Fernhorizont (> 2 km) völlig ausreichend; **Oberflächenmodell (DSM)** inkl. Wald/Bebauung (Radar) |
| **Copernicus DEM GLO-30** | 30 m (1″) | wie GLO-90 | 31–42 MB je Kachel; Deutschland ≈ **2–2,5 GB** | ja | wenige Länder-Kacheln nicht öffentlich (Liste beim Anbieter); Mittelbereich 200 m–2 km besser |
| SRTM / NASADEM | 30 m | gemeinfrei (US-Behörde) | ähnlich GLO-30 | ja, aber Bezug teils mit Earthdata-Konto | älter (2000), nur bis 60° N; kein Vorteil gegenüber GLO-30 |
| EU-DEM v1.1 | 25 m | Copernicus Land, frei mit Quellenangabe | Europa in großen Blöcken (GB) | ja | wird nicht mehr gepflegt; GLO-30 ist Nachfolger |
| **DGM1/DOM1 der Bundesländer** | 1 m (Gelände bzw. Oberfläche) | je Land verschieden: z. B. Berlin `dl-zero-de/2.0`, viele Länder `dl-de/by-2.0` oder CC BY 4.0, **nicht alle frei** (z. B. M-V Einzelplatzlizenz laut Katalog) | sehr groß (1 km² ≈ 1–4 MB; Landkreis GB) | ja | ideal für den Nahbereich: **nDSM = DOM − DGM** liefert Haus- und Baumhöhen direkt; nur DE, Bezug je Land unterschiedlich → Import einer vom Nutzer selbst geladenen Datei statt eigenem Downloader |

**Empfehlung:** GLO-90 als Standard (klein, weltweit, gleiche Lizenz), GLO-30 als Option „genauer“. Landesdaten nur als
**Import einer Datei**, die der Nutzer selbst lädt (kein Abruf mit Ort durch AstroFlow, keine Lizenzpflege je Land in der App).

### 1.2 Nahbereich: Gebäude und Vegetation

| Quelle | Inhalt | Lizenz | Bemerkung |
|---|---|---|---|
| **OpenStreetMap** `building=*` | Grundrisse, teils `height`/`building:levels` | ODbL 1.0 (Quellenangabe „© OpenStreetMap-Mitwirkende“; Horizont = „Produced Work“) | Grundrisse in DE fast vollständig, Höhen lückenhaft → Rückfall Geschosse × 3 m bzw. 8 m pauschal |
| OSM `natural=tree`, `natural=tree_row`, `landuse=forest`/`natural=wood` | Einzelbäume, Baumreihen, Waldflächen ohne Höhe | ODbL | Höhe pauschal (Einzelbaum 12 m, Wald 20 m) oder aus Kronenhöhenkarte |
| GlobalBuildingAtlas (TUM, 2025) | weltweit Gebäude mit geschätzter Höhe, LoD1 | Polygone teils ODbL, **Höhen/LoD1 CC BY-NC 4.0** | Höhenfehler 1,5–8,9 m; NC verträgt sich mit PolyForm NC, aber nur als getrennte Komponente |
| ETH Global Canopy Height 10 m (2020) | Kronenhöhe | CC BY 4.0 | weltweit, 10 m – gut für Waldränder |
| Meta/WRI Canopy Height 1 m (v2 auf AWS) | Kronenhöhe 1 m | CC BY 4.0 | sehr fein, groß; Bilddaten-Grundlage © Maxar (Datenlizenz trotzdem CC BY) |
| Copernicus HRL Tree Cover Density 10 m | Bedeckungsgrad, keine Höhe | Copernicus Land, frei | nur ob Baum, nicht wie hoch |
| nDSM aus DOM1 − DGM1 (Länder) | Höhe aller Objekte 1 m | je Land (s. o.) | genaueste Quelle, wenn vorhanden |

**Empfehlung:** Nahbereich zuerst **ohne** Fremddaten (manuell, aus eigenen Aufnahmen – Abschnitt 2, Variante c/e),
danach OSM-Gebäude/Bäume aus dem Regionalpaket, Kronenhöhe ETH 10 m als Option; Landes-nDSM als Datei-Import.

### 1.3 Lichtverschmutzung (für B)

| Quelle | Inhalt | Lizenz | Bezug | Bemerkung |
|---|---|---|---|---|
| **World Atlas of Artificial Night Sky Brightness** (Falchi et al. 2016, GFZ) | künstliche Zenithelligkeit (mcd/m²), 30″ ≈ 1 km | **CC BY-NC 4.0** (seit 13.11.2019) | GeoTIFF, Bezug über Anfrageformular | Stand 2015 (vor LED-Umstellung), Modell statt Messung; NC → nur als getrennte Komponente, nicht unter PolyForm umlizenzierbar |
| VIIRS VNL v2.x (Earth Observation Group, Colorado School of Mines) | Jahreskomposit Abstrahlung nach oben, ~500 m | laut Katalogen gemeinfrei/„not subject to copyright“, Readme beim Anbieter prüfen | Download mit (kostenlosem) Konto | misst Abstrahlung, nicht Himmelshelligkeit → braucht eigenes Ausbreitungsmodell (Glocke) |
| NASA Black Marble VNP46A4 | Jahreswerte, ~500 m | NASA, offen | Earthdata-Konto | wie VIIRS |
| lightpollutionmap.info u. ä. | Darstellungen der obigen | Nutzungsbedingungen der Seite | — | nicht als Datenquelle verwenden |

**Empfehlung:** Ausschnitt (Europa) des World Atlas als eigene Komponente mit Lizenzdatei, einmal von uns aufbereitet
(16 bit, komprimiert, Größe schätzungsweise einige 10 MB – vor Umsetzung messen), mit SHA-256 im Code. Eigene Messungen aus
`skyquality` (mag/″² je Ort) **überschreiben bzw. eichen** den Atlas in der Umgebung bekannter Orte (Versatz Atlas ↔ Messung).
Rechtlich offen: ob die CC-BY-NC-Datei über unser GitHub-Release weitergegeben werden darf (BY-NC erlaubt Weitergabe mit Quelle,
Anfrageformular beim GFZ deutet auf Wunsch nach Registrierung) → vorher beim GFZ/ISTIL nachfragen (Frage 4).

### 1.4 Wege, Parkplätze, Zugang, Routing (für B)

| Quelle | Inhalt | Lizenz | Größe | Bemerkung |
|---|---|---|---|---|
| **OSM-Auszüge (Geofabrik, `.osm.pbf`)** | Straßen, Feldwege (`highway=track`, `tracktype`), Parkplätze (`amenity=parking`), Zugang (`access`, `motor_vehicle`), Schutzgebiete, Siedlungen | ODbL | Bundesland 0,2–0,8 GB, Deutschland ~4 GB (Größenordnung, beim Bezug prüfen) | Parser `pyosmium` (BSD-2) als neue Abhängigkeit, oder von uns vorgefilterte Pakete (nur benötigte Tags, ~ 1/10 der Größe) |
| Overpass-API | gleiche Daten, Abfrage je Bereich | ODbL | klein | **verrät den Suchbereich** → nur als Variante d |
| Routing **offline** | eigener Graph aus OSM: Dijkstra **einmal vom Startort zu allen Kandidaten** (`scipy.sparse.csgraph.dijkstra`, schon vorhanden) | — | Graph Umkreis 50 km: einige 100 000 Kanten, Sekunden | Fahrzeit grob über Straßenklasse (Autobahn 110, Landstraße 70, Ort 40, Feldweg 15 km/h); reicht für Rangfolge |
| OSRM/GraphHopper/Valhalla | vollwertige Router | BSD/Apache/MIT | Vorverarbeitung GB-RAM, eigener Dienst | für AstroFlow überdimensioniert |
| Online-Router (OSRM-Demo, ORS) | — | Nutzungsbedingungen | — | sendet Start = Wohnort → **ausgeschlossen** |

**Vereinbarkeit:** Alle empfohlenen Quellen (Copernicus, OSM, ETH, CC BY) sind mit PolyForm NC vereinbar, solange die Daten
als **getrennte Dateien mit eigener Lizenz** bleiben (wie heute `messier_caldwell.csv`). CC-BY-NC-Daten (World Atlas, GBA-Höhen)
sind für den nicht-kommerziellen Gebrauch ebenfalls vereinbar, würden aber eine spätere kommerzielle Lizenzierung von
AstroFlow blockieren, solange sie mitgeliefert werden. Quellenangaben müssen in der UI sichtbar sein (Fußzeile Horizontdiagramm/Karte).

---

## 2. Datenschutz-Varianten (Kernentscheidung)

Grundsatz bleibt: **Kein Abruf enthält die genauen Koordinaten eines Standorts.** Zum Vergleich: Jeder Abruf verrät ohnehin die
IP-Adresse, aus der Anbieter meist Stadt/Region ableiten können. Was darüber hinaus verraten wird, ist der eigentliche Maßstab.

| Var. | Ablauf | Was verlässt den Rechner | Größe | Genauigkeit | Aufwand |
|---|---|---|---|---|---|
| **a – Regionalpaket** | Nutzer wählt einmalig ein Land/Bundesland („Deutschland“); AstroFlow lädt alle Kacheln (+ optional OSM-Auszug) wie eine Komponente | nur „interessiert sich für Deutschland“ | GLO-90 DE ≈ 0,3 GB, GLO-30 DE ≈ 2–2,5 GB, OSM-Auszug Bundesland 0,2–0,8 GB | voll (je nach Paket) | mittel: Paketliste je Region mit SHA-256 (Manifest im Repo erzeugen), Fortschritt, Speicherplatz |
| **b – grobe Kachel** | nur die 1°-Kachel(n) um den Ort; für Fernhorizont bis 100 km 3×3 Kacheln | Bereich ~110 × 70 km je Kachel, bei 3×3 ~330 × 210 km | 9 × 4,5 MB (GLO-90) bzw. 9 × 35 MB (GLO-30) | voll | gering |
| b′ – Bereichsabruf im COG | nur die benötigten Byte-Bereiche einer Kachel (HTTP-Range) | **interne Kachel ≈ 30 km** – deutlich genauer als b | sehr klein | voll | gering – **nicht empfohlen** |
| **c – manuell** | Horizont im Diagramm ziehen/zeichnen (Sektoren), Werte mit Handy-Kompass/Neigungsmesser ablesen; Import `.hrz` (N.I.N.A./APCC) bzw. Stellarium-Horizontliste | nichts | 0 | Nahbereich gut, Fernbereich grob | gering |
| c′ – Handy-Sensoren | gekoppeltes Handy im Browser schwenken (DeviceOrientation) | nichts | 0 | ±2–5° (Kompass-Störung!) | mittel; **Haken:** Sensor-API braucht sicheren Kontext (HTTPS) – unser LAN-Server läuft über http → nur mit lokalem Zertifikat |
| c″ – Panoramafoto | Foto/Panorama hochladen, zwei Punkte mit bekanntem Azimut markieren (oder Plate-Solving eines Nachtpanoramas mit Sternen), Horizontlinie halbautomatisch | nichts | 0 | gut | hoch (Bildverarbeitung) |
| **d – Zustimmung je Abruf** | genaue Abfrage bei Fremddienst (HeyWhatsThat, Overpass mit kleinem Ausschnitt, Open-Meteo-Höhe) nach Dialog „Dieser Abruf sendet deinen Ort an …“ | genauer Ort | klein | voll | gering – widerspricht der Regel, nur als bewusste Ausnahme |
| **e – aus eigenen Aufnahmen lernen** | aus Plate-Solving + Zeit jeder Aufnahme ist Höhe/Azimut bekannt; fehlgeschlagene Lösungen, plötzlicher Sternverlust, helle Ränder bei bestimmtem Azimut ⇒ Hindernis. Ebenso SkyTrace-Läufe | nichts | 0 | nur dort, wo das Teleskop hingezeigt hat; wird mit jeder Nacht besser | mittel (Daten liegen schon in `frames.info.solve` / Metriken) |

**Empfehlung:** **a mit GLO-90 als Standard** (0,3 GB für ein Land ist vertretbar und datenschutzrechtlich sauber),
GLO-30 als optionales Zusatzpaket; Nahbereich über **c** (sofort, MVP) und **e** (wächst mit), OSM-Gebäude aus dem Regionalpaket
später. **b** als „kleines Paket“ für Nutzer mit wenig Platz, mit klarer Anzeige des verratenen Bereichs. **b′ nie.** **d** nur dort,
wo es heute schon geschieht – und `/api/sites/elevation` auf das lokale DEM umstellen, sobald ein Paket da ist (Frage 8).
Herunterladen bleibt ein Knopfdruck (keine automatischen Abrufe) wie bei allen Komponenten.

---

## 3. Rechenweg

### 3.1 Horizont aus dem Gelände (A)

1. **Standpunkt:** Geländehöhe `z0` am Ort (bilinear aus dem DEM; ersetzt `alt_m` nur, wenn der Nutzer nichts eingetragen hat)
   + **Aufbauhöhe der Optik** `h_opt` (Geräteprofil, Standard 1,0 m, im Standort änderbar). Bei GLO-30/90 als DSM liegt der
   Punkt im Wald/Ort evtl. auf Dachhöhe → Hinweis, Wert prüfen.
2. **Strahlen:** 720 Azimute (0,5°), je Strahl Abtastung von 30 m bis 50 km (Alpen/Mittelgebirge bis 150 km), Schrittweite
   wachsend `max(Auflösung/2, d · 0,005)` → ~400 Punkte je Strahl, ~300 000 Abfragen, mit numpy einige 10 ms.
3. **Höhenwinkel je Punkt:** `θ(d) = atan((z(d) − z0 − h_opt − d²/(2·R_eff)) / d)` mit `R_eff = R / (1 − k)`,
   `k ≈ 0,13` (terrestrische Refraktion). Erdkrümmung: bei 50 km 196 m (mit Refraktion 171 m), bei 100 km 785 m (683 m) –
   ferne Berge verschwinden also merklich. Horizont `H(az) = max_d θ(d)`, dazu `D(az)` = Entfernung des höchsten Punkts
   (für Texte wie „Bergkamm in 23 km“).
4. **Nahbereich (≤ 500 m):** Gebäude/Bäume als Prismen (Grundriss + Höhe) bzw. nDSM-Raster; Schnitt jedes Strahls mit den
   Polygonen, Oberkante `θ = atan((z_Boden + h_Objekt − z0 − h_opt) / d)`. Beispiele: Haus 10 m in 30 m ≈ 16–18°, Baumreihe 20 m
   in 100 m ≈ 11°, Wald 25 m in 300 m ≈ 4,5°. Der Nahbereich bestimmt im Flachland den Horizont fast allein.
5. **Unsicherheit:** `accuracy_m` des Standorts (Standard 100 m) – im Nahbereich ist ein Haus in 30 m bei 100 m Ortsunsicherheit
   reiner Zufall. Daher: Nahbereich aus Rasterdaten erst ab `accuracy_m ≤ 20 m`, sonst nur manuell/gelernt. Fernhorizont ist
   robust.
6. **Astronomische Refraktion** hebt Objekte am Horizont scheinbar an (~0,5° bei 0°, ~0,1° bei 10°): bei Vergleich Objekt ↔ Horizont
   mit Bennett-Formel berücksichtigen (für Bildaufnahmen unter 15° ohnehin unwichtig).
7. **Zusammensetzen:** `H_eff(az) = max(H_Gelände, H_Nah, H_manuell)`; manuelle Sektoren dürfen auch **ersetzen** (Schalter je Sektor,
   z. B. „Baum ist gefällt“). Gelernte Hindernisse (Variante e) erscheinen zuerst als Vorschlag, nicht automatisch.

### 3.2 Speicherung je Standort

- Datei `data/horizons/<site_id>.json` (nicht in `settings.json`, nicht in `sites.py`):
  `{version: HORIZON_VERSION, step: 0.5, alt: [720], dist: [720], sources: {terrain: "glo90", near: "osm|manual|ndsm"},
  manual: [{az0, az1, alt, mode: "max|replace", note}], learned: [...], params: {h_opt, k}, created, data_hash}`.
  ~15 kB je Ort. Fällt ein Ort weg, wird die Datei gelöscht. Neu berechnen, wenn Ort/Höhe/Paket/`HORIZON_VERSION` sich ändern.
- Rechnen in einem neuen Kernmodul `core/horizon.py` (numpy erlaubt, weil nur der Server es nutzt; `sites.py` bleibt Standardbibliothek)
  oder als Dienst `horizon_for(site_id) → (az, alt)` in `ctx.services` – **fehlt der Dienst, rechnet „Heute Nacht“ wie bisher**.
- Pakete unter `data/gelaende/<quelle>/<kachel>.tif` (Datenordner, nicht Programmordner), Liste in `settings.json` → `terrain`.
  Lesen der COGs: `tifffile` (BSD) oder `rasterio` (BSD, schwer unter Windows) – Empfehlung: `tifffile` + eigener Kachel-Lesezugriff.

### 3.3 Anbindung an `tonight.py`

- In `plan()` zusätzlich **Azimut** analytisch aus Stundenwinkel/Deklination/Breite (wie heute die Höhe, Matrix Objekte × Zeit),
  dann `visible = alt ≥ max(min_alt_gerät, H_eff(az) + Rand)` statt `alt ≥ MIN_ALT`. `Rand` = 2° (Seeing/Dunst am Hindernis, Feldgröße/2).
- Gilt für `eff_hours`, Saison (`_season`), Kulmination und Nachtplan (`_night_plan`) gleichermaßen; Cache-Schlüssel um `data_hash` des
  Horizonts erweitern.
- **Gründe** im Klartext: „Ab 23:40 hinter dem Gelände im Südwesten (Kamm in 12 km)“, „Bis 21:10 durch das Haus im Osten verdeckt“,
  „Steht heute nie über deinem Horizont“ (dann nicht empfehlen). Neu: Ziele, die *nur* wegen des Horizonts ausfallen, erscheinen nicht
  (anders als Wolken, wo „bei klarem Himmel“ angezeigt wird).
- **Mindesthöhe:** `MIN_ALT = 30°` bleibt Qualitätsschwelle (Luftmasse 2); der Horizont kommt *zusätzlich* dazu. Optional je Gerät/Standort
  absenkbar, wenn der Horizont frei ist (Frage 3).
- **Feldrotation je Gerät** statt pauschal 80°: Drehrate `ω = 15,04″/s · cos φ · cos A / cos h`; Verschmierung in der Bildecke
  = `ω · t_sub · r_Ecke` (Pixel). Grenze `h_rot` je Gerät aus Belichtung je Einzelbild und Sensorhalbdiagonale (z. B. 1 px) →
  ersetzt `ZENITH_ALT` gerätebezogen. Achtung: Smart-Teleskope drehen zwischen Aufnahmen per Software zurück – entscheidend ist nur die
  Verschmierung *innerhalb* einer Aufnahme und das Zenitloch der Montierung.
- Neuer Versionszähler nicht nötig (Heute Nacht cacht nur 10 min).

### 3.4 Darstellung

- **Panoramastreifen** (SVG, 0–360° Azimut × 0–90° Höhe, Himmelsrichtungen beschriftet): Horizontlinie gefüllt (Gelände braun, Nahbereich
  dunkler, manuell gestrichelt), Bahnen der Empfehlungen von heute Nacht mit Uhrzeiten, Mond, Dämmerungsfarben im Hintergrund, Lichtrichtung
  aus `skyquality` als Pfeil. Ziehen an der Linie = manuelle Korrektur. Daneben klein ein **Polardiagramm** (Zenit in der Mitte), wie man es von
  Planetariumsprogrammen kennt.
- **Karte** (Leaflet aus `static/vendor/`, wie SkyTrace): Sichtstrahlen-Fächer, Hindernisse, bei B die Kandidaten. **Offline-Grundkarte** aus
  den eigenen Paketen zeichnen (Schummerung aus dem DEM + Wege aus dem OSM-Auszug als Canvas-Ebene); OSM-/OpenTopoMap-Kacheln nur auf Knopfdruck
  mit Hinweis, dass der Kachelserver den Kartenausschnitt sieht (Frage 7). Die 3D-Erde aus SkyTrace wird nicht gebraucht.
- In „Heute Nacht“: kleine Horizontleiste je Empfehlung („frei 21:30–01:10“) und Link in den Reiter.
- Reiter: `AF.register('horizont', {nav: [{area: 'work', …}]})` als eigener Hauptpunkt „Standort & Horizont“; Ansicht in `ROUTES` von
  `tests/test_ui_layout.py` aufnehmen. Wahrscheinlich als Teil des Bilder-Moduls (nutzt `tonight`, `deepsky`) oder Kern (Standorte) –
  Empfehlung Kern (`core/horizon.py` + Shell-Ansicht), weil auch SkyTrace (Satelliten tief am Horizont) profitieren kann.

### 3.5 Standortsuche (B)

1. **Eingaben:** Startort (aus der Ortsliste, z. B. „Zuhause“), Umkreis (10–100 km bzw. max. Fahrzeit), Gerät, Gewichte (Dunkelheit, Horizont,
   Erreichbarkeit, Entfernung), max. Fußweg vom Parkplatz (Standard 300 m – Stellina ~ 11 kg samt Stativ), optional Himmelsrichtung/Zielobjekte
   („Südhorizont frei für Sagittarius“, „M31 im Herbst“).
2. **Kandidaten:** Gitter 250–500 m im Umkreis (50 km → ~10 000–40 000 Zellen). Ausschluss: Wasser, Wald (innen), Siedlung, Hangneigung > 10 %
   (aus DEM), `access=private|no`, Militär. **Nur Hinweis** (kein Ausschluss): Naturschutzgebiet (nächtliches Betreten/Fahrverbote – Rechtslage je
   Land), Feldweg mit Fahrverbot, Abstand zu Wohnhäusern.
3. **Erreichbarkeit:** nächster Parkplatz/befahrbarer Weg im OSM-Graph, Fußweg-Luftlinie dorthin; Fahrzeit vom Startort per **eine** Dijkstra-
   Rechnung über den Straßengraphen (alle Kandidaten auf einmal). MVP ohne OSM: Luftlinie × 1,3 / 60 km/h.
4. **Bewertung je Kandidat:** Dunkelheit (Atlas, geeicht mit eigenen Messungen; Richtung großer Lichtglocken relativ zur Wunschrichtung),
   Horizontfreiheit (grober Horizont mit GLO-90, 180 Strahlen, nur für die nach Schritt 2–3 übrig gebliebenen, z. B. 2 000 Orte: Anteil Himmel über
   20°, Gewicht Süd höher; oder: Stunden, die die Wunschobjekte heute/zu Saison über dem Horizont stehen), Höhenlage über Talnebel (relative Höhe zur
   Umgebung, Kuppenlage), Fahrzeit, Fußweg.
5. **Ergebnis:** Pareto-Liste (Top 10) mit Begründungen („21,3 mag/″² geschätzt · Süd frei bis 3° · 35 min Fahrt · 120 m vom Parkplatz“),
   Karte, „Als Standort übernehmen“ → `SiteBook` (mit `accuracy_m` 30 m) und Horizont sofort in voller Auflösung rechnen.
6. **Rechenzeit:** Vorfilter + grober Horizont ~2 000 × 180 × 150 Abfragen ≈ 5·10⁷ → mit numpy wenige Sekunden bis ~30 s; im Hintergrund-Thread mit
   Fortschritt in `activity()`.
7. **Hinweise ohne Gewähr:** Betretungsrecht, Eigentum, Sicherheit nachts – klarer Text, dass AstroFlow nur Daten zeigt.

---

## 4. Teleskopprofile

Heute in `tutor.DEVICES`: Öffnung, Brennweite, `alt_az`, Böengrenze, Tipps; `deepsky.MAG_LIMITS` je Familie; Bildfeld nur aus dem
Plate-Solving der letzten Aufnahme oder Stellina-Rückfall 1° × 0,7°. **Es fehlt:** Modellvarianten als eigene Profile, Sensor/Bildfeld je
Modell, Belichtung je Einzelbild, Montierungsgrenzen, Aufbauhöhe.

| Modell | Öffnung / Brennweite | Sensor (Pixel) | Bildfeld (gerechnet) | Quelle / Status |
|---|---|---|---|---|
| Vaonis Stellina | 80 / 400 mm, f/5 | Sony IMX178, 3072 × 2080, 2,4 µm | 1,06° × 0,71° | `tutor.DEVICES`, `SHAPES`; deckt sich mit Bildformat in echten Daten |
| Vaonis Vespera (I) | 50 / 200 mm, f/4 | Sony IMX462, 1920 × 1080, 2,9 µm | 1,6° × 0,9° | Herstellerangabe lt. Händlervergleichen; **an Vaonis-Datenblatt prüfen** |
| Vaonis Vespera II | 50 / 250 mm, f/5 | Sony IMX585, 3840 × 2160, 2,9 µm | 2,55° × 1,43° (Hersteller 2,5° × 1,4°) | skiesandscopes.com-Vergleich; **prüfen** |
| Vaonis Vespera Pro | 50 / 250 mm, f/5 | Sony IMX676, 3536 × 3536, 2,0 µm | 1,62° × 1,62° | skiesandscopes.com; neuere Quelle nennt „Vespera Pro II“ mit 245 mm f/4,9 → **Modellstand klären** |
| ZWO Seestar S50 | 50 / 250 mm, f/5 | Sony IMX462, 1920 × 1080 (hochkant), 2,9 µm | 1,28° × 0,72° | Herstellerangabe ~1,3° × 0,7°, ~2,4″/px |
| ZWO Seestar S30 | 30 / 150 mm, f/5 | Sony IMX662, 1920 × 1080 (hochkant), 2,9 µm | 2,13° × 1,20° | Optik übereinstimmend; Bildfeldangaben in Tests widersprüchlich (2,46° bzw. 2,59°) → **prüfen** |
| Seestar S30 Pro / S50 Pro, Vespera Pro II | — | — | — | neue Modelle (160/260 mm laut Tests); erst nach verifizierten Daten aufnehmen |

Bildfeld = Sensorkante / Brennweite (eigene Rechnung aus Pixelzahl × Pixelgröße). Weiter benötigte Profilfelder, **noch ohne gesicherte Quelle**
(aus Handbüchern/Apps ermitteln oder als Nutzerwert mit vernünftigem Standard):

- `min_alt` – tiefste anfahrbare/sinnvolle Höhe der Montierung (Standard 15°, Nutzer kann ändern);
- `zenith_hole` – Höhe, ab der die Alt-Az-Montierung nicht mehr folgt (Standard 80°, bisher `ZENITH_ALT`); Seestar im **EQ-Modus** (Polhöhenwiege)
  ohne Feldrotation/Zenitloch → `alt_az` je Standort/Abend umschaltbar;
- `sub_exposure` – Belichtung je Einzelbild (Smart-Teleskope typisch 10 s, Seestar wählbar 10/20/30 s) für die Feldrotationsgrenze;
- `optics_height_m` – Höhe der Optik über Grund auf dem Herstellerstativ (Standard 1,0 m; Nutzer misst einmal);
- `mosaic` – Mosaikmodus vorhanden (Vespera, Seestar) → große Objekte nicht verwerfen, sondern „als Mosaik“ markieren;
- `mag_limit` – bisher Schätzwerte je Familie; Vespera II/Pro vermutlich tiefer als Vespera I (später aus eigenen Messungen der Grenzgröße).

Geräteerkennung `detect_device` unterscheidet heute nur Familien; die Varianten über `INSTRUME` und Bildformat (`SHAPES`) zuordnen
(1920 × 1080 → Seestar/Vespera I, 3840 × 2160 → Vespera II, 3536 × 3536 → Vespera Pro) und im Reiter manuell wählbar machen
(„Heute nehme ich mit: …“), weil man für einen *neuen* Standort oft ein anderes Gerät plant als das der letzten Aufnahme.

---

## 5. Umfang, Ausbaustufen, Aufwand

Aufwand in Sitzungen (je ~1 Arbeitstag einer Agenten-Sitzung inkl. Tests), grob.

| Stufe | Inhalt | Aufwand | Version |
|---|---|---|---|
| **1 – MVP Horizont** | Reiter „Standort & Horizont“; Geräteprofile (6 Modelle, Abschnitt 4) + Gerätewahl; Horizont **manuell** (Sektoren im Panoramastreifen) + Import/Export `.hrz`/Stellarium; `tonight` mit Azimut + Horizontmaske + Gründen; Panoramastreifen mit Bahnen | 2 | 0.55 |
| **2 – Gelände** | Regionalpaket GLO-90 (Manifest + SHA-256, Komponente „Geländedaten“), optional GLO-30; Raycast mit Erdkrümmung/Refraktion; `elevation` lokal statt Open-Meteo; Polardiagramm; Karte mit Sichtfächer | 2 | 0.56 |
| **3 – Nahbereich** | Hindernisse aus eigenen Aufnahmen lernen (Variante e, Vorschläge); OSM-Gebäude/Bäume aus vorgefiltertem Paket; Kronenhöhe ETH 10 m optional; Import Landes-nDSM | 2–3 | 0.57 |
| **4 – Standortsuche** | Lichtatlas-Komponente + Eichung mit eigenen Messungen; OSM-Wege/Parkplätze/Zugang; Offline-Routing (Dijkstra); Bewertung, Pareto-Liste, Karte; „als Standort übernehmen“ | 3–4 | 0.58+ |

**MVP-Begründung:** Stufe 1 bringt ohne einen einzigen Fremddatensatz den größten Nutzen (Häuser/Bäume am eigenen Garten sind das häufigste
Problem) und schafft die Schnittstelle, an die Stufen 2–3 nur weitere Quellen anhängen.

**Risiken**

- **Speicher/Download:** GLO-30 Deutschland 2–2,5 GB, OSM-Auszüge hunderte MB → immer optional, Platzanzeige, Löschen-Knopf (vgl. „volles Laufwerk“).
- **Neue Abhängigkeiten:** `tifffile` (klein), `pyosmium` (Windows-Wheels prüfen; Alternative: Pakete von uns vorgefiltert als kompaktes Format liefern → kein
  Parser beim Nutzer, aber Pflege der Pakete bei uns, ODbL-Weitergabe mit Quellenangabe).
- **Genauigkeit:** GLO-30/90 ist ein Oberflächenmodell (Radar dringt teils in Wald ein) → Waldkanten ±5–10 m; Ortsungenauigkeit macht Nahbereich wertlos
  (Regel `accuracy_m ≤ 20 m`); Gebäudehöhen in OSM lückenhaft.
- **Lichtatlas veraltet** (2015, vor LED-Umstellung) und Lizenz NC/Bezug per Formular – ohne Klärung keine Weitergabe; Eichung mit eigenen Messungen mildert.
- **Recht/Sicherheit** in B: Schutzgebiete, Privatgrund, Fahrverbote – nur Hinweise, keine Garantie.
- **Lizenzpflege:** jede Quelle in `NOTICE` + sichtbare Quellenangabe; CC-BY-NC-Komponenten blockieren eine spätere kommerzielle Lizenz, solange sie
  mitgeliefert werden.
- **Paketprüfung (Windows):** neue Komponente → manueller Windows-Paket-Lauf vor Release (AGENTS.md).

---

## 6. Datenschutz-Prüfliste für die Umsetzung

- Kein Abruf mit Koordinaten feiner als die Kachel (1°); keine HTTP-Range-Abrufe auf Kacheln.
- Downloads nur auf Knopfdruck, nur https, feste Prüfsummen (Manifest).
- Kartenkacheln nur auf Knopfdruck mit Hinweis; Standard ist die Offline-Grundkarte.
- Horizontdateien, Startort und Suchergebnisse bleiben in `data/`; keine Angaben in Logs außer IDs.
- Tests nur mit erfundenen Orten und synthetischem DEM (Kegelberg, Haus-Quader) – keine echten Kacheln im Testlauf (Netzsperre in `conftest.py`).
- Screenshots für PRs nur mit erfundenem Ort.

---

## 7. Offene Entscheidungen für den Nutzer

1. **Geländedaten-Weg:** Regionalpaket (Land/Bundesland) einmalig laden, kleine 1°-Kachel um den Ort, oder nur manuell?
   *Empfehlung:* Regionalpaket **GLO-90** als Standard (~0,3 GB für Deutschland), GLO-30 (~2–2,5 GB) optional; 1°-Kachel als Sparvariante; nie Bereichsabrufe.
2. **Nahbereich (Häuser/Bäume) zuerst:** manuell + aus eigenen Aufnahmen lernen, oder gleich OSM-Gebäude/Baumhöhen?
   *Empfehlung:* zuerst manuell (MVP) und Lernen aus eigenen Aufnahmen; OSM/Kronenhöhe in Stufe 3.
3. **Mindesthöhe:** bleibt 30° als Qualitätsschwelle und der Horizont kommt hinzu – oder bei freiem Horizont je Gerät tiefer (z. B. 20°) erlauben?
   *Empfehlung:* 30° bleibt Standard, im Standort einstellbar (15–40°); Horizont immer zusätzlich.
4. **Lichtverschmutzungsatlas (CC BY-NC, Stand 2015):** als getrennte Komponente akzeptabel, obwohl er eine spätere kommerzielle Lizenz von AstroFlow
   ausschließt, solange er mitgeliefert wird? *Empfehlung:* ja, aber erst nach Rückfrage beim GFZ/ISTIL zur Weitergabe; vorher nur eigene Messungen nutzen.
5. **Routing:** einfache Schätzung (Luftlinie × Umwegfaktor) oder offline aus OSM-Wegen?
   *Empfehlung:* MVP Schätzung, dann offline-Dijkstra aus OSM; nie Online-Router.
6. **Platz des Reiters:** eigener Hauptpunkt „Standort & Horizont“ oder Unterpunkt von „Heute Nacht“?
   *Empfehlung:* eigener Hauptpunkt (wie gewünscht), dazu Kurzanzeige in „Heute Nacht“.
7. **Karte:** offline aus eigenen Paketen gezeichnet oder OSM-/Topo-Kacheln (verraten den Ausschnitt) auf Knopfdruck?
   *Empfehlung:* Offline-Grundkarte als Standard, Kacheln optional auf Knopfdruck mit Hinweis (wie SkyTrace).
8. **Bestehende Höhenabfrage** `/api/sites/elevation` schickt heute die genauen Koordinaten an Open-Meteo: umstellen?
   *Empfehlung:* ja – lokal aus dem DEM, sobald ein Paket da ist; bis dahin Hinweis im Knopf oder auf 0,01° runden (eigener kleiner Fix, unabhängig von diesem Konzept).
9. **Geräteumfang:** Stellina, Vespera, Vespera II, Vespera Pro, Seestar S50, S30 – die neuen Pro-Modelle (S30 Pro, S50 Pro, Vespera Pro II) gleich mit?
   *Empfehlung:* die sechs zuerst, Pro-Modelle nach Prüfung der Herstellerdatenblätter; Profilwerte ohne Quelle als änderbare Standards kennzeichnen.
10. **Standortsuche – Ausschlüsse:** Naturschutzgebiete, Fahrverbote, Privatwege hart ausschließen oder nur markieren?
    *Empfehlung:* `access=private`/Militär ausschließen, alles andere nur als Hinweis (Rechtslage je Land unterschiedlich).

---

## Quellen (Stand 10.11.2026)

- Copernicus DEM auf AWS (Lizenz, Kachelraster, ohne Konto): <https://registry.opendata.aws/copernicus-dem/>, <https://copernicus-dem-30m.s3.amazonaws.com/readme.html>
- World Atlas 2016, GFZ-Datensatz (CC BY-NC 4.0 seit 13.11.2019): <https://b2find.eudat.eu/dataset/df8cd562-df6a-51bf-ad64-7421cf8c8623>
- EOG VIIRS Nighttime Lights: <https://eogdata.mines.edu/products/vnl/>, Katalog-Einordnung <https://gee-community-catalog.org/projects/eog_viirs_ntl/>
- Meta/WRI Canopy Height (CC BY 4.0): <https://registry.opendata.aws/dataforgood-fb-forestsv2>; ETH Canopy Height 10 m: <https://research-collection.ethz.ch/handle/20.500.11850/636572>
- GlobalBuildingAtlas (Lizenzaufteilung ODbL / CC BY-NC): <https://essd.copernicus.org/articles/17/6647/2025/>, <https://gee-community-catalog.org/projects/gba/>
- Deutsche DGM-Daten (Übersicht, Lizenzen je Land): <https://help.emd.dk/mediawiki/index.php/German_DGM_datasets>
- Vespera II / Pro: <https://www.skiesandscopes.com/vespera-ii-vs-vespera-pro>; Vespera Pro II Datenblatt (Händler): <https://www.cliftoncameras.co.uk/uploads/specifications/Vaonis%20Vespera%20Pro%20II%20Specification.pdf>
- Seestar S30: <https://scopetrader.com/seestar-s30-telescope-specifications-and-photos/>, <https://www.highpointscientific.com/astronomy-hub/post/zwo-seestar-s30-review>
