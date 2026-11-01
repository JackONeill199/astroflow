import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests laufen ohne Netz: keine Gaia-Abfragen (die Farbkalibrierung fällt dann auf das Sternmittel zurück)."""
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    monkeypatch.setenv("ASTROFLOW_GRAXPERT", "0")             # nie ein installiertes GraXpert aufrufen
    monkeypatch.setenv("ASTROFLOW_GPU", "0")                  # Stacking auf der CPU (GPU-Vergleich: test_gpu.py)
    monkeypatch.setenv("ASTROFLOW_SAGE_OFFLINE", "1")          # Sage: keine SIMBAD-/arXiv-Abfragen
    # Standard: nur das Stacking-Modul laden; Modultests schalten weitere gezielt dazu
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")
