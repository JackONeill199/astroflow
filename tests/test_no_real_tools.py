"""Schutz: in der Testsuite startet nie ein echtes GraXpert/StarNet (Regression 04.11.2026: Dutzende Prozesse, RAM voll)."""
import subprocess
import sys

import pytest

from astroflow.modules.stacking import graxpert


def test_graxpert_is_disabled_for_whole_session():
    import os
    assert os.environ["ASTROFLOW_GRAXPERT"] == "0" and graxpert.graxpert_path() is None


@pytest.mark.parametrize("name", ["GraXpert.exe", "starnet2.exe", "StarNet++"])
def test_real_programs_cannot_be_started(name):
    with pytest.raises(RuntimeError, match="echtes Programm"):
        subprocess.run([r"C:\Programme\\" + name, "-v"])


def test_fakes_in_tmp_are_allowed(tmp_path):
    fake = tmp_path / "graxpert_fake.py"
    fake.write_text("print('ok')", encoding="utf-8")
    assert subprocess.run([sys.executable, str(fake)], capture_output=True, text=True).stdout.strip() == "ok"
