"""Benchmark: MuseCell agent vs the NSC baseline, compound-level MoA accuracy under LOCO (spec §6).

Methods
  (a) NSC 1-NN on all wells (published protocol: treatment profiles, majority vote per compound)
  (b) NSC 1-NN on the same number of wells drawn at random from the specimen (vote over wells; mean of R draws)
  (c) MuseCell agent (Scientist + Microscopist + PI reviewer, full run incl. unblinding/literature) at budgets 2/4/6
  (d) optional comparison agents (`--compare`), e.g. Muse Spark Open on Baseten (open weights) at budget 4

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
  python scripts/evaluate.py --provider baseten --budgets 4 --model muse-spark-1.3-contributor --yes
      # open weights: Scientist + Microscopist on meta-models/muse-spark-open-1026 (Baseten), reviewer stays
      # muse-wonder-1.0 (Meta API); the outputs keep --model as the main agent and add the open model as a
      # comparison (same as --compare meta-models/muse-spark-open-1026)
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
from app.agent import AGENT_VERSION  # noqa: E402
from app.agent.guardrails import BlindingScanner  # noqa: E402
from app.agent.specimens import Specimen  # noqa: E402
from app.config import OPEN_WEIGHTS_MODEL, PRICES, PROVIDERS, Settings, provider_of  # noqa: E402
from app.llm import cost_of  # noqa: E402
from app.runs.manager import RunManager  # noqa: E402
from app.runs.store import RunBus  # noqa: E402
from app.science.atlas import Atlas, compound_level, nsc_baseline  # noqa: E402
from well_choice import load_agent_wells, markdown as well_choice_markdown, well_choice_report  # noqa: E402

EVAL = ROOT / "eval"
RUNS_LOG = EVAL / "agent_runs.jsonl"
EVAL_RUNS_DIR = ROOT / "runs" / "eval"
BUDGETS = (2, 4, 6)
RECORDED = ("run_87e2787d1f1e", "run_39e2d32f1edc", "run_93391a0830e0")  # Phase-4 runs, budget 4
# Stretch: the same three specimens on Muse Spark Open via Baseten (scripts/run_agent_cli.py, budget 4)
RECORDED_OPEN = ("run_d5a53adc9c63", "run_0d9f5361415e", "run_0f1c579db128")
COMPARE_LABELS = {OPEN_WEIGHTS_MODEL: "Muse Spark Open (Baseten)"}


def version_of(r: dict) -> str:
    """Agent version of a logged run; runs logged before versions existed are v1."""
    return r["models"].get("agent", "v1")


def parse_spec(spec: str) -> tuple[str, str]:
    """'model' or 'model@v2' -> (model, version); no version means v1 (every run before agent v2)."""
    model, _, ver = spec.partition("@")
    return model, ver or "v1"


def short_label(model: str, ver: str) -> str:
    return COMPARE_LABELS.get(model, model) + ("" if ver == "v1" else f", agent {ver}")


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


def nsc_random_wells(manager: RunManager, specimens: list[Specimen], k: int, draws: int, seed: int,
                     atlas: Atlas) -> dict:
    """1-NN per randomly drawn well, majority vote (ties: summed similarity); accuracy per draw."""
    catalog = manager.catalog
    nn: dict[str, tuple[str, float]] = {}
    for sp in specimens:
        for w in sp.wells:
            vec = atlas.well_vector(w.well_key)
            hit = atlas.neighbors(catalog.profile(w.well_key) if vec is None else vec, 1,
                                  exclude_compound=sp.compound)[0]
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

def token_profile(recorded: tuple[str, ...] = RECORDED) -> dict[str, list[float]]:
    """Mean [input, output] tokens per role in the recorded budget-4 runs."""
    sums: dict[str, list[float]] = {}
    for run_id in recorded:
        run = json.loads((ROOT / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        for c in run["model_calls"]:
            role = c["purpose"].split(" turn")[0]
            s = sums.setdefault(role, [0.0, 0.0])
            s[0] += c["input_tokens"] / len(recorded)
            s[1] += c["output_tokens"] / len(recorded)
    return sums


def recorded_latency(recorded: tuple[str, ...]) -> float:
    """Mean wall time (s) of recorded runs, first to last event."""
    out = []
    for run_id in recorded:
        lines = (ROOT / "runs" / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        first, last = json.loads(lines[0])["ts"], json.loads(lines[-1])["ts"]
        out.append((datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds())
    return float(np.mean(out))


def estimate(n_specimens: int, budgets: tuple[int, ...], model: str, reviewer: str,
             recorded: tuple[str, ...] = RECORDED) -> dict:
    """Scientist and Microscopist tokens scale with the budget (0.75× at 2 wells, 1.25× at 6); others fixed."""
    prof = token_profile(recorded)
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
            "reviewer_total": reviewer_share, "with_margin": total * 1.3, "latency_s": recorded_latency(recorded)}


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
        # agent v5+: the final call is the protocol's; the Scientist's own conclusion is scored separately
        "call_source": run.verdict.call_source if run.verdict else None,
        "scientist_moa": run.verdict.scientist_moa if run.verdict else None,
        "scientist_correct": bool(run.verdict and run.verdict.scientist_correct),
        "first_moa": first.top_moa if first else None, "first_correct": bool(first and first.top_moa == sp.moa),
        "review": run.review.verdict if run.review else None,
        "wells_used": run.budget_used, "turns": run.turns, "cost_usd": round(run.cost_usd, 5),
        "latency_s": round((events[-1].ts - events[0].ts).total_seconds(), 1),
        "citations_cited": cited, "citations_removed": removed, "report": report is not None,
        "leak_hits": await leak_hits(manager, sp, loop.bus),
    }


async def run_agents(manager: RunManager, specimens: list[Specimen], budgets: tuple[int, ...],
                     concurrency: int, model: str) -> None:
    done = {(r["specimen_id"], r["budget"]) for r in load_log()
            if r["models"]["scientist"] == model and version_of(r) == AGENT_VERSION}
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


def nsc_on_chosen_wells(manager: RunManager, records: list[dict]) -> None:
    """Adds `nsc_chosen` (bool) to each record whose run log is on disk: the NSC protocol (1-NN per well, majority
    vote) applied to exactly the wells the agent acquired, in the atlas space that agent searched. This separates
    the agent's experiment choice from its final call (scripts/nsc_on_agent_wells.py has the full breakdown)."""
    handcrafted = Atlas.load(manager.settings.atlas_dir)
    deep = manager.atlas if manager.atlas is not None and manager.atlas.space == "resnet50_tvn" else None
    wells = {w.well_id: (sp, w) for sp in manager.catalog.specimens.values() for w in sp.wells}
    for r in records:
        path = EVAL_RUNS_DIR / f"{r.get('run_id')}.json"
        if not path.exists():
            continue
        acquired = [wells[w] for w in json.loads(path.read_text(encoding="utf-8"))["acquired_well_ids"] if w in wells]
        if not acquired:
            r["nsc_chosen"] = False
            continue
        atlas = deep if deep is not None and r["models"].get("atlas", "").startswith("ResNet50") else handcrafted
        votes: dict[str, list[float]] = {}
        for sp, w in acquired:
            vec = atlas.well_vector(w.well_key)
            hit = atlas.neighbors(manager.catalog.profile(w.well_key) if vec is None else vec, 1,
                                  exclude_compound=sp.compound)[0]
            votes.setdefault(hit.moa, []).append(hit.similarity)
        r["nsc_chosen"] = max(votes, key=lambda m: (len(votes[m]), sum(votes[m]))) == r["truth_moa"]


