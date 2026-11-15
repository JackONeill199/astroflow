# Teleskopsteuerung (Vaonis Vespera/Stellina) – Konzept, Phase 1

Stand 14.11.2026. Nutzerwunsch: „AstroFlow steuert das Teleskop
intelligent, hübsch animiert und eingebunden in die Planung (Observatorium-Planer, Heute Nacht, Belichtungsbedarf). Es erkennt
Hindernisse automatisch und steuert dann andere Ziele an. Bei schlechtem Wetter und bei Dämmerung klappt es das Teleskop ein.“

Grundlage ist eine **lokale, inoffizielle Bibliothek** außerhalb des Repos (ein Python-Client mit Kommandozeile, README, zwei
Testskripte, ein mitgeschnittener Status). Dieses Dokument beschreibt Protokoll und Architektur; **es enthält bewusst keine
Schlüssel, Gerätekennungen, Ortsangaben oder Netzdaten** und darf so später ins Repo.

## 1. Was die Bibliothek kann (gelesen, nichts ausgeführt)

### Herkunft
Laut Kopf „rekonstruiert aus der offiziellen App“ (Vaonis-App, Android, Version 1.40.6). Keine Lizenzdatei, kein Autor, keine
Quellenangabe; die erwähnte `PROTOKOLL.md` fehlt. Am Gerät geprüft (README, 14.11.2026, Vespera, Firmware 3.5.3): Status lesen,
Socket-Verbindung, Kontrolle übernehmen, ein harmloser authentifizierter Befehl (`setSettings` mit unverändertem Wert).
**Nicht** geprüft: alle Bewegungs- und Beobachtungsbefehle, Stellina überhaupt.

### Verbindung
- Teleskop öffnet ein **eigenes WLAN** (Access Point), Gerät unter fester Adresse im AP-Netz. Der PC muss in dieses WLAN.
- **REST** `http://<gerät>:8082/v1/<pfad>` (JSON, `{"success": true, "result": …}`), **Socket.IO** auf Port 8083 mit
  **Engine.IO v3** (darum `python-socketio<5`, `python-engineio<4`; neuere Firmware evtl. v4).
- Socket-Anmeldung: Query `id`, `name`, `countryCode`; danach Nachricht `setUserName {device, user}`, optional `takeControl`.
  Der Status kommt als Ereignis `STATUS_UPDATED` (bzw. `status`) – laufend, nicht nur auf Anfrage. Weitere Nachrichten:
  `releaseControl`, `setSystemTime <ms>`.
- **Kontrolle:** Nur ein Gerät ist „Master“ (`masterDeviceId`); schreibende Befehle brauchen die Kontrolle. Die Handy-App
  kann sie jederzeit zurückholen (und umgekehrt) – das ist ein Zustand, den AstroFlow erkennen und anzeigen muss.

### Steuerbefehle (privat)
Steuernde Befehle (Öffnen, Ausrichten, Beobachten, Einklappen …) werden mit einem Schlüssel aus der Vaonis-App signiert. Ihre
Beschreibung steht seit dem Plugin-Umbau (Abschnitt 14) nicht mehr hier, sondern im privaten Plugin (`PROTOKOLL.md`). Ohne
Anmeldung gehen: `GET app/status` (REST, Port 8082) und der anonyme FTP (Rohbilder).

### Rückmeldungen (aus dem mitgeschnittenen Status)
- Gerät: `model`, Firmware, `apiVersion`, `initialized`, `shuttingDown`, Akku (`chargeLevel`, `chargeStatus`, `chargeAlert`),
  Speicher (system/data/public/usb), `filter`, Einstellungen, verbundene Geräte, Master.
- **Motoren** `AZ`, `ALT` (Grad), `DER` (Derotator), `MAP` (Fokus) je mit `position`, `state` (IDLE …), `calibrated` → reicht
  für eine **echte Live-Animation** der Ausrichtung.
- Operationen: `currentOperation`, `otherCurrentOperations`, `previousOperations.{plan,observation}` mit `startTime/endTime`,
  `stopped`, `error.name`, `state`. Beobachtung in **Schritten**: `POINT_DEEP_SKY` → `GO_TARGET`, `START_TRACKING`,
  `ASTROMETRY` (`astrometryDelta` in Grad), `START_TRACKING`; danach `CAPTURE`.
- Aufnahme: `acquisitionCount`, `stackingCount`, `stackingErrorCount`, `stackingErrorMap` (z. B. `StackingRoundnessError`),
  Vorschaubilder mit URL/Ausschnitt/Zeit, `framing` (gelöste Lage inkl. Drehung), `startHumidity/Temperature` (an der Vespera 0 –
  wohl kein Sensor), `cameraParams`.
- Fehlernamen bisher gesehen: `OPERATION.USER_INTERRUPTION`, `OPERATION.INTERRUPTED_BY_PARENT`, `PLANNER.END_TIME_ALREADY_PASSED`.
  Weitere (Astrometrie gescheitert, Ziel unter Horizont, Akku …) kennen wir noch nicht → Treiber übernimmt unbekannte Namen
  unverändert ins Protokoll, Automatik wertet nur bekannte aus.

### Fehlerfälle, mit denen der Treiber rechnen muss
Socket weg (WLAN, Gerät aus, Akku leer) · Kontrolle von der App übernommen · Auth abgelehnt (401/„AUTH“, neue Firmware) ·
Engine.IO-Version passt nicht · Befehl abgelehnt, weil eine andere Operation läuft · Operation endet mit `error` · Status
veraltet (kein `STATUS_UPDATED` seit n Sekunden) · Speicher voll · Gerät fährt herunter.

