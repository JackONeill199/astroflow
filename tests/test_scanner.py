from astroflow.modules.stacking import scanner


def test_stellina_refocus_folder_is_scanned(tmp_path):
    obs = tmp_path / "2022-07-18_21-45-37_observation_NGC6888"
    for sub in ("01-images-initial", "02-images-adjust-focus", "process"):
        (obs / sub).mkdir(parents=True)
        (obs / sub / "img-0001r.fits").write_bytes(b"")
    found = {p.parent.name for p in scanner.list_fits(tmp_path)}
    assert found == {"01-images-initial", "02-images-adjust-focus"}      # Nachfokus-Aufnahmen sind normale Lights


def test_finished_stack_in_observation_folder_is_not_a_light(tmp_path):
    """Astro-Pixel-Processor-Integrationen lagen neben den Stellina-Rohbildern (M17) und wurden als eigene Objekte gestackt."""
    import numpy as np
    from astropy.io import fits
    obs = tmp_path / "2022-06-20_22-32-36_observation_M17"
    (obs / "01-images-initial").mkdir(parents=True)
    raw = fits.PrimaryHDU(np.zeros((20, 30), np.uint16))
    raw.header["OBJECT"] = "M17"
    raw.writeto(obs / "01-images-initial" / "img-0001r.fits")
    app = fits.PrimaryHDU(np.zeros((3, 22, 33), np.float32))
    app.header["SOFTWARE"] = "Astro Pixel Processor by Aries Productions"
    app.header["NUMFRAME"] = 197
    app.writeto(obs / "M17.fits")
    edited = fits.PrimaryHDU(np.zeros((3, 22, 33), np.float32))      # weiterbearbeitet: ohne NUMFRAME, nur Farbwürfel
    edited.writeto(obs / "M17-lpc-cbg.fits")
    frames, errors = scanner.scan_dir(tmp_path, set(), threads=2)
    assert not errors
    kinds = {f.path.split("\\")[-1].split("/")[-1]: f.kind for f in frames}
    assert kinds == {"img-0001r.fits": "light", "M17.fits": "processed", "M17-lpc-cbg.fits": "processed"}
