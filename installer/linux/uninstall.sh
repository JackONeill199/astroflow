#!/bin/sh
# AstroFlow entfernen. Deine Daten (~/AstroFlow) und Einstellungen (~/.config/astroflow) bleiben erhalten.
#   ./uninstall.sh              Programm, Befehl, Menüeintrag und Autostart entfernen
#   ./uninstall.sh --werkzeuge  zusätzlich ASTAP, GraXpert und Zusatzpakete (GPU, SkyTrace-Engine), soweit AstroFlow sie installiert hat
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$DIR/python/bin/python3" -I "$DIR/linux_install.py" uninstall --prefix "$DIR" "$@"
