"""Reflection validation and the dossier (spec §5, §8 screen 4): Markdown + JSON built from the run by code.

Citations: the reflection may only cite PMIDs that search_literature returned in this run. Anything else is
stripped from the text and dropped from the citation list; the dossier states "Citations verified: n/n".
"""

from __future__ import annotations

from typing import Any

from app.agent.guardrails import validate_citations
from app.agent.schemas import AgentRun, Citation, Reflection

DISCLAIMER = "Research tool for cell-biology screening data. Not for clinical use."


def validate_reflection(raw: dict, allowed_pmids: list[str]) -> Reflection:
    """Strip citations of PMIDs not returned in this run, from every text field and from the citation list."""
    allowed = set(allowed_pmids)
    cited: list[str] = []

    def clean(text: str) -> str:
        check = validate_citations(text or "", allowed)
        cited.extend(check.cited)
        return check.text.strip()

    text = clean(raw.get("reflection", ""))
    right = [clean(x) for x in raw.get("what_was_right", [])]
    wrong = [clean(x) for x in raw.get("what_was_wrong", [])]
    next_experiment = clean(raw.get("next_experiment", ""))
    citations = []
    for c in raw.get("citations", []):
        pmid = str(c.get("pmid", "")).strip().upper().removeprefix("PMID").strip(": ")
        if pmid:
            cited.append(pmid)
            if pmid in allowed:
                citations.append(Citation(pmid=pmid, claim=clean(c.get("claim", ""))))
    cited = list(dict.fromkeys(cited))
    return Reflection(text=text, what_was_right=right, what_was_wrong=wrong, citations=citations,
                      next_experiment=next_experiment, citations_cited=cited,
                      citations_removed=[p for p in cited if p not in allowed])


def citations_label(reflection: Reflection | None) -> str:
    if reflection is None:
        return "Citations verified: 0/0"
    verified = len(reflection.citations_cited) - len(reflection.citations_removed)
    return f"Citations verified: {verified}/{len(reflection.citations_cited)}"


def articles(run: AgentRun) -> dict[str, dict]:
    """Every PubMed record search_literature returned in this run, by PMID."""
    out: dict[str, dict] = {}
    for e in run.evidence:
        if e.kind == "literature":
            for a in e.data.get("articles", []):
                out.setdefault(a["pmid"], a)
    return out


def experiments(run: AgentRun) -> list[dict]:
    return [{"evidence_id": e.evidence_id, "well_id": e.data["well_id"], "concentration_uM": e.data["concentration_uM"],
             "replicate": e.data["replicate"], "cells_measured": e.data["segmentation"]["cells_measured"],
             "top_deviations": e.data["top_deviations"][:4], "image_ids": e.image_ids}
            for e in run.evidence if e.kind == "well"]


def build_json(run: AgentRun) -> dict[str, Any]:
    arts = articles(run)
    cited = [r.pmid for r in (run.reflection.citations if run.reflection else [])]
    cited += [p for p in (run.reflection.citations_cited if run.reflection else []) if p in arts and p not in cited]
    return {
        "run_id": run.run_id, "mode": run.mode, "specimen_id": run.specimen_id, "status": run.status,
        "question": "Which mechanism-of-action class explains this compound's phenotype in MCF-7 cells?",
        "budget": {"total": run.budget_total, "used": run.budget_used},
        "experiments": experiments(run),
        "steps": [s.model_dump(mode="json") for s in run.steps],
        "hypothesis_history": [h.model_dump() for h in run.hypothesis_history],
        "conclusions": [c.model_dump() for c in run.conclusion_history],
        "review": run.review.model_dump() if run.review else None,
        "unblinding": run.unblinding.model_dump() if run.unblinding else None,
        "verdict": run.verdict.model_dump() if run.verdict else None,
        "reflection": run.reflection.model_dump() if run.reflection else None,
        "citations": [arts[p] for p in cited if p in arts],
        "citations_label": citations_label(run.reflection),
        "cost_usd": round(run.cost_usd, 4), "model_ids": run.model_ids, "turns": run.turns,
        "model_calls": [c.model_dump() for c in run.model_calls],
        "disclaimer": DISCLAIMER,
    }