## 2. Lizenz und Weitergabe (Einschätzung, keine Rechtsberatung)
- Die Bibliothek hat **keine Lizenz** → ohne Zustimmung des Urhebers keine Weitergabe. (Falls sie vom Nutzer selbst stammt,
  kann er sie lizenzieren – siehe offene Fragen.)
- Unabhängig davon enthält sie einen **aus der Vaonis-App extrahierten privaten Signaturschlüssel**. Den darf AstroFlow
  **nicht** mitliefern – weder im Repo noch im Windows-Paket. Das Ableiten von Schnittstellen zur Herstellung von
  Interoperabilität ist in der EU begrenzt erlaubt (§ 69e UrhG), die Weitergabe eines fremden Schlüssels bzw. das Umgehen einer
  Zugangssperre ist ein anderes Thema (§ 95a UrhG, Vaonis-Nutzungsbedingungen, Garantie). PolyForm Noncommercial ändert daran nichts.
- **Folgerung für die Architektur (seit Abschnitt 14 als Plugin umgesetzt):** AstroFlow enthält nur den **Treiber-Vertrag, die Automatik, den Simulator und einen
  Adapter, der die Bibliothek zur Laufzeit von einem vom Nutzer eingestellten Pfad lädt** (wie ASTAP/StarNet: Pfad nur am
  Rechner selbst einstellbar). Ohne Bibliothek läuft das Modul im Simulator. Nichts aus der Bibliothek wird kopiert.
- Abhängigkeiten der Bibliothek (`requests`, `pynacl`, alte `python-socketio`/`python-engineio`) kommen **nicht** in
  `requirements.txt`, sondern wie die SkyTrace-Engine in eine eigene, optionale Datei; `pynacl` (Apache-2.0), `python-socketio`
  (MIT) wären unkritisch, die alten Versionen aber ein Wartungsrisiko.

## 3. Grundsätzliches Problem: Netz
Der PC muss im **WLAN des Teleskops** hängen. Dann fehlt ihm in der Regel Internet (Wetter) und das Heimnetz (Handy-Zugriff
auf AstroFlow), außer er hat eine zweite Verbindung (LAN-Kabel, zweiter WLAN-Stick). Bei **zwei Teleskopen** braucht er sogar
zwei WLAN-Verbindungen gleichzeitig (beide Geräte haben dieselbe Adresse im jeweils eigenen Netz). Das entscheidet die
Architektur der Automatik mit:
- Wetter wird **vor** Beginn geholt (vorhandene Open-Meteo-Schicht, `tonight._forecast`, Ort wie bisher auf 0,1° gerundet,
  1-h-Cache) und unterwegs nur aktualisiert, wenn Internet da ist. Ohne Internet zählen die **Messung im Bild**
  (Sterne/Astrometrie) und die zuletzt geholte Vorhersage – Abbruchgrenzen dann vorsichtiger.
- AstroFlow verändert **keine** Netzwerkeinstellungen des Rechners (kein automatisches WLAN-Umschalten, keine Firewall).
  Die Geräteadresse ist eine Einstellung (Standard wie in der Bibliothek).

## 4. Architektur

```
astroflow/modules/telescope/
  __init__.py      class Module(BaseModule): Treiber je Gerät, Autopilot-Threads, status()/activity(), Ereignisse
  api.py           Router /api/telescope (Abschnitt 6)
  driver.py        Treiber-Vertrag + normierter Zustand (TelescopeState)
  vaonis.py        Adapter: lädt die Bibliothek per importlib vom eingestellten Pfad, übersetzt Status/Fehler
  sim.py           Simulator (virtuelle Zeit, Motoren, Wolken, Hindernisse, Tau, Akku, Fehlerinjektion)
  machine.py       Zustandsautomat (erlaubte Übergänge, Not-Aus)
  autopilot.py     Abarbeiten des Plans + Wächter (Dämmerung, Wetter, Tau, Hindernis, Akku, Speicher, Kontrolle)
  obstacles.py     Hindernis-Erkennung, Vorschläge für die Horizontmaske
  static/telescope.js|css   Live-Ansicht #/teleskop
  AGENTS.md
```

### Treiber-Vertrag (`driver.py`)
```python
class Driver:
    def connect(self) -> None; def disconnect(self) -> None
    def take_control(self) -> None; def release_control(self) -> None
    def state(self) -> TelescopeState          # normiert, threadsicher, mit Alter des Status
    def open(self); def park(self)             # park() muss aus jedem Zustand gehen (stoppt vorher)
    def init(self, lat, lon, skip_focus=False) # Ort nur ans Gerät, nie ins Protokoll an den Browser
    def observe(self, target: Target, exposure_s, gain) ; def stop(self)
    def focus(self); def images(self) -> list[ImageRef]; def download(self, ref, dest: Path)
```
`TelescopeState`: `model`, `connected`, `master` (wir/andere/keiner), `initialized`, `az`, `alt`, `der`, `focus`,
`moving`, `operation` + `step` (GO_TARGET, ASTROMETRY, CAPTURE …), `target`, `frames`, `stacked`, `rejected`,
`astrometry_delta`, `battery`, `storage_free`, `humidity`/`temperature` (falls vorhanden), `error`, `age_s`.
Kennungen (Teleskop-ID, Gerätenamen der App, Ort) bleiben im Adapter und gehen **nicht** in `TelescopeState`.
Ablehnungen: `DriverError` (feste Meldung) bzw. `DriverBusy` (Unterklasse, nur wenn das Gerät laut Status noch eine Operation
ausführt). Der Autopilot wiederholt mit Backoff und meldet erst nach einer Zeitgrenze (`GIVE_UP`) einen Fehler; Restdauer je Phase aus
Erfahrungswerten (`EXPECT_S`), im Betrieb durch gemessene Dauern ersetzt (`took`). Befund 14.11.2026 (erster Autopilot-Lauf): Die Vespera
lehnte Ausrichten minutenlang mit HTTP 500 ab, obwohl keine Operation lief – die Geräteuhr stand auf 2018 (die App setzt die Zeit beim
Verbinden). HTTP 500 heißt also nicht immer „beschäftigt“.

