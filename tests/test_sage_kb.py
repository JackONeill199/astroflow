"""Sage 2.0: Wissensbasis, Research Harness, Validierung, Gedächtnis, Entdeckungen, Chat, Erzählformate.
SIMBAD/arXiv und die KI sind Attrappen – kein Netz."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from astroflow.core.config import load_settings
from astroflow.modules.sage import agent, sources
from astroflow.modules.sage.assistant import calculate
from astroflow.modules.sage.kb import KB, norm
from astroflow.modules.sage.research import validate
from astroflow.server import create_app

M45 = {"oid": 675533, "main_id": "Cl Melotte   22", "otype": "OpC", "ra": 56.6008, "dec": 24.1139, "plx_value": 7.364, "plx_err": 0.02,
       "plx_bibcode": "2018A&A...616A..10G", "sp_type": None, "rvz_redshift": 0.00002, "rvz_radvel": 5.9, "galdim_majaxis": 76.9,
       "galdim_minaxis": 76.9, "morph_type": None, "nbref": 3737}
IDS = ["M  45", "NAME Pleiades", "NAME Seven Sisters", "Cl Melotte   22"]
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2610.01234v1</id><title>Brown dwarfs in the Pleiades</title><summary>We study the Pleiades cluster.</summary>
<published>2026-10-20T00:00:00Z</published><author><name>A. Author</name></author></entry>
<entry><id>http://arxiv.org/abs/2610.09999v2</id><title>Unrelated quasars</title><summary>Nothing about it.</summary>
<published>2026-10-21T00:00:00Z</published><author><name>B. Author</name></author></entry></feed>"""


def tap(rows, cols=None):
    cols = cols or (list(rows[0]) if rows else ["x"])
    return json.dumps({"metadata": [{"name": c} for c in cols], "data": [[r.get(c) for c in cols] for r in rows]})


def fake_fetch(self, url, data=None, params=None, headers=None, ttl=0, binary=False):
    self.calls += 1
    if "arxiv" in url:
        return ATOM
    q = (data or {}).get("QUERY", "")
    if "JOIN ident AS i" in q and "FROM basic AS b" in q:
        if "'M 45'" in q:
            return tap([M45])
        if "'Merope'" in q or "'NGC 1435'" in q:
            return tap([{**M45, "oid": 1, "main_id": "NGC  1435", "otype": "RNe", "plx_value": None}])
        return tap([], list(M45))
    if q.startswith("SELECT id FROM ident"):
        return tap([{"id": i} for i in (IDS if "675533" in q else ["NGC  1435", "NAME Merope Nebula"])])
    if "mesDistance" in q:
        return tap([{"dist": 134.4, "unit": "pc  ", "minus_err": -2.8, "plus_err": 2.9, "method": "paral", "bibcode": "2017A&A...598A..48G"},
                    {"dist": 130, "unit": "pc", "minus_err": None, "plus_err": None, "method": "", "bibcode": "2005A&A...438.1163K"}])
    if "h_link" in q and "b.oid=h.parent" in q:
        return tap([{"main_id": "NAME Pleiades Moving Group", "otype": "MGr", "membership": 91, "link_bibcode": "2025A&A...699L...5S"}])
    if "h_link" in q:
        return tap([{"main_id": "* eta Tau", "otype": "Be*", "name": "NAME Alcyone", "membership": 100, "link_bibcode": "2001A&A...373..159C"}])
    if "has_ref" in q:
        return tap([{"bibcode": "2024A&A...100A...1X", "title": "The Pleiades revisited", "yr": 2024, "journal": "A&A", "doi": None,
                     "abstract": "Gaia view of the Pleiades.", "obj_freq": 40}])
    if "FROM flux" in q:
        return tap([{"filter": "V", "flux": 1.6, "flux_err": None, "bibcode": "2002yCat.2237....0D"}])
    if "CONTAINS(POINT" in q:
        return tap([{"main_id": "NGC  2805", "otype": "G", "ra": 1, "dec": 2, "galdim_majaxis": 6.3, "nbref": 100},
                     {"main_id": "M  81", "otype": "Sy2", "ra": 1, "dec": 2, "galdim_majaxis": 21.4, "nbref": 4000}])
    return tap([])


