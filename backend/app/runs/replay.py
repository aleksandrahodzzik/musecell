"""Replay a recorded real run with its original pacing (spec §5, demo insurance).

The replayed events are the recorded ones, unchanged, re-sent on a new bus. `run_started` gains a `replay`
block (source run id, recording date, model ids) so the UI can show its permanent "Replay of a live run" badge.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app.runs.store import RunBus

MIN_SPEED, MAX_SPEED = 0.25, 20.0
MAX_GAP_S = 60.0  # a single recorded pause longer than this (e.g. a retry backoff) is shortened to it


def replay_info(source: RunBus) -> dict:
    started = next((e for e in source.events if e.type == "run_started"), None)
    return {"replay_of": source.run_id,
            "recorded_at": source.events[0].ts.isoformat() if source.events else None,
            "model_ids": (started.data.get("model_ids") if started else None) or {}}


async def replay(source: RunBus, target: RunBus, speed: float,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    speed = min(MAX_SPEED, max(MIN_SPEED, speed))
    info = replay_info(source)
    prev = None
    for event in source.events:
        if prev is not None:
            await sleep(min(MAX_GAP_S, max(0.0, (event.ts - prev).total_seconds())) / speed)
        prev = event.ts
        data = dict(event.data)
        if event.type == "run_started":
            data["replay"] = info
        target.emit(event.type, data)
    target.close()  # a recording that was cut short still ends the stream
