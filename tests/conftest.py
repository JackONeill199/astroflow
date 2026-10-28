import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests laufen ohne Netz: keine Gaia-Abfragen (die Farbkalibrierung fällt dann auf das Sternmittel zurück)."""
    monkeypatch.setenv("ASTROFLOW_GAIA", "0")
    # Standard: nur das Stacking-Modul laden; Modultests schalten weitere gezielt dazu
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking")