### Zustandsautomat (`machine.py`)
```
getrennt → verbunden → [Kontrolle] → eingeklappt ⇄ geöffnet → ausgerichtet (Auto-Init) → schwenkt → richtet aus (Astrometrie)
        → nimmt auf ⇄ pausiert → (nächstes Ziel: schwenkt …) → klappt ein → eingeklappt
Querwege: jeder Zustand → „klappt ein“ (Not-Aus) · jeder Zustand → „Kontrolle verloren“ · „Fehler“ → nur Einklappen/Trennen
```
Übergänge nur über den Automaten; der Automat prüft, was der Gerätestatus tatsächlich meldet (Soll/Ist), und meldet
Abweichungen (z. B. „Befehl gesendet, Gerät bewegt sich nach 30 s nicht“) ins Ereignisprotokoll.

### Not-Aus „Sofort einklappen“
- Eigener Endpunkt, eigener Thread, **ohne Warteschlange**: stoppt laufende Beobachtung/Init, dann `park`. Wiederholt bis der
  Status „geparkt“ meldet oder 3 Versuche gescheitert sind (dann deutliche Warnung: „Bitte Teleskop von Hand einklappen“).
- Auch bei verlorener Kontrolle wird `takeControl` + `park` versucht (Sicherheit vor Höflichkeit gegenüber der App).
- Beim Beenden des Servers (`stop()`) und wenn der Autopilot abstürzt: einklappen, falls er etwas bewegt hat.

### Autopilot (`autopilot.py`)
1. **Quelle:** Plan der Nacht aus dem Planer (`settings.json` → `observatory.plan`, Blöcke je Gerät mit Start/Ende/Ziel) –
   über einen neuen Stacking-Dienst `night_plan(night)`; sonst die Empfehlungen aus „Heute Nacht“ (`tonight_plan`), sonst
   eine von Hand zusammengestellte Liste. Ziel-Koordinaten über `catalog_resolve`.
2. **Ablauf je Block:** schwenken → Astrometrie abwarten → aufnehmen bis Blockende bzw. bis der Bedarf (`need`) gedeckt ist →
   nächstes Ziel. Fällt ein Ziel aus, rückt das nächste vor, das **jetzt** frei ist (gleiche Regel wie Planer/Kuppel:
   `tonight._limit` + astronomisch dunkel + Horizontmaske + verdeckte Richtungen dieser Nacht).
3. **Wächter** (alle 30 s, jeder mit klarer Begründung im Protokoll):
   - **Dämmerung:** `core/sites.sun_alt` – abends erst ab Sonne < −12° (einstellbar) starten, morgens bei Sonne > −12°
     einklappen. Nachtdatum nur über `sites.night_of`.
   - **Wetter:** Vorhersage (Wolken gesamt/tief, Wind/Böen gegen `devices.json` → `gust`, Regenwahrscheinlichkeit, falls in
     `HOURLY` ergänzt) für die nächsten 30–60 min; über der Grenze → einklappen, bei Besserung wieder öffnen (höchstens n-mal je Nacht).
   - **Tau:** Taupunktabstand aus der Vorhersage (`dew_spread`) und – wo vorhanden – Gerätesensor. Stellina: Warnung < 3 °C,
     einklappen < 1,5 °C (trotz Heizung, Erfahrung des Nutzers); Vespera ohne Heizung: Warnung früher. Zusätzlich Messung im
     Bild: gleichmäßig sinkende Sternzahl/steigende FWHM über alle Richtungen bei klarer Vorhersage → „Beschlag?“.
   - **Hindernis** (`obstacles.py`): Astrometrie scheitert, Sterne fehlen oder der Hintergrund ist auffällig hell (Laterne),
     während **ein anderes Ziel in anderer Richtung** in derselben Viertelstunde gut läuft → Richtung (Az/Höhe) als „verdeckt“
     für diese Nacht merken, Ziel überspringen. Scheitern alle Richtungen → Wolken, nicht Hindernis. Am Morgen: Vorschlag
     „Horizontmaske hier anheben?“ mit den gesammelten Punkten im vorhandenen Horizont-Editor (`horizonEditor({points})`) –
     **übernommen wird nur nach Rückfrage**.
   - **Akku/Speicher/Kontrolle/Verbindung:** Akku < 15 % → letztes Ziel beenden und einklappen; Speicher knapp → Warnung;
     App hat Kontrolle übernommen → Autopilot pausiert und fragt nicht zurück; Status älter als 2 min → nach Wiederkehr zuerst
     Lage prüfen, nie blind weiterschwenken.
4. **Bilder:** Am Ende jedes Ziels Rohbilder (falls am Gerät aktiviert) bzw. TIFF holen und unter `input/<Gerät>/<Ziel>/` ablegen;
   die vorhandene Pipeline übernimmt (Scan, Stacken). Neues Ereignis `telescope.captured(device, target, folder)`, damit
   Bilder sofort einliest statt beim nächsten Scan.

