"""Agent tools on a synthetic specimen: schemas, budget, phase gating, evidence, Microscopist request,
and the blinding-leak test over every blinded payload."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from app.agent import tools as T
from app.agent.guardrails import BlindingScanner
from app.agent.schemas import AgentRun
from app.agent.specimens import SpecimenCatalog
from app.config import Settings
from app.external.pubmed import PubMedArticle, PubMedSearch
from app.llm import CallRecord, CostCapExceeded, CostMeter
from app.science.atlas import Atlas
from app.science.features import PROFILE_FEATURES

COMPOUND = "testazole"
SMILES = "CC(=O)OC1=CC=CC=C1C(=O)O"
PLATE = "Week9_90001"
CLASSES = ["Eg5 inhibitors", "Microtubule destabilizers", "Protein synthesis"]
CONCS = [0.1, 1.0, 10.0]


def _profile(rng, base):
    return dict(zip(PROFILE_FEATURES, base + rng.normal(0, 0.1, len(PROFILE_FEATURES))))


@pytest.fixture
def catalog(tmp_path):
    rng = np.random.default_rng(0)
    eg5 = np.zeros(len(PROFILE_FEATURES)); eg5[[0, 21]] = [4.0, 6.0]
    index, wells = [], []
    n = 0

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
                Image.new("RGB" if img == "composite" else "L", (64, 48), 30).save(tmp_path / rel)
                row[f"png_{img}"] = rel
            index.append(row)
        wells.append({"plate": PLATE, "well": well, "well_key": f"{PLATE}/{well}", "compound": compound,
                      "concentration_uM": conc, "moa": moa, "role": role, "replicate": rep, "field_ids": fids,
                      "n_cells": 50.0, **_profile(rng, base)})

    for i, conc in enumerate(CONCS):
        for rep in (1, 2):
            add_well(f"C{i}{rep}", COMPOUND, conc, "Eg5 inhibitors", "treated", rep, eg5 * (i + 1) / 3)
    add_well("B01", "DMSO", 0.0, None, "control", 1, np.zeros(len(PROFILE_FEATURES)))
    add_well("B02", "DMSO", 0.0, None, "control", 1, np.zeros(len(PROFILE_FEATURES)))
    norm = pd.DataFrame(wells)
    raw = norm.copy()
    raw[PROFILE_FEATURES] = raw[PROFILE_FEATURES] + 10
    return SpecimenCatalog(pd.DataFrame(index), raw, norm, salt="test-salt", smiles={COMPOUND: SMILES},
                           data_path=tmp_path)


@pytest.fixture
def atlas():
    rng = np.random.default_rng(1)
    eg5 = np.zeros(len(PROFILE_FEATURES)); eg5[[0, 21]] = [4.0, 6.0]
    mt = np.zeros(len(PROFILE_FEATURES)); mt[[5, 10]] = [5.0, -5.0]
    rows = [(COMPOUND, 1.0, "Eg5 inhibitors", eg5)] + [("refA", c, "Eg5 inhibitors", eg5 * 0.8) for c in (1, 3)] + \
           [("refB", c, "Microtubule destabilizers", mt) for c in (1, 3)] + [("refC", 1, "Protein synthesis", -eg5)]
    df = pd.DataFrame([{"compound": c, "concentration_uM": conc, "moa": m, "moa_annotated_conc": True,
                        "thumbnail_field_id": f"f_ref{i:09x}", **_profile(rng, b)} for i, (c, conc, m, b) in enumerate(rows)])
    return Atlas(df)


class FakeLLM:
    def __init__(self, cost=0.01):
        self.calls = []
        self.cost = cost

    async def chat_json(self, *, model, messages, meter, purpose, schema_name, schema, **kw):
        self.calls.append({"messages": messages, "schema": schema})
        fids = schema["properties"]["notable_cells"]["items"]["properties"]["field_id"]["enum"]
        reply = {"cell_density": "lower",
                 "observations": [{"channel": "dna", "finding": "compact bright nuclei", "agrees_with_measurements": True}],
                 "notable_cells": [{"field_id": fids[0], "x": 10, "y": 12, "why": "rounded"},
                                   {"field_id": "f_invented", "x": 1, "y": 1, "why": "not a real field"}],
                 "candidate_mechanisms": [{"moa": "eg5 inhibitors", "why": "monopolar"}, {"moa": "unknown", "why": "-"}],
                 "uncertainty": "small sample"}
        rec = CallRecord(model=model, purpose=purpose, cost_usd=self.cost)
        meter.add(rec)
        return reply, rec


class FakePubMed:
    async def search(self, query, max_results=5):
        arts = [PubMedArticle(pmid=p, title=f"t{p}", journal="J", year=2001, authors=[], url=f"u/{p}")
                for p in ("101", "202")][:max_results]
        return PubMedSearch(query=query, total_count=2, articles=arts)


def make_ctx(catalog, atlas, budget=3, llm=None, cap=1.0):
    sp = next(iter(catalog.specimens.values()))
    run = AgentRun(run_id="r1", specimen_id=sp.specimen_id, budget_total=budget)
    return T.ToolContext(run=run, specimen=sp, catalog=catalog, atlas=atlas, moa_classes=CLASSES,
                         meter=CostMeter(run_id="r1", cap_usd=cap), llm=llm or FakeLLM(), pubmed=FakePubMed(),
                         settings=Settings(_env_file=None))


def call(ctx, tool, **args):
    args.setdefault("rationale", "testing")
    return asyncio.run(T.execute(ctx, tool, args))


# --- schemas --------------------------------------------------------------------------------------

def _assert_strict(schema, path="$"):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False, path
        assert sorted(schema["required"]) == sorted(schema["properties"]), path
        for k, v in schema["properties"].items():
            _assert_strict(v, f"{path}.{k}")
    if schema.get("type") == "array":
        _assert_strict(schema["items"], f"{path}[]")


def test_nine_strict_tools_with_rationale():
    specs = T.all_tool_specs(CLASSES)
    assert list(specs) == T.BLINDED_TOOLS + T.UNBLINDED_TOOLS and len(specs) == 9
    for name, spec in specs.items():
        fn = spec["function"]
        assert spec["type"] == "function" and fn["strict"] is True and fn["name"] == name
        _assert_strict(fn["parameters"], name)
        assert "rationale" in fn["parameters"]["required"]
        assert set(fn["parameters"]["properties"]) == set(T.ARG_MODELS[name].model_fields), name
    assert specs["update_hypotheses"]["function"]["parameters"]["properties"]["hypotheses"]["items"][
        "properties"]["moa"]["enum"] == CLASSES


def test_phase_gating(catalog, atlas):
    ctx = make_ctx(catalog, atlas)
    assert [s["function"]["name"] for s in T.tool_specs(ctx)] == T.BLINDED_TOOLS
    out = call(ctx, "search_literature", query="x", max_results=2)
    assert not out.ok and "not available in the blinded phase" in out.error
    ev = call(ctx, "list_available_experiments").evidence.evidence_id
    assert call(ctx, "submit_conclusion", top_moa="Eg5 inhibitors", probability=0.6, key_evidence_ids=[ev],
                next_experiment_if_this_were_a_real_lab="dose series").ok
    assert ctx.run.phase == "review" and T.tool_specs(ctx) == []
    assert "not available" in call(ctx, "acquire_well", concentration_index=0, replicate=1).error
    ctx.run.phase = "unblinded"
    assert [s["function"]["name"] for s in T.tool_specs(ctx)] == T.UNBLINDED_TOOLS
    lit = call(ctx, "search_literature", query="eg5", max_results=2)
    assert lit.ok and ctx.run.allowed_pmids == ["101", "202"]


# --- arguments ------------------------------------------------------------------------------------

def test_rationale_required_and_truncated(catalog, atlas):
    ctx = make_ctx(catalog, atlas)
    out = asyncio.run(T.execute(ctx, "list_available_experiments", {}))
    assert not out.ok and "rationale" in out.error
    out = call(ctx, "list_available_experiments", rationale="x" * 500)
    assert out.ok and len(out.rationale) == T.RATIONALE_MAX and ctx.run.steps[-1].rationale == out.rationale
    assert "Invalid arguments" in call(ctx, "list_available_experiments", surprise=1).error  # extra field
    assert "not valid JSON" in asyncio.run(T.execute(ctx, "acquire_well", "{nope")).error
    assert "Unknown tool" in call(ctx, "do_magic").error


# --- budget ---------------------------------------------------------------------------------------

def test_budget_enforced_server_side(catalog, atlas):
    ctx = make_ctx(catalog, atlas, budget=2)
    a = call(ctx, "acquire_well", concentration_index=0, replicate=1)
    assert a.ok and ctx.run.budget_used == 1
    dup = call(ctx, "acquire_well", concentration_index=0, replicate=1)
    assert not dup.ok and "already acquired" in dup.error and ctx.run.budget_used == 1
    assert "concentration_index must be" in call(ctx, "acquire_well", concentration_index=7, replicate=1).error
    assert "replicates 1-2" in call(ctx, "acquire_well", concentration_index=1, replicate=3).error
    assert call(ctx, "acquire_well", concentration_index=2, replicate=1).ok
    over = call(ctx, "acquire_well", concentration_index=1, replicate=1)
    assert not over.ok and "Budget exhausted" in over.error and ctx.run.budget_used == 2
    listing = call(ctx, "list_available_experiments").evidence.data
    assert listing["budget_left"] == 0 and [d["acquired_replicates"] for d in listing["doses"]] == [[1], [], [1]]


def test_budget_bounds():
    with pytest.raises(ValueError):
        T.ToolContext(run=AgentRun(run_id="r", specimen_id="s", budget_total=9), specimen=None, catalog=None,
                      atlas=None, moa_classes=CLASSES, meter=CostMeter(run_id="r", cap_usd=1))


def test_acquire_returns_measurements(catalog, atlas):
    ctx = make_ctx(catalog, atlas)
    ev = call(ctx, "acquire_well", concentration_index=2, replicate=1).evidence
    d = ev.data
    assert ev.kind == "well" and ev.evidence_id == "e1" and d["well_id"].startswith("w_")
    assert len(d["top_deviations"]) == T.TOP_FEATURES
    assert {t["feature"] for t in d["top_deviations"][:2]} == {PROFILE_FEATURES[0], PROFILE_FEATURES[21]}
    assert d["top_deviations"][0]["value"] is not None and d["dmso_field_ids"]
    assert ev.image_ids == [f"{f}.composite" for f in d["field_ids"]]


# --- other tools ----------------------------------------------------------------------------------

def test_atlas_is_loco(catalog, atlas):
    ctx = make_ctx(catalog, atlas)
    w = call(ctx, "acquire_well", concentration_index=2, replicate=1).evidence.data["well_id"]
    assert "not been acquired" in call(ctx, "compare_to_reference_atlas", well_ids=["w_nope"], k=3).error
    ev = call(ctx, "compare_to_reference_atlas", well_ids=[w], k=5).evidence
    assert all(n["compound"] != COMPOUND for n in ev.data["neighbors"])
    assert ev.data["moa_summary"][0]["moa"] == "Eg5 inhibitors"


def test_dose_response_and_hypotheses(catalog, atlas):
    ctx = make_ctx(catalog, atlas, budget=4)
    assert "No wells" in call(ctx, "dose_response", feature="nuc_area").error
    for ci in range(3):
        call(ctx, "acquire_well", concentration_index=ci, replicate=1)
    dr = call(ctx, "dose_response", feature=PROFILE_FEATURES[21]).evidence
    assert dr.data["spearman_rho"] == pytest.approx(1.0) and len(dr.data["points"]) == 3
    assert "Unknown feature" in call(ctx, "dose_response", feature="vibes").error
    bad = call(ctx, "update_hypotheses", hypotheses=[
        {"moa": "Eg5 inhibitors", "probability": 2, "supporting_evidence_ids": ["e99"], "contradicting_evidence_ids": []}])
    assert "Unknown evidence" in bad.error
    ok = call(ctx, "update_hypotheses", hypotheses=[
        {"moa": "Eg5 inhibitors", "probability": 3, "supporting_evidence_ids": ["e1", dr.evidence_id],
         "contradicting_evidence_ids": []},
        {"moa": "Microtubule destabilizers", "probability": 1, "supporting_evidence_ids": [],
         "contradicting_evidence_ids": ["e2"]}])
    assert ok.ok and [h.probability for h in ctx.run.hypotheses] == pytest.approx([0.75, 0.25])
    assert len(ctx.run.hypothesis_history) == 1


def test_describe_phenotype_request(catalog, atlas):
    llm = FakeLLM()
    ctx = make_ctx(catalog, atlas, llm=llm)
    first = catalog.index["field_id"].iloc[0]
    assert "not in an acquired well" in call(ctx, "describe_phenotype", field_ids=[first]).error
    well = call(ctx, "acquire_well", concentration_index=2, replicate=1).evidence.data
    out = call(ctx, "describe_phenotype", field_ids=well["field_ids"])
    assert out.ok and out.cost_usd == pytest.approx(0.01) and ctx.run.cost_usd == pytest.approx(0.01)
    content = llm.calls[0]["messages"][1]["content"]
    images = [p for p in content if p["type"] == "image_url"]
    texts = " ".join(p["text"] for p in content if p["type"] == "text")
    dmso = out.evidence.data["dmso_field_ids"]
    assert len(images) == 4 * (len(well["field_ids"]) + len(dmso))  # composite + 3 channels per field
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert dmso and f"DMSO control field {dmso[0]}" in texts
    assert well["top_deviations"][0]["feature"] in texts  # grounded in our measurements
    report = out.evidence.data["report"]
    assert report["candidate_mechanisms"][0]["moa"] == "Eg5 inhibitors"  # canonicalised
    assert [c["field_id"] for c in report["notable_cells"]] == [well["field_ids"][0]]  # invented id dropped


def test_cost_cap_ends_the_run(catalog, atlas):
    ctx = make_ctx(catalog, atlas, llm=FakeLLM(cost=0.0), cap=0.0)
    well = call(ctx, "acquire_well", concentration_index=0, replicate=1).evidence.data

    class CappedLLM(FakeLLM):
        async def chat_json(self, **kw):
            raise CostCapExceeded("Run cost cap reached")

    ctx.llm = CappedLLM()
    with pytest.raises(CostCapExceeded):
        call(ctx, "describe_phenotype", field_ids=well["field_ids"][:1])


# --- blinding -------------------------------------------------------------------------------------

def test_blinded_run_payloads_never_leak_identity(catalog, atlas):
    llm = FakeLLM()
    ctx = make_ctx(catalog, atlas, llm=llm, budget=3)
    sp = ctx.specimen
    scanner = BlindingScanner(sp.blinding_terms(["Testazol", "TZ-12345"]))
    assert scanner.terms  # compound, synonyms, SMILES, plate, well keys

    outcomes = [call(ctx, "list_available_experiments")]
    for ci in range(3):
        outcomes.append(call(ctx, "acquire_well", concentration_index=ci, replicate=1))
    wells = [o.evidence.data for o in outcomes[1:]]
    outcomes += [
        call(ctx, "describe_phenotype", field_ids=wells[-1]["field_ids"]),
        call(ctx, "compare_to_reference_atlas", well_ids=[w["well_id"] for w in wells], k=5),
        call(ctx, "dose_response", feature="nuc_area"),
        call(ctx, "update_hypotheses", hypotheses=[{"moa": "Eg5 inhibitors", "probability": 1,
                                                    "supporting_evidence_ids": ["e2"], "contradicting_evidence_ids": []}]),
        call(ctx, "acquire_well", concentration_index=0, replicate=2),  # over budget -> error text
        call(ctx, "submit_conclusion", top_moa="Eg5 inhibitors", probability=0.7, key_evidence_ids=["e2"],
             next_experiment_if_this_were_a_real_lab="Eg5 ATPase assay"),
    ]
    assert all(o.ok for o in outcomes[:-2]) and not outcomes[-2].ok and outcomes[-1].ok

    payloads = {"tool messages": [json.loads(o.for_model()) for o in outcomes],
                "tool specs": T.all_tool_specs(CLASSES), "run": ctx.run.model_dump(mode="json"),
                "microscopist request": [p.get("text", "") for p in llm.calls[0]["messages"][1]["content"]]
                + [llm.calls[0]["messages"][0]["content"]]}
    for what, payload in payloads.items():
        assert scanner.scan(payload) == [], what
    # and the scanner would have caught it:
    assert scanner.scan({"x": wells[0]["well_id"] + " " + sp.wells[0].well_key})


# --- unblinded compound identity ------------------------------------------------------------------

class FakePubChem:
    def __init__(self, by_name, by_smiles):
        self.by_name, self.by_smiles = by_name, by_smiles

    async def lookup(self, name):
        return self.by_name.get(name)

    async def lookup_smiles(self, smiles):
        return self.by_smiles


class FakeChembl:
    def __init__(self):
        self.calls = []

    async def lookup(self, names, inchikey=None):
        self.calls.append((names, inchikey))
        return None


def _pc(cid, key):
    from app.external.pubchem import PubChemCompound
    return PubChemCompound(cid=cid, title=f"c{cid}", iupac_name=None, smiles=None, inchikey=key,
                           molecular_formula=None, molecular_weight=None, synonyms=[], structure_png_url="u", url="u")


@pytest.mark.parametrize("smiles, by_name, by_smiles, expect_cid, expect", [
    (SMILES, _pc(1, "AAAAAAAAAAAAAA-XX-N"), _pc(2, "AAAAAAAAAAAAAA-YY-N"), 1, "agree"),
    (SMILES, _pc(1, "AAAAAAAAAAAAAA-XX-N"), _pc(2, "BBBBBBBBBBBBBB-YY-N"), 1, "mismatch"),
    (SMILES, None, _pc(2, "BBBBBBBBBBBBBB-YY-N"), 2, "from the BBBC021 structure"),
    (None, _pc(1, "AAAAAAAAAAAAAA-XX-N"), None, None, "no structure"),  # internal code: name hit not trusted
])
def test_specimen_identity_checked_against_dataset_structure(catalog, atlas, smiles, by_name, by_smiles,
                                                             expect_cid, expect):
    ctx = make_ctx(catalog, atlas)
    ctx.specimen = ctx.specimen.model_copy(update={"smiles": smiles})
    ctx.pubchem, ctx.chembl = FakePubChem({COMPOUND: by_name}, by_smiles), FakeChembl()
    ctx.run.phase = "unblinded"
    out = call(ctx, "lookup_compound", name=COMPOUND)
    assert out.ok, out.error
    assert expect in out.evidence.data["identity_check"]
    assert (out.evidence.data["pubchem"] or {}).get("cid") == expect_cid
    assert len(ctx.chembl.calls) == (0 if smiles is None else 1)  # no name-only ChEMBL hit for an internal code


def test_other_compounds_are_name_only(catalog, atlas):
    ctx = make_ctx(catalog, atlas)
    ctx.pubchem, ctx.chembl = FakePubChem({"colchicine": _pc(6167, "IAKHMKGGTNLKSZ-INIZCTEOSA-N")}, None), FakeChembl()
    ctx.run.phase = "unblinded"
    out = call(ctx, "lookup_compound", name="colchicine")
    assert out.evidence.data["identity_check"] == "name only" and ctx.chembl.calls[0][1].startswith("IAKH")
