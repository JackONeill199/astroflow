import pytest


@pytest.fixture(scope="session", autouse=True)
def _safe_session():
    """Schutz für die ganze Sitzung – auch für Modul-/Sitzungs-Fixtures, die vor `_offline` laufen (z. B. der Server in
    test_ui_layout.py). Ohne das rief dessen Aufbau ein installiertes GraXpert auf; mit pytest -n auto liefen so Dutzende
    GraXpert-Prozesse gleichzeitig, der RAM lief voll und der Rechner stürzte ab (04.11.2026)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ASTROFLOW_GRAXPERT", "0")
        mp.setenv("ASTROFLOW_GPU", "0")
        mp.setenv("ASTROFLOW_GAIA", "0")
        mp.setenv("ASTROFLOW_SAGE_OFFLINE", "1")
        yield


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests laufen ohne Netz: keine Gaia-Abfragen (die Farbkalibrierung fällt dann auf das Sternmittel zurück)."""
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    monkeypatch.setenv("ASTROFLOW_GRAXPERT", "0")             # nie ein installiertes GraXpert aufrufen
    monkeypatch.setenv("ASTROFLOW_GPU", "0")                  # Stacking auf der CPU (GPU-Vergleich: test_gpu.py)
    monkeypatch.setenv("ASTROFLOW_SAGE_OFFLINE", "1")          # Sage: keine SIMBAD-/arXiv-Abfragen
    # Standard: nur das Stacking-Modul laden; Modultests schalten weitere gezielt dazu
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")