### Simulator (`sim.py`)
Gleicher Vertrag wie der Adapter. Virtuelle Uhr (Faktor einstellbar, Tests ×1000), Motoren fahren mit Grad/s auf Az/Alt,
Init dauert, Astrometrie hat Erfolgswahrscheinlichkeit, Szenarien: Wolkenband ab 01:00, Hauswand in Ost unter 35°,
Tau ab 03:00, Akku leer, App übernimmt Kontrolle, Verbindung bricht ab. Die Oberfläche zeigt im Simulator deutlich
„Simulation“. Er ist die Grundlage aller Tests und der Entwicklung der Oberfläche.

## 5. Oberfläche `#/teleskop` (Bilder-Bereich „Arbeit“, eigenes Modul)
- **Bühne:** animiertes, schematisches Teleskop (SVG, eigene Zeichnung, kein Hersteller-Artwork) – Arm folgt `ALT`, Sockel dreht
  mit `AZ`, Einklappen/Öffnen als Bewegung, Lichtkegel/Glühen beim Aufnehmen. Dahinter die Kuppel aus dem Observatorium
  („Von hier“-Projektion mit Horizontmaske, verdeckten Richtungen dieser Nacht, Ziel-Fadenkreuz und Bahn des aktuellen Ziels).
- **Aktuelles Ziel:** Name, Restzeit, gestapelte/verworfene Bilder, letzte Vorschau (sanft eingeblendet), Fortschritt am Bedarf.
- **Zeitleiste:** Plan-Blöcke der Nacht (Farben wie Planer), darüber das tatsächlich Geschehene (aufgenommen/übersprungen/verdeckt),
  Dämmerungs- und Wetterbänder, „jetzt“-Marke.
- **Ereignisprotokoll:** deutsche Klartexte mit Uhrzeit („23:41 M81 verdeckt (Astrometrie 3× gescheitert, M13 läuft) – weiter mit NGC 2403“).
- **Große Schalter:** Start · Pause · **Sofort einklappen** (immer sichtbar, rot, nie ausgegraut, auch auf dem Handy oben fixiert).
  Bewegungsbefehle verlangen beim ersten Mal je Nacht eine Bestätigung („Teleskop steht frei, Arm darf sich bewegen“).
- Einbindung: Planer und „Heute Nacht“ bekommen „Mit Teleskop aufnehmen“; Übersicht zeigt eine kleine Statuskarte.
  Alles mit `prefers-reduced-motion`-Ausstieg, Maße in `rem`, Tokens aus `:root` (Regeln aus `AGENTS.md`).

## 6. API und Sicherheit
`/api/telescope`: `GET status` (klein, gepollt), `GET events?since=`, `GET night` (Zeitleiste), `POST connect|disconnect`,
`POST start|pause|resume`, `POST park` (Not-Aus), `POST goto {target}` (einzelnes Ziel), `POST horizon-proposal/accept`,
`GET/POST settings`.

**Nur verbinden (Knopf „Verbinden“/„Trennen“ auf der Teleskop-Seite):** `POST connect` liest Status, Akku und Speicher und nimmt die
Kontrolle **nur, wenn sie frei ist** (`master == none`) – nie der Handy-App; steuert sie gerade, antwortet es `{master: "other", note}`
und die Oberfläche meldet es. Es bewegt nichts und startet keinen Autopiloten (Modus bleibt „aus“, kein `MOVES`-Befehl); dieselbe Regel
gilt beim Verbinden durch `start`. `POST disconnect` → 409, solange der Autopilot läuft oder wartet. Beide sind Bedienung (auch für
gekoppelte Geräte), kein `data-local-only`. Tests: `test_api_verbinden_*`.
- Alle Steuer-Endpunkte unterliegen Herkunftsschutz und Kopplung (Sicherheit 2/3): nur dieser Rechner oder gekoppelte Geräte.
- **Nur am Rechner selbst** (`require_local`): Pfad zur Bibliothek, Geräteadresse, Laden des Adapters.
- Keine Ausnahmetexte an den Browser (feste deutsche Meldungen); Status/Protokoll ohne Kennungen und Koordinaten des Ortes.
- Sonnen-Modus, WLAN-Umschaltung, Herunterfahren, Speicher löschen und Firmware sind im Adapter nicht erreichbar.

## 7. Tests
Nur mit Simulator: Automat (erlaubte/verbotene Übergänge, Not-Aus aus jedem Zustand), Autopilot mit virtueller Uhr
(Dämmerung, Wolken, Tau, Hindernis vs. Wolke, Akku, Kontrollverlust), Adapter gegen eine Attrappe der Bibliothek (Status-
Übersetzung aus einem **anonymisierten** Status-Beispiel in `tests/data/`), API inkl. `require_local`, Route in `test_ui_layout.py`.
Kein Test spricht ein echtes Gerät an; die Netzsperre aus `conftest.py` bleibt.

## 8. Umsetzungsschritte (nach Freigabe)
1. Modulgerüst, Treiber-Vertrag, Simulator, Automat, Not-Aus, API, Tests.
2. Vaonis-Adapter (Laden vom Pfad, Status-Übersetzung) – zuerst **nur lesend** am Gerät (Status), Bewegung erst nach Freigabe je Befehl.
3. Autopilot mit Wächtern, Stacking-Dienste `night_plan`/`forecast`, Hindernis-Erkennung und Horizont-Vorschlag.
4. Live-Ansicht mit Animation, Einbindung in Planer/Heute Nacht/Übersicht, Screenshots im Simulator.

## 9. Offene Fragen an den Nutzer
1. **Herkunft der Bibliothek:** Hast du sie selbst (mit Claude) erstellt? Soll sie dauerhaft privat bleiben, und ist es für dich in
   Ordnung, dass AstroFlow sie nur vom eingestellten Pfad lädt und **nie** mitliefert (öffentliche Version = nur Simulator)?
