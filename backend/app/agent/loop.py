"""The Scientist's run (spec §5): blinded tool loop → PI review (≤ 1 revision) → unblinding → literature →
reflection → dossier. Every step is an event on the run's bus (SSE + runs/<run_id>.jsonl).

Tool calls: Chat Completions with the phase's tools. A turn may hold several (parallel) tool calls; they run
in the order given and each gets exactly one tool message with its call id, in the same order.

Guardrails here: max 20 Scientist turns per run (split across phases below), the per-run / daily $ caps
(LLMClient, CostCapExceeded ends the run), the well budget (ToolContext), the verdict computed by code, and in a
blinded run the leak scanner over every event and every message sent to a model until the unblinding.
Failures end the run with an `error` event and `run_finished {status: "failed"}`; nothing is filled in.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agent import prompts, reviewer
from app.agent import tools as T
from app.agent.dossier import build_json, build_markdown, citations_label, validate_reflection
from app.agent.guardrails import BlindingScanner, GuardrailError, validate_citations
from app.agent.schemas import AgentRun, RunEvent, Unblinding, Verdict
from app.config import MAX_MODEL_TURNS
from app.llm import CallRecord, LLMError
from app.runs.store import RunBus, write_json

# Scientist turns per phase; they sum to MAX_MODEL_TURNS (the reflection is the last turn).
BLINDED_TURNS = 13
REVISION_TURNS = 4
LITERATURE_TURNS = 2
REFLECTION_TURNS = 1
assert BLINDED_TURNS + REVISION_TURNS + LITERATURE_TURNS + REFLECTION_TURNS == MAX_MODEL_TURNS
MAX_NUDGES = 2  # text-only replies in a row before the phase gives up

PROTOCOL_RATIONALE = "Protocol step after the review: identify the unblinded specimen in PubChem and ChEMBL."


class BlindingLeak(RuntimeError):
    pass


class AgentLoop:
    def __init__(self, ctx: T.ToolContext, bus: RunBus, scanner: BlindingScanner | None, runs_dir=None):
        self.ctx = ctx
        self.run: AgentRun = ctx.run
        self.bus = bus
        self.scanner = scanner if self.run.mode == "blinded" else None
        self.runs_dir = runs_dir
        self.messages: list[dict] = []
        bus.guard = self._guard_event

    # --- blinding guard ---------------------------------------------------------------------------

    def _blinded(self) -> bool:
        return self.scanner is not None and self.run.phase in ("blinded", "review")

    def _check(self, payload: Any, what: str) -> None:
        if self._blinded() and self.scanner.scan(payload):
            # The message never names the term: it would leak it.
            raise BlindingLeak(f"Blinding guard stopped the run: {what} contained compound-identifying text.")

    def _guard_event(self, event: RunEvent) -> None:
        if event.type not in ("error", "run_finished"):
            self._check(event.data, f"a '{event.type}' event")

    def _withhold_leaks(self) -> None:
        """After a leak, blank whatever holds it, so the run snapshot and GET /api/runs cannot show it."""
        for e in self.run.evidence:
            if self.scanner.scan(e.model_dump(mode="json")):
                e.summary, e.data, e.image_ids = "Withheld by the blinding guard.", {"withheld": True}, []
        for s in self.run.steps:
            if self.scanner.scan(s.model_dump(mode="json")):
                s.args, s.rationale, s.error = {"withheld": True}, "Withheld by the blinding guard.", None

    # --- events -----------------------------------------------------------------------------------

    def _emit(self, type: str, **data: Any) -> None:
        self.bus.emit(type, data)

    def _cost(self, record: CallRecord | None = None) -> None:
        self.run.model_calls = list(self.ctx.meter.calls)
        self.run.cost_usd = self.ctx.meter.total_usd
        last = {k: getattr(record, k) for k in ("purpose", "model", "provider", "billing", "input_tokens",
                                                 "output_tokens", "reasoning_tokens", "cost_usd", "seconds")} if record else None
        self._emit("cost_updated", cost_usd=round(self.run.cost_usd, 5), calls=len(self.run.model_calls), last=last)

    # --- run --------------------------------------------------------------------------------------

    async def run_all(self) -> AgentRun:
        run, ctx = self.run, self.ctx
        try:
            self._emit("run_started", specimen_id=run.specimen_id, mode=run.mode, budget_total=run.budget_total,
                       model_ids=run.model_ids, moa_classes=ctx.moa_classes, max_turns=MAX_MODEL_TURNS,
                       doses=len(ctx.specimen.concentrations_uM))
            self.messages = [
                {"role": "system", "content": prompts.scientist_system(ctx.moa_classes, run.budget_total,
                                                                       BLINDED_TURNS, run.mode)},
                {"role": "user", "content": prompts.scientist_start(
                    run.specimen_id, run.budget_total, ctx.specimen.compound if run.mode == "open" else None)}]
            if not await self._tool_phase(BLINDED_TURNS, until="conclusion"):
                raise LLMError("The Scientist did not submit a conclusion within its turns.")
            await self._review()
            await self._unblind()
            await self._tool_phase(LITERATURE_TURNS, until="text")
            await self._reflect()
            self._dossier()
            run.status, run.phase = "finished", "finished"
        except (LLMError, BlindingLeak, GuardrailError) as exc:
            if isinstance(exc, BlindingLeak):
                self._withhold_leaks()
            run.status, run.error = "failed", str(exc)
            self._emit("error", message=str(exc), fatal=True)
        except asyncio.CancelledError:
            run.status, run.error = "failed", "The run was stopped."
            self._emit("error", message=run.error, fatal=True)
            raise
        except Exception as exc:  # noqa: BLE001 - a bug must still end the stream visibly
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
            self._emit("error", message=f"Internal error: {type(exc).__name__}: {exc}", fatal=True)
        finally:
            self.run.model_calls = list(ctx.meter.calls)
            self.run.cost_usd = ctx.meter.total_usd
            if self.runs_dir is not None:
                write_json(self.runs_dir / f"{run.run_id}.json", run.model_dump(mode="json"))
            self._emit("run_finished", status=run.status, error=run.error, cost_usd=round(run.cost_usd, 5),
                       turns=run.turns, budget_used=run.budget_used,
                       verdict=run.verdict.model_dump() if run.verdict else None)
        return run

    async def _tool_phase(self, max_turns: int, until: str) -> bool:
        """Scientist turns with the phase's tools. until="conclusion": stop when submit_conclusion succeeds
        (the last turn offers only that tool). until="text": stop at the first reply without tool calls."""
        nudges = 0
        for i in range(max_turns):
            last = i == max_turns - 1
            specs = T.tool_specs(self.ctx)
            if until == "conclusion" and last:
                specs = [s for s in specs if s["function"]["name"] == "submit_conclusion"]
                self.messages.append({"role": "user", "content": prompts.LAST_TURN})
            if not specs:
                return False
            message = await self._turn(specs)
            calls = message.tool_calls or []
            if not calls:
                if until == "text":
                    return True
                nudges += 1
                if nudges > MAX_NUDGES:
                    return False
                self.messages.append({"role": "user", "content": prompts.NUDGE})
                continue
            nudges = 0
            offered = {spec["function"]["name"] for spec in specs}
            for call in calls:
                await self._execute(call, offered=offered)
            if until == "conclusion" and self.run.phase == "review":
                return True
        return until == "text"

    async def _turn(self, specs: list[dict]):
        run = self.run
        run.turns += 1
        self._emit("thinking", turn=run.turns, phase=run.phase)
        self._check({"messages": self.messages, "tools": specs}, "the Scientist's request")
        result = await self.ctx.llm.chat(model=self.ctx.settings.scientist_id, messages=self.messages,
                                         meter=self.ctx.meter, purpose=f"scientist turn {run.turns}", tools=specs)
        self._cost(result.record)
        m = result.message
        entry: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
        if m.tool_calls:
            entry["tool_calls"] = [{"id": c.id, "type": "function",
                                    "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                   for c in m.tool_calls]
        self.messages.append(entry)
        if (m.content or "").strip():
            # After unblinding the note may cite papers: the same citation check as the dossier applies.
            note = validate_citations(m.content.strip(), run.allowed_pmids).text if run.phase == "unblinded"                 else m.content.strip()
            self._emit("thinking", turn=run.turns, phase=run.phase, note=note[:2000])
        return m

    async def _execute(self, call, offered: set[str] | None = None) -> T.ToolOutcome:
        name, raw = call.function.name, call.function.arguments
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {"_unparsed": str(raw)[:500]}
        args = args if isinstance(args, dict) else {"_unparsed": str(args)[:500]}
        rationale = " ".join(str(args.get("rationale", "")).split())[:T.RATIONALE_MAX]
        shown = {k: v for k, v in args.items() if k != "rationale"}
        self._emit("tool_call", call_id=call.id, name=name, args=shown, rationale=rationale)

        if offered is not None and name not in offered:  # e.g. the forced last turn offers only submit_conclusion
            outcome = T.ToolOutcome(name=name, ok=False, rationale=rationale, args=shown,
                                    error=f"'{name}' is not offered this turn. Offered: {sorted(offered)}.")
        else:
            outcome = await T.execute(self.ctx, name, raw)
        content = outcome.for_model()
        self._check(content, f"the result of {name}")
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
        ev = outcome.evidence
        self._emit("tool_result", call_id=call.id, name=name, ok=outcome.ok, error=outcome.error,
                   evidence_id=ev.evidence_id if ev else None, kind=ev.kind if ev else None,
                   summary=ev.summary if ev else outcome.error, image_ids=ev.image_ids if ev else [],
                   data=ev.data if ev else None, cost_usd=round(outcome.cost_usd, 5))
        if outcome.cost_usd:
            self._cost(self.ctx.meter.calls[-1])
        if outcome.ok:
            self._after(name, outcome)
        return outcome

    def _after(self, name: str, outcome: T.ToolOutcome) -> None:
        run = self.run
        if name == "acquire_well":
            self._emit("budget_updated", budget_total=run.budget_total, budget_used=run.budget_used,
                       budget_left=run.budget_left, acquired_well_ids=list(run.acquired_well_ids))
        elif name == "update_hypotheses":
            self._emit("hypotheses_updated", step=len(run.steps), evidence_id=outcome.evidence.evidence_id,
                       hypotheses=[h.model_dump() for h in run.hypotheses])
        elif name == "submit_conclusion":
            run.conclusion_history.append(run.conclusion)
            self._emit("conclusion", revision=len(run.conclusion_history) - 1, **run.conclusion.model_dump())

    # --- review -----------------------------------------------------------------------------------

    async def _review(self) -> None:
        run, ctx = self.run, self.ctx
        self._check(reviewer.ledger(run, ctx.specimen), "the reviewer's ledger")
        review, record, _ = await reviewer.review(ctx.llm, ctx.settings.reviewer_model, run, ctx.specimen,
                                                  ctx.moa_classes, ctx.meter)
        run.review = review
        self._cost(record)
        self._emit("review", round=1, model=record.model, **review.model_dump())
        if review.verdict != "request_experiment":
            return
        run.phase = "blinded"  # one revision round: blinded tools are unlocked again
        self.messages.append({"role": "user", "content": prompts.revision_request(
            review.critique, review.requested_experiment or "", run.budget_left, REVISION_TURNS)})
        if not await self._tool_phase(REVISION_TURNS, until="conclusion"):
            run.phase, run.conclusion = "review", run.conclusion_history[-1]
            self._emit("error", message="The Scientist did not resubmit after the review; its first conclusion "
                                        "stands.", fatal=False)

    # --- unblinding -------------------------------------------------------------------------------

    async def _unblind(self) -> None:
        run, ctx, sp = self.run, self.ctx, self.ctx.specimen
        run.phase = "unblinded"

        # The protocol's own lookup (a step + evidence, no tool_call event): the reveal is the `unblinded` event.
        outcome = await T.execute(ctx, "lookup_compound", {"rationale": PROTOCOL_RATIONALE, "name": sp.compound})
        if outcome.cost_usd:
            self._cost(ctx.meter.calls[-1])
        data = outcome.evidence.data if outcome.ok else {}
        pubchem, chembl = data.get("pubchem") or {}, data.get("chembl") or {}
        run.unblinding = Unblinding(
            compound=sp.compound, pubchem_cid=pubchem.get("cid"), structure_url=pubchem.get("structure_png_url"),
            chembl_mechanisms=chembl.get("mechanisms", []),
            identity_check=data.get("identity_check") or f"lookup failed: {outcome.error}")
        self._emit("unblinded", **run.unblinding.model_dump(), chembl_id=chembl.get("molecule_chembl_id"),
                   evidence_id=outcome.evidence.evidence_id if outcome.ok else None)
        agent_moa = run.conclusion.top_moa
        run.verdict = Verdict(correct=agent_moa == sp.moa, truth_moa=sp.moa, agent_moa=agent_moa)
        self._emit("verdict", **run.verdict.model_dump())
        mechs = [m.get("mechanism_of_action", "") for m in run.unblinding.chembl_mechanisms]
        self.messages.append({"role": "user", "content": prompts.unblinded_brief(
            sp.compound, run.unblinding.identity_check, mechs, agent_moa, sp.moa, run.verdict.correct)})

    # --- reflection and dossier -------------------------------------------------------------------

    def _reflection_input(self) -> str:
        run = self.run
        lit = [{"pmid": a["pmid"], "title": a["title"], "journal": a.get("journal"), "year": a.get("year")}
               for e in run.evidence if e.kind == "literature" for a in e.data.get("articles", [])]
        payload = {
            "evidence": [{"id": e.evidence_id, "tool": e.tool, "summary": e.summary} for e in run.evidence],
            "steps": [{"tool": s.tool, "why": s.rationale, "error": s.error} for s in run.steps],
            "hypothesis_history": [h.model_dump() for h in run.hypothesis_history],
            "conclusions": [c.model_dump() for c in run.conclusion_history],
            "review": run.review.model_dump() if run.review else None,
            "unblinding": run.unblinding.model_dump() if run.unblinding else None,
            "verdict": run.verdict.model_dump() if run.verdict else None,
        }
        return (json.dumps(payload, indent=1, default=str) + "\n\nLiterature found in this run:\n"
                + (json.dumps(lit, indent=1) if lit else "(none)"))

    async def _reflect(self) -> None:
        run, ctx = self.run, self.ctx
        run.turns += 1
        self._emit("thinking", turn=run.turns, phase=run.phase, note="Writing the reflection.")
        raw, record = await ctx.llm.chat_json(
            model=ctx.settings.scientist_id, meter=ctx.meter, purpose="reflection",
            messages=[{"role": "system", "content": prompts.REFLECTION_SYSTEM},
                      {"role": "user", "content": self._reflection_input()}],
            schema_name="reflection", schema=prompts.reflection_schema())
        self._cost(record)
        run.reflection = validate_reflection(raw, run.allowed_pmids)

    def _dossier(self) -> None:
        run = self.run
        doc = build_json(run)
        md = build_markdown(run, doc)
        paths = {}
        if self.runs_dir is not None:
            write_json(self.runs_dir / f"{run.run_id}.dossier.json", doc)
            md_path = self.runs_dir / f"{run.run_id}.dossier.md"
            md_path.write_text(md, encoding="utf-8")
            paths = {"markdown": md_path.name, "json": f"{run.run_id}.dossier.json"}
        r = run.reflection
        self._emit("report_ready", files=paths, citations_label=citations_label(r),
                   citations_verified=[c.pmid for c in r.citations] if r else [],
                   citations_removed=r.citations_removed if r else [],
                   reflection=r.model_dump() if r else None, markdown=md)
