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


def test_ui_endpoints_recorded_dossier_eval_stop(client):
    c, m = client
    sid = c.get("/api/specimens").json()[0]["specimen_id"]
    run_id = c.post("/api/runs", json={"specimen_id": sid, "budget": 3}).json()["run_id"]
    sse_events(c, run_id)

    # the recorded list carries no compound, MoA or verdict (a replay is still a blinded story)
    rec = [r for r in c.get("/api/recorded").json() if r["run_id"] == run_id]
    assert len(rec) == 1 and rec[0]["specimen_label"] == "Specimen 1" and rec[0]["status"] == "finished"
    assert "verdict" not in rec[0]
    assert BlindingScanner(m.catalog.get(sid).blinding_terms(SYNONYMS)).scan(rec) == []

    md = c.get(f"/api/runs/{run_id}/dossier.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert c.get(f"/api/runs/{run_id}/dossier.json").json()
    assert c.get(f"/api/runs/{run_id}/dossier.exe").status_code == 404
    assert c.get("/api/runs/run_missing/dossier.md").status_code == 404

    assert c.get("/api/eval/secrets").status_code == 404
    assert c.post(f"/api/runs/{run_id}/stop").status_code == 409  # already finished

    # stopping a replay ends its stream with a visible error and run_finished
    m.buses[run_id].events[1].ts = m.buses[run_id].events[0].ts.replace(year=2100)  # a long recorded pause
    rep = c.post(f"/api/runs/{run_id}/replay?speed=1").json()["run_id"]
    assert c.post(f"/api/runs/{rep}/stop").json() == {"stopped": rep}
    tail = [e["type"] for e in sse_events(c, rep)]
    assert tail[-2:] == ["error", "run_finished"]


def test_field_segmentation_endpoints(client, tmp_path):
    import gzip

    import numpy as np
    import pandas as pd

    c, m = client
    fid = m.catalog.index["field_id"].iloc[0]
    assert c.get(f"/api/fields/{fid}/cells").status_code == 404  # nothing cached yet
    m.settings.data_dir = str(tmp_path / "data")
    cells_dir = m.settings.cells_dir
    cells_dir.mkdir(parents=True)
    labels = np.zeros((8, 10), np.uint16)
    labels[2:6, 2:6] = 1
    np.savez_compressed(cells_dir / f"{fid}.npz", nuclei=labels, cells=labels)
    row = {"label": 1, "centroid_x": 3.5, "centroid_y": 3.5, "touches_border": False, "nuc_in_cluster": False,
           **{k: 1.0 for k in ["nuc_area", "nuc_dna_mean", "nuc_dna_integrated", "nuc_solidity", "cell_area",
                               "cell_roundness", "tub_mean", "act_mean", "act_tub_ratio"]}}
    pd.DataFrame([row]).to_parquet(cells_dir / f"{fid}.parquet")
    table = c.get(f"/api/fields/{fid}/cells").json()
    assert table["width"] == 10 and table["cells"][0]["x"] == 0.35 and table["cells"][0]["label"] == 1
    r = c.get(f"/api/fields/{fid}/labels")
    w, h = int(r.headers["x-label-width"]), int(r.headers["x-label-height"])
    raw = r.content if len(r.content) == 2 * 2 * w * h else gzip.decompress(r.content)
    assert (w, h) == (5, 4) and np.frombuffer(raw, "<u2").reshape(2, h, w)[0, 1, 1] == 1
    assert c.get("/api/fields/..%2Fx/cells").status_code == 404


def test_gallery_lists_servable_demo_composites(client):
    c, _ = client
    ids = c.get("/api/gallery").json()
    assert ids and all(main.IMAGE_ID.match(i) and i.endswith(".composite") for i in ids)
    assert c.get(f"/api/images/{ids[0]}").headers["content-type"] == "image/png"


def test_provider_switch_per_run(client):
    c, m = client
    sid = c.get("/api/specimens").json()[0]["specimen_id"]
    r = c.post("/api/runs", json={"specimen_id": sid, "budget": 3, "provider": "baseten"})
    assert r.status_code == 400 and "not available" in r.json()["detail"]  # no Baseten key in this fixture
    m.providers = ["meta", "baseten"]
    providers = {p["provider"]: p for p in c.get("/api/health").json()["providers"]}
    assert providers["baseten"]["model_ids"]["scientist"] == "meta-models/muse-spark-open-1026"
    assert providers["baseten"]["model_ids"]["reviewer"] == "muse-wonder-1.0"  # mixed setup, explicit
    run_id = c.post("/api/runs", json={"specimen_id": sid, "budget": 3, "provider": "baseten"}).json()["run_id"]
    assert m.runs[run_id].model_ids["provider"] == "baseten"
    sse_events(c, run_id)
