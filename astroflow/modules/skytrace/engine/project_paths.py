# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR AGPL-3.0-or-later
# Copyright 2026 JackONeill199 – Doppellizenz (wahlweise), siehe engine/LICENSE und NOTICE.
"""Stable paths for source files and local project data, independent of cwd.

In AstroFlow: CODE = Engine-Quellen, ASSETS = Modulordner (Modell, evidence.js/css),
BASE = Datenordner (Ergebnisse, Kataloge, Einstellungen) aus SKYTRACE_HOME."""
import os
from pathlib import Path

CODE = Path(__file__).resolve().parent
ASSETS = CODE.parent
BASE = Path(os.environ.get('SKYTRACE_HOME') or ASSETS/'data').resolve()