class ObsLog:
    def __call__(self):
        s = lambda night, n, exp: {"night": night, "frames": n, "used": n - 2, "exposure_s": n * exp, "used_s": (n - 2) * exp,
                                   "instruments": ["stellina-a1b2c3"], "filters": [], "fwhm_px": 5.5 + n / 1000, "first": None, "last": None}
        return [{"id": 16, "name": "M45 Plejaden", "obj_id": "M45", "kind": "reflection", "ra": 56.85, "dec": 24.12, "created": 1,
                 "sessions": [s("2025-01-03", 200, 10), s("2025-12-20", 300, 10), s("2026-10-25", 548, 10)],
                 "results": [{"id": 1, "created": 1, "n_frames": 198, "exposure_s": 1980, "score": 8, "snr": 12.0},
                             {"id": 3, "created": 1.5, "n_frames": 198, "exposure_s": 1980, "score": 9, "snr": 12.0},
                             {"id": 2, "created": 2, "n_frames": 1040, "exposure_s": 10400, "score": 10, "snr": 25.7}]}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROFLOW_MODULES", "stacking,sage")
    monkeypatch.setenv("ASTROFLOW_SAGE_OFFLINE", "0")
    monkeypatch.setattr(sources.Net, "fetch", fake_fetch)
    app = create_app(load_settings(tmp_path))
    app.state.ctx.services["observation_log"] = ObsLog()
    with TestClient(app) as c:
        c.app_mod = app.state.modules["sage"]
        yield c


def wait(c, tid, timeout=30):
    # großzügig nach Uhrzeit: auf ausgelasteten CI-Rechnern (xdist) laufen Start-Verknüpfung und Recherchen parallel
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        t = c.get(f"/api/sage/kb/tasks/{tid}").json()
        if t["state"] != "running":
            return t
        time.sleep(0.03)
    raise AssertionError("Auftrag hängt")


def test_kennungen_normieren():
    assert norm("M  45") == norm("M45") == norm("Messier 45") == "m 45"
    assert norm("NGC  1976") == "ngc 1976" and norm("NAME Pleiades") == "pleiades" and norm("Sh2-101") == "sh2 101"


def test_validierung_belege_und_widersprueche():
    art = {"sections": [{"key": "overview", "body": "Die Plejaden sind rund 444 Lichtjahre entfernt [1].\n\n"
                                                    "Ein langer Absatz ganz ohne Beleg, der trotzdem viel behauptet und deshalb markiert werden muss.\n\n"
                                                    "Manche nennen 1.350 Lichtjahre [2] [7]."}]}
    sec = validate(art, 2, 443.0)[0]
    types = [f["type"] for f in sec["flags"]]
    assert "unknown_source" in types and "uncited" in types and "conflict" in types
    assert "[7]" not in sec["body"] and sec["cites"] == [1, 2]


def test_rechner():
    assert abs(calculate("444 ly * tan(1)")["ergebnis"] - 444 * 0.017455) < 0.01
    assert abs(calculate("1 pc")["ergebnis"] - 3.26156) < 1e-6
    with pytest.raises(Exception):
        calculate("__import__('os')")


def test_recherche_katalog_und_seite(client):
    t = wait(client, client.post("/api/sage/kb/research", json={"name": "M45", "deep": False}).json()["id"])
    assert t["state"] == "done" and t["entity"] == "m45"
    d = client.get("/api/sage/kb/entities/m45").json()
    e = d["entity"]
    assert e["name"] == "M45 Plejaden" and e["otype_label"] == "Offener Sternhaufen" and e["constellation"] == "Taurus"
    dist = next(f for f in d["facts"] if f["key"] == "distance_ly")
    assert 440 < dist["num"] < 446 and dist["origin"] == "derived" and dist["status"] == "current" and "Parallaxe" in dist["note"]
    assert sum(f["key"] == "distance" for f in d["facts"]) == 2                       # jede Messung mit eigenem Beleg
    rels = {(x["rel"], x["other"]["id"]) for x in d["edges"]}
    assert ("Mitglied von", "pleiadesmovinggroup") in rels and ("ist ein", "k-offener-haufen") in rels
    assert ("zeigt", "k-reflexionsnebel") in rels and any(x["other"]["name"] == "Alcyone" for x in d["edges"])
    assert d["papers"][0]["title"] == "The Pleiades revisited"
    assert [p["title"] for p in d["preprints"]] == ["Brown dwarfs in the Pleiades"]       # Unpassendes aussortiert
    assert d["sections"] == [] and "443 Lichtjahre" in e["summary"]                     # ohne KI: nur Katalogwissen
    # persönliches Gedächtnis aus echten Aufnahmedaten
    texts = " ".join(x["text"] for x in d["mine"]["derived"])
    assert "dreimal aufgenommen" in texts and "2026-10-25" in texts and "12 auf 26 gestiegen" in texts
    assert client.get("/api/sage/kb/entities", params={"q": "Plejaden"}).json()["items"][0]["id"] == "m45"
    assert client.get("/api/sage/kb/entities", params={"q": "Seven Sisters"}).json()["items"][0]["id"] == "m45"
    # Grundeinträge aus der Hierarchie ohne eigenes Bild: nicht im Raster, aber über die Suche
    lst = client.get("/api/sage/kb/entities").json()
    assert "m45" in [i["id"] for i in lst["items"]] and "pleiadesmovinggroup" not in [i["id"] for i in lst["items"]]
    assert lst["stats"]["hidden_stubs"] >= 1
    assert "pleiadesmovinggroup" in [i["id"] for i in client.get("/api/sage/kb/entities", params={"q": "Pleiades Moving"}).json()["items"]]
    # Dashboard: Helligkeit, berechneter Durchmesser, Bezeichnungen, Fokus, Himmelskarte, Export
    facts = {f["key"]: f for f in d["facts"] if f["status"] != "alternative"}
    assert facts["mag"]["value"] == "1,6 mag (V)" and 9 < facts["diameter"]["num"] < 11 and facts["diameter"]["origin"] == "derived"
    assert d["designations"][0] == "M45" and "Pleiades" in d["designations"]
    assert client.get("/api/sage/kb/featured").json()[0]["id"] == "m45"
    home = client.get("/api/sage/kb/home").json()                     # Startseite: Fokus mit Aufnahmedaten, Himmel heute Nacht
    f0 = home["featured"][0]
    assert f0["id"] == "m45" and f0["nights"] >= 2 and "443" in f0["distance"]
    tn = home["tonight"]
    assert tn["assumed"] and tn["lat"] == 50.0 and len(tn["items"]) <= 8
    assert all(t["alt"] >= 30 and 0 <= t["transit"] < 24 for t in tn["items"])
    sm = client.get("/api/sage/kb/skymap/m45").json()
    assert sm["center"]["id"] == "m45" and any(o["id"] == "m45" for o in sm["objects"])
    # zwei Versionen aus verschiedenen Frame-Mengen = zwei Aufnahmen; dieselbe neu entwickelt bliebe eine
    assert [g["key"] for g in d["gallery"]] == ["r2", "r3"] and d["gallery"][0]["nights"][-1] == "2026-10-25"
    md = client.get("/api/sage/kb/entities/m45/export.md")
    assert md.status_code == 200 and md.text.startswith("# M45 Plejaden") and "## Quellen" in md.text and "SIMBAD" in md.text


def test_tiefe_recherche_mit_agent(client, monkeypatch):
    client.post("/api/sage/config", json={"story_provider": "lmstudio", "budget_month_usd": 5})
    calls = []

    def chat(self, messages, tools=None, max_tokens=3000, json_mode=False, timeout=240):
        calls.append(bool(tools))
        self.kb.log_usage("test", 100, 50, 0.001, False, self.run_id, self.purpose)
        self.cost += 0.001
        if len(calls) == 1:
            return {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "paper_details", "arguments": '{"n": 2}'}}]}
        return {"content": json.dumps({"summary": "Ein junger Sternhaufen [1].", "sections": [
            {"key": "overview", "title": "Überblick", "body": "Die Plejaden sind ein junger Haufen [1].\n\nSie sind 1.350 Lichtjahre entfernt [2]."},
            {"key": "open_questions", "title": "Offene Fragen", "body": "Die genaue Entfernung war lange umstritten und wird bis heute diskutiert, ohne Beleg."}],
            "relations": [{"name": "NGC 1435", "rel": "beleuchtet", "sources": [1], "reason": "Merope-Nebel"}], "uncertainties": []})}

    monkeypatch.setattr(agent.LLM, "chat", chat)
    t = wait(client, client.post("/api/sage/kb/research", json={"name": "M45"}).json()["id"])
    assert t["state"] == "done", t["error"]
    d = client.get("/api/sage/kb/entities/m45").json()
    secs = {s["key"]: s for s in d["sections"]}
    assert secs["overview"]["status"] == "draft" and secs["overview"]["origin"] == "ai"
    assert "[S" in secs["overview"]["body"] and "[1]" not in secs["overview"]["body"]           # globale Quellen-IDs
    assert any(f["type"] == "conflict" for f in secs["overview"]["flags"])
    assert any(f["type"] == "uncited" for f in secs["open_questions"]["flags"])
    assert any(x["status"] == "suggested" and x["origin"] == "ai" and x["rel"] == "beleuchtet" for x in d["edges"])
    assert d["entity"]["status"] == "researched" and d["runs"][0]["state"] == "done"
    assert any(s.get("tool") == "paper_details" for s in d["runs"][0]["steps"])
    u = client.get("/api/sage/kb/usage").json()
    assert u["calls"] == 2 and u["cost"] > 0
    # prüfen + neue Version durch den Nutzer
    sid = secs["overview"]["id"]
    client.patch(f"/api/sage/kb/sections/{sid}", json={"body": "Eigene Fassung [S1]."})
    hist = client.get(f"/api/sage/kb/sections/{sid}/history").json()
    assert [h["origin"] for h in hist][:2] == ["user", "ai"] and hist[0]["version"] == 2


