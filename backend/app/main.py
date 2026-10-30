"""FastAPI app (spec §5, §7): runs, SSE events, specimens, replay, stop, dossier, images, field segmentation,
eval files, health; serves frontend/dist at /.

Run with:  python -m uvicorn app.main:app --app-dir backend --port 8787
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import fields
from app.agent.schemas import RunMode
from app.config import MAX_BUDGET_WELLS, ROOT
from app.runs.manager import RunManager, RunRequestError
from app.runs.recorded import list_recorded
from app.runs.store import DEMO_RUNS_DIR, RunBus, snapshot_path

HEARTBEAT_S = 15.0
IMAGE_ID = re.compile(r"^(f_[0-9a-f]{6,64})\.(composite|dna|tubulin|actin)$")
FIELD_ID = re.compile(r"^f_[0-9a-f]{6,64}$")
EVAL_FILES = {"results": ROOT / "eval" / "results.json", "baseline": ROOT / "eval" / "baseline.json"}
DEMO_THUMBS = ROOT / "demo" / "thumbnails"
DEMO_LABELS = ROOT / "demo" / "specimen_labels.json"  # opaque specimen id -> "Specimen N" (no compound, dose or MoA)
FRONTEND_DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = getattr(app.state, "manager", None) or RunManager()
    if not getattr(app.state, "manager", None):
        await manager.startup()
    app.state.manager = manager
    yield
    await manager.aclose()


app = FastAPI(title="MuseCell", lifespan=lifespan)


def mgr(request: Request) -> RunManager:
    return request.app.state.manager


class RunRequest(BaseModel):
    specimen_id: str
    budget: int = Field(4, ge=1, le=MAX_BUDGET_WELLS)
    mode: RunMode = "blinded"
    provider: str | None = None  # None = PROVIDER from backend/.env; see /api/health "providers"


@app.get("/api/health")
async def health(request: Request) -> dict:
    m = mgr(request)
    return {"ok": True, "live_runs": m.live_enabled, "data": m.catalog is not None, "data_error": m.data_error,
            "model_error": m.llm_error, "model_ids": m.model_ids(), "specimens": len(m.specimen_list()),
            "providers": m.provider_list(), "provider_notes": m.provider_notes}


@app.get("/api/specimens")
async def specimens(request: Request) -> list[dict]:
    return mgr(request).specimen_list()


@app.post("/api/runs")
async def create_run(body: RunRequest, request: Request) -> dict:
    try:
        run_id = await mgr(request).start_run(body.specimen_id, body.budget, body.mode, body.provider)
    except RunRequestError as exc:
        raise HTTPException(status_code=400 if mgr(request).live_enabled else 503, detail=str(exc)) from None
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict:
    m = mgr(request)
    if run_id in m.runs:
        return m.runs[run_id].model_dump(mode="json")
    run_id = m.source_run_id(run_id)
    path = snapshot_path(run_id, (m.runs_dir, DEMO_RUNS_DIR))
    if path is None:
        raise HTTPException(status_code=404, detail=f"No run '{run_id}'.")
    return json.loads(path.read_text(encoding="utf-8"))


async def _sse(bus: RunBus, after: int) -> AsyncIterator[str]:
    q = bus.subscribe(after)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                return
            yield f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
    finally:
        bus.unsubscribe(q)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = Query(0, ge=0)):
    """SSE. Resumes after the browser's Last-Event-ID header (or ?after=seq)."""
    bus = mgr(request).bus(run_id)
    if bus is None:
        raise HTTPException(status_code=404, detail=f"No run '{run_id}'.")
    header = request.headers.get("last-event-id", "")
    after = int(header) if header.isdigit() else after
    return StreamingResponse(_sse(bus, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/runs/{run_id}/replay")
async def replay_run(run_id: str, request: Request, speed: float = Query(1.0, gt=0, le=20)) -> dict:
    try:
        return await mgr(request).start_replay(run_id, speed)
    except RunRequestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@app.post("/api/runs/{run_id}/stop")
async def stop_run(run_id: str, request: Request) -> dict:
    try:
        await mgr(request).stop(run_id)
    except RunRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"stopped": run_id}


@app.get("/api/runs/{run_id}/dossier.{fmt}")
async def dossier(run_id: str, fmt: str, request: Request):
    """The dossier files the loop wrote (spec §8 screen 4: export Markdown + JSON)."""
    if fmt not in ("md", "json"):
        raise HTTPException(status_code=404, detail="Dossier formats: md, json.")
    m = mgr(request)
    source = m.source_run_id(run_id)
    if not source.replace("_", "").isalnum():
        raise HTTPException(status_code=404, detail=f"No run '{run_id}'.")
    for folder in (m.runs_dir, DEMO_RUNS_DIR):
        path = folder / f"{source}.dossier.{fmt}"
        if path.exists():
            media = "text/markdown; charset=utf-8" if fmt == "md" else "application/json"
            return FileResponse(path, media_type=media, filename=f"musecell_{source}.dossier.{fmt}")
    raise HTTPException(status_code=404, detail="No dossier for this run yet. It is written when the run finishes.")


@app.get("/api/recorded")
async def recorded(request: Request) -> list[dict]:
    """Finished recorded runs that can be replayed; no compound, MoA or verdict."""
    m = mgr(request)
    labels = {s["specimen_id"]: s["label"] for s in m.specimen_list()}
    if not labels and DEMO_LABELS.exists():  # replay-only server: the labels shipped with the demo runs
        labels = json.loads(DEMO_LABELS.read_text(encoding="utf-8"))
    return list_recorded([(DEMO_RUNS_DIR, "demo"), (m.runs_dir, "local")], labels)


@app.get("/api/gallery")
async def gallery() -> list[str]:
    """Composite image ids shipped in demo/thumbnails (real BBBC021 fields; opaque ids, no compound or dose)."""
    return sorted(p.name.removesuffix(".png") for p in DEMO_THUMBS.glob("f_*.composite.png"))


@app.get("/api/fields/{field_id}/cells")
async def field_cells(field_id: str, request: Request) -> dict:
    m = mgr(request)
    if not FIELD_ID.match(field_id) or m.catalog is None:
        raise HTTPException(status_code=404, detail="No segmentation for this field on this machine.")
    table = fields.cell_table(m.settings.cells_dir, field_id, m.reference)
    if table is None:
        raise HTTPException(status_code=404, detail="No segmentation for this field on this machine.")
    return table


@app.get("/api/fields/{field_id}/labels")
async def field_labels(field_id: str, request: Request):
    """Half-resolution label maps, gzip([cells | nuclei] uint16 LE); size in X-Label-Width / X-Label-Height."""
    m = mgr(request)
    maps = fields.label_maps(m.settings.cells_dir, field_id) if FIELD_ID.match(field_id) and m.catalog else None
    if maps is None:
        raise HTTPException(status_code=404, detail="No segmentation for this field on this machine.")
    body, w, h = maps
    return Response(body, media_type="application/octet-stream",
                    headers={"Content-Encoding": "gzip", "X-Label-Width": str(w), "X-Label-Height": str(h),
                             "Access-Control-Expose-Headers": "X-Label-Width, X-Label-Height",
                             "Cache-Control": "public, max-age=86400"})


@app.get("/api/eval/{name}")
async def eval_file(name: str):
    """eval/results.json (Phase 6, scripts/evaluate.py) and eval/baseline.json, as written by the scripts."""
    path = EVAL_FILES.get(name)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=f"eval/{name}.json does not exist yet.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/images/{image_id}")
async def image(image_id: str, request: Request):
    match = IMAGE_ID.match(image_id)
    if not match:
        raise HTTPException(status_code=404, detail="Unknown image id.")
    field_id, kind = match.groups()
    catalog = mgr(request).catalog
    if catalog is not None and field_id in catalog.index.index:
        path = catalog.field_path(field_id, kind)
    else:
        path = DEMO_THUMBS / f"{image_id}.png"  # recorded demo runs carry their own thumbnails
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not available.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
