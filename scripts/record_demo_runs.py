"""Record live demo runs and copy them, with the thumbnails they need, into demo/ (spec §5 "Replay").

Specimens are chosen by phenotype strength, not by outcome: the norm of the compound's robust-z treatment
profile at its strongest annotated dose, one specimen per MoA class, strongest first. Runs are recorded in
that order until there are --n finished runs and at least one of them got "request_experiment" from the PI
reviewer (then the weakest non-request run is dropped), or --max-runs is reached.

demo/runs/ gets each run's events (.jsonl), state (.json) and dossier (.dossier.{md,json}).
demo/thumbnails/ gets composite PNGs, downscaled, within the spec limit (≤ 40 PNGs, ≤ 15 MB): first the first
field of every acquired well, then each well's same-plate DMSO field (the microscope's comparison pane),
then the remaining well fields, taken round-robin across runs. Fields left out show as "not in demo".

Usage:
  python scripts/record_demo_runs.py --dry-run          # show the candidate order, no API calls
  python scripts/record_demo_runs.py [--budget 5] [--n 4] [--max-runs 6]
  python scripts/record_demo_runs.py --copy run_a run_b ...   # only copy already recorded runs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.agent.guardrails import BlindingScanner  # noqa: E402
from app.runs.manager import RunManager  # noqa: E402
from app.runs.store import RUNS_DIR, RunBus  # noqa: E402

DEMO_RUNS = ROOT / "demo" / "runs"
DEMO_THUMBS = ROOT / "demo" / "thumbnails"
MAX_PNGS, MAX_BYTES = 40, 15 * 1024 * 1024
THUMB_WIDTH = 512


def candidates(manager: RunManager) -> list[tuple[str, str, float]]:
    """(specimen_id, moa, strength), one per MoA class, strongest first."""
    ref = manager.atlas.reference
    strength = ref.assign(norm=np.linalg.norm(np.nan_to_num(ref[manager.atlas.features].to_numpy(float)), axis=1))
    best = strength.groupby("compound")["norm"].max()
    by_compound = {sp.compound: sp for sp in manager.catalog.specimens.values()}
    rows = sorted(((by_compound[c].specimen_id, by_compound[c].moa, float(n)) for c, n in best.items()
                   if c in by_compound), key=lambda r: -r[2])
    seen, out = set(), []
    for sid, moa, n in rows:
        if moa not in seen:
            seen.add(moa)
            out.append((sid, moa, n))
    return out


async def record(manager: RunManager, n: int, max_runs: int, budget: int) -> list[str]:
    kept: list[tuple[str, bool]] = []  # (run_id, reviewer requested)
    for i, (sid, _moa, strength) in enumerate(candidates(manager)[:max_runs], start=1):
        if len(kept) >= n and any(r for _, r in kept):
            break
        loop = await manager.create_run(sid, budget, "blinded")
        run = await loop.run_all()
        requested = bool(run.review and run.review.verdict == "request_experiment")
        print(f"[{i}] {run.run_id} {sid} strength {strength:.1f}: {run.status}, review {run.review and run.review.verdict}, "
              f"{'correct' if run.verdict and run.verdict.correct else 'wrong'}, ${run.cost_usd:.3f}", flush=True)
        if run.status != "finished":
            continue
        kept.append((run.run_id, requested))
        if len(kept) > n:  # over by one only when the newest run is the first request: drop a non-request run
            drop = next(k for k in reversed(kept[:-1]) if not k[1])
            kept.remove(drop)
    if not any(r for _, r in kept):
        print("!! no recorded run got a reviewer request within --max-runs")
    return [r for r, _ in kept]


async def leak_check(manager: RunManager, run_id: str) -> int:
    bus = RunBus.from_file(RUNS_DIR / f"{run_id}.jsonl")
    sp = manager.catalog.get(bus.events[0].data["specimen_id"])
    scanner = BlindingScanner(await manager._blinding_terms(sp))
    hits = 0
    for e in bus.events:
        if e.type == "unblinded":
            break
        if e.type not in ("error", "run_finished"):
            hits += len(scanner.scan(e.data))
    return hits


def wanted_fields(run_id: str) -> list[list[str]]:
    """Priority tiers of field ids for one run (see module docstring)."""
    first, dmso, rest = [], [], []
    for line in (RUNS_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["type"] == "tool_result" and e["data"].get("name") == "acquire_well" and e["data"].get("ok"):
            d = e["data"].get("data") or {}
            fields = d.get("field_ids") or []
            first += fields[:1]
            rest += fields[1:]
            dmso += (d.get("dmso_field_ids") or [])[:1]
    return [list(dict.fromkeys(t)) for t in (first, dmso, rest)]


def copy_thumbnails(manager: RunManager, run_ids: list[str]) -> None:
    tiers = [wanted_fields(r) for r in run_ids]
    order: list[str] = []
    for t in range(3):
        for depth in range(max(len(x[t]) for x in tiers)):
            for x in tiers:
                if depth < len(x[t]) and x[t][depth] not in order:
                    order.append(x[t][depth])
    if DEMO_THUMBS.exists():
        shutil.rmtree(DEMO_THUMBS)
    DEMO_THUMBS.mkdir(parents=True)
    total, written = 0, 0
    for fid in order:
        if written >= MAX_PNGS:
            break
        img = Image.open(manager.catalog.field_path(fid, "composite")).convert("RGB")
        img = img.resize((THUMB_WIDTH, round(img.height * THUMB_WIDTH / img.width)), Image.LANCZOS)
        out = DEMO_THUMBS / f"{fid}.composite.png"
        img.save(out, optimize=True)
        if total + out.stat().st_size > MAX_BYTES:
            out.unlink()
            break
        total += out.stat().st_size
        written += 1
    print(f"thumbnails: {written} of {len(order)} wanted fields, {total / 1e6:.1f} MB")


def copy_runs(run_ids: list[str]) -> None:
    DEMO_RUNS.mkdir(parents=True, exist_ok=True)
    for old in DEMO_RUNS.glob("run_*"):
        old.unlink()
    for r in run_ids:
        for suffix in (".jsonl", ".json", ".dossier.md", ".dossier.json"):
            shutil.copy2(RUNS_DIR / f"{r}{suffix}", DEMO_RUNS / f"{r}{suffix}")
    print(f"demo/runs: {', '.join(run_ids)}")


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=4)
    p.add_argument("--max-runs", type=int, default=6)
    p.add_argument("--budget", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--copy", nargs="+", metavar="RUN_ID", help="copy these recorded runs; record nothing")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    manager = RunManager()
    await manager.startup(check_models=not (args.dry_run or args.copy))
    try:
        if manager.catalog is None:
            print(manager.data_error)
            return 1
        if args.dry_run:
            for sid, moa, n in candidates(manager):
                print(f"{sid}  strength {n:6.1f}  ({moa})")
            return 0
        run_ids = args.copy or await record(manager, args.n, args.max_runs, args.budget)
        for r in run_ids:
            hits = await leak_check(manager, r)
            print(f"leak re-scan {r}: {hits} hits")
            if hits:
                print("!! not copying: a recorded run leaks the compound before unblinding")
                return 1
        copy_runs(run_ids)
        copy_thumbnails(manager, run_ids)
        return 0
    finally:
        await manager.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