def test_budget_stoppt_ki(client, monkeypatch):
    client.post("/api/sage/config", json={"story_provider": "lmstudio", "budget_month_usd": 0.01})
    client.app_mod.kb.log_usage("x", 1, 1, 0.02, False, None, "test")
    r = client.post("/api/sage/kb/chat", json={"question": "Was ist M45?"})
    assert r.status_code == 402 and "Monatsbudget" in r.json()["detail"]


def test_chat_mit_werkzeugen(client, monkeypatch):
    client.app_mod.backfill_thread.join(30)          # Start-Verknüpfung fertig – sie konkurriert sonst um die Datenbank
    wait(client, client.post("/api/sage/kb/research", json={"name": "M45", "deep": False}).json()["id"])
    client.post("/api/sage/config", json={"story_provider": "lmstudio"})
    seen = {}

    def chat(self, messages, tools=None, **kw):
        if messages[-1]["role"] == "tool":
            seen["tool"] = json.loads(messages[-1]["content"])
            sid = next(f["quelle"] for f in seen["tool"]["fakten"] if f["wert"] == "distance_ly")
            return {"content": f"Die Plejaden sind etwa 443 Lichtjahre entfernt [S{sid}]. Du hast sie dreimal aufgenommen (Deine Daten)."}
        return {"content": "", "tool_calls": [{"id": "a", "function": {"name": "get_entry", "arguments": '{"id": "m45"}'}}]}

    monkeypatch.setattr(agent.LLM, "chat", chat)
    r = client.post("/api/sage/kb/chat", json={"question": "Wie weit weg?", "entity": "m45", "scope": "entity:m45"}).json()
    assert r["steps"][0]["tool"] == "get_entry" and r["sources"] and "(Deine Daten)" in r["answer"]
    assert len(client.get("/api/sage/kb/chat", params={"scope": "entity:m45"}).json()) == 2


