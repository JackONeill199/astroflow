#!/bin/sh
# AstroFlow für Linux installieren bzw. aktualisieren – nur für den eigenen Nutzer, ohne root/sudo.
#   ./install.sh               Programm nach ~/.local/share/astroflow/app, Befehl „astroflow“, Menüeintrag, danach starten
#   ./install.sh --autostart   zusätzlich beim Anmelden im Hintergrund starten
#   ./install.sh --no-start    nicht sofort starten;  --no-desktop: kein Menüeintrag
# Ein Update ist dasselbe: neues Paket entpacken und install.sh erneut ausführen (Daten, Einstellungen, Zusatzpakete bleiben).
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$DIR/python/bin/python3" -I "$DIR/linux_install.py" install "$@"
