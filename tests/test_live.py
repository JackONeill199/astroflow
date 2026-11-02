"""Live-Vorschauen: geringe Auflösung, unveränderte Bilddaten, Fehlerisolation und Job-Zuordnung."""
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.stacking import live, objects, processing
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.server import create_app


def test_preview_is_small_atomic_and_keeps_input(tmp_path):
    run = live.LiveRun(tmp_path, 1, job=12)
    img = np.full((1, 1600, 2400), 12.0, np.float32)
    img[:, 300:600, 800:1200] = 80
    before = img.copy()
    run.event('Stacking', '10 Aufnahmen')
    run.picture(img, n=10, exposure=100, force=True)
    state = live.read_state(tmp_path, 1)
    assert state['job'] == 12 and state['n'] == 10
    assert Image.open(run.path / state['preview']).size == (800, 534)
    np.testing.assert_array_equal(img, before)
    for _ in range(3):
        run.picture(img, force=True)
    assert len(list(run.path.glob('*.jpg'))) == 2
    assert not list(run.path.glob('*.tmp'))
    run.event('Bearbeitung', 'Stretch')
    assert run.data['steps'][-2]['done'] and not run.data['steps'][-1]['done']
    run.event('Fertig')
    assert run.finished and run.data['steps'][-1]['done']


def test_preview_cannot_change_render_or_abort_it():
    rng = np.random.default_rng(7)
    img = rng.normal(1000, 4, (3, 96, 128)).astype(np.float32)
    img[:, 35:45, 55:75] += 70
    cov = np.ones((96, 128), np.float32)
    params = {**objects.profile_for('generic'), 'star_separation': 'off', 'deconv': 0}
    before = img.copy()
    expected, _ = processing.process(img, cov, params)
    seen = []

    def broken_preview(a, linear=True):
        seen.append((a.shape, linear))
        raise OSError('Vorschau nicht schreibbar')

    actual, _ = processing.process(img, cov, params, preview=broken_preview)
    assert any(not linear for _, linear in seen)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(img, before)


def test_progress_stays_monotone_but_cancel_propagates(tmp_path):
    s = load_settings(tmp_path)
    pl = Pipeline(s, DB(s.db_path))
    values = []
    run, prog = pl._track_live(1, lambda p, *_: values.append(p))
    prog(.9, 'Bearbeitung', 'Stretch')
    prog(.8, 'Bearbeitung', 'Qualitätsrunde 2')
    assert values == [.9, .9]

    def cancel(*args):
        raise InterruptedError('angehalten')

    _, prog = pl._track_live(1, cancel)
    with pytest.raises(InterruptedError):
        prog(.95, 'Speichern')


def test_live_api_isolates_projects_and_jobs(tmp_path):
    s = load_settings(tmp_path)
    s.auto = False
    db = DB(s.db_path)
    pid = db.x("INSERT INTO projects(name, created) VALUES('Testnebel',0)")
    jid = db.x("INSERT INTO jobs(project_id,state,created) VALUES(?,'running',0)", (pid,))
    run = live.LiveRun(s.work_dir, pid, job=jid)
    run.picture(np.ones((3, 20, 30), np.float32), force=True)
    # Ohne Lifespan: kein Worker verarbeitet den künstlichen Auftrag.
    c = TestClient(create_app(s))
    state = c.get(f'/api/stacking/projects/{pid}').json()['live']
    assert state['job'] == jid
    url = f"/api/stacking/projects/{pid}/live/{state['preview']}"
    response = c.get(url)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.headers['cache-control'] == 'no-store'
    assert c.get(f"/api/stacking/projects/{pid + 1}/live/{state['preview']}").status_code == 404
    assert c.get(f'/api/stacking/projects/{pid}/live/state.json').status_code == 404
    db.x("INSERT INTO jobs(project_id,state,created) VALUES(?,'queued',1)", (pid,))
    assert c.get(f'/api/stacking/projects/{pid}').json()['live'] == {}
    c.close()
