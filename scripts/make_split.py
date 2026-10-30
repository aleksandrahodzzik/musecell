"""Freeze a dev / test split of the 38 MoA-annotated compounds (written before agent v4 was explored).

Agent v2/v3 were designed after reading runs on all 38 compounds, so their accuracy is not held out. From v4 on,
changes are explored on `dev` only (offline analyses and any trial runs) and `test` is looked at once, in the
final eval. Stratified by MoA class: within each class the compounds are shuffled with a fixed seed and dealt
alternately, so every class with >= 2 compounds is in both halves. Output: eval/split.json (committed).

Usage: python scripts/make_split.py
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.runs.manager import RunManager  # noqa: E402

SEED = 20261030


async def main() -> int:
    out = ROOT / "eval" / "split.json"
    if out.exists():
        print(f"{out.relative_to(ROOT)} exists; a frozen split is never regenerated.")
        return 1
    manager = RunManager()
    await manager.startup(check_models=False)
    specimens = sorted(manager.catalog.specimens.values(), key=lambda s: s.specimen_id)
    rng = random.Random(SEED)
    by_moa: dict[str, list] = {}
    for sp in specimens:
        by_moa.setdefault(sp.moa, []).append(sp)
    dev, test = [], []
    turn = 0
    for moa in sorted(by_moa):
        group = by_moa[moa]
        rng.shuffle(group)
        for sp in group:  # deal alternately; the starting half alternates across classes to balance sizes
            (dev if turn % 2 == 0 else test).append(sp.specimen_id)
            turn += 1
    split = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": SEED,
             "note": "dev: explore agent changes; test: look once, in the final eval. Specimen ids are opaque.",
             "dev": sorted(dev), "test": sorted(test)}
    out.write_text(json.dumps(split, indent=1), encoding="utf-8")
    print(f"dev {len(dev)}, test {len(test)} -> {out.relative_to(ROOT)}")
    for moa in sorted(by_moa):
        ids = {sp.specimen_id for sp in by_moa[moa]}
        print(f"  {moa:<28} dev {len(ids & set(dev))}  test {len(ids & set(test))}")
    await manager.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