def _fmt_dev(d: dict) -> str:
    return f"{d['feature']} z={d['z']:+.1f}"


def build_markdown(run: AgentRun, doc: dict | None = None) -> str:
    doc = doc or build_json(run)
    md = [f"# MuseCell dossier · specimen {run.specimen_id}", "",
          f"Run `{run.run_id}` · {run.mode} · {run.started_at:%Y-%m-%d %H:%M} UTC · models: "
          + ", ".join(f"{k} {v}" for k, v in run.model_ids.items()), "",
          f"**Question.** {doc['question']}", "",
          f"## Experiments ({run.budget_used} of {run.budget_total} wells)", "",
          "| Evidence | Dose (µM) | Replicate | Cells | Strongest changes vs DMSO |", "|---|---|---|---|---|"]
    md += [f"| {x['evidence_id']} | {x['concentration_uM']:g} | {x['replicate']} | {x['cells_measured']} | "
           + "; ".join(_fmt_dev(d) for d in x["top_deviations"][:3]) + " |" for x in doc["experiments"]]
    md += ["", "## Steps", ""]
    md += [f"{s.step}. `{s.tool}` — {s.rationale}" + (f" *(error: {s.error})*" if s.error else "") for s in run.steps]
    md += ["", "## Hypothesis history", ""]
    for snap in run.hypothesis_history:
        md.append(f"- after step {snap.step}: " + ", ".join(f"{h.moa} {h.probability:.2f}" for h in snap.hypotheses))
    for i, c in enumerate(run.conclusion_history, 1):
        title = "Conclusion" if len(run.conclusion_history) == 1 else f"Conclusion {i}"
        md += ["", f"## {title}", "", f"**{c.top_moa}** (stated probability {c.probability:.2f}); key evidence "
               + ", ".join(c.key_evidence_ids) + ".", "", f"Why: {c.rationale}", "",
               f"Next experiment if this were a real lab: {c.next_experiment_if_this_were_a_real_lab}"]
    if run.review:
        md += ["", "## PI review", "", f"Verdict: {run.review.verdict.replace('_', ' ')}.", "", run.review.critique]
        if run.review.requested_experiment:
            md += ["", f"Requested: {run.review.requested_experiment}"]
    if run.unblinding:
        u = run.unblinding
        mechs = "; ".join(m.get("mechanism_of_action", "") for m in u.chembl_mechanisms) or \
            "no curated mechanism in ChEMBL"
        md += ["", "## Unblinding", "", f"Compound: **{u.compound}**"
               + (f" (PubChem CID [{u.pubchem_cid}](https://pubchem.ncbi.nlm.nih.gov/compound/{u.pubchem_cid}))"
                  if u.pubchem_cid else " (no PubChem record shown)"),
               "", f"Identity check: {u.identity_check}", "", f"ChEMBL mechanisms: {mechs}"]
    if run.verdict:
        v = run.verdict
        md += ["", "## Verdict (computed by code)", "",
               f"{'Correct' if v.correct else 'Incorrect'}: agent said **{v.agent_moa}**, BBBC021 annotates "
               f"**{v.truth_moa}**."]
    if run.reflection:
        r = run.reflection
        md += ["", "## Reflection", "", r.text]
        if r.what_was_right:
            md += ["", "What was right:", *[f"- {x}" for x in r.what_was_right]]
        if r.what_was_wrong:
            md += ["", "What was wrong or missed:", *[f"- {x}" for x in r.what_was_wrong]]
        md += ["", f"Proposed next experiment: {r.next_experiment}"]
    md += ["", "## Citations", "", doc["citations_label"], ""]
    claims = {c.pmid: c.claim for c in (run.reflection.citations if run.reflection else [])}
    for a in doc["citations"]:
        claim = f" — {claims[a['pmid']]}" if a["pmid"] in claims else ""
        md.append(f"- PMID [{a['pmid']}]({a['url']}): {a['title']} *{a.get('journal') or ''}* "
                  f"({a.get('year') or 'n.d.'}){claim}")
    md += ["", f"Cost: ${run.cost_usd:.4f} over {len(run.model_calls)} model calls; {run.turns} Scientist turns.",
           "", f"*{DISCLAIMER}*", ""]
    return "\n".join(md)
