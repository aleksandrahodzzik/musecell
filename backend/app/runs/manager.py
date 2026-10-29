"""Services shared by the API and the CLI: data, model client, external databases, live runs and replays.

Startup: the data (index, atlas, MoA classes) is optional — a fresh clone without DATA_DIR can still replay the
recorded demo runs. The model client is optional too (no key → replay only), but when a key is set the model
ids are checked with GET /v1/models and a missing id refuses the start (spec §4).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from app.agent import tools as T
from app.agent.guardrails import BlindingScanner, load_moa_classes
from app.agent.loop import AgentLoop
from app.agent.schemas import AgentRun, RunMode
from app.agent.specimens import Specimen, SpecimenCatalog
from app.config import MAX_BUDGET_WELLS, Settings, get_settings
from app.external.chembl import ChemblClient
from app.fields import load_reference
from app.external.pubchem import PubChemClient
from app.external.pubmed import PubMedClient
from app.llm import CostMeter, LLMClient
from app.runs.replay import replay, replay_info
from app.runs.store import RUNS_DIR, RunBus, recorded_path

SEGMENTATION_LABEL = {"classical": "classical (scikit-image)", "sam": "sam-3.1"}


class RunRequestError(ValueError):
    """Bad request (unknown specimen, budget out of range, live runs unavailable); safe to show."""


class RunManager:
    def __init__(self, settings: Settings | None = None, runs_dir: Path = RUNS_DIR):
        self.settings = settings or get_settings()
        self.runs_dir = runs_dir
        self.catalog: SpecimenCatalog | None = None
        self.atlas = None
        self.reference = None  # DMSO medians for per-cell phenotype flags (microscope hover)
        self.moa_classes: list[str] = []
        self.llm: LLMClient | None = None
        self.data_error: str | None = None
        self.llm_error: str | None = None
        self.pubchem = PubChemClient(self.settings.cache_dir)
        self.chembl = ChemblClient(self.settings.cache_dir)
        self.pubmed = PubMedClient(self.settings.cache_dir, self.settings.ncbi_email)
        self.buses: dict[str, RunBus] = {}
        self.runs: dict[str, AgentRun] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    async def startup(self, check_models: bool = True) -> None:
        from app.science.atlas import Atlas  # heavy imports only when the data is there

        try:
            self.catalog = SpecimenCatalog.load(self.settings)
            self.atlas = Atlas.load(self.settings.atlas_dir)
            self.moa_classes = load_moa_classes(self.settings.moa_csv)
            self.reference = load_reference(self.settings.atlas_dir)
            _ = self.catalog.specimens
        except (OSError, KeyError, ValueError) as exc:
            self.catalog, self.data_error = None, (f"BBBC021 data not found under DATA_DIR ({type(exc).__name__}). "
                                                   "Live runs are off; recorded runs can still be replayed.")
        if self.settings.model_api_key is None and self.settings.provider == "meta":
            self.llm_error = "MODEL_API_KEY is empty in backend/.env. Live runs are off; replays work."
            return
        self.llm = LLMClient(self.settings)
        if check_models:
            await self.llm.check_models()  # raises ModelCheckError: refuse to start

    @property
    def live_enabled(self) -> bool:
        return self.catalog is not None and self.llm is not None

    def specimen_list(self) -> list[dict]:
        """Opaque ids only: no compound, MoA, plate or dose values."""
        if self.catalog is None:
            return []
        out = []
        for i, sid in enumerate(sorted(self.catalog.specimens), start=1):
            sp = self.catalog.specimens[sid]
            out.append({"specimen_id": sid, "label": f"Specimen {i}", "doses": len(sp.concentrations_uM),
                        "wells": len(sp.wells)})
        return out

    def model_ids(self) -> dict[str, str]:
        s = self.settings
        return {"scientist": s.scientist_model, "microscopist": s.microscopist_model, "reviewer": s.reviewer_model,
                "segmentation": SEGMENTATION_LABEL[s.nuclei_segmenter]}

    async def _blinding_terms(self, sp: Specimen) -> list[str]:
        """Name, PubChem synonyms (found via the dataset structure when there is one), SMILES, plates, wells."""
        synonyms: list[str] = []
        try:
            rec = await self.pubchem.lookup_smiles(sp.smiles) if sp.smiles else None
            if rec is None and sp.smiles:  # internal codes (no SMILES) would match unrelated PubChem names
                for name in [sp.compound, *sp.compound.split("/")]:
                    if (rec := await self.pubchem.lookup(name.strip())) is not None:
                        break
            if rec is not None:
                synonyms = await self.pubchem.synonyms(rec.cid)
        except Exception:  # noqa: BLE001 - the guard still has name, SMILES, plates and wells
            synonyms = []
        return sp.blinding_terms(synonyms)

    async def create_run(self, specimen_id: str, budget: int, mode: RunMode = "blinded") -> AgentLoop:
        if not self.live_enabled:
            raise RunRequestError(self.data_error or self.llm_error or "Live runs are not available.")
        if not 1 <= budget <= MAX_BUDGET_WELLS:
            raise RunRequestError(f"Budget must be 1-{MAX_BUDGET_WELLS} wells.")
        try:
            sp = self.catalog.get(specimen_id)
        except KeyError as exc:
            raise RunRequestError(str(exc.args[0])) from None
        run_id = "run_" + uuid.uuid4().hex[:12]
        run = AgentRun(run_id=run_id, mode=mode, specimen_id=sp.specimen_id, budget_total=budget,
                       model_ids=self.model_ids())
        ctx = T.ToolContext(run=run, specimen=sp, catalog=self.catalog, atlas=self.atlas, moa_classes=self.moa_classes,
                            meter=CostMeter(run_id=run_id, cap_usd=self.settings.run_cost_cap_usd), llm=self.llm,
                            pubchem=self.pubchem, chembl=self.chembl, pubmed=self.pubmed, settings=self.settings)
        scanner = BlindingScanner(await self._blinding_terms(sp)) if mode == "blinded" else None
        bus = RunBus(run_id, self.runs_dir / f"{run_id}.jsonl")
        self.buses[run_id], self.runs[run_id] = bus, run
        return AgentLoop(ctx, bus, scanner, runs_dir=self.runs_dir)

    async def start_run(self, specimen_id: str, budget: int, mode: RunMode = "blinded") -> str:
        loop = await self.create_run(specimen_id, budget, mode)
        self.tasks[loop.run.run_id] = asyncio.create_task(loop.run_all())
        return loop.run.run_id

    def bus(self, run_id: str) -> RunBus | None:
        if run_id in self.buses:
            return self.buses[run_id]
        path = recorded_path(run_id, (self.runs_dir, RUNS_DIR.parent / "demo" / "runs"))
        return RunBus.from_file(path) if path else None

    async def start_replay(self, run_id: str, speed: float) -> dict:
        source = self.bus(run_id)
        if source is None or not source.events:
            raise RunRequestError(f"No recorded run '{run_id}'.")
        if not source.finished:
            raise RunRequestError(f"Run '{run_id}' is still live; replay it when it has finished.")
        replay_id = "replay_" + uuid.uuid4().hex[:12]
        target = RunBus(replay_id)  # replays are not recorded again
        self.buses[replay_id] = target
        self.tasks[replay_id] = asyncio.create_task(replay(source, target, speed))
        return {"run_id": replay_id, **replay_info(source)}

    async def stop(self, run_id: str) -> None:
        """Stop a live run (the loop reports "The run was stopped.") or a replay."""
        task, bus = self.tasks.get(run_id), self.buses.get(run_id)
        if task is None or bus is None or task.done():
            raise RunRequestError(f"Run '{run_id}' is not running.")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the loop already emitted its error event
            pass
        if not bus.finished:  # a replay: its recorded events stop here
            bus.emit("error", {"message": "Replay stopped.", "fatal": True})
            bus.emit("run_finished", {"status": "failed", "error": "Replay stopped."})

    def source_run_id(self, run_id: str) -> str:
        """A replay's recorded run id; any other id unchanged."""
        bus = self.buses.get(run_id)
        if bus is not None and bus.events and bus.events[0].data.get("replay"):
            return bus.events[0].data["replay"]["replay_of"]
        return run_id

    async def aclose(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        for client in (self.pubchem, self.chembl, self.pubmed):
            await client.aclose()
        if self.llm is not None:
            await self.llm.aclose()
