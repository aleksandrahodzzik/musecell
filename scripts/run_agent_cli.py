"""Run one blinded specimen in the terminal: pretty-print the run's events, then the verdict and the cost.

Same code path as the API (RunManager → AgentLoop); events also go to runs/<run_id>.jsonl, the run state to
runs/<run_id>.json and the dossier to runs/<run_id>.dossier.{md,json}.

Usage:
  python scripts/run_agent_cli.py --list
  python scripts/run_agent_cli.py --specimen s_… [--budget 4] [--mode blinded|open] [--summary out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.agent.schemas import RunEvent  # noqa: E402
from app.runs.manager import RunManager  # noqa: E402

WIDTH = 110


def wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(" ".join(str(text).split()), WIDTH, initial_indent=indent, subsequent_indent=indent)


def show(e: RunEvent) -> None:
    d, t = e.data, e.type
    if t == "run_started":
        print(f"run {e.run_id} · specimen {d['specimen_id']} · {d['mode']} · budget {d['budget_total']} wells · "
              f"{d['doses']} doses · models {d['model_ids']}")
    elif t == "thinking":
        print(f"\n[turn {d['turn']} · {d['phase']}]" if "note" not in d else wrap(f"(says) {d['note']}"))
    elif t == "tool_call":
        print(f"  → {d['name']}({json.dumps(d['args'], ensure_ascii=False)[:160]})")
        print(wrap(f"Why: {d['rationale']}"))
    elif t == "tool_result":
        mark = "✓" if d["ok"] else "✗"
        print(wrap(f"{mark} {d['evidence_id'] or ''} {d['summary']}  (${d['cost_usd']:.4f})"
                   if d["ok"] else f"{mark} error: {d['error']}"))
    elif t == "hypotheses_updated":
        print(wrap("board: " + " · ".join(f"{h['moa']} {h['probability']:.2f}" for h in d["hypotheses"])))
    elif t == "budget_updated":
        print(f"      budget {d['budget_used']}/{d['budget_total']} wells used")
    elif t == "conclusion":
        print(f"\n== conclusion{' (revised)' if d['revision'] else ''}: {d['top_moa']} p={d['probability']:.2f} "
              f"· key evidence {d['key_evidence_ids']}")
        print(wrap(f"next real-lab experiment: {d['next_experiment_if_this_were_a_real_lab']}"))
    elif t == "review":
        print(f"\n== PI review ({d['model']}): {d['verdict']}")
        print(wrap(d["critique"]))
        if d.get("requested_experiment"):
            print(wrap(f"requested: {d['requested_experiment']}"))
    elif t == "protocol_call":
        print(f"
== protocol call (NSC over {d['wells']} wells): {d['moa']} ({d['votes']}/{d['wells']}) · "
              f"Scientist {'agrees' if d['agrees'] else 'dissents: ' + str(d['scientist_moa'])}"
              f"{' · this is the final call' if d.get('final') else ''}")
    elif t == "unblinded":
        mechs = "; ".join(m["mechanism_of_action"] for m in d["chembl_mechanisms"]) or "no curated mechanism in ChEMBL"
        print(f"\n== unblinded: {d['compound']} · PubChem CID {d['pubchem_cid']} · ChEMBL {d.get('chembl_id')}: {mechs}")
        print(wrap(f"identity check: {d['identity_check']}"))
    elif t == "verdict":
        print(f"== verdict (code): {'CORRECT' if d['correct'] else 'WRONG'} · agent {d['agent_moa']} · "
              f"truth {d['truth_moa']}")
    elif t == "report_ready":
        r = d.get("reflection") or {}
        print(f"\n== reflection · {d['citations_label']} (removed: {d['citations_removed'] or 'none'})")
        print(wrap(r.get("text", ""), "   "))
        print(f"   dossier: {d['files']}")
    elif t == "error":
        print(f"\n!! {'fatal ' if d.get('fatal') else ''}error: {d['message']}")
    elif t == "run_finished":
        print(f"\n== run finished: {d['status']} · {d['turns']} Scientist turns · {d['budget_used']} wells · "
              f"${d['cost_usd']:.4f}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--specimen", help="opaque specimen id (see --list)")
    parser.add_argument("--budget", type=int, default=4)
    parser.add_argument("--mode", choices=["blinded", "open"], default="blinded")
    parser.add_argument("--list", action="store_true", help="list specimen ids and exit")
    parser.add_argument("--summary", type=Path, help="append a one-line JSON summary of the run to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    manager = RunManager()
    await manager.startup(check_models=not args.list)
    try:
        if args.list:
            for s in manager.specimen_list():
                print(f"{s['specimen_id']}  {s['label']:<12} {s['doses']} doses, {s['wells']} wells")
            return 0
        if not args.specimen:
            parser.error("--specimen is required (see --list)")
        loop = await manager.create_run(args.specimen, args.budget, args.mode)
        q = loop.bus.subscribe()
        task = asyncio.create_task(loop.run_all())
        while (event := await q.get()) is not None:
            show(event)
        run = await task

        calls: dict[str, list[float]] = {}
        for c in run.model_calls:
            key = f"{c.purpose.split(' turn')[0]} ({c.model})"
            calls.setdefault(key, []).append(c.cost_usd)
        print("\ncost by role:")
        for k, v in calls.items():
            print(f"  {k:<36} {len(v):>3} calls  ${sum(v):.4f}")
        print(f"  total ${run.cost_usd:.4f}; today so far ${manager.llm.ledger.total_today():.4f}")
        if args.summary:
            line = {"run_id": run.run_id, "specimen_id": run.specimen_id, "status": run.status, "error": run.error,
                    "budget": [run.budget_used, run.budget_total], "turns": run.turns,
                    "conclusions": [(c.top_moa, c.probability) for c in run.conclusion_history],
                    "review": run.review.verdict if run.review else None,
                    "compound": run.unblinding.compound if run.unblinding else None,
                    "verdict": run.verdict.model_dump() if run.verdict else None,
                    "citations": [c.pmid for c in run.reflection.citations] if run.reflection else [],
                    "cost_usd": round(run.cost_usd, 4)}
            with args.summary.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        return 0 if run.status == "finished" else 1
    finally:
        await manager.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
