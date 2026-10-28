from astroflow.modules.stacking import scanner


def test_stellina_refocus_folder_is_scanned(tmp_path):
    obs = tmp_path / "2022-07-18_21-45-37_observation_NGC6888"
    for sub in ("01-images-initial", "02-images-adjust-focus", "process"):
        (obs / sub).mkdir(parents=True)
        (obs / sub / "img-0001r.fits").write_bytes(b"")
    found = {p.parent.name for p in scanner.list_fits(tmp_path)}
    assert found == {"01-images-initial", "02-images-adjust-focus"}      # Nachfokus-Aufnahmen sind normale Lights
