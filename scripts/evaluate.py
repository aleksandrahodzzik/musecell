"""Benchmark: MuseCell agent vs the NSC baseline, compound-level MoA accuracy under LOCO (spec §6).

Methods
  (a) NSC 1-NN on all wells (published protocol: treatment profiles, majority vote per compound)
  (b) NSC 1-NN on the same number of wells drawn at random from the specimen (vote over wells; mean of R draws)
  (c) MuseCell agent (Scientist + Microscopist + PI reviewer, full run incl. unblinding/literature) at budgets 2/4/6

Steps
  1. Baselines (no API) and a cost estimate from the token use of recorded runs. Without --yes it stops here.
  2. Agent runs, `--concurrency` at a time. Each finished run appends one line to eval/agent_runs.jsonl,
     so an interrupted eval resumes where it stopped. Run logs go to runs/eval/ (gitignored).
  3. eval/results.json (contract in docs/DECISIONS.md, Phase 5), eval/RESULTS.md, eval/accuracy_vs_budget.png.
     Numbers are written by this script only.

Usage
  python scripts/evaluate.py                                   # baselines + estimate, no API calls
  python scripts/evaluate.py --model muse-spark-1.3-contributor --yes
  python scripts/evaluate.py --model muse-spark-1.3-contributor --aggregate-only
      # rebuild the outputs from eval/agent_runs.jsonl; only runs of --model are used (default muse-spark-1.3)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.agent.guardrails import BlindingScanner  # noqa: E402
from app.agent.specimens import Specimen  # noqa: E402
from app.config import PRICES, Settings  # noqa: E402
from app.llm import cost_of  # noqa: E402
from app.runs.manager import RunManager  # noqa: E402
from app.runs.store import RunBus  # noqa: E402
from app.science.atlas import compound_level, nsc_baseline  # noqa: E402

EVAL = ROOT / "eval"
RUNS_LOG = EVAL / "agent_runs.jsonl"
EVAL_RUNS_DIR = ROOT / "runs" / "eval"
BUDGETS = (2, 4, 6)
RECORDED = ("run_87e2787d1f1e", "run_39e2d32f1edc", "run_93391a0830e0")  # Phase-4 runs, budget 4


# --- specimens and baselines ----------------------------------------------------------------------

def stratified(specimens: list[Specimen], n: int | None, seed: int) -> list[Specimen]:
    """All specimens, or n of them taking one per MoA class in turn (classes shuffled with the seed)."""
    if n is None or n >= len(specimens):
        return specimens
    rng = random.Random(seed)
    by_moa: dict[str, list[Specimen]] = {}
    for sp in specimens:
        by_moa.setdefault(sp.moa, []).append(sp)
    for group in by_moa.values():
        rng.shuffle(group)
    order, picked = sorted(by_moa), []
    while len(picked) < n:
        for moa in order:
            if by_moa[moa] and len(picked) < n:
                picked.append(by_moa[moa].pop())
    return sorted(picked, key=lambda s: s.specimen_id)


def nsc_all_wells(atlas, compounds: set[str]) -> float:
    comp = compound_level(nsc_baseline(atlas))
    comp = comp[comp["compound"].isin(compounds)]
    return float((comp["moa"] == comp["predicted"]).mean())


def nsc_random_wells(manager: RunManager, specimens: list[Specimen], k: int, draws: int, seed: int) -> dict:
    """1-NN per randomly drawn well, majority vote (ties: summed similarity); accuracy per draw."""
    atlas, catalog = manager.atlas, manager.catalog
    nn: dict[str, tuple[str, float]] = {}
    for sp in specimens:
        for w in sp.wells:
            hit = atlas.neighbors(catalog.profile(w.well_key), 1, exclude_compound=sp.compound)[0]
            nn[w.well_key] = (hit.moa, hit.similarity)
    rng = random.Random(seed)
    accs = []
    for _ in range(draws):
        correct = 0
        for sp in specimens:
            wells = rng.sample(sp.wells, min(k, len(sp.wells)))
            votes: dict[str, list[float]] = {}
            for w in wells:
                moa, sim = nn[w.well_key]
                votes.setdefault(moa, []).append(sim)
            best = max(votes, key=lambda m: (len(votes[m]), sum(votes[m])))
            correct += best == sp.moa
        accs.append(correct / len(specimens))
    return {"accuracy": float(np.mean(accs)), "sd": float(np.std(accs)),
            "mean_wells": float(np.mean([min(k, len(sp.wells)) for sp in specimens]))}


# --- cost estimate --------------------------------------------------------------------------------

def token_profile() -> dict[str, list[float]]:
    """Mean [input, output] tokens per role in the recorded budget-4 runs."""
    sums: dict[str, list[float]] = {}
    for run_id in RECORDED:
        run = json.loads((ROOT / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        for c in run["model_calls"]:
            role = c["purpose"].split(" turn")[0]
            s = sums.setdefault(role, [0.0, 0.0])
            s[0] += c["input_tokens"] / len(RECORDED)
            s[1] += c["output_tokens"] / len(RECORDED)
    return sums


def estimate(n_specimens: int, budgets: tuple[int, ...], model: str, reviewer: str) -> dict:
    """Scientist and Microscopist tokens scale with the budget (0.75× at 2 wells, 1.25× at 6); others fixed."""
    prof = token_profile()
    per_budget = {}
    for b in budgets:
        scale = 0.5 + 0.125 * b
        cost = 0.0
        for role, (tin, tout) in prof.items():
            m = reviewer if role == "reviewer" else model
            f = scale if role in ("scientist", "microscopist") else 1.0
            cost += cost_of(m, tin * f, tout * f)
        per_budget[b] = cost
    reviewer_share = sum(cost_of(reviewer, *prof["reviewer"]) for _ in budgets) * n_specimens
    total = sum(per_budget.values()) * n_specimens
    return {"runs": n_specimens * len(budgets), "per_run": per_budget, "total": total,
            "reviewer_total": reviewer_share, "with_margin": total * 1.3}


# --- agent runs -----------------------------------------------------------------------------------

def load_log() -> list[dict]:
    if not RUNS_LOG.exists():
        return []
    return [json.loads(line) for line in RUNS_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


async def leak_hits(manager: RunManager, sp: Specimen, bus: RunBus) -> int:
    """Offline re-scan of every recorded event before `unblinded` (the runtime guard already ran live)."""
    scanner = BlindingScanner(await manager._blinding_terms(sp))
    hits = 0
    for e in bus.events:
        if e.type == "unblinded":
            break
        if e.type not in ("error", "run_finished"):
            hits += len(scanner.scan(e.data))
    return hits


async def one_run(manager: RunManager, sp: Specimen, budget: int) -> dict:
    loop = await manager.create_run(sp.specimen_id, budget, "blinded")
    run = await loop.run_all()
    events = loop.bus.events
    report = next((e.data for e in events if e.type == "report_ready"), None)
    cited = len((run.reflection.citations_cited if run.reflection else []))
    removed = len((run.reflection.citations_removed if run.reflection else []))
    first = run.conclusion_history[0] if run.conclusion_history else None
    return {
        "run_id": run.run_id, "specimen_id": sp.specimen_id, "compound": sp.compound, "truth_moa": sp.moa,
        "budget": budget, "models": run.model_ids, "status": run.status, "error": run.error,
        "agent_moa": run.verdict.agent_moa if run.verdict else None,
        "correct": bool(run.verdict and run.verdict.correct),
        "first_moa": first.top_moa if first else None, "first_correct": bool(first and first.top_moa == sp.moa),
        "review": run.review.verdict if run.review else None,
        "wells_used": run.budget_used, "turns": run.turns, "cost_usd": round(run.cost_usd, 5),
        "latency_s": round((events[-1].ts - events[0].ts).total_seconds(), 1),
        "citations_cited": cited, "citations_removed": removed, "report": report is not None,
        "leak_hits": await leak_hits(manager, sp, loop.bus),
    }


async def run_agents(manager: RunManager, specimens: list[Specimen], budgets: tuple[int, ...],
                     concurrency: int, model: str) -> None:
    done = {(r["specimen_id"], r["budget"]) for r in load_log() if r["models"]["scientist"] == model}
    todo = [(sp, b) for b in budgets for sp in specimens if (sp.specimen_id, b) not in done]
    print(f"agent runs: {len(done)} already in {RUNS_LOG.relative_to(ROOT)}, {len(todo)} to go")
    sem, lock, stop = asyncio.Semaphore(concurrency), asyncio.Lock(), asyncio.Event()
    ledger, cap = manager.llm.ledger, manager.settings.daily_cost_cap_usd

    async def worker(sp: Specimen, b: int, i: int) -> None:
        async with sem:
            if stop.is_set():
                return
            if ledger.total_today() > cap - 2 * manager.settings.run_cost_cap_usd:
                print(f"!! stopping: ${ledger.total_today():.2f} spent today, daily cap ${cap:.2f}")
                stop.set()
                return
            try:
                rec = await one_run(manager, sp, b)
            except Exception as exc:  # noqa: BLE001 - recorded as a failed run, visible in the outputs
                rec = {"specimen_id": sp.specimen_id, "compound": sp.compound, "truth_moa": sp.moa, "budget": b,
                       "models": manager.model_ids(), "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                       "correct": False, "first_correct": False, "wells_used": 0, "cost_usd": 0.0}
            async with lock:
                with RUNS_LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            mark = "ok " if rec["correct"] else "x  "
            print(f"[{i:>3}/{len(todo)}] {mark} b={b} {sp.specimen_id} {rec['status']:<8} "
                  f"{rec.get('agent_moa')} | truth {sp.moa} | ${rec['cost_usd']:.3f} "
                  f"{rec.get('latency_s', 0):.0f}s {rec.get('error') or ''}", flush=True)

    await asyncio.gather(*(worker(sp, b, i) for i, (sp, b) in enumerate(todo, start=1)))


# --- outputs --------------------------------------------------------------------------------------

def leak_tests() -> str:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-k", "leak or blind", "backend/tests"],
                          cwd=ROOT, capture_output=True, text=True)
    return (proc.stdout.strip().splitlines() or ["(no output)"])[-1]


def agent_rows(records: list[dict], specimens: list[Specimen], budgets, model: str) -> tuple[list[dict], list[str]]:
    ids = {sp.specimen_id for sp in specimens}
    rows, notes = [], []
    label = f"MuseCell agent ({model})"
    for b in budgets:
        rs = [r for r in records if r["budget"] == b and r["specimen_id"] in ids and r["models"]["scientist"] == model]
        if not rs:
            continue
        ok = [r for r in rs if r["status"] == "finished"]
        cited = sum(r.get("citations_cited", 0) for r in rs)
        removed = sum(r.get("citations_removed", 0) for r in rs)
        rows.append({
            "method": "agent", "label": label, "budget": b, "n": len(rs),
            "accuracy": round(sum(r["correct"] for r in rs) / len(rs), 4), "correct": sum(r["correct"] for r in rs),
            "accuracy_before_review": round(sum(r["first_correct"] for r in rs) / len(rs), 4),
            "mean_wells": round(float(np.mean([r["wells_used"] for r in rs])), 2),
            "mean_cost_usd": round(float(np.mean([r["cost_usd"] for r in ok])), 4) if ok else None,
            "mean_latency_s": round(float(np.mean([r["latency_s"] for r in ok])), 1) if ok else None,
            "citation_validity": round((cited - removed) / cited, 4) if cited else None,
            "citations": f"{cited - removed}/{cited}", "failed": len(rs) - len(ok),
            "reviewer_requests": sum(r.get("review") == "request_experiment" for r in rs),
        })
        if len(rs) < len(specimens):
            notes.append(f"Budget {b}: {len(rs)} of {len(specimens)} specimens have a recorded run.")
        for r in rs:
            if r["status"] != "finished":
                notes.append(f"Budget {b}, {r['specimen_id']}: run {r['status']} ({r.get('error')}); counted as wrong.")
    return rows, notes


def analysis(records: list[dict]) -> dict:
    """Where the agent's calls go wrong, over all budgets: calls per class vs truth, per-class accuracy, review."""
    truth = Counter(r["truth_moa"] for r in records)
    called = Counter(r.get("agent_moa") or "(no conclusion)" for r in records)
    per_class = {m: {"correct": sum(r["correct"] for r in records if r["truth_moa"] == m), "runs": n}
                 for m, n in sorted(truth.items())}
    return {
        "runs": len(records), "total_cost_usd": round(sum(r["cost_usd"] for r in records), 2),
        "calls_vs_truth": {m: {"called": called.get(m, 0), "true": truth.get(m, 0)}
                           for m in sorted(set(called) | set(truth), key=lambda m: (-called.get(m, 0), m))},
        "per_class": per_class,
        "review": {"request_experiment": sum(r.get("review") == "request_experiment" for r in records),
                   "approve": sum(r.get("review") == "approve" for r in records),
                   "wrong_to_right": sum(r["correct"] and not r["first_correct"] for r in records),
                   "right_to_wrong": sum(r["first_correct"] and not r["correct"] for r in records)},
    }


