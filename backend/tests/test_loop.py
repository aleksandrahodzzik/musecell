"""Agent loop on the synthetic specimen with a scripted model: parallel tool calls, phases, review + revision,
unblinding, verdict by code, citation validation, turn cap, cost cap, blinding guard, event log."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent import loop as L
from app.agent import tools as T
from app.agent.guardrails import BlindingScanner
from app.llm import CallRecord, CostCapExceeded
from app.runs.store import RunBus
from tests.test_tools import COMPOUND, FakeChembl, FakeLLM, FakePubChem, FakePubMed, _pc, atlas, catalog, make_ctx  # noqa: F401

SYNONYMS = ["Testazol", "TZ-12345"]


def tc(i, name, **args):
    args.setdefault("rationale", f"testing {name}")
    return SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ScriptedLLM(FakeLLM):
    """Scientist turns come from `script` (a list of callables run -> list of tool calls, or a str for text)."""

    def __init__(self, script, review=None, reflection=None, cost=0.001, pheno_extra=""):
        super().__init__(cost=cost)
        self.script = list(script)
        self.review = review or {"verdict": "approve", "critique": "Evidence is consistent.", "requested_experiment": ""}
        self.reflection = reflection
        self.pheno_extra = pheno_extra
        self.sent: list[dict] = []  # every request: purpose, messages snapshot, tool names

    async def chat(self, *, model, messages, meter, purpose, tools=None, **kw):
        self.sent.append({"purpose": purpose, "messages": json.loads(json.dumps(messages)),
                          "tools": [t["function"]["name"] for t in tools or []]})
        rec = CallRecord(model=model, purpose=purpose, cost_usd=self.cost)
        meter.add(rec)
        step = self.script.pop(0) if self.script else "done"
        out = step(self) if callable(step) else step
        if isinstance(out, str):
            return SimpleNamespace(message=SimpleNamespace(content=out, tool_calls=None), record=rec)
        return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=out), record=rec)

    async def chat_json(self, *, model, messages, meter, purpose, schema_name, schema, **kw):
        self.sent.append({"purpose": purpose, "messages": json.loads(json.dumps(messages)), "tools": []})
        if purpose == "microscopist":
            reply, rec = await super().chat_json(model=model, messages=messages, meter=meter, purpose=purpose,
                                                 schema_name=schema_name, schema=schema)
            reply["uncertainty"] += self.pheno_extra
            return reply, rec
        rec = CallRecord(model=model, purpose=purpose, cost_usd=self.cost)
        meter.add(rec)
        if purpose == "reviewer":
            return self.review, rec
        return self.reflection or {
            "reflection": "The atlas call held (PMID 101). A paper I recall (PMID 999) is not from this run.",
            "what_was_right": ["dose choice"], "what_was_wrong": ["PMID 31415 claim"],
            "citations": [{"pmid": "101", "claim": "supports it"}, {"pmid": "999", "claim": "invented"}],
            "next_experiment": "Eg5 ATPase assay"}, rec


def well_ids(run):
    return [e.data["well_id"] for e in run.evidence if e.kind == "well"]


def fields(run):
    return next(e for e in run.evidence if e.kind == "well").data["field_ids"][:2]


HAPPY = [
    lambda llm: [tc(1, "list_available_experiments"), tc(2, "acquire_well", concentration_index=0, replicate=1),
                 tc(3, "acquire_well", concentration_index=2, replicate=1)],
    lambda llm: [tc(4, "describe_phenotype", field_ids=fields(llm.run)),
                 tc(5, "compare_to_reference_atlas", well_ids=well_ids(llm.run), k=5)],
    lambda llm: [tc(6, "update_hypotheses", hypotheses=[
        {"moa": "Eg5 inhibitors", "probability": 0.7, "supporting_evidence_ids": ["e5"], "contradicting_evidence_ids": []},
        {"moa": "Microtubule destabilizers", "probability": 0.3, "supporting_evidence_ids": [],
         "contradicting_evidence_ids": ["e5"]}])],
    lambda llm: [tc(7, "submit_conclusion", top_moa="Eg5 inhibitors", probability=0.7, key_evidence_ids=["e5"],
                    next_experiment_if_this_were_a_real_lab="Eg5 ATPase assay"),
                 tc(8, "acquire_well", concentration_index=1, replicate=1)],  # after submit: refused (review phase)
    lambda llm: [tc(9, "search_literature", query="eg5 inhibitor monopolar spindle", max_results=2)],
    "I have the papers I need (PMID 202; PMID 777 from memory).",
]


def make_loop(catalog, atlas, tmp_path, llm, budget=3, mode="blinded"):
    ctx = make_ctx(catalog, atlas, budget=budget, llm=llm)
    ctx.run.mode = mode
    ctx.pubchem = FakePubChem({COMPOUND: _pc(1, "AAAAAAAAAAAAAA-XX-N")}, _pc(1, "AAAAAAAAAAAAAA-YY-N"))
    ctx.chembl, ctx.pubmed = FakeChembl(), FakePubMed()
    llm.run = ctx.run
    bus = RunBus(ctx.run.run_id, tmp_path / f"{ctx.run.run_id}.jsonl")
    scanner = BlindingScanner(ctx.specimen.blinding_terms(SYNONYMS))
    return L.AgentLoop(ctx, bus, scanner, runs_dir=tmp_path), bus, scanner


def run(loop):
    return asyncio.run(loop.run_all())


def types(bus):
    return [e.type for e in bus.events]


def test_full_blinded_run(catalog, atlas, tmp_path):
    llm = ScriptedLLM(HAPPY)
    loop, bus, scanner = make_loop(catalog, atlas, tmp_path, llm)
    r = run(loop)
    assert r.status == "finished", r.error

    # parallel calls: one tool message per call id, in order, right after the assistant message
    first = llm.sent[1]["messages"]
    i = next(k for k, m in enumerate(first) if m.get("tool_calls"))
    assert [m["tool_call_id"] for m in first[i + 1:i + 4]] == ["call_1", "call_2", "call_3"]
    assert all(m["role"] == "tool" for m in first[i + 1:i + 4])
    def has_call7(msgs):
        return any(m.get("tool_calls") and m["tool_calls"][0]["id"] == "call_7" for m in msgs)

    after_submit = next(s["messages"] for s in llm.sent if has_call7(s["messages"]))
    j = next(k for k, m in enumerate(after_submit) if has_call7([m]))
    assert [m["tool_call_id"] for m in after_submit[j + 1:j + 3]] == ["call_7", "call_8"]
    assert "not available in the review phase" in after_submit[j + 2]["content"] and r.budget_used == 2

    t = types(bus)
    for expected in ("run_started", "thinking", "tool_call", "tool_result", "budget_updated", "hypotheses_updated",
                     "conclusion", "review", "unblinded", "verdict", "report_ready", "cost_updated", "run_finished"):
        assert expected in t, expected
    assert t[0] == "run_started" and t[-1] == "run_finished" and "error" not in t
    assert t.index("conclusion") < t.index("review") < t.index("unblinded") < t.index("verdict")
    assert [e.seq for e in bus.events] == list(range(1, len(bus.events) + 1))
    lines = (tmp_path / f"{r.run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(bus.events) and json.loads(lines[-1])["type"] == "run_finished"

    # verdict by code; unblinding via the protocol lookup
    assert r.verdict.correct and r.verdict.truth_moa == "Eg5 inhibitors"
    assert r.unblinding.compound == COMPOUND and "agree" in r.unblinding.identity_check
    unblinded = bus.events[t.index("unblinded")].data
    assert unblinded["evidence_id"] and r.steps[-2].tool == "lookup_compound"  # protocol step, then literature

    # citations: only PMIDs search_literature returned survive
    assert r.allowed_pmids == ["101", "202"]
    assert [c.pmid for c in r.reflection.citations] == ["101"]
    assert set(r.reflection.citations_removed) == {"999", "31415"} and "999" not in r.reflection.text
    report = next(e for e in bus.events if e.type == "report_ready").data
    assert report["citations_label"] == "Citations verified: 1/3"
    assert (tmp_path / f"{r.run_id}.dossier.md").exists() and (tmp_path / f"{r.run_id}.json").exists()
    assert "Citations verified: 1/3" in report["markdown"] and "PMID [101]" in report["markdown"]
    assert r.turns == 6 + 1  # 6 scripted turns + reflection
    notes = [e.data["note"] for e in bus.events if e.type == "thinking" and "note" in e.data]
    assert any("PMID 202" in n for n in notes) and not any("777" in n for n in notes)

    # blinding: nothing before the unblinding names the compound, neither events nor model requests
    cut = t.index("unblinded")
    assert scanner.scan([e.data for e in bus.events[:cut]]) == []
    blinded_requests = [s for s in llm.sent if s["purpose"] in ("microscopist", "reviewer")
                        or s["purpose"] in {f"scientist turn {n}" for n in range(1, 5)}]
    assert scanner.scan(blinded_requests) == []
    assert scanner.scan(bus.events[cut].data)  # and the unblinded event does carry it


def test_review_requests_one_revision(catalog, atlas, tmp_path):
    script = HAPPY[:4] + [
        lambda llm: [tc(10, "acquire_well", concentration_index=1, replicate=1)],
        lambda llm: [tc(11, "submit_conclusion", top_moa="Microtubule destabilizers", probability=0.5,
                        key_evidence_ids=["e5"], next_experiment_if_this_were_a_real_lab="tubulin polymerisation")],
    ] + HAPPY[4:]
    llm = ScriptedLLM(script, review={"verdict": "request_experiment", "critique": "Single dose pair.",
                                      "requested_experiment": "dose 1 replicate 1"})
    loop, bus, _ = make_loop(catalog, atlas, tmp_path, llm)
    r = run(loop)
    assert r.status == "finished", r.error
    assert [c.top_moa for c in r.conclusion_history] == ["Eg5 inhibitors", "Microtubule destabilizers"]
    assert r.conclusion.top_moa == "Microtubule destabilizers" and not r.verdict.correct
    assert sum(s["purpose"] == "reviewer" for s in llm.sent) == 1  # at most one review round
    assert types(bus).count("conclusion") == 2 and r.budget_used == 3
    revision = next(s for s in llm.sent if s["purpose"] == "scientist turn 5")
    assert "dose 1 replicate 1" in revision["messages"][-1]["content"]


def test_turn_cap_forces_conclusion_or_fails(catalog, atlas, tmp_path):
    llm = ScriptedLLM([lambda llm: [tc(n, "list_available_experiments")] for n in range(30)])
    loop, bus, _ = make_loop(catalog, atlas, tmp_path, llm)
    r = run(loop)
    assert r.status == "failed" and "did not submit a conclusion" in r.error
    assert r.turns == L.BLINDED_TURNS
    assert llm.sent[L.BLINDED_TURNS - 1]["tools"] == ["submit_conclusion"]  # last turn offers only this
    last_result = [e for e in bus.events if e.type == "tool_result"][-1].data
    assert not last_result["ok"] and "not offered this turn" in last_result["error"]
    assert types(bus)[-2:] == ["error", "run_finished"] and bus.events[-1].data["status"] == "failed"


def test_cost_cap_ends_run_with_error_event(catalog, atlas, tmp_path):
    class Capped(ScriptedLLM):
        async def chat(self, **kw):
            raise CostCapExceeded("Run cost cap reached: $1.0000 of $1.00.")

    loop, bus, _ = make_loop(catalog, atlas, tmp_path, Capped([]))
    r = run(loop)
    assert r.status == "failed" and "cost cap" in r.error
    assert types(bus)[-2:] == ["error", "run_finished"]


def test_blinding_guard_stops_a_leaking_run(catalog, atlas, tmp_path):
    llm = ScriptedLLM(HAPPY, pheno_extra=f" Looks like {SYNONYMS[0].upper()}.")  # the microscopist "names" it
    loop, bus, scanner = make_loop(catalog, atlas, tmp_path, llm)
    r = run(loop)
    assert r.status == "failed" and "Blinding guard" in r.error
    assert scanner.scan([e.data for e in bus.events]) == []  # the leaking payload went nowhere
    assert scanner.scan(llm.sent[2:]) == []  # and was not sent back to the Scientist
    assert "unblinded" not in types(bus)
    assert scanner.scan(r.model_dump(mode="json")) == []  # the snapshot (GET /api/runs) is clean too
    assert any(e.data == {"withheld": True} for e in r.evidence)
