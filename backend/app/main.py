"""FastAPI app (spec §5, §7): runs, SSE events, specimens, replay, images, health; serves frontend/dist at /.

Run with:  python -m uvicorn app.main:app --app-dir backend --port 8787
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.schemas import RunMode
from app.config import MAX_BUDGET_WELLS, ROOT
from app.runs.manager import RunManager, RunRequestError
from app.runs.store import RunBus, snapshot_path

HEARTBEAT_S = 15.0
IMAGE_ID = re.compile(r"^(f_[0-9a-f]{6,64})\.(composite|dna|tubulin|actin)$")
DEMO_THUMBS = ROOT / "demo" / "thumbnails"
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


@app.get("/api/health")
async def health(request: Request) -> dict:
    m = mgr(request)
    return {"ok": True, "live_runs": m.live_enabled, "data": m.catalog is not None, "data_error": m.data_error,
            "model_error": m.llm_error, "model_ids": m.model_ids(), "specimens": len(m.specimen_list())}


@app.get("/api/specimens")
async def specimens(request: Request) -> list[dict]:
    return mgr(request).specimen_list()


@app.post("/api/runs")
async def create_run(body: RunRequest, request: Request) -> dict:
    try:
        run_id = await mgr(request).start_run(body.specimen_id, body.budget, body.mode)
    except RunRequestError as exc:
        raise HTTPException(status_code=400 if mgr(request).live_enabled else 503, detail=str(exc)) from None
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict:
    m = mgr(request)
    if run_id in m.runs:
        return m.runs[run_id].model_dump(mode="json")
    bus = m.bus(run_id)
    if bus is not None and bus.events and bus.events[0].data.get("replay"):
        run_id = bus.events[0].data["replay"]["replay_of"]
    path = snapshot_path(run_id, (m.runs_dir, ROOT / "demo" / "runs"))
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
