"""Open-world eval: analysis for scripts/evaluate.py (pre-registered in docs/DECISIONS.md, "Open-world eval,
pre-registered"; every rule here is the registered one).

For each of the 38 compounds the specimen's whole MoA class is left out of the reference library
(app.agent.open_world.restricted_atlas). The NSC baselines are computed under that exclusion and must be 0/38; the
agent ("v5-open") is compared with the strongest label-only guess by a paired bootstrap over compounds.
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

from app.agent.open_world import restricted_atlas
from app.agent.tools import well_vote
from app.science.atlas import compound_level
from well_choice import RULES, bootstrap_diff

BUDGET = 4
RESAMPLES = 10_000
SEED = 6

# "Says so in words" (measure 3), exactly as registered: within one sentence, case-insensitive; a match whose span
# contains a hedge word is a restated instruction, not a judgement, and is not counted.
SAYS_PARTS = [
    r"\b(library|atlas|references?|reference set|neighbou?rs?|nearest|nsc|vote)\b[^.;:!?]{0,80}?\b(don'?t|doesn'?t|"
    r"do not|does not|not|never|no longer|fails? to|failed to|cannot|can'?t|poorly|badly)\b[^.;:!?]{0,30}?\b(fit|fits|"
    r"match|matches|contain|contains|include|includes|represent|represents|cover|covers|explain|explains|capture|"
    r"captures)\b",
    r"\b(absent|missing|lacking|unrepresented|not represented|not present|outside)\b[^.;:!?]{0,40}?\b(library|atlas|"
    r"reference)",
    r"\b(library|atlas|reference set|references)\b[^.;:!?]{0,40}?\b(lacks?|lacking|is missing|are missing|has no|"
    r"have no|without)\b",
    r"\bno (close|good|clear|matching|fitting|convincing|true) (match|reference|neighbou?r|analog|analogue)",
    r"\b(novel|unrepresented|missing) (mechanism|class|moa)\b",
]
SAYS = re.compile("|".join(SAYS_PARTS), re.IGNORECASE)
HEDGE = re.compile(r"\b(may|might|could|whether|if)\b", re.IGNORECASE)


def says_dont_fit(texts: list[str]) -> list[str]:
    return [m.group(0) for t in texts for m in SAYS.finditer(t or "") if not HEDGE.search(m.group(0))]


def atlas_classes(data: dict) -> set[str]:
    """Every class an atlas tool result names: neighbours, k-neighbour summary, per-well votes, tally, vote."""
    nsc = data.get("nsc") or {}
    return ({n["moa"] for n in data.get("neighbors", [])} | {r["moa"] for r in data.get("moa_summary", [])}
            | {w["nearest_moa"] for w in nsc.get("per_well", [])} | {t["moa"] for t in nsc.get("tally", [])}
            | ({nsc["moa"]} if nsc.get("moa") else set()))


def run_signals(events: list[dict], truth: str, final_moa: str | None, vote_moa: str | None) -> dict:
    """The registered "references don't fit" measures for one run, from its events before the unblinding.
    final_moa: the Scientist's final conclusion; vote_moa: the NSC vote over its wells (the protocol call)."""
    texts, seen, micro = [], set(), set()
    for e in events:
        if e["type"] == "unblinded":
            break
        d = e["data"]
        if e["type"] == "tool_call":
            texts.append(d.get("rationale") or "")
        elif e["type"] == "thinking" and d.get("note"):
            texts.append(d["note"])
        elif e["type"] == "conclusion":
            texts.append(d.get("rationale") or "")
        elif e["type"] == "tool_result" and d.get("ok") and d.get("name") == "compare_to_reference_atlas":
            seen |= atlas_classes(d.get("data") or {})
        elif e["type"] == "tool_result" and d.get("ok") and d.get("name") == "describe_phenotype":
            micro |= {c["moa"] for c in ((d.get("data") or {}).get("report") or {}).get("candidate_mechanisms", [])}
    spans = says_dont_fit(texts)
    return {"vote_moa": vote_moa, "classes_seen": sorted(seen),
            "leaves_vote": bool(final_moa and vote_moa and final_moa != vote_moa),
            "leaves_all_refs": bool(final_moa and final_moa not in seen),
            "says_dont_fit": bool(spans), "says_spans": spans[:3],
            "microscopist_named_truth": truth in micro}


# --- baselines under the exclusion (no API) --------------------------------------------------------

