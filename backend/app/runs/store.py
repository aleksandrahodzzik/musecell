"""Run event bus and on-disk store (spec §5).

Each run has a `RunBus`: events get a sequence number, are appended to `runs/<run_id>.jsonl` with a timestamp,
kept in memory, and pushed to every subscriber's asyncio queue (SSE streams, the CLI). A subscriber first
receives the events it missed, so a browser that connects late or reconnects (Last-Event-ID) sees the whole run.
A `None` on the queue means the run is over.

An optional guard checks every event before it is stored or sent (the blinding-leak scanner during a blinded
run); a guard that raises keeps the event from going anywhere.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from app.agent.schemas import EventType, RunEvent
from app.config import ROOT

RUNS_DIR = ROOT / "runs"
DEMO_RUNS_DIR = ROOT / "demo" / "runs"


class RunBus:
    def __init__(self, run_id: str, path: Path | None = None):
        self.run_id = run_id
        self.path = path
        self.events: list[RunEvent] = []
        self.finished = False
        self.guard: Callable[[RunEvent], None] | None = None
        self._subscribers: set[asyncio.Queue] = set()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: EventType, data: dict[str, Any] | None = None) -> RunEvent:
        if self.finished:
            raise RuntimeError(f"run {self.run_id} is finished; cannot emit '{type}'")
        event = RunEvent(type=type, run_id=self.run_id, seq=len(self.events) + 1, data=data or {})
        if self.guard is not None:
            self.guard(event)
        self._store(event)
        return event

    def _store(self, event: RunEvent) -> None:
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        for q in self._subscribers:
            q.put_nowait(event)
        if event.type == "run_finished":
            self.close()

    def close(self) -> None:
        if not self.finished:
            self.finished = True
            for q in self._subscribers:
                q.put_nowait(None)

    def subscribe(self, after_seq: int = 0) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for e in self.events:
            if e.seq > after_seq:
                q.put_nowait(e)
        if self.finished:
            q.put_nowait(None)
        else:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    @classmethod
    def from_file(cls, path: Path) -> RunBus:
        """A finished bus holding a recorded run (read-only)."""
        events = [RunEvent.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        bus = cls(events[0].run_id if events else path.stem)
        bus.events = events
        bus.finished = True
        return bus


def recorded_path(run_id: str, dirs: tuple[Path, ...] = (RUNS_DIR, DEMO_RUNS_DIR)) -> Path | None:
    """runs/<id>.jsonl, else demo/runs/<id>.jsonl. Run ids are checked so a path cannot escape the folders."""
    if not run_id.replace("_", "").replace("-", "").isalnum():
        return None
    for d in dirs:
        path = d / f"{run_id}.jsonl"
        if path.exists():
            return path
    return None


def snapshot_path(run_id: str, dirs: tuple[Path, ...] = (RUNS_DIR, DEMO_RUNS_DIR)) -> Path | None:
    if not run_id.replace("_", "").replace("-", "").isalnum():
        return None
    for d in dirs:
        path = d / f"{run_id}.json"
        if path.exists():
            return path
    return None


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
