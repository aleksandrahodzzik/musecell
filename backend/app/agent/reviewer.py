"""PI reviewer (spec §4–§5): muse-wonder-1.0 reads the blinded evidence ledger and the conclusion and
returns approve / request_experiment with a critique. Strict structured output. The loop allows at most one
revision round. The reviewer is blinded too: it sees exactly what the Scientist saw, by opaque id.
"""

from __future__ import annotations

import json

from app.agent.schemas import AgentRun, Review
from app.agent.specimens import Specimen
from app.llm import CallRecord, CostMeter, LLMClient

REVIEWER_MAX_TOKENS = 12000  # Wonder reasons before answering; reasoning shares this budget

SYSTEM = (
    "You are the principal investigator reviewing a blinded mechanism-of-action (MoA) call made by an "
    "autonomous screening agent on MCF-7 cells imaged for DNA, beta-tubulin and F-actin. You do not know the "
    "compound. Judge only the evidence ledger you are given:\n"
    "- Does the evidence favour the top MoA over the runner-up hypotheses, or could the runner-up explain it "
    "equally well?\n"
    "- Were the acquisitions chosen to discriminate between hypotheses? Is any key result a single well or a "
    "weak effect that a replicate or another dose would test?\n"
    "- Is the stated probability calibrated to the evidence?\n"
    "Approve when the conclusion is justified, or when no affordable experiment could change it. Request an "
    "experiment only when one specific, affordable step (a named dose index and replicate, or a free analysis "
    "such as an atlas comparison, a dose response or a phenotype description) could discriminate between the "
    "leading hypotheses. The run's final call is made by protocol: the NSC vote over the wells the agent "
    "acquired (`protocol_call`, the latest one the atlas tool reported). When that vote is split or weak, or the "
    "conclusion disagrees with it, ask for the one affordable well (dose index and replicate) or free analysis "
    "most likely to make the vote decisive, if the budget allows. Be concrete and brief; do not guess the compound. Never write the name of any specific "
    "compound or drug, not even as a control: a name may be the hidden compound or one of its synonyms, and the "
    "blinding guard then stops the run. Refer to controls by class (agent v3 eval: one review was stopped so)."
)


def response_schema() -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": ["verdict", "critique", "requested_experiment"],
            "properties": {
                "verdict": {"type": "string", "enum": ["approve", "request_experiment"]},
                "critique": {"type": "string", "description": "at most 120 words"},
                "requested_experiment": {"type": "string",
                                         "description": "the one experiment you request; empty string if approving"}}}


def ledger(run: AgentRun, specimen: Specimen) -> dict:
    """Everything the Scientist saw and decided, blinded (opaque ids, doses, measurements)."""
    acquired = {w for w in run.acquired_well_ids}
    return {
        "specimen_id": run.specimen_id,
        "doses_uM": specimen.concentrations_uM,
        "replicates_per_dose": [specimen.replicates(i) for i in range(len(specimen.concentrations_uM))],
        "budget": {"total": run.budget_total, "used": run.budget_used, "left": run.budget_left},
        "wells_acquired": len(acquired),
        "steps": [{"step": s.step, "tool": s.tool, "why": s.rationale, "evidence_id": s.evidence_id,
                   "error": s.error} for s in run.steps],
        "evidence": [{"evidence_id": e.evidence_id, "kind": e.kind, "summary": e.summary, "data": e.data}
                     for e in run.evidence if e.kind not in ("experiments", "conclusion")],
        "hypothesis_history": [h.model_dump() for h in run.hypothesis_history],
        "conclusion": run.conclusion.model_dump() if run.conclusion else None,
        # agent v5: the run's final call is the NSC vote over the acquired wells; show it next to the conclusion
        "protocol_call": _protocol(run),
    }


def _protocol(run: AgentRun) -> dict | None:
    """The latest NSC vote the atlas tool reported (over all wells acquired at that time)."""
    atlas = [e for e in run.evidence if e.kind == "atlas" and (e.data or {}).get("nsc", {}).get("moa")]
    if not atlas:
        return None
    nsc = atlas[-1].data["nsc"]
    return {"moa": nsc["moa"], "votes": nsc["votes"], "wells": nsc["wells"], "tally": nsc["tally"],
            "covers_all_acquired_wells": nsc["wells"] == len(run.acquired_well_ids),
            "agrees_with_conclusion": bool(run.conclusion and run.conclusion.top_moa == nsc["moa"])}


def build_messages(run: AgentRun, specimen: Specimen, moa_classes: list[str]) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": (f"MoA classes: {moa_classes}\n\nEvidence ledger and conclusion:\n"
                                         + json.dumps(ledger(run, specimen), indent=1, default=str))}]


async def review(llm: LLMClient, model: str, run: AgentRun, specimen: Specimen, moa_classes: list[str],
                 meter: CostMeter) -> tuple[Review, CallRecord, list[dict]]:
    """Returns the review, its call record and the messages that were sent (for the leak guard and tests)."""
    messages = build_messages(run, specimen, moa_classes)
    result, record = await llm.chat_json(model=model, messages=messages, meter=meter, purpose="reviewer",
                                         schema_name="pi_review", schema=response_schema(),
                                         max_tokens=REVIEWER_MAX_TOKENS)
    requested = (result.get("requested_experiment") or "").strip()
    verdict = result["verdict"]
    return (Review(verdict=verdict, critique=result["critique"].strip(),
                   requested_experiment=requested if verdict == "request_experiment" else None), record, messages)