def test_chat_belege_nur_aus_dem_lauf(client, monkeypatch):
    """Erfundene Quellenverweise zählen nicht als Beleg; fehlgeschlagene Frage bleibt nicht im Verlauf (#30, #33)."""
    client.post("/api/sage/config", json={"story_provider": "lmstudio"})
    other = client.app_mod.kb.add_source("test:x", "catalog", "Fremde Quelle")

    def chat(self, messages, tools=None, **kw):
        if messages[-1]["role"] == "tool":
            return {"content": f"Erfunden [S{other}]. Rechnung (Interpretation)."}
        return {"content": "", "tool_calls": [{"id": "a", "function": {"name": "calculate", "arguments": '"1+1"'}}]}
    monkeypatch.setattr(agent.LLM, "chat", chat)
    r = client.post("/api/sage/kb/chat", json={"question": "Wie viel?", "scope": "t"}).json()
    assert not r["sources"] and f"[S{other}]" not in r["answer"]
    assert r["steps"][0]["args"] == {}                # Argumente kein Objekt → leer statt AttributeError

    def kaputt(self, messages, tools=None, **kw):
        raise agent.clients.ServiceError("weg")
    monkeypatch.setattr(agent.LLM, "chat", kaputt)
    client.post("/api/sage/kb/chat", json={"question": "Noch mal?", "scope": "t"})
    assert [m["content"] for m in client.get("/api/sage/kb/chat", params={"scope": "t"}).json() if m["role"] == "user"] == ["Wie viel?"]


