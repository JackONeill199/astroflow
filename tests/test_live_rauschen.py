"""„Das Bild entsteht aus dem Rauschen“: echte Zwischenstände des Stacks mit fester Streckung, im Hintergrund gerechnet."""
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from astroflow.core.config import load_settings
from astroflow.modules.stacking import demo, live
from astroflow.modules.stacking.db import DB
from astroflow.modules.stacking.pipeline import Pipeline
from astroflow.modules.stacking.reviewer import ReviewerConfig
from astroflow.modules.stacking.stacking import StackState
from astroflow.server import create_app


def _frames(rng, b=12, shape=(3, 120, 160)):
    sky = np.full(shape, 100, np.float32)
    sky[:, 50:54, 70:74] += 400                                   # ein „Stern“
    X = sky[None] + rng.uniform(-35, 35, (b, *shape)).astype(np.float32)   # gleichverteilt: kein Wert über 3σ → kein Clipping
    return X, np.ones((b, *shape[1:]), bool), np.ones(b, np.float32)


def test_schedule_dense_at_start_then_sparse():
    s = live.SCHEDULE
    assert s[:7] == [1, 2, 3, 4, 5, 6, 8]
    assert all(b > a for a, b in zip(s, s[1:]))
    assert s[s.index(45) + 1] >= 1.4 * 45                          # später je ×1,5
    assert live.stack_targets(3, 10) == [4, 5, 6, 8, 10]


def test_cumulative_prefixes_equal_per_k_sums():
    """Laufende Summen = frühere Rechnung je k (gleiche Clipping-Maske aus dem ganzen Stapel). Wo das Clipping die
    ersten k Aufnahmen eines Pixels verwirft (hier Aufnahme 1 an zwei Pixeln: 126 gegen ~80 in den übrigen), hat der
    Zwischenstand dort kein Gewicht – das war schon immer so und ist kein Fehler der Summen."""
    from astroflow.modules.stacking.stacking import clip_batch
    rng = np.random.default_rng(2)
    X, V, w = _frames(rng)
    st = StackState(X.shape[1:])
    st.add_batch(X, V, w, prefixes=[1, 4, 12])
    keep = clip_batch(X, V)                                        # leerer Stack: keine Prüfung gegen den Stack
    for k in (1, 4, 12):
        m, c = st.snaps[k]
        sw = keep[:k].sum(0)
        np.testing.assert_allclose(m, np.where(keep[:k], X[:k], 0).sum(0) / np.maximum(sw, 1e-9), rtol=1e-5, atol=1e-3)
        np.testing.assert_array_equal(c, sw.min(0))
    assert int((st.snaps[1][1] == 0).sum()) >= 1                  # diese Daten haben solche Pixel
    np.testing.assert_allclose(st.result()[0], st.snaps[12][0], rtol=1e-5, atol=1e-3)


def test_lone_holes_filled_edges_stay_black(tmp_path):
    a = np.full((3, 32, 48), 50.0, np.float32)
    covered = np.ones((32, 48), bool)
    covered[:, :16] = False                                        # Bildrand ohne Daten (zwei JPEG-Blöcke breit)
    covered[12, 30] = False                                        # einzelne Lücke (alle ersten Aufnahmen verworfen)
    a[:, ~covered] = 0
    out = live.fill_holes(a, covered)
    assert np.allclose(out[:, 12, 30], 50) and np.all(out[:, :, :15] == 0)
    run = live.LiveRun(tmp_path, 1)
    run.stack_frame(a, n=1, cov=covered.astype(np.float32))
    pic = np.asarray(Image.open(run.path / run.data['frames'][0]['name']), np.float32).mean(-1)[::-1]
    assert pic[12, 30] > 20 and pic[12, 3] < 5                     # kein schwarzer Punkt, Rand bleibt schwarz