def agent_rows(records: list[dict], specimens: list[Specimen], budgets, model: str,
               method: str = "agent", ver: str = "v1") -> tuple[list[dict], list[str]]:
    ids = {sp.specimen_id for sp in specimens}
    rows, notes = [], []
    label = (f"MuseCell agent, {short_label(model, ver)}" if model in COMPARE_LABELS or ver != "v1"
             else f"MuseCell agent ({model})")
    for b in budgets:
        rs = [r for r in records if r["budget"] == b and r["specimen_id"] in ids
              and r["models"]["scientist"] == model and version_of(r) == ver]
        if not rs:
            continue
        ok = [r for r in rs if r["status"] == "finished"]
        cited = sum(r.get("citations_cited", 0) for r in rs)
        removed = sum(r.get("citations_removed", 0) for r in rs)
        rows.append({
            "method": method, "label": label, "model": model, "agent_version": ver,
            "atlas": rs[0]["models"].get("atlas", "hand-crafted features"),
            "short": short_label(model, ver), "budget": b, "n": len(rs),
            "accuracy": round(sum(r["correct"] for r in rs) / len(rs), 4), "correct": sum(r["correct"] for r in rs),
            "accuracy_before_review": round(sum(r["first_correct"] for r in rs) / len(rs), 4),
            "mean_wells": round(float(np.mean([r["wells_used"] for r in rs])), 2),
            "mean_cost_usd": round(float(np.mean([r["cost_usd"] for r in ok])), 4) if ok else None,
            "mean_latency_s": round(float(np.mean([r["latency_s"] for r in ok])), 1) if ok else None,
            "citation_validity": round((cited - removed) / cited, 4) if cited else None,
            "citations": f"{cited - removed}/{cited}", "failed": len(rs) - len(ok),
            "reviewer_requests": sum(r.get("review") == "request_experiment" for r in rs),
            "scientist_accuracy": (round(sum(r.get("scientist_correct", False) for r in rs) / len(rs), 4)
                                   if any(r.get("call_source") == "protocol" for r in rs) else None),
            # the NSC protocol on exactly the wells this agent chose (its own atlas space); None without run logs
            "nsc_on_chosen_wells": (round(sum(r["nsc_chosen"] for r in rs) / len(rs), 4)
                                    if all("nsc_chosen" in r for r in rs) else None),
        })
        if len(rs) < len(specimens):
            notes.append(f"{label}, budget {b}: {len(rs)} of {len(specimens)} specimens have a recorded run.")
        for r in rs:
            if r["status"] != "finished":
                # A run can fail after the code computed its verdict (e.g. in the reflection): the verdict stands.
                outcome = (f"after the verdict, which stands ({'right' if r['correct'] else 'wrong'})"
                           if r.get("agent_moa") else "before a conclusion; counted as wrong")
                notes.append(f"{label}, budget {b}, {r['specimen_id']}: run {r['status']} {outcome}: "
                             f"{(r.get('error') or '').splitlines()[0]}")
    return rows, notes