def test_werkzeuglimit_und_think():
    from astroflow.modules.sage.clients import strip_think
    assert strip_think("<think>a</think>Hallo<think>b</think> Welt") == "Hallo Welt"
    assert strip_think("<think>" * 20000 + "x").endswith("x")
    n = []

    class L:
        def __init__(self):
            self.i = 0

        def chat(self, msgs, tools, **kw):
            self.i += 1
            if self.i == 1:
                return {"tool_calls": [{"id": str(k), "function": {"name": "research_object", "arguments": '{"name": "M1"}'}} for k in range(5)]}
            return {"content": "fertig"}
    t = agent.tool("research_object", "x", lambda name: n.append(name) or {"quelle": 1}, name=("string", "x"))
    text, steps = agent.run_agent(L(), "s", "u", [t])
    assert text == "fertig" and len(n) == agent.TOOL_LIMITS["research_object"]
    assert [s["ok"] for s in steps] == [True] * 3 + [False] * 2 and steps[0]["sources"] == [1]


def test_notizen_karte_netz_entdeckungen(client):
    wait(client, client.post("/api/sage/kb/research", json={"name": "M45", "deep": False}).json()["id"])
    n = client.post("/api/sage/kb/notes", json={"entity_id": "m45", "kind": "plan", "title": "Mehr Nebel", "date": "2026-11-15"}).json()
    assert client.patch(f"/api/sage/kb/notes/{n['id']}", json={"body": "Mit 30 s belichten"}).json()["body"] == "Mit 30 s belichten"
    assert len(client.get("/api/sage/kb/notes", params={"entity": "m45"}).json()) == 1
    # Wissenskarte: unbekanntes Objekt aus dem lokalen Katalog wird sofort angelegt
    k = client.get("/api/sage/kb/card", params={"name": "M42 Orionnebel"}).json()
    assert k["found"] and k["id"] == "m42" and k["status"] == "stub"
    assert client.get("/api/sage/kb/card", params={"name": "M45"}).json()["mine"][0]["text"].startswith("Du hast")
    # Wissensnetz mit persönlicher Kette: Objekt → Aufnahme → Session → Gerät, Bearbeitung
    g = client.get("/api/sage/kb/graph", params={"center": "m45"}).json()
    ids = {x["id"] for x in g["nodes"]}
    assert {"proj:16", "ver:16", "eq:stellina-a1b2c3", "k-offener-haufen", "k-staub"} <= ids
    assert any(lk["rel"] == "Beobachtungssession" for lk in g["links"])
    assert client.delete(f"/api/sage/kb/notes/{n['id']}").json()["ok"]