def test_mini_snapshots_match_full_prefixes_on_sampled_pixels(monkeypatch):
    monkeypatch.setattr(live, "SIDE", 40)                          # erzwingt Schritt 4
    rng = np.random.default_rng(3)
    X, V, w = _frames(rng)
    X[3, :, 10, 10] += 5000                                        # Ausreißer: wird in beiden gleich verworfen
    full = StackState(X.shape[1:])
    full.add_batch(X, V, w, prefixes=[2, 5])
    snaps = live.mini_snapshots(live.mini_inputs(StackState(X.shape[1:]), X, V, w), [2, 5])
    for k in (2, 5):
        np.testing.assert_allclose(snaps[k][0], full.snaps[k][0][:, ::4, ::4], rtol=1e-5, atol=1e-3)


def test_fixed_stretch_shows_noise_sinking(tmp_path):
    rng = np.random.default_rng(4)
    X, V, w = _frames(rng, b=16)
    run = live.LiveRun(tmp_path, 1, job=3)
    for k in (1, 4, 16):
        run.stack_frame(X[:k].mean(0), n=k, exposure=10 * k)
    params = run.stretch['']
    f = run.data['frames']
    assert [x['n'] for x in f] == [1, 4, 16] and f[0]['noise'] == 1.0
    assert f[1]['noise'] < 0.6 and f[2]['noise'] < 0.32              # ~1/√n
    pics = [np.asarray(Image.open(run.path / x['name']), np.float32).mean(-1) for x in f]
    sky = [float(np.median(p)) for p in pics]
    assert max(sky) - min(sky) < 12                                # gleiche Kurve: Himmel bleibt gleich hell …
    spread = [float(np.std(p[:40, :60])) for p in pics]
    assert spread[2] < 0.5 * spread[0]                             # … aber sein Rauschen sinkt sichtbar
    assert run.stretch[''] is params


def test_frames_capped_and_previews_keep_frames(tmp_path):
    rng = np.random.default_rng(5)
    X, V, w = _frames(rng, b=2)
    run = live.LiveRun(tmp_path, 1)
    for k in range(live.FRAMES_KEEP + 3):
        run.stack_frame(X[0], n=k + 1)
    names = [x['name'] for x in run.data['frames']]
    assert len(names) == live.FRAMES_KEEP and all((run.path / n).is_file() for n in names)
    assert len(list(run.path.glob('*-f*.jpg'))) == live.FRAMES_KEEP   # ältere Zwischenstände gelöscht
    for _ in range(3):
        run.picture(X[0], force=True)                               # Endbearbeitung räumt nur ihre Vorschauen auf
    assert all((run.path / n).is_file() for n in names)
    assert run.data['n'] == live.FRAMES_KEEP + 3                    # Vorschau ohne n überschreibt nichts


def test_pipeline_frames_from_noise(tmp_path):
    s = load_settings(tmp_path / "data")
    s.auto = False
    db = DB(s.db_path)
    pl = Pipeline(s, db, ReviewerConfig())
    demo.generate(s.input_dir / "demo", n_lights=12, n_darks=2, n_flats=2)
    pl.scan()
    pid = db.one("SELECT project_id FROM frames WHERE kind='light' GROUP BY project_id ORDER BY COUNT(*) DESC")["project_id"]
    run = pl.begin_live(pid, job=1)
    pl.process(pid)
    run.wait()
    f = run.data['frames']
    ns = [x['n'] for x in f]
    assert ns[:3] == [1, 2, 3] and ns == sorted(ns)                 # anfangs fast jeder Schritt
    assert f[-1]['noise'] is not None and f[-1]['noise'] < 0.8      # Rauschen sinkt mit dem echten Stack
    c = TestClient(create_app(s))
    jid = db.x("INSERT INTO jobs(project_id,state,created) VALUES(?,'running',0)", (pid,))
    run.data['job'] = jid
    run._write()
    state = c.get(f"/api/stacking/projects/{pid}").json()['live']
    assert [x['name'] for x in state['frames']] == [x['name'] for x in f]
    r = c.get(f"/api/stacking/projects/{pid}/live/{f[0]['name']}")
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    c.close()