def analysis_markdown(a: dict) -> list[str]:
    lines = ["## Where the agent goes wrong", "",
             f"All {a['runs']} agent runs pooled over budgets (total API cost ${a['total_cost_usd']:.2f}).", "",
             "| Mechanism class | Agent calls | True runs | Correct |", "|---|---|---|---|"]
    for m, c in a["calls_vs_truth"].items():
        pc = a["per_class"].get(m)
        correct = f"{pc['correct']}/{pc['runs']}" if pc else "–"
        lines.append(f"| {m} | {c['called']} | {c['true']} | {correct} |")
    rv = a["review"]
    lines += ["", f"PI review: {rv['request_experiment']} requests for another experiment, {rv['approve']} approvals; "
              f"the final call changed from wrong to right in {rv['wrong_to_right']} runs and from right to wrong in "
              f"{rv['right_to_wrong']}.", ""]
    return lines


def write_markdown(res: dict, path: Path) -> None:
    def pct(x):
        return "–" if x is None else f"{100 * x:.1f}%"

    def num(x, fmt):
        return "–" if x is None else format(x, fmt)

    lines = ["# MuseCell evaluation", "",
             f"Generated {res['generated_at']} by `scripts/evaluate.py`. Numbers are written by the script, "
             "never edited by hand.", "",
             f"- Compounds: {res['n_compounds']} MoA-annotated BBBC021 compounds, leave-one-compound-out "
             "(the atlas never contains the compound under test).",
             "- Metric: compound-level MoA accuracy (one call per compound).",
             f"- Models: {', '.join(f'{k} `{v}`' for k, v in res['models'].items())}.",
             f"- Blinding-leak test: {res['blinding_leak_test']}", "",
             "| Method | Wells / compound | Accuracy | Correct | Before review | Mean wells used | Mean cost | "
             "Mean latency | Citations verified |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        budget = "all" if r["budget"] is None else str(r["budget"])
        acc = pct(r["accuracy"]) + (f" ± {100 * r['sd']:.1f}" if r.get("sd") else "")
        correct = f"{r['correct']}/{r['n']}" if r.get("correct") is not None else "–"
        cost = "–" if r.get("mean_cost_usd") is None else f"${r['mean_cost_usd']:.3f}"
        lat = "–" if r.get("mean_latency_s") is None else f"{r['mean_latency_s']:.0f} s"
        cites = r.get("citations", "–") if r.get("citation_validity") is None else \
            f"{r['citations']} ({pct(r['citation_validity'])})"
        lines.append(f"| {r['label']} | {budget} | {acc} | {correct} | {pct(r.get('accuracy_before_review'))} | "
                     f"{num(r.get('mean_wells'), '.2f')} | {cost} | {lat} | {cites} |")
    lines += ["", "± = standard deviation over random well draws. \"Before review\" = the agent's first conclusion, "
              "before the PI reviewer's critique and any requested experiment.", "",
              "![Accuracy against wells per compound](accuracy_vs_budget.png)", ""]
    if res.get("analysis"):
        lines += analysis_markdown(res["analysis"])
    if res.get("notes"):
        lines += ["## Notes", "", *[f"- {n}" for n in res["notes"]], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def plot(res: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, graphite, paper = "#17202A", "#5B6672", "#F6F8F9"
    styles = [(ink, "-", 2.0), (graphite, (0, (5, 3)), 1.5), (graphite, (0, (1.5, 2.5)), 1.5)]
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=150, facecolor=paper)
    ax.set_facecolor(paper)
    series: dict[str, list[dict]] = {}
    for r in res["rows"]:
        series.setdefault(r["label"], []).append(r)
    budgets = sorted({r["budget"] for r in res["rows"] if r["budget"] is not None})
    xmax = max(budgets) + 0.4
    for label, pts in series.items():  # the agent is the primary line; baselines are graphite references
        color, dash, width = styles[{"agent": 0, "nsc_random": 1}.get(pts[0]["method"], 2)]
        if pts[0]["budget"] is None:
            ax.axhline(pts[0]["accuracy"], color=color, linestyle=(0, (2, 3)), linewidth=1.25, label=label)
            ax.text(xmax, pts[0]["accuracy"], f" {label}", va="center", fontsize=8, color=color)
            continue
        pts = sorted(pts, key=lambda p: p["budget"])
        xs, ys = [p["budget"] for p in pts], [p["accuracy"] for p in pts]
        ax.plot(xs, ys, color=color, linestyle=dash, linewidth=width, marker="o", markersize=5, label=label)
        ax.text(xmax, ys[-1], f" {label}", va="center", fontsize=8, color=color)
    ax.set_xlim(min(budgets) - 0.4, xmax)
    ax.set_ylim(0, 1)
    ax.set_xticks(budgets)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("Wells per compound", color=graphite, fontsize=9)
    ax.set_ylabel("Compound-level MoA accuracy", color=graphite, fontsize=9)
    ax.set_title(f"Accuracy against wells acquired · {res['n_compounds']} compounds, LOCO", loc="left",
                 color=ink, fontsize=10)
    ax.grid(axis="y", color=ink, alpha=0.08)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color((0.09, 0.13, 0.16, 0.25))
    ax.tick_params(colors=graphite, labelsize=8)
    ax.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=ink)
    fig.tight_layout()
    fig.savefig(path, facecolor=paper, bbox_inches="tight")
    plt.close(fig)


# --- main -----------------------------------------------------------------------------------------

async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="muse-spark-1.3", help="Scientist + Microscopist + reflection model")
    p.add_argument("--reviewer-model", default="muse-wonder-1.0")
    p.add_argument("--budgets", type=int, nargs="+", default=list(BUDGETS))
    p.add_argument("--compounds", type=int, help="stratified subset size (spec: >= 24); default all")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--draws", type=int, default=500, help="random-well baseline draws")
    p.add_argument("--seed", type=int, default=6)
    p.add_argument("--yes", action="store_true", help="run the agent (spends API credits)")
    p.add_argument("--aggregate-only", action="store_true")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    budgets = tuple(args.budgets)

    contributor = args.model.endswith("-contributor")
    settings = Settings(scientist_model=args.model, microscopist_model=args.model,
                        reviewer_model=args.reviewer_model, allow_contributor=contributor)
    for m in (args.model, args.reviewer_model):
        if m not in PRICES:
            p.error(f"no price for '{m}'")
    EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    manager = RunManager(settings, runs_dir=EVAL_RUNS_DIR)
    await manager.startup(check_models=args.yes)
    try:
        if manager.catalog is None:
            print(manager.data_error)
            return 1
        specimens = stratified(sorted(manager.catalog.specimens.values(), key=lambda s: s.specimen_id),
                               args.compounds, args.seed)
        compounds = {sp.compound for sp in specimens}
        print(f"specimens: {len(specimens)} ({len({sp.moa for sp in specimens})} MoA classes)")

        rows = [{"method": "nsc_all", "label": "NSC baseline, all wells", "budget": None, "n": len(specimens),
                 "accuracy": round(nsc_all_wells(manager.atlas, compounds), 4)}]
        rows[0]["correct"] = round(rows[0]["accuracy"] * len(specimens))
        for b in budgets:
            r = nsc_random_wells(manager, specimens, b, args.draws, args.seed)
            rows.append({"method": "nsc_random", "label": "NSC baseline, random wells", "budget": b,
                         "n": len(specimens), "accuracy": round(r["accuracy"], 4), "sd": round(r["sd"], 4),
                         "mean_wells": round(r["mean_wells"], 2)})
        for r in rows:
            print(f"  {r['label']:<30} budget {str(r['budget'] or 'all'):>3}  accuracy {r['accuracy']:.3f}"
                  + (f" ± {r['sd']:.3f}" if r.get("sd") else ""))

        if not args.aggregate_only:
            est = estimate(len(specimens), budgets, args.model, args.reviewer_model)
            print(f"\ncost estimate ({est['runs']} agent runs; scientist/microscopist/reflection {args.model}, "
                  f"reviewer {args.reviewer_model}; from the token use of {len(RECORDED)} recorded runs):")
            for b, c in est["per_run"].items():
                print(f"  budget {b}: ~${c:.3f} per run")
            print(f"  total ~${est['total']:.2f} (reviewer ~${est['reviewer_total']:.2f}); "
                  f"with 30% margin for revisions/retries ~${est['with_margin']:.2f}")
            print(f"  spent today so far: ${manager.llm.ledger.total_today():.2f} of the "
                  f"${settings.daily_cost_cap_usd:.2f} daily cap" if manager.llm else "  (no MODEL_API_KEY)")
            if not args.yes:
                print("\nNo agent runs started. Re-run with --yes to spend the credits.")
                return 0
            await run_agents(manager, specimens, budgets, args.concurrency, args.model)

        records = load_log()
        a_rows, notes = agent_rows(records, specimens, budgets, args.model)
        used = [r for r in records if r["specimen_id"] in {s.specimen_id for s in specimens}
                and r["models"]["scientist"] == args.model and r["budget"] in budgets]
        hits = sum(r.get("leak_hits", 0) for r in used)
        leak = (f"pytest leak/blinding tests: {leak_tests()}; offline re-scan of {len(used)} eval run logs "
                f"(events before unblinding): {hits} hits")
        if contributor:
            notes.insert(0, f"Eval runs used `{args.model}` (same model, cheaper tier that lets Meta train on "
                            "prompts; allowed only because BBBC021 is public). The live app uses `muse-spark-1.3`.")
        notes.append("The random-well baseline votes over 1-NN calls of single wells; the all-wells baseline uses "
                     "treatment profiles (median over replicates), the published NSC protocol.")
        res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "models": {"scientist": args.model, "microscopist": args.model, "reviewer": args.reviewer_model,
                          "segmentation": manager.model_ids()["segmentation"]},
               "n_compounds": len(specimens), "blinding_leak_test": leak, "notes": notes, "rows": rows + a_rows,
               "analysis": analysis(used) if used else None}
        (EVAL / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        write_markdown(res, EVAL / "RESULTS.md")
        plot(res, EVAL / "accuracy_vs_budget.png")
        print(f"\n{leak}\nwrote eval/results.json, eval/RESULTS.md, eval/accuracy_vs_budget.png")
        return 0
    finally:
        await manager.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