def _treatment_vote(library, rows: pd.DataFrame, compound: str) -> str:
    """Published NSC protocol for one compound: 1-NN per treatment profile, majority vote (ties: summed similarity)."""
    pred = []
    for _, t in rows.iterrows():
        nn = library.neighbors(t, 1, exclude_compound=compound)[0]
        pred.append({"compound": compound, "moa": t["moa"], "predicted": nn.moa, "similarity": nn.similarity})
    return compound_level(pd.DataFrame(pred))["predicted"].iloc[0]


def baselines(atlas, catalog, specimens) -> dict[str, dict[str, str]]:
    """Calls per specimen id: NSC on all wells and on the 4 highest doses, under the exclusion and (same code, full
    library, a check that the code path is right) under plain LOCO."""
    out = {k: {} for k in ("nsc_all", "nsc_top4", "nsc_all_full_library", "nsc_top4_full_library")}
    for sp in specimens:
        lib = restricted_atlas(atlas, catalog, sp)
        rows = atlas.reference[atlas.reference["compound"] == sp.compound]
        top = RULES["top doses"](sp, BUDGET, None)
        out["nsc_all"][sp.specimen_id] = _treatment_vote(lib, rows, sp.compound)
        out["nsc_top4"][sp.specimen_id] = well_vote(lib, catalog, top, sp.compound)["moa"]
        out["nsc_all_full_library"][sp.specimen_id] = _treatment_vote(atlas, rows, sp.compound)
        out["nsc_top4_full_library"][sp.specimen_id] = well_vote(atlas, catalog, top, sp.compound)["moa"]
    return out


def priors(specimens, n_classes: int) -> dict[str, np.ndarray]:
    """Expected per-compound correctness of the label-only guesses. Majority: always the most frequent class over all
    38 labels (the specimen's own included), ties broken uniformly. Under the exclusion it is 0 by construction."""
    counts = Counter(sp.moa for sp in specimens)
    tied = [m for m, c in counts.items() if c == max(counts.values())]
    return {"majority": np.array([1 / len(tied) if sp.moa in tied else 0.0 for sp in specimens]),
            "majority_excluded": np.zeros(len(specimens)),
            "uniform": np.full(len(specimens), 1 / n_classes), "tied": sorted(tied)}


# --- report ----------------------------------------------------------------------------------------

