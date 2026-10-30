"""The Scientist's nine tools (spec §5): strict JSON schemas, Pydantic validation, server-side budget,
phase gating and evidence registration.

`tool_specs(ctx)` lists only the tools unlocked in the run's current phase; `execute(ctx, name, args)`
validates, enforces and runs one call and always returns a ToolOutcome (errors are results the agent
sees, not exceptions) — except CostCapExceeded, which ends the run.

Every tool takes a required `rationale` (≤ 240 chars, longer text is cut), shown in the UI as "Why".
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agent import microscopist
from app.agent.guardrails import GuardrailError, UNKNOWN_MOA, canonical_moa, normalize_hypotheses
from app.agent.schemas import AgentRun, Conclusion, Evidence, EvidenceKind, Hypothesis, HypothesisSnapshot, Step
from app.agent.specimens import Specimen, SpecimenCatalog, Well
from app.config import MAX_BUDGET_WELLS, PROVIDERS, Settings, get_settings, provider_of
from app.external.chembl import ChemblClient
from app.external.pubchem import PubChemClient
from app.external.pubmed import PubMedClient
from app.llm import CostCapExceeded, CostMeter, LLMClient, LLMError
from app.science.atlas import Atlas
from app.science.features import PROFILE_FEATURES
from app.science.stats import compare_to_dmso, dose_response

RATIONALE_MAX = 240
TOP_FEATURES = 8
MAX_PHENOTYPE_FIELDS = 4
MAX_ATLAS_K = 10
MAX_LITERATURE = 10

BLINDED_TOOLS = ["list_available_experiments", "acquire_well", "describe_phenotype", "compare_to_reference_atlas",
                 "dose_response", "update_hypotheses", "submit_conclusion"]
UNBLINDED_TOOLS = ["lookup_compound", "search_literature"]


class ToolError(Exception):
    """A rejected or failed call; the message goes back to the agent."""


def image_id(field_id: str, image: str = "composite") -> str:
    return f"{field_id}.{image}"


# --- argument models ------------------------------------------------------------------------------

class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("rationale must say why you make this call")
        return v if len(v) <= RATIONALE_MAX else v[:RATIONALE_MAX - 1].rstrip() + "…"


class ListExperimentsArgs(Args):
    pass


class AcquireWellArgs(Args):
    concentration_index: int
    replicate: int


class DescribePhenotypeArgs(Args):
    field_ids: list[str] = Field(min_length=1, max_length=MAX_PHENOTYPE_FIELDS)


class CompareAtlasArgs(Args):
    well_ids: list[str] = Field(min_length=1)
    k: int = Field(ge=1, le=MAX_ATLAS_K)


class DoseResponseArgs(Args):
    feature: str


class HypothesisArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    moa: str
    probability: float
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]


class UpdateHypothesesArgs(Args):
    hypotheses: list[HypothesisArg] = Field(min_length=1)


class SubmitConclusionArgs(Args):
    top_moa: str
    probability: float = Field(ge=0, le=1)
    key_evidence_ids: list[str] = Field(min_length=1)
    next_experiment_if_this_were_a_real_lab: str = Field(min_length=1)


class LookupCompoundArgs(Args):
    name: str = Field(min_length=1, max_length=200)


class SearchLiteratureArgs(Args):
    query: str = Field(min_length=1, max_length=300)
    max_results: int = Field(ge=1, le=MAX_LITERATURE)


# --- strict JSON schemas --------------------------------------------------------------------------

def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _fn(name: str, description: str, props: dict) -> dict:
    rationale = {"type": "string", "description": f"Why you make this call, max {RATIONALE_MAX} characters. "
                                                  "Shown to the user as 'Why'."}
    return {"type": "function", "function": {"name": name, "description": description, "strict": True,
                                             "parameters": _obj({"rationale": rationale, **props})}}


def _ids(desc: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


def all_tool_specs(moa_classes: list[str]) -> dict[str, dict]:
    moa = {"type": "string", "enum": list(moa_classes)}
    return {
        "list_available_experiments": _fn(
            "list_available_experiments",
            "List the specimen's doses (index + µM), replicates per dose, wells already acquired and the "
            "remaining budget. Free.", {}),
        "acquire_well": _fn(
            "acquire_well",
            "Image one well (costs 1 budget unit): returns its 4 or 2 field ids, segmentation counts, the "
            "DMSO-normalised feature profile and the top deviating features vs same-plate DMSO.",
            {"concentration_index": {"type": "integer", "description": "dose index from list_available_experiments"},
             "replicate": {"type": "integer", "description": "replicate number, starting at 1"}}),
        "describe_phenotype": _fn(
            "describe_phenotype",
            f"Ask the microscopist (vision model) to describe up to {MAX_PHENOTYPE_FIELDS} acquired fields "
            "against a same-plate DMSO control, grounded in the measured features.",
            {"field_ids": _ids("field ids from acquired wells")}),
        "compare_to_reference_atlas": _fn(
            "compare_to_reference_atlas",
            "Nearest annotated reference treatments (other compounds only) to the median profile of the given "
            "acquired wells, with their MoA and cosine similarity.",
            {"well_ids": _ids("acquired well ids"),
             "k": {"type": "integer", "description": f"number of neighbours, 1-{MAX_ATLAS_K}"}}),
        "dose_response": _fn(
            "dose_response",
            "Per-dose medians and Spearman rho of one feature over all acquired wells.",
            {"feature": {"type": "string", "enum": PROFILE_FEATURES}}),
        "update_hypotheses": _fn(
            "update_hypotheses",
            "Replace the hypothesis board. Probabilities are renormalised to sum to 1; evidence ids must exist.",
            {"hypotheses": {"type": "array", "items": _obj({
                "moa": moa, "probability": {"type": "number"},
                "supporting_evidence_ids": _ids("evidence ids that support this MoA"),
                "contradicting_evidence_ids": _ids("evidence ids that argue against it")})}}),
        "submit_conclusion": _fn(
            "submit_conclusion",
            "Commit to a blinded conclusion. Ends the blinded phase; the compound is revealed only after this.",
            {"top_moa": moa, "probability": {"type": "number", "description": "0-1"},
             "key_evidence_ids": _ids("the evidence ids the conclusion rests on"),
             "next_experiment_if_this_were_a_real_lab": {"type": "string"}}),
        "lookup_compound": _fn(
            "lookup_compound",
            "After unblinding: PubChem identity and structure plus ChEMBL curated mechanisms for a compound name.",
            {"name": {"type": "string"}}),
        "search_literature": _fn(
            "search_literature",
            "After unblinding: PubMed search. Only PMIDs returned here may be cited in the dossier.",
            {"query": {"type": "string"},
             "max_results": {"type": "integer", "description": f"1-{MAX_LITERATURE}"}}),
    }


ARG_MODELS: dict[str, type[Args]] = {
    "list_available_experiments": ListExperimentsArgs, "acquire_well": AcquireWellArgs,
    "describe_phenotype": DescribePhenotypeArgs, "compare_to_reference_atlas": CompareAtlasArgs,
    "dose_response": DoseResponseArgs, "update_hypotheses": UpdateHypothesesArgs,
    "submit_conclusion": SubmitConclusionArgs, "lookup_compound": LookupCompoundArgs,
    "search_literature": SearchLiteratureArgs,
}


# --- context --------------------------------------------------------------------------------------

@dataclass
class ToolContext:
    run: AgentRun
    specimen: Specimen
    catalog: SpecimenCatalog
    atlas: Atlas
    moa_classes: list[str]
    meter: CostMeter
    llm: LLMClient | None = None
    pubchem: PubChemClient | None = None
    chembl: ChemblClient | None = None
    pubmed: PubMedClient | None = None
    settings: Settings = field(default_factory=get_settings)

    def __post_init__(self) -> None:
        if not 1 <= self.run.budget_total <= MAX_BUDGET_WELLS:
            raise ValueError(f"budget must be 1-{MAX_BUDGET_WELLS} wells")

    def register(self, kind: EvidenceKind, tool: str, summary: str, data: dict,
                 image_ids: list[str] | None = None) -> Evidence:
        ev = Evidence(evidence_id=f"e{len(self.run.evidence) + 1}", kind=kind, tool=tool, summary=summary,
                      data=data, image_ids=image_ids or [])
        self.run.evidence.append(ev)
        return ev

    def acquired_wells(self) -> list[Well]:
        return [self.specimen.well_by_id(w) for w in self.run.acquired_well_ids]

    def acquired_well(self, well_id: str) -> Well:
        if well_id not in self.run.acquired_well_ids:
            raise ToolError(f"Well '{well_id}' has not been acquired. Acquired: {self.run.acquired_well_ids}.")
        return self.specimen.well_by_id(well_id)

    def well_of_field(self, field_id: str) -> Well:
        for w in self.acquired_wells():
            if field_id in w.field_ids:
                return w
        raise ToolError(f"Field '{field_id}' is not in an acquired well.")

    def top_features(self, well: Well) -> list[dict]:
        devs = compare_to_dmso(self.catalog.profile(well.well_key), k=TOP_FEATURES,
                               raw=self.catalog.raw_profile(well.well_key),
                               dmso_median=self.catalog.dmso_raw_median(well.plate))
        return [_round(d.model_dump()) for d in devs]


class ToolOutcome(BaseModel):
    name: str
    ok: bool
    rationale: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    evidence: Evidence | None = None
    error: str | None = None
    cost_usd: float = 0.0

    def for_model(self) -> str:
        """The tool message content the Scientist reads."""
        if not self.ok:
            return json.dumps({"error": self.error})
        return json.dumps({"evidence_id": self.evidence.evidence_id, "summary": self.evidence.summary,
                           "data": self.evidence.data})


def available_tools(run: AgentRun) -> list[str]:
    tools = list(BLINDED_TOOLS) if run.phase == "blinded" else []
    if run.phase == "unblinded" or run.mode == "open":
        tools += UNBLINDED_TOOLS
    return tools


def tool_specs(ctx: ToolContext) -> list[dict]:
    specs = all_tool_specs(ctx.moa_classes)
    return [specs[name] for name in available_tools(ctx.run)]


def _round(obj: Any, nd: int = 3) -> Any:
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


# --- tools ----------------------------------------------------------------------------------------

async def list_available_experiments(ctx: ToolContext, a: ListExperimentsArgs) -> Evidence:
    sp, run = ctx.specimen, ctx.run
    acquired = [ctx.specimen.well_by_id(w) for w in run.acquired_well_ids]
    doses = [{"concentration_index": i, "concentration_uM": c, "replicates": sp.replicates(i),
              "acquired_replicates": sorted(w.replicate for w in acquired if w.concentration_index == i)}
             for i, c in enumerate(sp.concentrations_uM)]
    data = {"specimen_id": sp.specimen_id, "doses": doses, "budget_total": run.budget_total,
            "budget_used": run.budget_used, "budget_left": run.budget_left,
            "acquired_wells": [{"well_id": w.well_id, "concentration_index": w.concentration_index,
                                "replicate": w.replicate} for w in acquired]}
    summary = (f"{len(doses)} doses ({sp.concentrations_uM[0]:g}-{sp.concentrations_uM[-1]:g} µM); "
               f"{run.budget_left} of {run.budget_total} wells left")
    return ctx.register("experiments", "list_available_experiments", summary, data)


async def acquire_well(ctx: ToolContext, a: AcquireWellArgs) -> Evidence:
    sp, run = ctx.specimen, ctx.run
    if not 0 <= a.concentration_index < len(sp.concentrations_uM):
        raise ToolError(f"concentration_index must be 0-{len(sp.concentrations_uM) - 1}.")
    well = sp.well(a.concentration_index, a.replicate)
    if well is None:
        raise ToolError(f"Dose {a.concentration_index} has replicates 1-{sp.replicates(a.concentration_index)}.")
    if well.well_id in run.acquired_well_ids:
        ev = next(e for e in run.evidence if e.kind == "well" and e.data["well_id"] == well.well_id)
        raise ToolError(f"Well {well.well_id} is already acquired (evidence {ev.evidence_id}); no budget spent.")
    if run.budget_left <= 0:
        raise ToolError(f"Budget exhausted: {run.budget_used} of {run.budget_total} wells used. "
                        "Conclude with the evidence you have.")
    run.budget_used += 1
    run.acquired_well_ids.append(well.well_id)

    fields = [{"field_id": f, **(ctx.catalog.segmentation_counts(f) or {})} for f in well.field_ids]
    profile = ctx.catalog.profile(well.well_key)
    top = ctx.top_features(well)
    n_cells = ctx.catalog.n_cells(well.well_key)
    data = {"well_id": well.well_id, "concentration_index": well.concentration_index,
            "concentration_uM": well.concentration_uM, "replicate": well.replicate,
            "field_ids": well.field_ids,
            "segmentation": {"nuclei_method": ctx.settings.nuclei_segmenter,
                             "cells_method": ctx.settings.cell_segmenter,
                             "cells_measured": n_cells, "fields": fields},
            "profile_z": _round(profile.dropna().to_dict(), 2),
            "top_deviations": top,
            "dmso_field_ids": ctx.catalog.dmso_field_ids(well.plate)[:2],
            "budget_left": run.budget_left}
    top_txt = ", ".join(f"{d['feature']} z={d['z']:+.1f}" for d in top[:3])
    summary = (f"Well {well.well_id} (dose {well.concentration_index}, {well.concentration_uM:g} µM, "
               f"rep {well.replicate}): {n_cells} cells measured; {top_txt}")
    return ctx.register("well", "acquire_well", summary, data, [image_id(f) for f in well.field_ids])


async def describe_phenotype(ctx: ToolContext, a: DescribePhenotypeArgs) -> Evidence:
    if ctx.llm is None:
        raise ToolError("Microscopist unavailable: no model client configured (MODEL_API_KEY).")
    field_ids = list(dict.fromkeys(a.field_ids))
    wells = {f: ctx.well_of_field(f) for f in field_ids}
    plates = list(dict.fromkeys(w.plate for w in wells.values()))
    controls = [ctx.catalog.dmso_field_ids(p)[0] for p in plates]  # one same-plate DMSO field per plate

    def paths(fid: str) -> dict:
        return {img: ctx.catalog.field_path(fid, img) for img in ("composite", *microscopist.CHANNELS)}

    measurements = {}
    for w in {w.well_id: w for w in wells.values()}.values():
        measurements[w.well_id] = {"concentration_uM": w.concentration_uM,
                                   "field_ids": [f for f in field_ids if f in w.field_ids],
                                   "cells_measured": ctx.catalog.n_cells(w.well_key),
                                   "top_deviations": [{k: d[k] for k in ("feature", "z", "value", "dmso_median")}
                                                      for d in ctx.top_features(w)]}
    # Composite + 3 channels per field, unless that exceeds the provider's image cap (Baseten: 8 per request);
    # then composites only (<= 4 treated + <= 4 controls), and the prompt says so.
    model = ctx.settings.microscopist_id
    max_images = PROVIDERS[provider_of(model)].max_images
    n_fields = len(field_ids) + len(controls)
    channels = n_fields * (1 + len(microscopist.CHANNELS)) <= max_images
    if n_fields > max_images:
        raise ToolError(f"Too many fields for one Microscopist call on this provider ({n_fields} > {max_images}).")
    messages = microscopist.build_messages([(f, paths(f)) for f in field_ids], [(c, paths(c)) for c in controls],
                                           measurements, ctx.moa_classes, channels=channels)
    result, record = await ctx.llm.chat_json(
        model=model, messages=messages, meter=ctx.meter, purpose="microscopist",
        schema_name="microscopist_report", schema=microscopist.response_schema(ctx.moa_classes, field_ids))
    for c in result.get("candidate_mechanisms", []):
        if c["moa"] != UNKNOWN_MOA:
            c["moa"] = canonical_moa(c["moa"], ctx.moa_classes)
    result["notable_cells"] = [c for c in result.get("notable_cells", []) if c.get("field_id") in field_ids]
    data = {"field_ids": field_ids, "dmso_field_ids": controls, "model": record.model,
            "images": "composite + channels" if channels else "composites only", "report": result}
    cands = ", ".join(c["moa"] for c in result.get("candidate_mechanisms", [])) or "none"
    summary = (f"Microscopist on {len(field_ids)} field(s): density {result.get('cell_density')} vs DMSO; "
               f"{len(result.get('observations', []))} observations; candidates: {cands}")
    return ctx.register("phenotype", "describe_phenotype", summary, data,
                        [image_id(f) for f in field_ids + controls])


async def compare_to_reference_atlas(ctx: ToolContext, a: CompareAtlasArgs) -> Evidence:
    wells = [ctx.acquired_well(w) for w in dict.fromkeys(a.well_ids)]
    query = pd.concat([ctx.catalog.profile(w.well_key) for w in wells], axis=1).median(axis=1)
    hits = ctx.atlas.neighbors(query, a.k, exclude_compound=ctx.specimen.compound)
    neighbors = [{**_round(h.model_dump(exclude={"thumbnail_field_id"})),
                  "image_id": image_id(h.thumbnail_field_id)} for h in hits]
    by_moa: dict[str, list[float]] = {}
    for h in hits:
        by_moa.setdefault(h.moa, []).append(h.similarity)
    moa_summary = sorted(({"moa": m, "neighbors": len(s), "mean_similarity": round(sum(s) / len(s), 3)}
                          for m, s in by_moa.items()), key=lambda r: (-r["neighbors"], -r["mean_similarity"]))
    data = {"well_ids": [w.well_id for w in wells], "k": a.k, "similarity": "cosine on robust-z profiles",
            "excluded": "the specimen's own compound (leave-one-compound-out)",
            "neighbors": neighbors, "moa_summary": moa_summary}
    summary = "Nearest references: " + ", ".join(f"{r['moa']} ({r['neighbors']}/{a.k})" for r in moa_summary[:3])
    return ctx.register("atlas", "compare_to_reference_atlas", summary, data, [n["image_id"] for n in neighbors])


async def dose_response_tool(ctx: ToolContext, a: DoseResponseArgs) -> Evidence:
    if a.feature not in PROFILE_FEATURES:
        raise ToolError(f"Unknown feature '{a.feature}'. Use one of {PROFILE_FEATURES}.")
    wells = ctx.acquired_wells()
    if not wells:
        raise ToolError("No wells acquired yet.")
    df = pd.DataFrame([{"concentration_uM": w.concentration_uM, a.feature: ctx.catalog.profile(w.well_key)[a.feature]}
                       for w in wells])
    res = dose_response(a.feature, df)
    data = _round(res.model_dump(), 4)
    rho = f"Spearman rho={res.spearman_rho:+.2f} (p={res.p_value:.2g})" if res.spearman_rho is not None else res.note
    summary = f"Dose response of {a.feature} over {res.n_wells} wells, {len(res.points)} doses: {rho}"
    return ctx.register("dose_response", "dose_response", summary, data)


async def update_hypotheses(ctx: ToolContext, a: UpdateHypothesesArgs) -> Evidence:
    hyps = normalize_hypotheses([Hypothesis(**h.model_dump()) for h in a.hypotheses], ctx.moa_classes,
                                ctx.run.evidence_ids())
    ctx.run.hypotheses = hyps
    ctx.run.hypothesis_history.append(HypothesisSnapshot(step=len(ctx.run.steps) + 1, hypotheses=hyps))
    data = {"hypotheses": [_round(h.model_dump()) for h in hyps]}
    summary = "Hypotheses: " + ", ".join(f"{h.moa} {h.probability:.2f}" for h in hyps[:4])
    return ctx.register("hypotheses", "update_hypotheses", summary, data)


async def submit_conclusion(ctx: ToolContext, a: SubmitConclusionArgs) -> Evidence:
    moa = canonical_moa(a.top_moa, ctx.moa_classes)
    unknown = [e for e in a.key_evidence_ids if e not in ctx.run.evidence_ids()]
    if unknown:
        raise ToolError(f"Unknown evidence id(s) {unknown}.")
    ctx.run.conclusion = Conclusion(top_moa=moa, probability=a.probability, key_evidence_ids=a.key_evidence_ids,
                                    next_experiment_if_this_were_a_real_lab=a.next_experiment_if_this_were_a_real_lab,
                                    rationale=a.rationale)
    ctx.run.phase = "review"
    data = ctx.run.conclusion.model_dump()
    return ctx.register("conclusion", "submit_conclusion", f"Conclusion: {moa} (p={a.probability:.2f})", data)


def _same_structure(a, b) -> bool:
    """Same InChIKey connectivity block (first 14 characters): same constitution, stereo/salt may differ."""
    return bool(a.inchikey and b.inchikey and a.inchikey[:14] == b.inchikey[:14])


async def _resolve_pubchem(ctx: ToolContext, names: list[str]) -> tuple[Any, str]:
    """PubChem record + how its identity was checked.

    For the specimen's own compound the dataset structure (BBBC021 SMILES) is the reference: internal codes
    such as "AZ-C" match unrelated PubChem synonyms by name, so a name hit is never shown unchecked.
    """
    by_name = None
    for n in names:
        if (by_name := await ctx.pubchem.lookup(n)) is not None:
            break
    if not any(n.casefold() == ctx.specimen.compound.casefold() for n in names):
        return by_name, "name only"
    if not ctx.specimen.smiles:
        return None, ("BBBC021 gives no structure for this compound (an internal code); name matches in PubChem "
                      "would be unrelated compounds, so none is shown")
    by_structure = await ctx.pubchem.lookup_smiles(ctx.specimen.smiles)
    if by_name and by_structure:
        if _same_structure(by_name, by_structure):
            return by_name, "name and BBBC021 structure agree"
        return by_name, (f"mismatch: name gives CID {by_name.cid}, BBBC021 structure gives CID {by_structure.cid} "
                         f"({by_structure.title})")
    if by_structure:
        return by_structure, "from the BBBC021 structure (the name is not in PubChem)"
    return by_name, "name only (the BBBC021 structure is not in PubChem)"


async def lookup_compound(ctx: ToolContext, a: LookupCompoundArgs) -> Evidence:
    if ctx.pubchem is None or ctx.chembl is None:
        raise ToolError("Compound databases are not configured.")
    # BBBC021 names like "mevinolin/lovastatin" list two names; try the whole string, then each part.
    names = list(dict.fromkeys([a.name.strip()] + [p.strip() for p in a.name.split("/") if p.strip()]))
    pubchem, identity = await _resolve_pubchem(ctx, names)
    unverifiable = pubchem is None and identity.startswith("BBBC021 gives no structure")
    chembl = None if unverifiable else await ctx.chembl.lookup(names, inchikey=pubchem.inchikey if pubchem else None)
    if pubchem is None and chembl is None and not unverifiable:
        raise ToolError(f"Neither PubChem nor ChEMBL knows '{a.name}'.")
    data = {"name": a.name, "identity_check": identity,
            "pubchem": pubchem.model_dump(exclude={"synonyms"}) | {"synonyms": pubchem.synonyms[:10]} if pubchem else None,
            "chembl": chembl.model_dump() if chembl else None}
    mechs = [m.mechanism_of_action for m in (chembl.mechanisms if chembl else [])]
    summary = (f"{a.name}: PubChem CID {pubchem.cid if pubchem else 'none'}; ChEMBL "
               f"{chembl.molecule_chembl_id if chembl else 'none'}; mechanisms: "
               f"{'; '.join(mechs) if mechs else 'none curated'} ({identity})")
    return ctx.register("compound", "lookup_compound", summary, data)


async def search_literature(ctx: ToolContext, a: SearchLiteratureArgs) -> Evidence:
    if ctx.pubmed is None:
        raise ToolError("PubMed client is not configured.")
    res = await ctx.pubmed.search(a.query, a.max_results)
    for art in res.articles:
        if art.pmid not in ctx.run.allowed_pmids:
            ctx.run.allowed_pmids.append(art.pmid)
    data = res.model_dump()
    summary = f"PubMed '{a.query}': {res.total_count} hits, {len(res.articles)} returned"
    return ctx.register("literature", "search_literature", summary, data)


HANDLERS: dict[str, Callable[[ToolContext, Any], Awaitable[Evidence]]] = {
    "list_available_experiments": list_available_experiments, "acquire_well": acquire_well,
    "describe_phenotype": describe_phenotype, "compare_to_reference_atlas": compare_to_reference_atlas,
    "dose_response": dose_response_tool, "update_hypotheses": update_hypotheses,
    "submit_conclusion": submit_conclusion, "lookup_compound": lookup_compound,
    "search_literature": search_literature,
}


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'args'}: {e['msg']}" for e in exc.errors()[:5])


async def execute(ctx: ToolContext, name: str, raw_args: str | dict) -> ToolOutcome:
    """Validate and run one tool call; record it as a Step. CostCapExceeded propagates (it ends the run)."""
    t0 = time.perf_counter()
    cost0 = ctx.meter.total_usd
    args: dict = {}
    rationale = ""
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        if not isinstance(args, dict):
            raise ToolError("Arguments must be a JSON object.")
        rationale = str(args.get("rationale", ""))[:RATIONALE_MAX]
        if name not in HANDLERS:
            raise ToolError(f"Unknown tool '{name}'.")
        if name not in available_tools(ctx.run):
            raise ToolError(f"'{name}' is not available in the {ctx.run.phase} phase. "
                            f"Available: {available_tools(ctx.run)}.")
        parsed = ARG_MODELS[name].model_validate(args)
        rationale = parsed.rationale
        evidence = await HANDLERS[name](ctx, parsed)
        outcome = ToolOutcome(name=name, ok=True, rationale=rationale, args=args, evidence=evidence)
    except CostCapExceeded:
        raise
    except json.JSONDecodeError as exc:
        outcome = ToolOutcome(name=name, ok=False, rationale=rationale, error=f"Arguments are not valid JSON: {exc}")
    except ValidationError as exc:
        outcome = ToolOutcome(name=name, ok=False, rationale=rationale, args=args,
                              error=f"Invalid arguments: {_validation_message(exc)}")
    except (ToolError, GuardrailError, LLMError, RuntimeError, KeyError) as exc:
        outcome = ToolOutcome(name=name, ok=False, rationale=rationale, args=args, error=str(exc))
    outcome.cost_usd = ctx.meter.total_usd - cost0
    ctx.run.model_calls = list(ctx.meter.calls)
    ctx.run.cost_usd = ctx.meter.total_usd
    ctx.run.steps.append(Step(step=len(ctx.run.steps) + 1, tool=name, args=outcome.args, rationale=outcome.rationale,
                              evidence_id=outcome.evidence.evidence_id if outcome.evidence else None,
                              error=outcome.error, cost_usd=outcome.cost_usd,
                              seconds=round(time.perf_counter() - t0, 3)))
    return outcome
