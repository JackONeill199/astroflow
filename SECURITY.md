# Sicherheit

## Lücke melden

Bitte Sicherheitslücken **nicht** als öffentliches Issue melden, sondern vertraulich über GitHub:
**Security → Report a vulnerability** ([direkt](https://github.com/JackONeill199/astroflow/security/advisories/new)).

Hilfreich sind: betroffene Version (Einstellungen → Module & System), Betriebssystem, Schritte zum Nachstellen und die
mögliche Auswirkung. Bitte keine echten Schlüssel, Pfade oder Standorte mitschicken.

Wir melden uns in der Regel innerhalb einer Woche. AstroFlow ist ein Freizeitprojekt – Korrekturen kommen als neue
Version (siehe `CHANGELOG.md`), betroffene Meldende werden auf Wunsch genannt.

## Unterstützte Versionen

Nur die jeweils neueste Version bekommt Sicherheitskorrekturen.

## Was AstroFlow annimmt

- AstroFlow ist für **einen Nutzer auf dem eigenen Rechner** gebaut: keine Konten, keine Anmeldung. Der Server lauscht
  standardmäßig nur auf `127.0.0.1`.
- Wer „Im Heimnetz erreichbar“ einschaltet, gibt die Oberfläche für das lokale Netz frei (Windows-Firewall-Regel nur für
  das private Subnetz). Andere Geräte brauchen eine einmalige Kopplung per QR-Code (Geräte-Token als Cookie). Nicht ins
  Internet weiterleiten – es gibt kein https.
- Der `Host`-Kopf muss ein bekannter Name sein (localhost, IP-Adresse, Rechnername). Schreibende Anfragen brauchen eine
  `Origin` (bzw. `Referer`) mit derselben Adresse.
- Programmpfade, Komponenten, Heimnetz, Datenordner und Beenden gehen nur vom eigenen Rechner aus, auch für gekoppelte Geräte.
- Heruntergeladene Werkzeuge (ASTAP, GraXpert) werden gegen feste SHA-256-Prüfsummen geprüft und nur über https geladen.
- API-Schlüssel (z. B. für einen KI-Prüfer) stehen im Klartext in `settings.json` im Datenordner.

Lücken in diesen Grenzen (z. B. Umgehen des Herkunftsschutzes, Pfad-Traversal über die API, Zugriff auf Dateien außerhalb
des Datenordners) sind ausdrücklich willkommen.