def report(records: list[dict], specimens, base: dict, n_classes: int, split: dict | None,
           control: list[dict]) -> dict:
    """records: one per specimen (eval/open_world_runs.jsonl); control: the same signals for the closed-world v5 runs."""
    by_id = {r["specimen_id"]: r for r in records}
    sps = [sp for sp in specimens if sp.specimen_id in by_id]
    agent = np.array([float(by_id[sp.specimen_id]["correct"]) for sp in sps])
    first = np.array([float(by_id[sp.specimen_id].get("first_correct", False)) for sp in sps])
    pr = priors(sps, n_classes)
    parts = {"all": np.ones(len(sps), bool)}
    if split:
        parts["dev"] = np.array([sp.specimen_id in set(split["dev"]) for sp in sps])
        parts["test"] = np.array([sp.specimen_id in set(split["test"]) for sp in sps])

    def tally(a: np.ndarray) -> dict:
        return {k: {"correct": round(float(a[m].sum()), 2), "n": int(m.sum())} for k, m in parts.items()}

    def calls(name: str) -> np.ndarray:
        return np.array([float(base[name][sp.specimen_id] == sp.moa) for sp in sps])

    rows = [
        {"name": "MuseCell v5-open (the Scientist's call)", "key": "agent", **tally(agent)},
        {"name": "Scientist's first conclusion, before review", "key": "first", **tally(first)},
        {"name": "NSC on all wells (published protocol), under the exclusion", "key": "nsc_all", **tally(calls("nsc_all"))},
        {"name": "NSC on the 4 highest doses, under the exclusion", "key": "nsc_top4", **tally(calls("nsc_top4"))},
        {"name": "Majority-class guess, all 38 labels (expected; " + ", ".join(pr["tied"]) + " tie)",
         "key": "majority", **tally(pr["majority"])},
        {"name": "Majority-class guess under the exclusion", "key": "majority_excluded",
         **tally(pr["majority_excluded"])},
        {"name": f"Uniform guess, 1/{n_classes} (expected)", "key": "uniform", **tally(pr["uniform"])},
        {"name": "Check, same code on the full library (LOCO): NSC on all wells", "key": "nsc_all_full_library",
         **tally(calls("nsc_all_full_library"))},
        {"name": "Check, same code on the full library (LOCO): NSC on the 4 highest doses",
         "key": "nsc_top4_full_library", **tally(calls("nsc_top4_full_library"))},
    ]

    def boot(a: np.ndarray) -> dict:  # a fresh default_rng(SEED) for every comparison and part (registered)
        return {k: bootstrap_diff(a[m], RESAMPLES, np.random.default_rng(SEED)) for k, m in parts.items()}

    bootstrap = {"agent_minus_majority": boot(agent - pr["majority"]), "agent_accuracy": boot(agent),
                 "agent_minus_uniform": boot(agent - pr["uniform"])}
    lo = bootstrap["agent_minus_majority"]["all"]["ci95"][0]

    per_class = {}
    times_called = Counter(r.get("agent_moa") for r in records)
    for m in sorted({sp.moa for sp in sps}):
        rs = [by_id[sp.specimen_id] for sp in sps if sp.moa == m]
        per_class[m] = {"compounds": len(rs), "correct": sum(r["correct"] for r in rs),
                        "times_called": times_called.get(m, 0),  # over all runs, any true class
                        "called": dict(Counter(r.get("agent_moa") or "(no conclusion)" for r in rs).most_common())}

    def rate(rs: list[dict], key: str) -> dict:
        return {"n": sum(bool(r.get(key)) for r in rs), "of": len(rs)}

    ok = [r for r in records if r.get("status") == "finished"]
    saw_all = [r for r in records if len(r.get("classes_seen") or []) == n_classes - 1]
    signals = {key: {"open_world": rate(records, key), "closed_world_control": rate(control, key)}
               for key in ("leaves_vote", "leaves_all_refs", "says_dont_fit")}
    signals["right_when_leaving_vote"] = {"n": sum(r["correct"] for r in records if r.get("leaves_vote")),
                                          "of": sum(bool(r.get("leaves_vote")) for r in records)}
    return {
        "budget": BUDGET, "n": len(sps), "resamples": RESAMPLES, "seed": SEED, "rows": rows, "bootstrap": bootstrap,
        "excludes_zero": bool(lo > 0), "exclusion_leak_check": {k: int(calls(k).sum()) for k in ("nsc_all", "nsc_top4")},
        "per_class": per_class, "signals": signals,
        "elimination": {"classes_seen_mean": round(float(np.mean([len(r.get("classes_seen") or []) for r in records])), 2)
                        if records else None, "saw_all_library_classes": len(saw_all),
                        "right_among_those": sum(r["correct"] for r in saw_all)},
        "microscopist_named_truth": rate(records, "microscopist_named_truth"),
        "review": {"request_experiment": sum(r.get("review") == "request_experiment" for r in records),
                   "approve": sum(r.get("review") == "approve" for r in records),
                   "wrong_to_right": sum(r["correct"] and not r.get("first_correct") for r in records),
                   "right_to_wrong": sum(r.get("first_correct") and not r["correct"] for r in records)},
        "failed": [{"specimen_id": r["specimen_id"], "status": r.get("status"),
                    "error": (r.get("error") or "").splitlines()[0] if r.get("error") else ""}
                   for r in records if r.get("status") != "finished"],
        "total_cost_usd": round(sum(r.get("cost_usd", 0) for r in records), 2),
        "mean_cost_usd": round(float(np.mean([r["cost_usd"] for r in ok])), 4) if ok else None,
        "mean_latency_s": round(float(np.mean([r["latency_s"] for r in ok])), 1) if ok else None,
        "leak_hits": sum(r.get("leak_hits", 0) for r in records),
        "citations": f"{sum(r.get('citations_cited', 0) - r.get('citations_removed', 0) for r in records)}/"
                     f"{sum(r.get('citations_cited', 0) for r in records)}",
    }