2. **Netz:** Wie hängt der Rechner nachts am Teleskop – Laptop draußen nur im Teleskop-WLAN, oder hat er zusätzlich LAN/Internet?
   Sollen **beide** Teleskope gleichzeitig gesteuert werden (bräuchte zwei WLAN-Adapter) oder eins pro Nacht?
3. **Stellina:** Spricht sie dasselbe Protokoll (Port, Engine.IO-Version, Auth)? Ein einmaliges **nur lesendes** `status` an der
   Stellina würde das klären – darf ich das (nach Rückfrage, wenn sie an ist)?
4. **Grenzwerte:** Wolken (%), Wind/Böen, Taupunktabstand für Einklappen; Dämmerungsgrenze (−12° nautisch oder −18° astronomisch)?
   Wieder öffnen bei Besserung – ja/nein, wie oft je Nacht?
5. **Belichtung:** Gain/Belichtungszeit fest (10 s, Gain 200) oder je Ziel aus dem Bedarf? Rohbilder am Gerät speichern (für die
   AstroFlow-Pipeline nötig) – ist das bei dir eingeschaltet, und wohin sollen sie (Eingangsordner der Arbeitsinstanz)?
6. **Geräte-Plan vs. Feinsteuerung:** AstroFlow steuert Ziel für Ziel selbst (dafür muss der PC die ganze Nacht verbunden
   bleiben, kann aber umplanen). Alternative/Rückfall: den Plan als Geräte-Plan (`planner/startPlan`) übergeben, das Teleskop
   arbeitet dann auch ohne PC weiter – aber ohne Hindernis-Umplanung. Beides anbieten?
7. **Not-Aus ohne Verbindung:** Reicht dir eine laute Warnung, wenn das Einklappen nicht bestätigt wird, oder soll AstroFlow
   zusätzlich eine Benachrichtigung aufs Handy schicken (bräuchte Internet bzw. Heimnetz)?
8. **Ort ans Gerät:** Auto-Init braucht Breite/Länge – sie gehen nur über das Teleskop-WLAN ans Gerät (wie bei der App).
   Einverstanden, dass dafür der gespeicherte Standort ungerundet verwendet wird?
9. **Erste Probe am Gerät:** Welche Reihenfolge möchtest du? Vorschlag: (a) Status lesen, (b) Kontrolle übernehmen/abgeben,
   (c) Öffnen + Einklappen im Garten bei Tag mit freiem Arm, (d) Auto-Init, (e) ein Ziel 5 min, (f) Autopilot eine Stunde –
   jeder Schritt einzeln mit deiner Freigabe.

## 10. Entscheidungen des Nutzers (14.11.2026, über den Master)
- Bibliothek nur zur Laufzeit vom eingestellten Pfad (`require_local`), nie in Repo/Tests/Paket; ohne sie Simulator.
- Netz: Rechner mit LAN (Internet/Heimnetz) + WLAN (Teleskop). Wetter läuft die Nacht über weiter, Handy im Heimnetz bleibt nutzbar.
  Ohne Internet: letzte Vorhersage + Hinweis. Vorerst ein Teleskop, Architektur offen für zwei (je Einheit eine Verbindung).
- Steuerung: Ziel für Ziel live **und** Geräte-Plan als Rückfall bei Verbindungsverlust.
- Ort: genau an das eigene Teleskop (nur Teleskop-WLAN), sonst nirgendwohin.
- Voreinstellungen (änderbar): Wolken > 70 %, Wind > 30 km/h, Taupunktabstand < 2 °C (Stellina < 3 °C), Nacht ab/bis Sonne −12°
  (wahlweise −18°), wieder öffnen nach 20 min stabil gut; Belichtung Gerätestandard (10 s), Dauer aus dem Belichtungsbedarf (Planer-Blöcke);
  Einklappen nicht bestätigt → Warnung mit Ton + Browser-/Handy-Benachrichtigung (Seite offen), kein Push-Dienst.
- Live-Rohbilder: anonymer FTP am Teleskop, `user/<plan>/<beobachtung>/01-images-initial/img-NNNN.fits` (16 bit, ~4 MB), laufend neu.
- Proben am Gerät: Status (Vespera, dann Stellina) → Kontrolle übernehmen/abgeben → Öffnen/Parken → GoTo helles Ziel →
  kurze Beobachtung → Bilder abholen. Jede einzeln nach Freigabe.

## 11. Stand Phase 2 (Simulator, 14.11.2026)
Gebaut nach Abschnitt 4–7; Abweichungen und Details:
- **Takt:** `Unit` (Treiber + Autopilot + Thread, `TICK_S` 1 s Simulator / 2 s Gerät). `Autopilot.tick(now)` mit virtueller Uhr testbar.
- **Plan:** Blöcke des Planers für Nacht und Gerät (`plan_items`, `observatory.plan`); ohne Plan im Simulator eine Vorführ-Nacht
  aus dem Katalog (`demo_items`); Ziele von Hand (`POST /targets`, Katalogname). Lücke im Plan → nächstes freies Ziel früher beginnen.
