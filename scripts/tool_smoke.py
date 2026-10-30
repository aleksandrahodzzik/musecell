"""Smoke test: run each of the nine agent tools once on one blinded specimen; print results and cost.

1. GET /v1/models; refuse to run if a configured model id is missing.
2. One real Scientist call (muse-spark-1.3) with the seven blinded tool schemas, asked to call
   list_available_experiments — checks that the API accepts our strict schemas; the call it makes is executed.
3. The other tools with scripted arguments (this is a tool test, not an agent run; the hypotheses are taken
   from the atlas vote, not from a model). describe_phenotype makes a real Microscopist vision call.
4. Blinding-leak scan of every blinded-phase payload, then unblinding: lookup_compound + search_literature.

Writes docs/probe/tool_smoke.json. Usage: python scripts/tool_smoke.py [--specimen s_…] [--budget 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.agent import tools as T  # noqa: E402
from app.agent.guardrails import BlindingScanner, load_moa_classes  # noqa: E402
from app.agent.schemas import AgentRun, Verdict  # noqa: E402
from app.agent.specimens import SpecimenCatalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.external.chembl import ChemblClient  # noqa: E402
from app.external.pubchem import PubChemClient  # noqa: E402
from app.external.pubmed import PubMedClient  # noqa: E402
from app.llm import CostMeter, LLMClient  # noqa: E402
from app.science.atlas import Atlas  # noqa: E402

OUT = ROOT / "docs" / "probe" / "tool_smoke.json"


def show(outcome: T.ToolOutcome) -> None:
    status = "ok " if outcome.ok else "ERR"
    text = outcome.evidence.summary if outcome.ok else outcome.error
    print(f"  [{status}] {outcome.name:<28} ${outcome.cost_usd:.4f}  {text}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--specimen", help="specimen id (default: the first one)")
    parser.add_argument("--budget", type=int, default=3)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # µ on a Windows console

    settings = get_settings()
    llm = LLMClient(settings)
    available = await llm.check_models()
    print(f"models OK: {llm.configured_models} (API offers {len(available)})")
    if not settings.ncbi_email:
        print("note: NCBI_EMAIL is empty; PubMed requests carry tool=musecell only. Set it in backend/.env.")

    catalog = SpecimenCatalog.load(settings)
    atlas = Atlas.load()
    classes = load_moa_classes(settings.moa_csv)
    sid = args.specimen or sorted(catalog.specimens)[0]
    specimen = catalog.get(sid)
    run = AgentRun(run_id="smoke_" + uuid.uuid4().hex[:8], specimen_id=sid, budget_total=args.budget,
                   model_ids={"scientist": settings.scientist_id, "microscopist": settings.microscopist_id})
    meter = CostMeter(run_id=run.run_id, cap_usd=settings.run_cost_cap_usd)
    pubchem, chembl = PubChemClient(settings.cache_dir), ChemblClient(settings.cache_dir)
    pubmed = PubMedClient(settings.cache_dir, settings.ncbi_email)
    ctx = T.ToolContext(run=run, specimen=specimen, catalog=catalog, atlas=atlas, moa_classes=classes, meter=meter,
                        llm=llm, pubchem=pubchem, chembl=chembl, pubmed=pubmed, settings=settings)
    print(f"specimen {sid} (blinded): {len(specimen.concentrations_uM)} doses, {len(specimen.wells)} wells; "
          f"budget {args.budget}; {len(classes)} MoA classes")

    # Leak terms: name, PubChem synonyms, SMILES, plates, well keys (server side, never sent to the model).
    synonyms = (await pubchem.synonyms((await pubchem.lookup(specimen.compound)).cid)) if specimen.compound else []
    scanner = BlindingScanner(specimen.blinding_terms(synonyms))

    print("\nblinded phase")
    outcomes: list[T.ToolOutcome] = []
    messages = [{"role": "system", "content": "You are testing a tool interface. Call exactly one tool."},
                {"role": "user", "content": "Call list_available_experiments once, with a one-sentence rationale."}]
    scanner.assert_clean({"messages": messages, "tools": T.tool_specs(ctx)}, "scientist request")
    res = await llm.chat(model=settings.scientist_id, messages=messages, meter=meter, purpose="schema check",
                         tools=T.tool_specs(ctx), reasoning_effort="low")
    calls = res.message.tool_calls or []
    print(f"  scientist accepted {len(T.tool_specs(ctx))} strict tool schemas; it called "
          f"{[c.function.name for c in calls]} (${res.record.cost_usd:.4f}, {res.record.input_tokens} in / "
          f"{res.record.output_tokens} out)")
    for c in calls[:1]:
        outcomes.append(await T.execute(ctx, c.function.name, c.function.arguments))
        show(outcomes[-1])
        print(f"        model's rationale: {outcomes[-1].rationale!r}")

    async def step(tool: str, **kw) -> T.ToolOutcome:
        out = await T.execute(ctx, tool, {"rationale": f"smoke test of {tool}", **kw})
        outcomes.append(out)
        show(out)
        return out

    top = len(specimen.concentrations_uM) - 1
    low = await step("acquire_well", concentration_index=top // 2, replicate=1)
    high = await step("acquire_well", concentration_index=top, replicate=1)
    wells = [o.evidence.data for o in (low, high) if o.ok]
    pheno = await step("describe_phenotype", field_ids=wells[-1]["field_ids"][:2])
    if pheno.ok:
        print("        " + json.dumps(pheno.evidence.data["report"], indent=1).replace("\n", "\n        "))
    near = await step("compare_to_reference_atlas", well_ids=[w["well_id"] for w in wells], k=5)
    await step("dose_response", feature="frac_mitotic_like")
    votes = near.evidence.data["moa_summary"]
    await step("update_hypotheses", hypotheses=[
        {"moa": v["moa"], "probability": v["neighbors"], "supporting_evidence_ids": [near.evidence.evidence_id],
         "contradicting_evidence_ids": []} for v in votes])
    best = run.hypotheses[0]
    await step("submit_conclusion", top_moa=best.moa, probability=round(best.probability, 2),
               key_evidence_ids=[near.evidence.evidence_id], next_experiment_if_this_were_a_real_lab="smoke test")

    blinded_payloads = [json.loads(o.for_model()) for o in outcomes] + [run.model_dump(mode="json")]
    hits = scanner.scan(blinded_payloads)
    print(f"  blinding-leak scan over {len(blinded_payloads)} payloads, {len(scanner.terms)} terms: "
          f"{'CLEAN' if not hits else hits[:3]}")

    print("\nunblinded phase")
    run.phase = "unblinded"
    print(f"  specimen {sid} is {specimen.compound} (truth MoA: {specimen.moa})")
    await step("lookup_compound", name=specimen.compound)
    await step("search_literature", query=f"{specimen.compound} mechanism of action", max_results=3)
    run.verdict = Verdict(correct=run.conclusion.top_moa == specimen.moa, truth_moa=specimen.moa,
                          agent_moa=run.conclusion.top_moa)
    print(f"  verdict (code, scripted atlas vote): {'correct' if run.verdict.correct else 'wrong'}; "
          f"allowed PMIDs {run.allowed_pmids}")

    print("\ncost")
    for c in meter.calls:
        print(f"  {c.purpose:<14} {c.model:<18} {c.input_tokens:>7} in {c.output_tokens:>6} out "
              f"({c.reasoning_tokens} reasoning) {c.seconds:>6.1f}s  ${c.cost_usd:.4f}")
    print(f"  total ${meter.total_usd:.4f} for {len(meter.calls)} model calls; "
          f"today so far ${llm.ledger.total_today():.4f}")
    failed = [o.name for o in outcomes if not o.ok]
    print(f"\n{len(outcomes)} tool calls, {len(failed)} failed {failed if failed else ''}; leak scan "
          f"{'clean' if not hits else 'FAILED'}")

    OUT.write_text(json.dumps({"run": run.model_dump(mode="json"), "leak_hits": [h.model_dump() for h in hits],
                               "total_cost_usd": meter.total_usd}, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for c in (pubchem, chembl, pubmed):
        await c.aclose()
    await llm.aclose()
    if failed or hits:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
