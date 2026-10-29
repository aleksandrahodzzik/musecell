"""List finished recorded runs for "Watch a recorded run".

Only what a viewer may see before the replay starts: no compound, no MoA, no verdict (the replay is still a
blinded story). Reads the first and last line of each `runs/*.jsonl` / `demo/runs/*.jsonl`.
"""

from __future__ import annotations

import json
from pathlib import Path


def _first_last(path: Path) -> tuple[dict, dict] | None:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    return json.loads(lines[0]), json.loads(lines[-1])


def list_recorded(dirs: list[tuple[Path, str]], labels: dict[str, str] | None = None) -> list[dict]:
    """dirs: (folder, source) pairs, e.g. (runs/, "local"), (demo/runs/, "demo"). Newest first; ids are unique."""
    out, seen = [], set()
    for folder, source in dirs:
        if not folder.exists():
            continue
        for path in folder.glob("run_*.jsonl"):
            pair = _first_last(path)
            if pair is None:
                continue
            first, last = pair
            if first.get("type") != "run_started" or last.get("type") != "run_finished" or first["run_id"] in seen:
                continue
            seen.add(first["run_id"])
            start, end = first["data"], last["data"]
            out.append({"run_id": first["run_id"], "source": source, "recorded_at": first["ts"],
                        "specimen_id": start.get("specimen_id"),
                        "specimen_label": (labels or {}).get(start.get("specimen_id", "")),
                        "mode": start.get("mode"), "budget_total": start.get("budget_total"),
                        "budget_used": end.get("budget_used"), "status": end.get("status"),
                        "cost_usd": end.get("cost_usd"), "model_ids": start.get("model_ids") or {}})
    return sorted(out, key=lambda r: r["recorded_at"], reverse=True)
