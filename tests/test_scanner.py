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


def test_device_name_from_project_key_hides_serials():
    from astroflow.modules.stacking.scanner import device_name
    assert device_name("ic443|stellina-b7337b|3072x2080") == "Vaonis Stellina"
    assert device_name("ic443|vespera|2160x3840").startswith("Vaonis Vespera")
    assert device_name("KOMET:12p|2026-11-01|seestar s50|1080x1920") == "ZWO Seestar S50"
    assert device_name("m31|unbekannt|100x100") is None and device_name("FELD1|x") is None and device_name(None) is None
    assert "b7337b" not in (device_name("m1|stellina-b7337b|1x1") or "")


def test_projects_show_device_and_other_devices(tmp_path):
    """Gleiches Objekt mit zwei Geräten: je Projekt ein Gerätename und der Hinweis auf das andere (nie eine Seriennummer)."""
    import json
    from fastapi.testclient import TestClient
    from astroflow.core.config import load_settings
    from astroflow.modules.stacking.db import DB
    from astroflow.server import create_app
    s = load_settings(tmp_path / "data")
    with TestClient(create_app(s, only=("stacking",))) as c:
        db = DB(s.db_path)
        for key, name, obj in (("ic443|stellina-b7337b|3072x2080", "IC 443", "IC443"), ("ic443|vespera|2160x3840", "IC 443", "IC443"),
                               ("m31|stellina-b7337b|3072x2080", "M 31", "M31")):
            db.x("INSERT INTO projects(key,name,obj_id,kind,created) VALUES(?,?,?,?,0)", (key, name, obj, "generic"))
        ps = {p["key"] if "key" in p else p["id"]: p for p in c.get("/api/stacking/projects").json()}
        by = sorted(ps.values(), key=lambda p: p["id"])
        assert [p["device"] for p in by] == ["Vaonis Stellina", by[1]["device"], "Vaonis Stellina"] and by[1]["device"].startswith("Vaonis Vespera")
        assert by[0]["other_devices"] == [{"id": by[1]["id"], "device": by[1]["device"]}]
        assert by[1]["other_devices"][0]["id"] == by[0]["id"] and by[2]["other_devices"] == []
        assert "b7337b" not in json.dumps(by)
