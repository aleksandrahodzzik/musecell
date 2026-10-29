"""Run state, evidence, hypotheses and SSE events (spec §5). Pydantic v2.

Everything here may be streamed to the browser during a blinded run, so it carries opaque ids only
(specimen `s_…`, well `w_…`, field `f_…`). The compound identity lives server-side in
`agent.specimens.Specimen` and appears only in `Unblinding` after the conclusion is submitted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm import CallRecord

RunMode = Literal["blinded", "open"]
RunPhase = Literal["blinded", "review", "unblinded", "finished"]
EvidenceKind = Literal["experiments", "well", "phenotype", "atlas", "dose_response", "hypotheses",
                       "conclusion", "compound", "literature"]
EventType = Literal["run_started", "thinking", "tool_call", "tool_result", "hypotheses_updated", "budget_updated",
                    "conclusion", "review", "unblinded", "verdict", "report_ready", "cost_updated", "error",
                    "run_finished"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Evidence(BaseModel):
    evidence_id: str  # "e1", "e2", … in order of registration
    kind: EvidenceKind
    tool: str
    summary: str
    data: dict[str, Any]
    image_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)


class Hypothesis(BaseModel):
    moa: str
    probability: float
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class HypothesisSnapshot(BaseModel):
    step: int
    hypotheses: list[Hypothesis]


class Step(BaseModel):
    step: int
    tool: str
    args: dict[str, Any]
    rationale: str
    evidence_id: str | None = None
    error: str | None = None
    cost_usd: float = 0.0
    started_at: datetime = Field(default_factory=now)
    seconds: float = 0.0


class Conclusion(BaseModel):
    top_moa: str
    probability: float
    key_evidence_ids: list[str]
    next_experiment_if_this_were_a_real_lab: str
    rationale: str


class Review(BaseModel):
    verdict: Literal["approve", "request_experiment"]
    critique: str
    requested_experiment: str | None = None


class Unblinding(BaseModel):
    compound: str
    pubchem_cid: int | None = None
    structure_url: str | None = None
    chembl_mechanisms: list[dict[str, Any]] = Field(default_factory=list)


class Verdict(BaseModel):
    """Computed by code (agent top MoA vs ground truth), never by a model."""

    correct: bool
    truth_moa: str
    agent_moa: str


class AgentRun(BaseModel):
    run_id: str
    mode: RunMode = "blinded"
    phase: RunPhase = "blinded"
    specimen_id: str
    budget_total: int
    budget_used: int = 0
    acquired_well_ids: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    hypothesis_history: list[HypothesisSnapshot] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    allowed_pmids: list[str] = Field(default_factory=list)  # every PMID search_literature returned in this run
    conclusion: Conclusion | None = None
    review: Review | None = None
    unblinding: Unblinding | None = None
    verdict: Verdict | None = None
    model_calls: list[CallRecord] = Field(default_factory=list)
    cost_usd: float = 0.0
    model_ids: dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=now)

    @property
    def budget_left(self) -> int:
        return self.budget_total - self.budget_used

    def evidence_ids(self) -> set[str]:
        return {e.evidence_id for e in self.evidence}


class RunEvent(BaseModel):
    """One SSE event; also one line of runs/<run_id>.jsonl."""

    type: EventType
    run_id: str
    seq: int
    ts: datetime = Field(default_factory=now)
    data: dict[str, Any] = Field(default_factory=dict)