- **Ersatzziel** für ein verdecktes/zu tiefes Ziel höchstens 30 min am Stück (`SUB_MAX`), danach neuer Versuch beim geplanten Ziel.
- **Hindernis oder Wolke** (`_failed`/`_failure`, seit dem Echtbetrieb 14.11.2026 strenger – ein kurzer Wolkenzug hatte C 51 bei 33 %
  Verwurf gesperrt): Scheitern = Astrometrie gescheitert oder ≥ 80 % verworfen in den letzten 10 Bildern **und** zusätzlich ≥ 80 %
  verworfen insgesamt (nie Sterne gesehen) bzw. schon 10 min am Stück (`BAD_GRACE_S`; so lange nimmt das Gerät weiter auf, Protokoll „Wolkenzug“).
  **Sicheres Hindernis** nur, wenn dieselbe Richtung in einem früheren, getrennten Versuch (> 20 min vorher) schon gescheitert ist
  **und** eine andere Richtung (≥ 20°) in den letzten 20 min gute Bilder lieferte, die dort nicht durch späteres Scheitern überholt
  sind, oder die Vorhersage klar ist – nie bei ≥ 60 % Bewölkung laut Wetterdaten (`CLOUDY`). Nur dann ein Vorschlag für die
  Horizontmaske. Sonst „vielleicht“ (Sperre läuft nach 20 min ab, dann neuer Versuch). Scheitert danach eine
  andere Richtung ohne Erfolg dazwischen → Wolken: einklappen, Vermutungen zurücknehmen, nach 20 stabilen Minuten wieder öffnen.
  Gesperrt wird ±10° Azimut bis 5° über die gescheiterte Höhe; Vorschläge je Richtung zusammengefasst, Übernahme in die Horizontmaske
  nur per Knopf mit Rückfrage (`horizon_with`: Stützstelle ± Nachbarn auf Höhe + 2°, nie absenken).
- **Wetter** linear zwischen den Stundenwerten; im Zeitraffer-Simulator keine echte Vorhersage (passt nicht zur virtuellen Zeit).
- **Rückfall:** Kann der Treiber einen Geräte-Plan (`device_plan`), startet jedes Ziel als Plan „dieses Ziel bis Blockende + Rest der
  Nacht“; das Gerät arbeitet bei Verbindungsverlust weiter und klappt am Ende ein. Beim Vaonis-Adapter ist das **aus**, bis die Probe
  den Körper von `planner/startPlan` bestätigt (Einstellung „Geräte-Plan freischalten (Versuch)“); bis dahin `startObservation`.
- **Einklappen bestätigen:** `park_pending` – nach 90 s neuer Versuch, nach 3 Versuchen `alarm` (Oberfläche: Banner, Ton alle 6 s,
  Browser-Benachrichtigung; Erlaubnis wird beim Start erfragt).
- **Live-Bilder** (`frames.py`): eigener Thread alle 20 s während `capture`; jüngster Bilderordner, der nach dem Zielstart geändert
  wurde; Datei erst holen, wenn ihre Größe zwischen zwei Blicken gleich bleibt (am Zielende alles); `.part` → umbenennen.
  Ablage `input/Teleskop/<Gerät>/<Nacht>/<Ziel>/` (Pipeline-Überwachung des Eingangsordners stackt mit). Nur Vaonis-Adapter.
- **Ausrichtung in der Bühne** (Nutzerentscheidung 14.11.2026: abstrakt, kein Modell je Teleskop): Viertelkreis Horizont–Zenit mit Skala
  0/30/60/90°, grüner Bogen = frei ab Mindesthöhe, Zeiger + schmaler Blickfeld-Keil = gemeldete Höhe (auf 0–90° begrenzt), Wert an der Spitze, Kompassrose für den Azimut, Phase darunter. Eingeklappt:
  Zeiger eingefahren und grau (`parked`, unabhängig davon, ob ein Gerät dabei −90° oder 0° meldet). Simulator: Vaonis klappt auf −90°
  (`sim.park_alt`), Öffnen schwenkt auf 10°.
- **Oberfläche** `#/teleskop`: Bühne (Canvas: Panorama 140° um die Blickrichtung, Himmel nach Sonnenhöhe, Sterne driften, Horizontmaske,
  verdeckte Stellen ✕, Zielmarke, Teleskop zielt auf die Marke, Lichtstrom bei Aufnahme, Wolken), Kuppel von oben (Bahnen, Maske,
  Mindesthöhe, Spur des Teleskops), aktuelles Ziel, Bedingungen, Zeitleiste (Blöcke nach Status, dunkle Zeit, Wolken, Jetzt), Hindernis-
  Vorschläge, Ereignisse. Knopf „Sofort einklappen“ immer sichtbar (Kopf klebt oben). Einstellungen unter Einstellungen → Teleskop.
- **Noch offen / ungeprüft am Gerät:** laufende Operation und „eingeklappt“ in `vaonis.translate` (aus einem Status abgeleitet),
  Fehlernamen der Astrometrie, Körper des Geräte-Plans, ob Beobachtung/Init/Park so akzeptiert werden, FTP-Ordner je Beobachtung,
  Stellina-Protokoll. Ereignis `telescope.captured` für sofortiges Einlesen (statt Ordnerüberwachung) noch nicht gebaut.

## 12. Proben am Gerät
- **Probe 1 (14.11.2026, Freigabe des Nutzers): Vespera-Status nur lesen** – `GET app/status` ohne Socket, ohne Kontrolle, ohne Bewegung.
  HTTP 200, apiVersion 2.0.0, Firmware 3.5.3. Eingeklappt: `currentOperation: null`, ALT −90° mit `atStop: true`, AZ 0 (`atStop`),
  `initialized: false` nach Neustart. Neue Felder gegenüber dem ersten Mitschnitt: `planner` (leer), `previousOperations.park`
  (Operationstyp `PARK` bestätigt). `storageFileCategories` enthält `RAW` und `DIRECT`. → `vaonis._parked` nutzt `atStop`/ALT ≤ −80°.
  Akku stand bei 20 % (Grenze des Autopiloten: 15 %). Rohantwort nur im Scratchpad der Sitzung, nicht im Repo.
