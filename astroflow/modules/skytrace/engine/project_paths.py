"""Stable paths for source files and local project data, independent of cwd.

In AstroFlow: CODE = Engine-Quellen, ASSETS = Modulordner (Modell, evidence.js/css),
BASE = Datenordner (Ergebnisse, Kataloge, Einstellungen) aus SKYTRACE_HOME."""
import os
from pathlib import Path

CODE = Path(__file__).resolve().parent
ASSETS = CODE.parent
BASE = Path(os.environ.get('SKYTRACE_HOME') or ASSETS/'data').resolve()
