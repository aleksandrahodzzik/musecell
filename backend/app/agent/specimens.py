"""Blinded specimens: the server-side map from opaque ids to compounds, plates and wells.

A specimen is one MoA-annotated compound with all its doses. The agent and the browser only ever see
`s_…` (specimen), `w_…` (well) and `f_…` (field) ids plus dose indices and µM values. Plate names and
well positions are hidden too: with the public BBBC021 metadata they would reveal the compound.
Ids are salted with DATA_DIR/field_id_salt (same reason as the field ids, docs/DECISIONS.md).
"""

from __future__ import annotations

import hashlib
from functools import cached_property
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.science.features import PROFILE_FEATURES


class Well(BaseModel):
    well_id: str  # opaque
    concentration_index: int  # 0 = lowest dose
    concentration_uM: float
    replicate: int  # 1..n within its concentration
    field_ids: list[str]
    plate: str  # server-side only
    well_key: str  # server-side only, "<plate>/<well>"


class Specimen(BaseModel):
    specimen_id: str
    compound: str  # server-side only until unblinding
    moa: str  # ground truth, server-side only
    smiles: str | None = None
    concentrations_uM: list[float]
    wells: list[Well]

    def well(self, concentration_index: int, replicate: int) -> Well | None:
        return next((w for w in self.wells if w.concentration_index == concentration_index
                     and w.replicate == replicate), None)

    def well_by_id(self, well_id: str) -> Well | None:
        return next((w for w in self.wells if w.well_id == well_id), None)

    def replicates(self, concentration_index: int) -> int:
        return sum(w.concentration_index == concentration_index for w in self.wells)

    @property
    def plates(self) -> list[str]:
        return sorted({w.plate for w in self.wells})

    def blinding_terms(self, synonyms: list[str] = ()) -> list[str]:
        """Everything that would reveal the compound if it reached the model or the browser."""
        return [self.compound, *(synonyms or []), *([self.smiles] if self.smiles else []), *self.plates,
                *(w.well_key for w in self.wells)]


def _opaque(salt: str, prefix: str, kind: str, key: str, n: int = 10) -> str:
    return prefix + hashlib.sha256(f"{salt}/{kind}/{key}".encode()).hexdigest()[:n]


class SpecimenCatalog:
    """Built from the index and the atlas tables (wells.parquet raw, wells_norm.parquet robust z)."""

    def __init__(self, index: pd.DataFrame, wells_raw: pd.DataFrame, wells_norm: pd.DataFrame, salt: str,
                 smiles: dict[str, str] | None = None, cells_dir: Path | None = None, data_path: Path | None = None):
        self.index = index.set_index("field_id", drop=False)
        self.raw = wells_raw.set_index("well_key", drop=False)
        self.norm = wells_norm.set_index("well_key", drop=False)
        self.salt = salt
        self.smiles = smiles or {}
        self.cells_dir = cells_dir
        self.data_path = data_path

    @classmethod
    def load(cls, settings: Settings | None = None) -> SpecimenCatalog:
        s = settings or get_settings()
        salt = (s.data_path / "field_id_salt").read_text().strip()
        compounds = pd.read_csv(s.data_path / "metadata" / "BBBC021_v1_compound.csv").dropna(subset=["smiles"])
        return cls(pd.read_parquet(s.index_path), pd.read_parquet(s.atlas_dir / "wells.parquet"),
                   pd.read_parquet(s.atlas_dir / "wells_norm.parquet"), salt,
                   smiles=dict(zip(compounds["compound"], compounds["smiles"])),
                   cells_dir=s.cells_dir, data_path=s.data_path)

    @cached_property
    def specimens(self) -> dict[str, Specimen]:
        treated = self.norm[(self.norm["role"] == "treated") & self.norm["moa"].notna()]
        annotated = set(treated["compound"])
        out: dict[str, Specimen] = {}
        for compound in sorted(annotated):
            rows = self.norm[(self.norm["role"] == "treated") & (self.norm["compound"] == compound)]
            concs = sorted(rows["concentration_uM"].unique())
            wells = []
            for ci, conc in enumerate(concs):
                at = rows[rows["concentration_uM"] == conc].sort_values(["replicate", "plate", "well"])
                for rep, (_, w) in enumerate(at.iterrows(), start=1):
                    wells.append(Well(well_id=_opaque(self.salt, "w_", "well", w["well_key"]), concentration_index=ci,
                                      concentration_uM=float(conc), replicate=rep, field_ids=list(w["field_ids"]),
                                      plate=w["plate"], well_key=w["well_key"]))
            sid = _opaque(self.salt, "s_", "specimen", compound)
            out[sid] = Specimen(specimen_id=sid, compound=compound, moa=str(rows["moa"].dropna().iloc[0]),
                                smiles=self.smiles.get(compound), concentrations_uM=[float(c) for c in concs],
                                wells=wells)
        return out

    def get(self, specimen_id: str) -> Specimen:
        try:
            return self.specimens[specimen_id]
        except KeyError:
            raise KeyError(f"Unknown specimen '{specimen_id}'.") from None

    # --- measurements -----------------------------------------------------------------------------

    def profile(self, well_key: str) -> pd.Series:
        """Robust z vs same-plate DMSO."""
        return self.norm.loc[well_key, PROFILE_FEATURES].astype(float)

    def raw_profile(self, well_key: str) -> pd.Series:
        return self.raw.loc[well_key, PROFILE_FEATURES].astype(float)

    def n_cells(self, well_key: str) -> int:
        return int(self.raw.loc[well_key, "n_cells"])

    def dmso_raw_median(self, plate: str) -> pd.Series:
        dmso = self.raw[(self.raw["plate"] == plate) & (self.raw["role"] == "control")]
        return dmso[PROFILE_FEATURES].astype(float).median()

    def dmso_field_ids(self, plate: str) -> list[str]:
        rows = self.index[(self.index["plate"] == plate) & (self.index["role"] == "control")]
        return list(rows.sort_values(["well", "field"])["field_id"])

    def field_row(self, field_id: str) -> pd.Series:
        return self.index.loc[field_id]

    def field_path(self, field_id: str, image: str) -> Path:
        """image: composite | dna | tubulin | actin"""
        if self.data_path is None:
            raise RuntimeError("catalog has no data_path")
        return self.data_path / self.index.at[field_id, f"png_{image}"]

    def segmentation_counts(self, field_id: str) -> dict[str, int] | None:
        """Nuclei found in a field and how many touch the border (from the cached cell tables)."""
        if self.cells_dir is None:
            return None
        path = self.cells_dir / f"{field_id}.parquet"
        if not path.exists():
            return None
        cells = pd.read_parquet(path, columns=["touches_border"])
        return {"nuclei": len(cells), "touching_border": int(cells["touches_border"].sum())}
