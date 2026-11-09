# AstroFlow – Weg zum Public Release

Stand 04.11.2026 (Version 0.21.0, Repo noch privat). Erledigte Punkte abhaken und Version/PR dazuschreiben, nichts löschen.
Plan: erst die Hürden unten beseitigen, dann **offene Beta** veröffentlichen, Rückmeldungen sammeln, danach gezielt vermarkten.

## Was schon trägt
- Vollautomatische Pipeline Rohbilder → fertiges Bild, dazu Tutor, Heute Nacht, SkyTrace (mit 3D) und Sage – in dieser
  Kombination ohne bekanntes Gegenstück.
- Für Smart-Teleskop-Nutzer ein einfacher Einstieg statt Siril + GraXpert + StarNet einzeln einzurichten.
- Tests (parallel), Versionierung, CHANGELOG, Feature-Wunschliste.

## Muss vor der Beta erledigt sein

### 1. Installation für Nicht-Entwickler
- [ ] Windows-Installer (oder portables Paket) ohne eigene Python-/venv-Einrichtung; Start/Stop als Startmenü-Eintrag.
- [ ] Docker-Image tatsächlich bauen und testen (Dockerfile mit ASTAP-Download wurde noch nie real gebaut), dazu
      eine Anleitung für Linux/NAS.
- [ ] macOS: zumindest Docker oder Anleitung.
- [ ] Erster Start mit kurzer Einrichtung: Datenordner, Gerät, Standort (optional), Hauptordner wählen.

### 2. Sicherheit im Netz
- [ ] Standard: Server lauscht nur auf diesem PC (`127.0.0.1`); Heimnetz-Freigabe bewusst in den Einstellungen einschalten.
- [ ] Bei Heimnetz-Freigabe optional ein Zugangsschutz (Passwort/Token), Hinweis auf die Folgen.
- [ ] API-Schlüssel nicht mehr im Klartext in `data/settings.json` (z. B. Windows-Anmeldeinformationsverwaltung/keyring) oder
      zumindest deutlich darauf hinweisen.
- [ ] Kurzer Sicherheitscheck der API (Pfadprüfungen bei Ordnerdialog/Upload/Dateiauslieferung, Herkunftsschutz).

### 3. Lizenzen sauber trennen
- [x] Lizenz festgelegt (nach v0.51.3): PolyForm Noncommercial 1.0.0 für AstroFlow, AGPL-3.0 für SkyTrace-Engine (`engine/`) und
      -Detektormodell (abgeleitet von Ultralytics YOLO), CC-BY-SA 4.0 für die Katalogdaten. Stände bis v0.51.3 bleiben MIT.
      GitHub zeigt die Lizenz als „Other“. Offen: Modell als getrennter, optionaler Download statt im Repo?
- [ ] `LICENSE`/`NOTICE` mit allen Fremdbestandteilen (ASTAP-Datenbanken, GraXpert GPL-3, StarNet „all rights reserved“ –
      nie mitliefern, Gaia/Open-Meteo/JPL-Nutzungsbedingungen).
- [ ] Abgrenzung der AGPL-Teile (`engine/`, Modell) zum übrigen Code prüfen (der Server importiert Hilfsmodule aus `engine/`).

### 4. Mehr Geräte testen
- [ ] **Vespera** (Nutzer besitzt eine – echte Aufnahmen besorgen): Dateiformat, Ordnerstruktur, Header (Gerät, Belichtung,
      Ort), Mosaik-/Panorama-Modus, Ergebnis gegen das Vespera-Fertigbild vergleichen.
- [ ] **Seestar S50/S30** (größte Nutzergruppe): Beispieldaten aus der Community besorgen, gleiche Prüfung; Seestar-eigene
      Stacks als „fertiges Bild“ erkennen.
- [ ] Klassische Astrokamera (ZWO/QHY, Mono mit Filtern) mit echten Daten durchspielen.
- [ ] `tutor.DEVICES`/`tonight`-Geräteprofile für diese Geräte prüfen (Bildfeld, Grenzhelligkeit, Tipps).

## Sollte vor der Beta erledigt sein
- [ ] Git-**Historie** nach privaten Angaben durchsuchen (Benutzerpfade, Laufwerke, Heimnetz, Standort, Schlüssel) –
      nicht nur den aktuellen Stand; notfalls Historie bereinigen oder neu beginnen.
- [ ] Englische Oberfläche (Texte zentral sammeln, Sprache in den Einstellungen) – die meisten Nutzer sprechen kein Deutsch.
- [ ] Datenschutz-Hinweis: welche Onlinedienste wann genutzt werden (Gaia-TAP, Open-Meteo, JPL Horizons, optionale KI-Anbieter),
      alles abschaltbar.
- [ ] README für Einsteiger: Screenshots, 5-Minuten-Start, Systemanforderungen (RAM, Speicherplatz, optionale GPU).
- [ ] Fehlerberichte einfach machen (Protokoll exportieren, Version, ohne private Pfade).
- [ ] Bekannte Grenzen offen nennen (keine Mosaike, kein Planetenmodus, Satellitenspur-Erkennung unzuverlässig).

## Nach der Beta: Vermarktung
- Orte: Reddit (r/SmartTelescope, r/seestar, r/astrophotography), Cloudy Nights, Stargazers Lounge, deutsche Foren
  (Astrotreff), Astro-YouTuber (z. B. Astrobiscuit – dessen APP→GraXpert→StarNet-Workflow war Vorbild für GraXpert-Einbindung).
- Inhalt: Vorher/Nachher – Fertigbild des Smart-Teleskops gegen AstroFlow, gleiche Rohdaten; kurze Videos zu SkyTrace/3D und Heute Nacht.
- Lizenz: PolyForm Noncommercial 1.0.0 (Quellcode offen, private und nicht-kommerzielle Nutzung frei) – in Beiträgen nicht „Open Source“ nennen.