def markdown(rep: dict, prereg: str) -> list[str]:
    def frac(c):
        v = c["correct"]
        return f"{v:.1f}/{c['n']}" if v != int(v) else f"{int(v)}/{c['n']}"

    def pct(c):
        return f"{100 * c['correct'] / c['n']:.1f}%"

    def pp(x):
        return f"{100 * x:+.1f}"

    parts = [k for k in ("all", "dev", "test") if k in rep["rows"][0]]
    head = {"all": "All", "dev": "Dev half", "test": "Held-out test half"}
    lines = ["## Open world: the mechanism left out of the reference library (pre-registered)", "",
             f"Protocol registered in `docs/DECISIONS.md` (\"Open-world eval, pre-registered\", commit `{prereg}`) "
             "before any code for it was written; one shot. For each compound, every atlas read in its run (neighbours, "
             "NSC vote, protocol call) excludes **all compounds of its MoA class**, so NSC and any well-choice rule "
             "cannot name it. Agent v5-open: v5 tools and loop, Muse Spark 1.3 (Scientist + Microscopist), "
             f"`muse-wonder-1.0` reviewer, budget {rep['budget']}; one prompt change (the reference library may not "
             "contain the mechanism; name one of the 12 classes from all the evidence); the final call is the "
             "Scientist's own.", "",
             "| Method | " + " | ".join(head[p] for p in parts) + " |", "|---|" + "---|" * len(parts)]
    for r in rep["rows"]:
        lines.append(f"| {r['name']} | " + " | ".join(f"{frac(r[p])} ({pct(r[p])})" for p in parts) + " |")
    b = rep["bootstrap"]
    lines += ["", f"Paired bootstrap over compounds ({rep['resamples']:,} resamples, percentile 95% interval), "
              "percentage points:", "",
              "| | " + " | ".join({"all": "All", "dev": "Dev", "test": "Test"}[p] for p in parts) + " |",
              "|---|" + "---|" * len(parts)]
    for key, name in (("agent_minus_majority", "Agent minus majority-class guess (primary)"),
                      ("agent_minus_uniform", "Agent minus uniform guess"), ("agent_accuracy", "Agent accuracy")):
        lines.append(f"| {name} | " + " | ".join(
            f"{pp(b[key][p]['mean'])} [{pp(b[key][p]['ci95'][0])}, {pp(b[key][p]['ci95'][1])}]" for p in parts) + " |")
    lo, hi = b["agent_minus_majority"]["all"]["ci95"]
    lines += ["", f"Reading (registered criterion): the interval of agent minus the majority-class guess on all "
              f"{rep['n']} compounds is [{pp(lo)}, {pp(hi)}] points, so it "
              + ("**excludes zero**." if rep["excludes_zero"] else "**does not exclude zero**."), "",
              f"| True class | Compounds | Right | What the Scientist called | Times it called this class (of {rep['n']} runs) |",
              "|---|---|---|---|---|"]
    for m, c in rep["per_class"].items():
        lines.append(f"| {m} | {c['compounds']} | {c['correct']} | "
                     + "; ".join(f"{k} ×{v}" for k, v in c["called"].items()) + f" | {c.get('times_called', '–')} |")
    s = rep["signals"]

    def r_(x):
        return f"{x['n']}/{x['of']}"

    lines += ["", "\"The nearest references don't fit\" (registered measures; control = the 38 closed-world v5 "
              "Muse Spark 1.3 runs, where the class is in the library):", "",
              "| Measure | Open world | Closed-world control |", "|---|---|---|",
              f"| Final call differs from the NSC vote over its own wells | {r_(s['leaves_vote']['open_world'])} | "
              f"{r_(s['leaves_vote']['closed_world_control'])} |",
              f"| Final call is a class no atlas output it saw named | {r_(s['leaves_all_refs']['open_world'])} | "
              f"{r_(s['leaves_all_refs']['closed_world_control'])} |",
              f"| Says so in words (registered regex) | {r_(s['says_dont_fit']['open_world'])} | "
              f"{r_(s['says_dont_fit']['closed_world_control'])} |", "",
              f"When the final call left the vote it was right {r_(s['right_when_leaving_vote'])} times. "
              f"Elimination check: the atlas outputs of a run showed {rep['elimination']['classes_seen_mean']} of the "
              f"11 library classes on average; {rep['elimination']['saw_all_library_classes']} run(s) saw all 11 "
              f"({rep['elimination']['right_among_those']} of them right). The Microscopist listed the true class "
              f"among its candidates in {r_(rep['microscopist_named_truth'])} runs. Review: "
              f"{rep['review']['request_experiment']} requests, {rep['review']['approve']} approvals; the Scientist's "
              f"call changed wrong→right {rep['review']['wrong_to_right']} times and right→wrong "
              f"{rep['review']['right_to_wrong']} times. Failed runs: {len(rep['failed'])}"
              + (" (" + "; ".join(f"{f['specimen_id']}: {f['status']} {f['error']}" for f in rep["failed"]) + ")"
                 if rep["failed"] else "")
              + f". Cost ${rep['total_cost_usd']:.2f} in total, ${rep['mean_cost_usd'] or 0:.3f} and "
              f"{rep['mean_latency_s'] or 0:.0f} s per run; blinding re-scan {rep['leak_hits']} hits; citations "
              f"verified {rep['citations']}.", ""]
    return lines
