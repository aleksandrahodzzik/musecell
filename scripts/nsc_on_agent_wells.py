"""Where does the agent lose accuracy? Post-hoc analysis of the logged eval runs, no API calls.

For every logged run (eval/agent_runs.jsonl, run state in runs/eval/<run_id>.json) it compares the agent's final
call with the NSC protocol applied to exactly the wells the agent chose to acquire (1-NN per well against the
leave-one-compound-out atlas, majority vote, ties by summed similarity), and counts how often the agent followed
that vote and whether overriding it was ever right. Grouped by Scientist model, agent version and budget.

This is how agent v2/v3 was found (docs/DECISIONS.md, "Agent v2/v3"); it reads the same 38 compounds the eval
scores, so its numbers describe these runs, they are not a held-out estimate.

Usage: python scripts/nsc_on_agent_wells.py   (needs DATA_DIR and the gitignored run logs in runs/eval/)
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.runs.manager import RunManager  # noqa: E402


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    manager = RunManager()
    await manager.startup(check_models=False)
    if manager.catalog is None:
        print(manager.data_error)
        return 1
    atlas, catalog = manager.atlas, manager.catalog
    wells = {w.well_id: (sp, w) for sp in catalog.specimens.values() for w in sp.wells}
    stats: dict[tuple, Counter] = defaultdict(Counter)
    for line in (ROOT / "eval" / "agent_runs.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        path = ROOT / "runs" / "eval" / f"{r.get('run_id')}.json"
        if not path.exists():
            continue
        acquired = [wells[w] for w in json.loads(path.read_text(encoding="utf-8"))["acquired_well_ids"] if w in wells]
        s = stats[(r["models"]["scientist"], r["models"].get("agent", "v1"), r["budget"])]
        s["runs"] += 1
        s["agent_right"] += r["correct"]
        if not acquired:
            continue
        votes: dict[str, list[float]] = defaultdict(list)
        for sp, w in acquired:
            hit = atlas.neighbors(catalog.profile(w.well_key), 1, exclude_compound=sp.compound)[0]
            votes[hit.moa].append(hit.similarity)
        nsc = max(votes, key=lambda m: (len(votes[m]), sum(votes[m])))
        s["nsc_right"] += nsc == r["truth_moa"]
        if r.get("agent_moa"):
            s["followed"] += r["agent_moa"] == nsc
            s["override_right"] += r["agent_moa"] != nsc and r["correct"]
            s["override_lost"] += r["agent_moa"] != nsc and nsc == r["truth_moa"]
    print("| Scientist | Agent | Budget | Runs | Agent right | NSC on the agent's wells right | Followed NSC | "
          "Override right | Override lost a right NSC call |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (model, ver, b), s in sorted(stats.items()):
        print(f"| `{model}` | {ver} | {b} | {s['runs']} | {s['agent_right']} | {s['nsc_right']} | {s['followed']} | "
              f"{s['override_right']} | {s['override_lost']} |")
    await manager.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
