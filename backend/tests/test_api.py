"""API (spec §5): specimens are opaque, a run streams over SSE to run_finished, run state, replay, images, health.
Also the run bus (late subscribers) and replay pacing."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent.guardrails import BlindingScanner
from app.config import Settings
from app.runs import replay as R
from app.runs.manager import RunManager
from app.runs.store import RunBus
from tests.test_loop import HAPPY, SYNONYMS, ScriptedLLM
from tests.test_tools import CLASSES, COMPOUND, FakeChembl, FakePubChem, FakePubMed, _pc, atlas, catalog  # noqa: F401


class Closable:
    async def aclose(self):
        pass


class SynPubChem(FakePubChem, Closable):
    async def synonyms(self, cid, limit=50):
        return SYNONYMS


class Chembl(FakeChembl, Closable):
    pass


class PubMed(FakePubMed, Closable):
    pass


class ManagerLLM(ScriptedLLM, Closable):
    @property
    def run(self):  # the scripted turns read acquired wells from the live run
        return list(self.manager.runs.values())[-1]

    @run.setter
    def run(self, value):
        pass


@pytest.fixture
def client(catalog, atlas, tmp_path):
    m = RunManager(Settings(_env_file=None), runs_dir=tmp_path)
    m.catalog, m.atlas, m.moa_classes = catalog, atlas, CLASSES
    m.pubchem = SynPubChem({COMPOUND: _pc(1, "AAAAAAAAAAAAAA-XX-N")}, _pc(1, "AAAAAAAAAAAAAA-YY-N"))
    m.chembl, m.pubmed = Chembl(), PubMed()
    m.llm = ManagerLLM(HAPPY)
    m.llm.manager = m
    main.app.state.manager = m
    with TestClient(main.app) as c:
        yield c, m
    main.app.state.manager = None


def sse_events(c, run_id):
    events = []
    with c.stream("GET", f"/api/runs/{run_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_health_and_specimens_are_opaque(client):
    c, m = client
    h = c.get("/api/health").json()
    assert h["live_runs"] and h["specimens"] == 1 and h["model_ids"]["reviewer"] == "muse-wonder-1.0"
    specs = c.get("/api/specimens").json()
    assert specs[0]["specimen_id"].startswith("s_") and specs[0]["label"] == "Specimen 1"
    assert set(specs[0]) == {"specimen_id", "label", "doses", "wells"}
    sp = m.catalog.get(specs[0]["specimen_id"])
    assert BlindingScanner(sp.blinding_terms(SYNONYMS)).scan(specs) == []


def test_run_streams_to_the_end_without_leaking(client):
    c, m = client
    sid = c.get("/api/specimens").json()[0]["specimen_id"]
    assert c.post("/api/runs", json={"specimen_id": sid, "budget": 9}).status_code == 422
    assert c.post("/api/runs", json={"specimen_id": "s_nope", "budget": 3}).status_code == 400
    run_id = c.post("/api/runs", json={"specimen_id": sid, "budget": 3}).json()["run_id"]
    events = sse_events(c, run_id)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished", kinds
    assert events[-1]["data"]["status"] == "finished", events[-1]
    # the blinding-leak test over every SSE payload before the unblinding
    scanner = BlindingScanner(m.catalog.get(sid).blinding_terms(SYNONYMS))
    cut = kinds.index("unblinded")
    assert scanner.scan(events[:cut]) == [] and scanner.scan(events[cut])

    state = c.get(f"/api/runs/{run_id}").json()
    assert state["verdict"]["correct"] and state["status"] == "finished"
    # a late subscriber resuming after event 5 gets the rest
    with c.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "5"}) as r:
        ids = [int(line[4:]) for line in r.iter_lines() if line.startswith("id: ")]
    assert ids[0] == 6 and ids[-1] == len(events)

    rep = c.post(f"/api/runs/{run_id}/replay?speed=20").json()
    assert rep["replay_of"] == run_id and rep["run_id"].startswith("replay_") and rep["model_ids"]
    replayed = sse_events(c, rep["run_id"])
    assert [e["type"] for e in replayed] == kinds and replayed[0]["data"]["replay"]["replay_of"] == run_id
    assert c.post("/api/runs/run_missing/replay").status_code == 404


def test_images(client, catalog):
    c, _ = client
    fid = catalog.index["field_id"].iloc[0]
    r = c.get(f"/api/images/{fid}.composite")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert c.get(f"/api/images/{fid}.secret").status_code == 404
    assert c.get("/api/images/..%2F..%2Fbackend%2F.env").status_code == 404


def test_live_runs_off_without_key(tmp_path):
    m = RunManager(Settings(_env_file=None, model_api_key=None, data_dir=str(tmp_path)), runs_dir=tmp_path)
    asyncio.run(m.startup())
    assert not m.live_enabled and "DATA_DIR" in m.data_error and "MODEL_API_KEY" in m.llm_error


def test_bus_late_subscriber_and_replay_pacing():
    bus = RunBus("r")
    bus.emit("run_started", {"model_ids": {"scientist": "m"}})
    early = bus.subscribe()
    bus.emit("thinking", {"turn": 1})
    bus.emit("run_finished", {"status": "finished"})
    late = bus.subscribe(after_seq=1)
    drain = lambda q: [q.get_nowait() for _ in range(q.qsize())]  # noqa: E731
    assert [e and e.type for e in drain(early)] == ["run_started", "thinking", "run_finished", None]
    assert [e and e.type for e in drain(late)] == ["thinking", "run_finished", None]
    with pytest.raises(RuntimeError):
        bus.emit("thinking")

    from datetime import timedelta
    bus.events[1].ts = bus.events[0].ts + timedelta(seconds=4)
    bus.events[2].ts = bus.events[1].ts + timedelta(seconds=500)
    waits: list[float] = []

    async def fake_sleep(s):
        waits.append(s)

    target = RunBus("rp")
    asyncio.run(R.replay(bus, target, speed=2, sleep=fake_sleep))
    assert waits == [2.0, R.MAX_GAP_S / 2] and target.finished
    assert target.events[0].data["replay"]["replay_of"] == "r"
