"""Open-world eval (agent "v5-open", docs/DECISIONS.md "Open-world eval, pre-registered"): the specimen's whole MoA
class is left out of the reference library. The key test proves that no reference of the held-out class is
reachable from any blinded tool or from the protocol call; the others check the wiring (prompts, reviewer, final
call, model ids) and, when the BBBC021 data is present, the exclusion on all 38 real specimens."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from app.agent import prompts, reviewer
from app.agent import tools as T
from app.agent.open_world import OPEN_WORLD_VERSION, REFERENCE_LIBRARY, held_out_well_keys, restricted_atlas
from app.agent.schemas import AgentRun
from app.agent.specimens import SpecimenCatalog
from app.config import Settings
from app.llm import CostMeter
from app.science.atlas import Atlas
from app.science.features import PROFILE_FEATURES
from tests.test_loop import HAPPY, ScriptedLLM, make_loop, run as run_loop
from tests.test_tools import FakeLLM, FakePubMed, atlas, catalog  # noqa: F401  (fixtures)

PLATE = "Week9_90002"
HELD_OUT = "Eg5 inhibitors"
CLASSES = ["Eg5 inhibitors", "Microtubule destabilizers", "Protein synthesis"]
# compound -> (class, signature features); "testazole" is the specimen, "refA" the other compound of its class
COMPOUNDS = {"testazole": (HELD_OUT, [0, 21]), "refA": (HELD_OUT, [0, 20]),
             "refB": ("Microtubule destabilizers", [5, 10]), "refC": ("Protein synthesis", [3, 7])}
CONCS = [0.1, 1.0, 10.0]


@pytest.fixture
def world(tmp_path):
    """A catalog where the held-out class has two compounds, and a deep-style atlas (per-well vectors) over it."""
    rng = np.random.default_rng(0)
    index, wells, n = [], [], 0

    def add_well(well, compound, conc, moa, role, rep, base):
        nonlocal n
        fids = []
        for field in (1, 2):
            n += 1
            fid = f"f_{n:012x}"
            fids.append(fid)
            row = {"field_id": fid, "plate": PLATE, "well": well, "field": field, "compound": compound,
                   "concentration_uM": conc, "moa": moa, "role": role, "replicate": rep}
            for img in ("composite", "dna", "tubulin", "actin"):
                rel = f"processed/fields/{fid}/{img}.png"
                (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB" if img == "composite" else "L", (32, 24), 30).save(tmp_path / rel)
                row[f"png_{img}"] = rel
            index.append(row)
        wells.append({"plate": PLATE, "well": well, "well_key": f"{PLATE}/{well}", "compound": compound,
                      "concentration_uM": conc, "moa": moa, "role": role, "replicate": rep, "field_ids": fids,
                      "n_cells": 50.0, **dict(zip(PROFILE_FEATURES, base + rng.normal(0, 0.1, len(PROFILE_FEATURES))))})

    for c, (compound, (moa, sig)) in enumerate(COMPOUNDS.items()):
        base = np.zeros(len(PROFILE_FEATURES))
        base[sig] = [4.0, 6.0]
        for i, conc in enumerate(CONCS):
            for rep in (1, 2):
                add_well(f"{'ABCD'[c]}{i}{rep}", compound, conc, moa, "treated", rep, base * (i + 1) / 3)
    add_well("H01", "DMSO", 0.0, None, "control", 1, np.zeros(len(PROFILE_FEATURES)))
    norm = pd.DataFrame(wells)
    raw = norm.copy()
    raw[PROFILE_FEATURES] = raw[PROFILE_FEATURES] + 10
    cat = SpecimenCatalog(pd.DataFrame(index), raw, norm, salt="ow-salt", data_path=tmp_path)

    treated = norm[norm["role"] == "treated"]
    tr = treated.groupby(["compound", "concentration_uM"])[PROFILE_FEATURES].median().reset_index()
    tr["moa"] = tr["compound"].map(lambda c: COMPOUNDS[c][0])
    tr["moa_annotated_conc"] = True
    tr["thumbnail_field_id"] = [f"f_thumb{c}{i}" for i, c in enumerate(tr["compound"])]
    atl = Atlas(tr)
    atl._wells = norm.set_index("well_key")[PROFILE_FEATURES].astype(float)  # per-well vectors, like the deep atlas
    specimen = next(sp for sp in cat.specimens.values() if sp.compound == "testazole")
    return cat, atl, specimen


def _ctx(cat, atl, sp, settings=None):
    run = AgentRun(run_id="r_ow", specimen_id=sp.specimen_id, budget_total=len(sp.wells))
    return T.ToolContext(run=run, specimen=sp, catalog=cat, atlas=atl, moa_classes=CLASSES,
                         meter=CostMeter(run_id="r_ow", cap_usd=1.0), llm=FakeLLM(), pubmed=FakePubMed(),
                         settings=settings or Settings(_env_file=None, open_world=True, final_call="scientist"))


def _call(ctx, tool, **args):
    args.setdefault("rationale", "testing")
    out = asyncio.run(T.execute(ctx, tool, args))
    assert out.ok, (tool, out.error)
    return out


def _atlas_classes(data: dict) -> set[str]:
    """Every MoA an atlas output names: neighbours, k-neighbour summary, per-well votes, tally, vote."""
    nsc = data.get("nsc", data)
    return ({n["moa"] for n in data.get("neighbors", [])} | {r["moa"] for r in data.get("moa_summary", [])}
            | {w["nearest_moa"] for w in nsc.get("per_well", [])} | {t["moa"] for t in nsc.get("tally", [])}
            | ({nsc["moa"]} if nsc.get("moa") else set()))


def test_restricted_atlas_drops_the_class_and_its_other_wells(world):
    cat, atl, sp = world
    other = held_out_well_keys(cat, sp)
    assert other and not other & {w.well_key for w in sp.wells}  # refA's wells, never the specimen's own
    r = restricted_atlas(atl, cat, sp)
    assert r.excluded_moa == HELD_OUT and HELD_OUT not in set(r.reference["moa"])
    assert not set(r.reference["compound"]) & {"testazole", "refA"}
    assert len(r.reference) == len(atl.reference) - 2 * len(CONCS) and r._matrix.shape[0] == len(r.reference)
    assert not other & set(r._wells.index) and {w.well_key for w in sp.wells} <= set(r._wells.index)
    # the full atlas is untouched (closed-world runs share it)
    assert HELD_OUT in set(atl.reference["moa"]) and other <= set(atl._wells.index)
    # even asking for every reference, from any direction, never returns the class
    rng = np.random.default_rng(3)
    for vec in [*rng.normal(size=(20, len(PROFILE_FEATURES))), *(r._wells.loc[w.well_key].to_numpy() for w in sp.wells)]:
        hits = r.neighbors(vec, k=len(atl.reference), exclude_compound=sp.compound)
        assert len(hits) == len(r.reference) and HELD_OUT not in {h.moa for h in hits}


def test_no_held_out_reference_is_reachable_from_any_tool(world):
    """Run every blinded tool on every well of the specimen, plus the post-review protocol call and the reviewer's
    ledger, against the restricted library: no output names a reference of the held-out class, its compounds or
    their thumbnails. The closed-world control (full library) shows the same calls do reach them."""
    cat, atl, sp = world
    held_names = {"refA"}
    held_thumbs = set(atl.reference.loc[atl.reference["moa"] == HELD_OUT, "thumbnail_field_id"])

    def exercise(library):
        ctx = _ctx(cat, library, sp)
        outs = {"list_available_experiments": [_call(ctx, "list_available_experiments")]}
        outs["acquire_well"] = [_call(ctx, "acquire_well", concentration_index=w.concentration_index,
                                      replicate=w.replicate) for w in sp.wells]
        ids = [w.well_id for w in sp.wells]
        fids = [f for w in sp.wells for f in w.field_ids][:T.MAX_PHENOTYPE_FIELDS]
        outs["describe_phenotype"] = [_call(ctx, "describe_phenotype", field_ids=fids)]
        outs["compare_to_reference_atlas"] = [_call(ctx, "compare_to_reference_atlas", well_ids=[i], k=T.MAX_ATLAS_K)
                                              for i in ids] + [_call(ctx, "compare_to_reference_atlas", well_ids=ids,
                                                                     k=T.MAX_ATLAS_K)]
        outs["dose_response"] = [_call(ctx, "dose_response", feature=f) for f in PROFILE_FEATURES]
        ev = outs["compare_to_reference_atlas"][-1].evidence.evidence_id
        outs["update_hypotheses"] = [_call(ctx, "update_hypotheses", hypotheses=[
            {"moa": c, "probability": 1 / 3, "supporting_evidence_ids": [ev], "contradicting_evidence_ids": []}
            for c in CLASSES])]
        outs["submit_conclusion"] = [_call(ctx, "submit_conclusion", top_moa=HELD_OUT, probability=0.5,
                                           key_evidence_ids=[ev], next_experiment_if_this_were_a_real_lab="assay")]
        assert set(outs) == set(T.BLINDED_TOOLS)  # every blinded tool is exercised; a new tool must be added here
        protocol = T.nsc_vote(ctx)  # what AgentLoop._protocol_call computes after the review
        ledger = reviewer.ledger(ctx.run, sp)
        return outs, protocol, ledger

    outs, protocol, ledger = exercise(restricted_atlas(atl, cat, sp))
    for tool, results in outs.items():
        for out in results:
            text = out.for_model() + json.dumps(out.evidence.image_ids)
            assert not any(n in text for n in held_names | held_thumbs), tool
    for out in outs["compare_to_reference_atlas"]:
        assert HELD_OUT not in _atlas_classes(out.evidence.data)
        assert len(out.evidence.data["neighbors"]) == 2 * len(CONCS)  # k=10 asks for every remaining reference
    assert protocol["moa"] != HELD_OUT and HELD_OUT not in _atlas_classes(protocol)
    assert HELD_OUT not in _atlas_classes(ledger["protocol_call"])
    assert not any(n in json.dumps(ledger, default=str) for n in held_names | held_thumbs)

    # control: the same calls on the full library (closed world, LOCO) do reach the held-out class
    outs, protocol, _ = exercise(atl)
    assert any(HELD_OUT in _atlas_classes(o.evidence.data) for o in outs["compare_to_reference_atlas"])
    assert any("refA" in o.for_model() for o in outs["compare_to_reference_atlas"])


def test_open_world_loop_uses_its_prompts_and_the_scientists_call(catalog, atlas, tmp_path):  # noqa: F811
    """Whole loop on the synthetic specimen: the one prompt change (Scientist and reviewer), the Scientist's call is
    final, the NSC vote is computed on the restricted library and shown as non-final."""
    sp = next(iter(catalog.specimens.values()))
    llm = ScriptedLLM(HAPPY)
    loop, bus, _ = make_loop(catalog, restricted_atlas(atlas, catalog, sp), tmp_path, llm)
    loop.ctx.settings = Settings(_env_file=None, open_world=True, final_call="scientist")
    r = run_loop(loop)
    assert r.status == "finished", r.error

    system = llm.sent[0]["messages"][0]["content"]
    assert prompts.open_world_scoring(len(CLASSES)) in system and prompts.V5_SCORING not in system
    review = next(s for s in llm.sent if s["purpose"] == "reviewer")
    assert review["messages"][0]["content"] == reviewer.OPEN_WORLD_SYSTEM
    assert r.verdict.call_source == "scientist" and r.verdict.agent_moa == "Eg5 inhibitors" and r.verdict.correct
    assert r.verdict.protocol["moa"] != "Eg5 inhibitors"
    pc = next(e for e in bus.events if e.type == "protocol_call")
    assert pc.data["final"] is False and pc.data["moa"] != "Eg5 inhibitors"
    atlas_events = [e for e in bus.events if e.type == "tool_result" and e.data["name"] == "compare_to_reference_atlas"]
    assert atlas_events and all("Eg5 inhibitors" not in _atlas_classes(e.data["data"]) for e in atlas_events)


def test_closed_world_prompts_are_v5():
    assert prompts.V5_SCORING in prompts.scientist_system(CLASSES, 4, 13)
    assert "may not contain" not in prompts.scientist_system(CLASSES, 4, 13)
    assert reviewer.V5_FINAL_CALL in reviewer.SYSTEM and reviewer.OPEN_WORLD_FINAL_CALL not in reviewer.SYSTEM
    # the open-world texts differ from v5 only in that one place
    assert (prompts.scientist_system(CLASSES, 4, 13, open_world=True).replace(prompts.open_world_scoring(3), "X")
            == prompts.scientist_system(CLASSES, 4, 13).replace(prompts.V5_SCORING, "X"))
    assert (reviewer.OPEN_WORLD_SYSTEM.replace(reviewer.OPEN_WORLD_FINAL_CALL, "X")
            == reviewer.SYSTEM.replace(reviewer.V5_FINAL_CALL, "X"))


def test_run_manager_wires_an_open_world_run(world, tmp_path):
    from app.runs.manager import RunManager

    cat, atl, sp = world
    m = RunManager(Settings(_env_file=None, data_dir=str(tmp_path / "data")), runs_dir=tmp_path / "runs")
    m.catalog, m.atlas, m.moa_classes, m.llm = cat, atl, CLASSES, FakeLLM()

    async def terms(specimen):
        return specimen.blinding_terms([])
    m._blinding_terms = terms

    open_loop = asyncio.run(m.create_run(sp.specimen_id, 4, open_world=True))
    s = open_loop.ctx.settings
    assert s.open_world and s.final_call == "scientist" and m.settings.final_call == "protocol"
    assert open_loop.ctx.atlas.excluded_moa == HELD_OUT and HELD_OUT not in set(open_loop.ctx.atlas.reference["moa"])
    assert open_loop.run.model_ids["agent"] == OPEN_WORLD_VERSION
    assert open_loop.run.model_ids["reference_library"] == REFERENCE_LIBRARY

    closed = asyncio.run(m.create_run(sp.specimen_id, 4))
    assert closed.ctx.atlas is atl and not closed.ctx.settings.open_world
    assert closed.ctx.settings.final_call == "protocol" and "reference_library" not in closed.run.model_ids


def test_real_data_exclusion_on_all_38_specimens():
    """With the BBBC021 data and the deep atlas present: for every specimen the restricted library has none of its
    class, no well vector of another compound of the class, and the NSC vote on any of its wells never names it."""
    from app.config import get_settings

    s = get_settings()
    if not (s.deep_atlas_dir / "treatments.parquet").exists() or not s.index_path.exists():
        pytest.skip("BBBC021 data / deep atlas not on this machine")
    cat = SpecimenCatalog.load(s)
    atl = Atlas.load_deep(s.deep_atlas_dir)
    assert len(cat.specimens) == 38
    for sp in cat.specimens.values():
        r = restricted_atlas(atl, cat, sp)
        assert sp.moa not in set(r.reference["moa"]) and len(set(r.reference["moa"])) == 11
        assert not held_out_well_keys(cat, sp) & set(r._wells.index)
        for w in sp.wells:
            assert T.well_vote(r, cat, [w], sp.compound)["moa"] != sp.moa
