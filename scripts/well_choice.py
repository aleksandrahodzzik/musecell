"""Does the Scientist choose wells better than a fixed rule? Offline, no API calls; used by scripts/evaluate.py.

Agent v5's final call is the NSC vote over the wells the Scientist chose, so its only lever on accuracy is the
choice of wells. This module scores label-free well-choice rules with the *same* vote function as the protocol
call (`app.agent.tools.well_vote`), in the same atlas space, at the same budget, on the same compounds, and
compares each with the agent by a paired bootstrap over compounds.

The rules were written down before they were run on the deep atlas (2026-10-31). The first four are the
hand-crafted-space rules from the dev exploration before agent v4 (docs/DECISIONS.md, "Deep atlas and agent v4");
the fifth is the obvious deep-space heuristic. Rules that read measurements (cell count, embedding norm) look at
every well of the compound, which the agent cannot do without spending its budget, so they are a generous
comparison. Picking the "best rule" after seeing all 38 compounds favours the rule, not the agent.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable

import numpy as np

from app.agent.specimens import Specimen, Well
from app.agent.tools import well_vote

TOP = 7  # highest concentration index (every BBBC021 specimen has 8 doses, 0 = lowest)


def _first_replicates(sp: Specimen, doses: list[int]) -> list[Well]:
    return [sp.well(i, 1) or next(w for w in sp.wells if w.concentration_index == i) for i in doses]


def _top_doses(sp: Specimen, k: int, ctx) -> list[Well]:
    return _first_replicates(sp, list(range(TOP, TOP - k, -1)))


def _top_two_doses(sp: Specimen, k: int, ctx) -> list[Well]:
    by_dose = {i: sorted((w for w in sp.wells if w.concentration_index == i), key=lambda w: w.replicate)
               for i in (TOP, TOP - 1)}
    out = []
    while len(out) < k and any(by_dose.values()):  # alternate the two doses: 7r1, 6r1, 7r2, 6r2, ...
        for i in (TOP, TOP - 1):
            if by_dose[i] and len(out) < k:
                out.append(by_dose[i].pop(0))
    return out


def _dose_spread(sp: Specimen, k: int, ctx) -> list[Well]:
    return _first_replicates(sp, sorted({int(round(x)) for x in np.linspace(0, TOP, k)}))


def _top_doses_alive(sp: Specimen, k: int, ctx) -> list[Well]:
    """Highest doses whose first replicate keeps a cell count z >= -2 vs same-plate DMSO (skip wiped-out wells)."""
    order = list(range(TOP, -1, -1))
    alive = [i for i in order if ctx.catalog.profile(_first_replicates(sp, [i])[0].well_key)["cell_count"] >= -2]
    return _first_replicates(sp, (alive + [i for i in order if i not in alive])[:k])


def _strongest(sp: Specimen, k: int, ctx) -> list[Well]:
    """The k wells farthest from DMSO in the atlas space (TVN centres DMSO at the origin)."""
    return sorted(sp.wells, key=lambda w: -float(np.linalg.norm(ctx.atlas.well_vector(w.well_key))))[:k]


RULES: dict[str, Callable] = {
    "top doses": _top_doses,
    "top 2 doses x replicates": _top_two_doses,
    "dose spread": _dose_spread,
    "top doses, cell count z >= -2": _top_doses_alive,
    "strongest wells (embedding norm)": _strongest,
}


class _Ctx:
    def __init__(self, atlas, catalog):
        self.atlas, self.catalog = atlas, catalog


def bootstrap_diff(diff: np.ndarray, resamples: int, rng: np.random.Generator) -> dict:
    """Mean of per-compound differences and a 95% percentile interval, resampling compounds with replacement."""
    idx = rng.integers(0, len(diff), (resamples, len(diff)))
    means = diff[idx].mean(axis=1)
    return {"mean": round(float(diff.mean()), 4), "ci95": [round(float(np.percentile(means, 2.5)), 4),
                                                            round(float(np.percentile(means, 97.5)), 4)]}


def well_choice_report(atlas, catalog, specimens: list[Specimen], agent_correct: dict[str, bool],
                       agent_wells: dict[str, list[str]], split: dict | None, budget: int,
                       draws: int = 2000, resamples: int = 10_000, seed: int = 6) -> dict:
    """Rules vs the agent at one budget. `agent_correct` / `agent_wells` are keyed by specimen id."""
    ctx = _Ctx(atlas, catalog)
    sps = [sp for sp in specimens if sp.specimen_id in agent_correct]

    def right(wells: list[Well], sp: Specimen) -> bool:
        return well_vote(atlas, catalog, wells, sp.compound)["moa"] == sp.moa

    # each well's 1-NN is fixed, so the random baseline caches it and replays the vote over sampled wells
    cache: dict[str, tuple[str, float]] = {}
    for sp in sps:
        for w in sp.wells:
            pw = well_vote(atlas, catalog, [w], sp.compound)
            cache[w.well_key] = (pw["moa"], pw["per_well"][0]["similarity"])
    rng = random.Random(seed)
    p_random = np.zeros(len(sps))
    for _ in range(draws):
        for j, sp in enumerate(sps):
            votes: dict[str, list[float]] = {}
            for w in rng.sample(sp.wells, min(budget, len(sp.wells))):
                m, s = cache[w.well_key]
                votes.setdefault(m, []).append(s)
            p_random[j] += max(votes, key=lambda m: (len(votes[m]), sum(votes[m]))) == sp.moa
    p_random /= draws

    scores = {"random wells (expected)": p_random}
    for name, rule in RULES.items():
        scores[name] = np.array([float(right(rule(sp, budget, ctx), sp)) for sp in sps])
    agent = np.array([float(agent_correct[sp.specimen_id]) for sp in sps])
    rules_only = [n for n in RULES]
    best = max(rules_only, key=lambda n: (scores[n].sum(), -rules_only.index(n)))

    parts = {"all": np.ones(len(sps), bool)}
    if split:
        parts["dev"] = np.array([sp.specimen_id in set(split["dev"]) for sp in sps])
        parts["test"] = np.array([sp.specimen_id in set(split["test"]) for sp in sps])

    def tally(a: np.ndarray) -> dict:
        return {k: {"correct": round(float(a[m].sum()), 2), "n": int(m.sum())} for k, m in parts.items()}

    brng = np.random.default_rng(seed)
    rows = [{"name": "agent (wells chosen by the Scientist)", **tally(agent)}]
    rows += [{"name": n, **tally(a)} for n, a in scores.items()]
    diffs = []
    for n in ["random wells (expected)", *rules_only]:
        d = agent - scores[n]
        diffs.append({"vs": n, "best_rule": n == best,
                      **{k: bootstrap_diff(d[m], resamples, brng) for k, m in parts.items()}})

    # how often the Scientist's wells were simply the top doses
    top = set(range(TOP, TOP - budget, -1))
    wells = {w.well_id: w for sp in sps for w in sp.wells}
    chosen = [wells[w] for sp in sps for w in agent_wells.get(sp.specimen_id, []) if w in wells]
    in_top = sum(w.concentration_index in top for w in chosen)
    return {"budget": budget, "n": len(sps), "draws": draws, "resamples": resamples, "seed": seed,
            "vote": "app.agent.tools.well_vote (the protocol call's own function)", "rows": rows,
            "differences": diffs, "best_rule": best,
            "agent_wells_in_top_doses": {"wells": in_top, "of": len(chosen),
                                         "top_doses": f"the {budget} highest of 8 doses"}}


def load_agent_wells(runs_dir: Path, records: list[dict]) -> dict[str, list[str]]:
    out = {}
    for r in records:
        path = runs_dir / f"{r.get('run_id')}.json"
        if path.exists():
            out[r["specimen_id"]] = json.loads(path.read_text(encoding="utf-8"))["acquired_well_ids"]
    return out


def markdown(rep: dict, agent_label: str) -> list[str]:
    def frac(c):
        v = c["correct"]
        return f"{v:.1f}/{c['n']}" if v != int(v) else f"{int(v)}/{c['n']}"

    def pp(x):
        return f"{100 * x:+.1f}"

    parts = [k for k in ("all", "dev", "test") if k in rep["rows"][0]]
    lines = [f"## Does the agent choose wells better than a fixed rule? (budget {rep['budget']})", "",
             f"{agent_label}: the final call is the NSC vote over the wells the Scientist chose, so its only lever is "
             "which wells to image. Each rule below picks wells without labels and is scored by the protocol call's own "
             "vote function (`app.agent.tools.well_vote`), in the same atlas space, on the same {rep['n']} compounds. Rules that read "
             "measurements look at every well, which the agent cannot do without spending its budget.", "",
             "| Well choice | " + " | ".join({"all": "All", "dev": "Dev half", "test": "Held-out test half"}[p]
                                            for p in parts) + " |", "|---|" + "---|" * len(parts)]
    for r in rep["rows"]:
        lines.append(f"| {r['name']} | " + " | ".join(frac(r[p]) for p in parts) + " |")
    lines += ["", "Agent minus each alternative, percentage points, mean over compounds with a paired bootstrap 95% "
              f"interval ({rep['resamples']:,} resamples of compounds):", "",
              "| Agent minus | " + " | ".join({"all": "All", "dev": "Dev", "test": "Test"}[p] for p in parts) + " |",
              "|---|" + "---|" * len(parts)]
    for d in rep["differences"]:
        name = d["vs"] + (" (best rule, picked after seeing all compounds)" if d["best_rule"] else "")
        lines.append(f"| {name} | " + " | ".join(
            f"{pp(d[p]['mean'])} [{pp(d[p]['ci95'][0])}, {pp(d[p]['ci95'][1])}]" for p in parts) + " |")
    t = rep["agent_wells_in_top_doses"]
    best = next(d for d in rep["differences"] if d["best_rule"])
    lo, hi = best["all"]["ci95"]
    verdict = ("the agent is better than the best fixed rule" if lo > 0 else
               "the best fixed rule is better than the agent" if hi < 0 else
               "the agent's well choice is not distinguishable from the best fixed rule")
    rnd = next(d for d in rep["differences"] if d["vs"].startswith("random"))
    lines += ["", f"Of the {t['of']} wells the Scientist chose, {t['wells']} were among {t['top_doses']}.", "",
              f"Reading: on these {rep['n']} compounds {verdict} (\"{rep['best_rule']}\"); against random wells the "
              f"interval is [{pp(rnd['all']['ci95'][0])}, {pp(rnd['all']['ci95'][1])}] points. What the agent adds "
              "beyond a rule is the blinded, audited record: why each well, the reviewer's critique, the explanation, "
              "and a dossier with verified citations.", ""]
    return lines