- **Probe 2 (14.11.2026, Freigabe des Nutzers): Kontrolle übernehmen und abgeben** über den AstroFlow-Adapter (`VaonisDriver`, Bibliothek
  vom Pfad, eigene venv mit pynacl, python-socketio 4.6.1, python-engineio 3.14.2 – läuft unter Python 3.14). Socket nach 0,2 s, Status
  kommt laufend; `takeControl` → Master nach < 0,5 s; `releaseControl` → kein Master. Kein Bewegungs- oder REST-Befehl gesendet.
  Befund: **beim Laden meldet die Vespera `chargeStatus: CHARGING`, `chargeLevel: 0`** (Socket und REST gleich) → `vaonis._battery`
  wertet das als unbekannt, `TelescopeState.charging`; der Akku-Wächter greift beim Laden nicht.
- **Probe 3 (14.11.2026, Freigabe des Nutzers): Öffnen und Einklappen.** Operation `OPEN`: ALT −90° → +20°, AZ wird dabei kalibriert
  (`calibrated` false → true), ~49 s. Operation `PARK`: ALT erst kurz auf ~32° hoch, dann auf −90°, `atStop: true`, die Operation endet
  ~7 s später; zusammen ~47 s. **Die REST-Antwort kommt erst nach dem Ende der Bewegung** – mit der 20-s-Frist der Bibliothek lief
  „Öffnen“ in einen Timeout, und das gleich danach gesendete Einklappen wurde mit **HTTP 500** abgelehnt, weil `OPEN` noch lief. Die
  Vespera stand dadurch kurz offen (20°) und wurde im zweiten Schritt sauber eingeklappt (`{"success":true}`).
  → Adapter: Befehle laufen im eigenen Thread mit 180 s Frist (`_send`, Körper wie die Bibliothek über `_body`), nur 4 s Warten auf
  eine schnelle Ablehnung, Erfolg über den Status; späte Ablehnungen erscheinen als `error`. `park()` stoppt zuerst die Beobachtung und
  wiederholt bei „beschäftigt“ alle 3 s bis zu 120 s. Autopilot wartet 150 s auf die Bestätigung des Einklappens (`PARK_CONFIRM_S`).
- **Probe 3 wiederholt (14.11.2026) mit dem korrigierten Adapter:** `open()` kehrt nach 4 s zurück; `park()` 6 s später (wie
  „Sofort einklappen“ während des Öffnens): Stoppen und Einklappen erst abgelehnt (HTTP 500, `OPEN` läuft), Wiederholung alle 3 s,
  angenommen direkt nach Ende von `OPEN` (33 s), eingeklappt nach 80 s. **Die Vespera kann ein laufendes Öffnen nicht abbrechen** –
  ein Not-Aus in dieser Phase wirkt erst nach dem Öffnen (höchstens ~50 s). Frühe Ablehnungen bleiben nicht mehr als Fehler stehen.
- Offen: Stellina-Status (Probe 1b), Kontrolle übernehmen/abgeben (2), Öffnen/Parken (3), GoTo (4), kurze Beobachtung (5), Bilder per FTP (6).

## 13. Import: Aufnahmen vom Teleskop (Nutzerwunsch 14.11.2026)
- Importansicht (`#/import`) zeigt oben „Aufnahmen auf dem Teleskop“, sobald ein Teleskop erreichbar ist (`importPanel`, Hook aus
  `stacking.js`; ohne Teleskop-Modul fehlt der Abschnitt). Echtes Teleskop eingerichtet, aber nicht erreichbar → leise Zeile „Erneut suchen“.
- Backend `captures.py`: `GET /api/telescope/captures` listet per FTP (nur LIST) alle Beobachtungen `user/<plan>/<beobachtung>/0x-images-*/`
  mit Bildzahl/Größe/Datum (30 s Cache). `POST /captures/import {keys}` – nur Schlüssel aus der zuletzt gelesenen Liste, keine Pfade.
  Ziel `input/Teleskop/<Gerät>/<plan>/<beobachtung>/` (Namen über `safe_name`), `.part` → umbenennen, vorhandene gleich große Dateien
  übersprungen (fortsetzbar). Danach Dienst `scan_input` (Bilder-Modul) → Einlesen/Stacken. „Schon importiert“ in `data/telescope/importiert.json`.
- Fortschritt in Aktivität/Statusleiste (`activity`, `busy_jobs`). Simulator: `sim.SimFtp` mit drei Beobachtungen aus kleinen synthetischen FITS.
- **Probe Liste (14.11.2026, Freigabe des Nutzers, nur LIST):** Pure-FTPd, MLSD vorhanden, Wurzel `system/` + `user/`. Schema
  `user/<JJJJ-MM-TT_hh-mm-ss>_plan_<Planname>/<NN>-observation-<objekt>/01-images-initial/img-NNNN.fits`; 3 Beobachtungen,
  992 Rohbilder, 4,1 GB (~4,15 MB je Bild). Auffällig: genau 400 Rohbilder bei langen Beobachtungen (Gerät speichert wohl höchstens 400).
  Anzeige jetzt lesbar (`captures.pretty`: „M77“, „Plan Astroflow · 13.11.2026 17:21“); Simulator nutzt dasselbe Schema.
- **Probe Bilder abholen (14.11.2026, Freigabe des Nutzers):** alle 992 Rohbilder (3 Beobachtungen) per FTP-RETR in einen Ordner des
  Nutzers außerhalb von AstroFlow kopiert, Struktur wie am Gerät; 459 s, ~8,9 MB/s, alle Größen stimmen, keine Reste. Rohbild: 1920 × 1080,
  BITPIX 16, Bayer GBRG, Schlüssel `DATE-OBS`, `EXPOSURE` (10 s, kein `EXPTIME`), `GAIN`, `OBJECT`, `FOCAL`, `PIXSZ`, `INSTRUME` (= Gerätekennung,
  privat), keine Ortsangaben. Eine Nacht mit ~400 Bildern dauert also rund 3 min; Live-Abholung (alle 10 s ein Bild à 4 MB) ist unkritisch.