def test_proaktive_entdeckungen(client):
    mod = client.app_mod
    wait(client, client.post("/api/sage/kb/research", json={"name": "M45", "deep": False}).json()["id"])
    # Bildbestimmung mit Bildfeld → Kegelsuche findet weitere Galaxien
    mod.store.add({"source": {"type": "upload"}, "preview_file": None, "width": 100, "height": 80, "title": "M81-Feld",
                   "solution": {"ra": 148.9, "dec": 69.07, "fov_w": 1.0, "fov_h": 0.7}, "objects": [{"name": "M81 Bodes Galaxie", "kind_label": "Galaxie"}]})
    from astroflow.modules.sage.research import Researcher
    Researcher(mod).ensure_stub("M81 Bodes Galaxie")
    mod.kb.add_alias("m81", "M 81")
    t = wait(client, client.post("/api/sage/kb/insights/run", json={"only": ["field", "papers", "region"]}).json()["id"])
    assert t["state"] == "done", t["error"]
    ins = client.get("/api/sage/kb/insights").json()
    kinds = {i["kind"]: i for i in ins["items"]}
    assert "eine weitere katalogisierte Galaxie" in kinds["field"]["title"] and "NGC 2805" in kinds["field"]["body"]
    assert kinds["research"]["title"] == "Neue Forschung zu M45 Plejaden" and kinds["research"]["sources"]
    assert ins["report"]["tasks"]["field"]["ok"]
    assert client.get("/api/status").json()["modules"]["sage"]["insights"] >= 2
    client.patch(f"/api/sage/kb/insights/{kinds['field']['id']}", json={"dismissed": True})
    assert all(i["kind"] != "field" for i in client.get("/api/sage/kb/insights").json()["items"])
    # zweiter Lauf meldet nichts doppelt
    wait(client, client.post("/api/sage/kb/insights/run", json={"only": ["field", "papers"]}).json()["id"])
    assert len(client.get("/api/sage/kb/insights", params={"include_dismissed": True}).json()["items"]) == len(ins["items"])


def test_audiofuehrung_ohne_ki(client):
    mod = client.app_mod
    e = mod.store.add({"source": {"type": "upload"}, "preview_file": "uploads/x.jpg", "width": 100, "height": 80, "title": "Orion",
                       "solution": {"ra": 83.8, "dec": -5.4}, "objects": [{"name": "M42 Orionnebel", "kind_label": "Emissionsnebel", "x": 30, "y": 20},
                                                                         {"name": "M43", "kind_label": "Emissionsnebel", "x": 40, "y": 10}]})
    mod.link_entry(e)
    assert client.post("/api/sage/kb/entities/m42/stories", json={"format": "doku"}).status_code == 400      # ohne KI nur die Führung
    s = client.post("/api/sage/kb/entities/m42/stories", json={"format": "tour"}).json()
    assert s["model"] == "Vorlage (ohne KI)" and [x["object"] for x in s["segments"]][1:3] == ["M42 Orionnebel", "M43"]
    assert s["image"]["entry"] == e["id"]
    assert client.get("/api/sage/kb/entities/m42").json()["stories"][0]["id"] == s["id"]


def test_kb_versionen_und_suche(tmp_path):
    kb = KB(tmp_path / "k.db")
    kb.upsert_entity("m1", name="M1 Krebsnebel", otype_label="Supernova-Überrest")
    kb.add_alias("m1", "NGC 1952")
    a = kb.put_section("m1", "overview", "Ü", "Pulsar im Zentrum [S1]")
    b = kb.put_section("m1", "overview", "Ü", "Neuer Text über den Pulsar")
    assert [s["id"] for s in kb.sections("m1")] == [b] and len(kb.sections("m1", history=True)) == 2
    assert kb.search("pulsar")[0]["id"] == "m1" and kb.resolve("ngc1952")["id"] == "m1"
    assert kb.add_insight("k", "x", "t", "b") and kb.add_insight("k", "x", "t", "b") is None