def analysis(records: list[dict]) -> dict:
    """Where the agent's calls go wrong, over all budgets: calls per class vs truth, per-class accuracy, review."""
    truth = Counter(r["truth_moa"] for r in records)
    called = Counter(r.get("agent_moa") or "(no conclusion)" for r in records)
    def after(r: dict) -> bool:
        return bool(r.get("scientist_correct") if r.get("call_source") == "protocol" else r["correct"])

    per_class = {m: {"correct": sum(r["correct"] for r in records if r["truth_moa"] == m), "runs": n}
                 for m, n in sorted(truth.items())}
    return {
        "runs": len(records), "total_cost_usd": round(sum(r["cost_usd"] for r in records), 2),
        "calls_vs_truth": {m: {"called": called.get(m, 0), "true": truth.get(m, 0)}
                           for m in sorted(set(called) | set(truth), key=lambda m: (-called.get(m, 0), m))},
        "per_class": per_class,
        "review": {"request_experiment": sum(r.get("review") == "request_experiment" for r in records),
                   "approve": sum(r.get("review") == "approve" for r in records),
                   # the review acts on the Scientist's conclusion; from v5 the final call is the protocol's, so
                   # the review's effect is first conclusion -> Scientist's final conclusion, not -> final call
                   "wrong_to_right": sum(after(r) and not r["first_correct"] for r in records),
                   "right_to_wrong": sum(r["first_correct"] and not after(r) for r in records)},
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
    lines += ["", f"PI review (it acts on the Scientist's conclusion): {rv['request_experiment']} requests for another experiment, {rv['approve']} approvals; "
              f"the Scientist's call changed from wrong to right in {rv['wrong_to_right']} runs and from right to wrong in "
              f"{rv['right_to_wrong']}.", ""]
    return lines


def comparison_markdown(res: dict) -> list[str]:
    """One column per agent model at each budget a comparison model was run at (same compounds, same reviewer)."""
    compare = [r for r in res["rows"] if r["method"] == "agent_open"]
    if not compare:
        return []
    lines = []
    for b in sorted({r["budget"] for r in compare}):
        cols = [r for r in res["rows"] if r["method"] in ("agent", "agent_open") and r["budget"] == b]
        bases = [r for m in ("nsc_random", "nsc_random_deep") for r in res["rows"]
                 if r["method"] == m and r["budget"] == b]
        heads = [r.get("short") or COMPARE_LABELS.get(r["model"], r["model"]) for r in cols]
        lines += [f"## Agents side by side, budget {b}", "",
                  f"Same {res['n_compounds']} compounds, same tools, same PI reviewer (`muse-wonder-1.0`, Meta API); "
                  "the columns differ in the Scientist + Microscopist model, the agent version and the atlas features.",
                  "", "| | " + " | ".join(heads) + "".join(f" | NSC baseline ({r['space']}), random wells" for r in bases)
                  + " |", "|---|" + "---|" * (len(cols) + len(bases))]

        def line(name, fn, base=None):
            base = base or (lambda r: "–")
            return f"| {name} | " + " | ".join(fn(r) for r in cols + []) + "".join(f" | {base(r)}" for r in bases) + " |"

        lines += [
            line("Scientist model", lambda r: f"`{r['model']}`"),
            line("Provider", lambda r: r.get("provider_label", "Meta Model API")),
            line("Atlas features", lambda r: r.get("atlas", "hand-crafted features"), lambda r: r["space"]),
            line("Accuracy", lambda r: f"{100 * r['accuracy']:.1f}% ({r['correct']}/{r['n']})",
                 lambda r: f"{100 * r['accuracy']:.1f}% ± {100 * r['sd']:.1f}"),
            line("Before review", lambda r: f"{100 * r['accuracy_before_review']:.1f}%"),
            line("Scientist's own call (v5: final call is the protocol's)", lambda r: "–" if r.get("scientist_accuracy")
                 is None else f"{100 * r['scientist_accuracy']:.1f}%"),
            line("NSC on the wells the agent chose", lambda r: "–" if r.get("nsc_on_chosen_wells") is None
                 else f"{100 * r['nsc_on_chosen_wells']:.1f}%"),
            line("Failed runs", lambda r: str(r["failed"])),
            line("Mean cost / run", lambda r: "–" if r["mean_cost_usd"] is None else
                 f"${r['mean_cost_usd']:.3f}" + (" (incl. credits)" if r.get("billing") == "credits" else "")),
            line("Mean latency", lambda r: "–" if r["mean_latency_s"] is None else f"{r['mean_latency_s']:.0f} s"),
            line("Citations verified", lambda r: r["citations"]),
            line("Reviewer requests", lambda r: str(r["reviewer_requests"])),
        ]
        lines += ["", "Cost on open weights is nominal: Baseten list price ($1.25 / $4.25 per 1M tokens) for the "
                  "Scientist/Microscopist tokens, paid from hackathon credits, plus the reviewer at Meta API prices.", ""]
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
              "before the PI reviewer's critique and any requested experiment (from agent v5 the final call is the NSC "
              "vote over the agent's wells, so the gap to \"Accuracy\" is not the review's effect). ± is the spread of "
              "the baseline over draws, not a confidence interval for the agent; the agent's intervals are in the "
              "well-choice section below.", "",
              "![Accuracy against wells per compound](accuracy_vs_budget.png)", ""]
    lines += comparison_markdown(res)
    if res.get("well_choice"):
        lines += well_choice_markdown(res["well_choice"], res["well_choice"]["agent_label"])
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
    styles = [(ink, "-", 2.0), (graphite, (0, (5, 3)), 1.5), (graphite, (0, (1.5, 2.5)), 1.5),
              (ink, (0, (4, 1.5, 1, 1.5)), 1.5), (graphite, (0, (8, 2, 1, 2)), 1.5)]
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=150, facecolor=paper)
    ax.set_facecolor(paper)
    series: dict[str, list[dict]] = {}
    for r in res["rows"]:
        series.setdefault(r["label"], []).append(r)
    budgets = sorted({r["budget"] for r in res["rows"] if r["budget"] is not None})
    xmax = max(budgets) + 0.4
    for label, pts in series.items():  # the agent is the primary line; baselines are graphite references
        color, dash, width = styles[{"agent": 0, "nsc_random": 1, "agent_open": 3,
                                     "nsc_random_deep": 4}.get(pts[0]["method"], 2)]
        if pts[0]["budget"] is None:
            ax.axhline(pts[0]["accuracy"], color=color, linestyle=(0, (2, 3)), linewidth=1.25, label=label)
            ax.text(xmax, pts[0]["accuracy"], f" {label}", va="center", fontsize=8, color=color)
            continue
        pts = sorted(pts, key=lambda p: p["budget"])
        xs, ys = [p["budget"] for p in pts], [p["accuracy"] for p in pts]
        is_open = pts[0]["method"] == "agent_open"  # hollow diamond: open weights
        ax.plot(xs, ys, color=color, linestyle=dash, linewidth=width, label=label, markersize=6 if is_open else 5,
                marker="D" if is_open else "o", markerfacecolor=paper if is_open else color)
        if is_open:  # a single point: label it beside the marker so it does not collide with the agent line
            ax.text(xs[-1] + 0.12, ys[-1], pts[0].get("short", label), va="center", fontsize=8, color=color)
            continue
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
    p.add_argument("--provider", choices=["meta", "baseten"], default="meta",
                   help="who serves the agent runs of this call; baseten = open weights (" + OPEN_WEIGHTS_MODEL + ")")
    p.add_argument("--compare", nargs="*", default=[],
                   help="extra agents to add as comparison rows: MODEL or MODEL@VERSION (default v1)")
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

    main_model, main_ver = parse_spec(args.model)  # the main agent rows (and the error analysis)
    contributor = main_model.endswith("-contributor")
    settings = Settings(scientist_model=main_model, microscopist_model=main_model, provider=args.provider,
                        reviewer_model=args.reviewer_model, allow_contributor=contributor)
    run_model = settings.scientist_id  # = main_model on meta, the open-weights id on another provider
    # runs of this call are always the current agent version; show them as a comparison unless they are the main rows
    this_run = (run_model, AGENT_VERSION)
    compare = list(dict.fromkeys([*map(parse_spec, args.compare),
                                  *([this_run] if not args.aggregate_only and this_run != (main_model, main_ver)
                                    else [])]))
    for m in (main_model, run_model, args.reviewer_model, *(m for m, _ in compare)):
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

        # Baselines in both feature spaces: hand-crafted (what agents v1-v3 searched) and, when the deep atlas is
        # built, ResNet50 + TVN (what v4 searches). Method names keep the hand-crafted rows as before.
        spaces = [("", "hand-crafted features", Atlas.load(manager.settings.atlas_dir))]
        if manager.atlas is not None and manager.atlas.space == "resnet50_tvn":
            spaces.append(("_deep", "ResNet50 + TVN", manager.atlas))
        rows = []
        for suffix, space, atlas in spaces:
            acc = nsc_all_wells(atlas, compounds)
            rows.append({"method": "nsc_all" + suffix, "label": f"NSC baseline ({space}), all wells", "budget": None,
                         "n": len(specimens), "accuracy": round(acc, 4), "correct": round(acc * len(specimens)),
                         "space": space})
            for b in sorted(set(BUDGETS) | set(budgets)):  # the table keeps every budget, whatever this call runs
                r = nsc_random_wells(manager, specimens, b, args.draws, args.seed, atlas)
                rows.append({"method": "nsc_random" + suffix, "label": f"NSC baseline ({space}), random wells",
                             "budget": b, "n": len(specimens), "accuracy": round(r["accuracy"], 4),
                             "sd": round(r["sd"], 4), "mean_wells": round(r["mean_wells"], 2), "space": space})
        for r in rows:
            print(f"  {r['label']:<30} budget {str(r['budget'] or 'all'):>3}  accuracy {r['accuracy']:.3f}"
                  + (f" ± {r['sd']:.3f}" if r.get("sd") else ""))

        if not args.aggregate_only:
            recorded = RECORDED if run_model == main_model else RECORDED_OPEN
            est = estimate(len(specimens), budgets, run_model, args.reviewer_model, recorded)
            print(f"\ncost estimate ({est['runs']} agent runs; scientist/microscopist/reflection {run_model} on "
                  f"{PROVIDERS[args.provider].label}, reviewer {args.reviewer_model}; from the token use of "
                  f"{len(recorded)} recorded runs):")
            for b, c in est["per_run"].items():
                print(f"  budget {b}: ~${c:.3f} per run")
            print(f"  total ~${est['total']:.2f} (reviewer ~${est['reviewer_total']:.2f}"
                  + (f" real USD; the rest is {PROVIDERS[args.provider].label} credits at list price"
                     if PRICES[run_model].billing == "credits" else "")
                  + f"); with 30% margin for revisions/retries ~${est['with_margin']:.2f}")
            wall = est["latency_s"] * est["runs"] / args.concurrency
            print(f"  latency ~{est['latency_s']:.0f} s per run -> ~{wall / 60:.0f} min wall time at concurrency "
                  f"{args.concurrency}")
            print(f"  spent today so far: ${manager.llm.ledger.total_today():.2f} of the "
                  f"${settings.daily_cost_cap_usd:.2f} daily cap" if manager.llm else "  (no MODEL_API_KEY)")
            if not args.yes:
                print("\nNo agent runs started. Re-run with --yes to spend the credits.")
                return 0
            await run_agents(manager, specimens, budgets, args.concurrency, run_model)

        records = load_log()
        nsc_on_chosen_wells(manager, records)
        a_rows, notes = agent_rows(records, specimens, BUDGETS, main_model, "agent", main_ver)
        for r in a_rows:  # the main agent may run on another provider too (open weights)
            r.update(provider=provider_of(main_model), provider_label=PROVIDERS[provider_of(main_model)].label,
                     billing=PRICES[main_model].billing)
        for m, ver in compare:
            c_rows, c_notes = agent_rows(records, specimens, sorted(set(BUDGETS) | set(budgets)), m, "agent_open", ver)
            provider = provider_of(m)
            for r in c_rows:
                r.update(provider=provider, provider_label=PROVIDERS[provider].label, billing=PRICES[m].billing)
            a_rows += c_rows
            notes += c_notes
        if any(ver != "v1" for _, ver in compare):
            v2 = [r for r in records if version_of(r) == "v2"]
            notes.insert(0, "Agent v2/v3 (the atlas tool also returns the NSC vote over the wells acquired so far, "
                            "the prompt says how to weigh it, and the blinded phase names no compound) was designed "
                            "after reading the v1 runs on these same 38 compounds (`scripts/nsc_on_agent_wells.py`). "
                            "BBBC021 has no other annotated compounds, so v3 accuracy is not a held-out estimate. "
                            f"A v2 eval was stopped after {len(v2)} runs ({sum(r['correct'] for r in v2)} correct, "
                            f"{sum(r['status'] != 'finished' for r in v2)} stopped by the blinding guard) to add the "
                            "naming rule; its runs stay in eval/agent_runs.jsonl.")
        used = [r for r in records if r["specimen_id"] in {s.specimen_id for s in specimens}
                and r["models"]["scientist"] == main_model and version_of(r) == main_ver and r["budget"] in BUDGETS]
        hits = sum(r.get("leak_hits", 0) for r in used)
        leak = (f"pytest leak/blinding tests: {leak_tests()}; offline re-scan of {len(used)} eval run logs "
                f"(events before unblinding): {hits} hits")
        split_path = EVAL / "split.json"
        if split_path.exists():  # agents designed after the split was frozen: report dev and held-out test separately
            split = json.loads(split_path.read_text(encoding="utf-8"))
            for row in a_rows:
                if row.get("agent_version", "v1") in ("v1", "v2", "v3", "v3.1"):
                    continue
                rs = [r for r in records if r["models"]["scientist"] == row["model"]
                      and version_of(r) == row["agent_version"] and r["budget"] == row["budget"]]
                part = {k: [r for r in rs if r["specimen_id"] in set(split[k])] for k in ("dev", "test")}
                row["split_accuracy"] = {k: round(sum(r["correct"] for r in v) / len(v), 4) for k, v in part.items() if v}
                notes.insert(0, f"{row['label']}, budget {row['budget']}: chosen on the dev half of a split frozen "
                                f"before it was explored (eval/split.json); dev {sum(r['correct'] for r in part['dev'])}"
                                f"/{len(part['dev'])}, held-out test {sum(r['correct'] for r in part['test'])}"
                                f"/{len(part['test'])}.")
        if contributor or any(m.endswith("-contributor") for m, _ in compare):
            notes.insert(0, "The v1 budget 2/4/6 rows used `muse-spark-1.3-contributor` (same model, cheaper tier that "
                            "lets Meta train on prompts; allowed only because BBBC021 is public). Rows labelled Muse "
                            "Spark Open ran on Baseten. The live app defaults to full-price `muse-spark-1.3`, which was "
                            "not benchmarked with agent v4/v5.")
        notes.append("The random-well baseline votes over 1-NN calls of single wells; the all-wells baseline uses "
                     "treatment profiles (median over replicates), the published NSC protocol.")
        well_choice = None
        main_row = next((r for r in a_rows if r["method"] == "agent" and r.get("scientist_accuracy") is not None), None)
        if main_row is not None and manager.atlas is not None:  # v5+: the agent's lever is the choice of wells
            b = main_row["budget"]
            rs = [r for r in used if r["budget"] == b]
            well_choice = well_choice_report(
                manager.atlas, manager.catalog, specimens, {r["specimen_id"]: bool(r["correct"]) for r in rs},
                load_agent_wells(EVAL_RUNS_DIR, rs),
                json.loads(split_path.read_text(encoding="utf-8")) if split_path.exists() else None, b)
            well_choice["agent_label"] = main_row["label"]
        res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "models": {"scientist": main_model, "microscopist": main_model, "reviewer": args.reviewer_model,
                          "segmentation": manager.model_ids()["segmentation"]},
               "comparisons": [{"model": m, "agent_version": v, "label": short_label(m, v)} for m, v in compare],
               "n_compounds": len(specimens), "blinding_leak_test": leak, "notes": notes, "rows": rows + a_rows,
               "analysis": analysis(used) if used else None, "well_choice": well_choice}
        (EVAL / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        write_markdown(res, EVAL / "RESULTS.md")
        plot(res, EVAL / "accuracy_vs_budget.png")
        print(f"\n{leak}\nwrote eval/results.json, eval/RESULTS.md, eval/accuracy_vs_budget.png")
        return 0
    finally:
        await manager.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