## 14. Plugin-Architektur (Nutzerentscheidung 14.11.2026, über eine abgezweigte Sitzung)
Lizenzeinschätzung (keine Rechtsberatung): Eigener Code und alles **ohne** den extrahierten Signaturschlüssel darf öffentlich in
AstroFlow – Status lesen (`GET app/status` ohne Anmeldung) und anonymer FTP (Import vom Teleskop, Live-Rohbilder). Heikel ist nur
das Steuern. Darum:
- **Im Repo:** Modul mit Simulator, Autopilot, Live-Ansicht, Planer-Anbindung, Wetter/Dämmerung; `vaonis_read.py` (Status-Übersetzung
  `translate` mit den Probe-Befunden, Treiber „Vaonis – nur Status und Bilder“ `VaonisReadOnly`: Status per REST alle 2 s, jeder
  Bewegungsbefehl → feste Meldung „Steuern braucht ein Treiber-Plugin“); `frames.py`/`captures.py` (FTP). **Keine** Anmelde- oder
  Befehlslogik, kein Laden der Bibliothek.
- **Treiber-Vertrag** `driver.CONTRACT = 1` (erhöhen, wenn Methoden/`TelescopeState`/`Target` sich unverträglich ändern). Plugin-Klasse:
  `__init__(self, options: dict)`, `connect/disconnect/take_control/release_control/state/open/park/init/observe/stop/focus`,
  optional `start_device_plan`, `capabilities`, `ip` (schaltet FTP-Import/Live-Bilder frei).
- **Plugins** (`plugins.py`): Ordner mit `plugin.json` (`name`, `family`, `models`, `contract`, `module`, `class`, `requires`,
  `options[{key,label,hint,type}]`) und Treiberdatei im selben Ordner. Gesucht in `<daten>/plugins/*`, in den Ordnern aus
  `telescope.plugin_paths` und über den Entry Point `astroflow.telescope_drivers`. `discover` führt nichts aus; geprüft werden
  Vertragsversion, fehlende Pakete (`requires`), Datei im Ordner. Kennung = Name + Hash des Ortes.
- **Aktivieren** (`POST /plugins/enable`), Plugin-Ordner (`/plugins/paths`), Geräteadresse, Plugin-Optionen und jede Steuerung außer
  dem Simulator nur am Rechner selbst (`require_local`); Hinweis „fremder Code“ beim Aktivieren. Nur aktivierte, nutzbare Plugins sind
  als Steuerung wählbar; Deaktivieren schaltet auf den Simulator zurück. Defektes/unpassendes Plugin → feste deutsche Meldung
  (`unit.error`), der Simulator läuft weiter.
- **Vaonis-Steuer-Plugin (privat):** liegt außerhalb des Repos in einem privaten Ordner neben der Bibliothek
  (`plugin.json`, `driver.py`, `PROTOKOLL.md`, eigene Tests `test_plugin.py`). Enthält alles mit Schlüsselbezug (Bibliothek laden,
  Signatur, Befehle) und die Probe-Befunde (lange blockierende Befehle, HTTP 500 bei „beschäftigt“, Park-Wiederholung). Für die eigene
  Instanz: Plugin-Ordner unter Einstellungen → Teleskop eintragen, aktivieren, Steuerung „Vaonis-Steuerung (privat) (Plugin)“ wählen;
  die Pakete der Bibliothek liegen im Plugin selbst (siehe unten), nicht in AstroFlow.
- **Eigene Pakete je Plugin** (Ergänzung 14.11.2026): `site-packages/` oder `vendor/` im Plugin-Ordner (`pip install --target`).
  `discover` prüft `requires` gegen AstroFlows Umgebung **und** diese Ordner (ohne Import); `load` hängt sie erst dann **hinten** an
  `sys.path` (AstroFlows Pakete haben Vorrang, die installierte Windows-Fassung bleibt unberührt). Fehlen Pakete → feste Meldung mit
  Liste und Hinweis auf `site-packages`. Falsche Vertragsversion → „Plugin passt nicht zu dieser AstroFlow-Version (…)“.
  Achtung bei kompilierten Paketen: das Windows-Paket nutzt Python 3.12 (`PBS_PY`), die Entwicklung 3.14 – beim Vaonis-Plugin liegen
  deshalb `_cffi_backend.cp312` und `.cp314` nebeneinander (`nacl/_sodium.pyd` ist versionsunabhängig).
- **Nichts aus Versehen veröffentlichen:** Fehler melden (`core/report.py`) maskiert `<daten>/plugins`, jeden Plugin-Ordner darin, alle
  gefundenen Plugins samt `private`-Ordnern aus plugin.json (Vaonis-Plugin: `../..` = Ordner mit der Bibliothek) und Pfade aus
  Plugin-Optionen (Dienst `report_private_dirs`, Kern legt die Liste an); Traceback-Zeilen aus diesen Ordnern samt Quelltextzeile
  entfallen im Protokollauszug („Zeile aus einem Treiber-Plugin ausgeblendet“), lange Schlüssel-Zeichenketten maskiert der Scrubber.
  Screenshots im Bericht maskieren dieselbe Wortliste. Plugins liegen in `data/` (per .gitignore ausgeschlossen) oder außerhalb des
  Repos; nie `git add -A`. Tests: `test_report.py::test_plugins_nie_im_bericht`, `test_telescope.py::test_plugin_bringt_eigene_pakete_mit`.
