"""Reference atlas of MoA-annotated treatment profiles with LOCO neighbours (spec §3).

Similarity is cosine on DMSO-normalised (robust z) profiles, optionally after
PCA whitening fitted on DMSO well profiles. `neighbors` always excludes the
query compound (leave-one-compound-out), so a compound can never match itself.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel

from app.config import get_settings
from app.science.features import PROFILE_FEATURES

WHITEN_EPS = 1e-3


class Neighbor(BaseModel):
    compound: str
    concentration_uM: float
    moa: str
    similarity: float
    thumbnail_field_id: str


class Atlas:
    def __init__(self, treatments: pd.DataFrame, features: list[str] = PROFILE_FEATURES,
                 dmso_wells: pd.DataFrame | None = None, whiten: bool = False):
        ref = treatments[treatments["moa_annotated_conc"] & treatments["moa"].notna()].reset_index(drop=True)
        self.features = features
        self.reference = ref
        self.whiten = whiten
        self._mean = np.zeros(len(features))
        self._proj = np.eye(len(features))
        if whiten:
            if dmso_wells is None:
                raise ValueError("whitening needs DMSO well profiles")
            x = np.nan_to_num(dmso_wells[features].to_numpy(dtype=float))
            self._mean = x.mean(axis=0)
            u, s, vt = np.linalg.svd(x - self._mean, full_matrices=False)
            scale = s / np.sqrt(max(len(x) - 1, 1))
            self._proj = vt.T / np.maximum(scale, WHITEN_EPS)
        self._matrix = self._embed(ref[features].to_numpy(dtype=float))

    @classmethod
    def load(cls, atlas_dir: Path | None = None, whiten: bool = False) -> Atlas:
        atlas_dir = atlas_dir or get_settings().atlas_dir
        treatments = pd.read_parquet(atlas_dir / "treatments.parquet")
        dmso = None
        if whiten:
            wells = pd.read_parquet(atlas_dir / "wells_norm.parquet")
            dmso = wells[wells["role"] == "control"]
        return cls(treatments, dmso_wells=dmso, whiten=whiten)

    def _embed(self, x: np.ndarray) -> np.ndarray:
        z = (np.nan_to_num(np.atleast_2d(x)) - self._mean) @ self._proj
        return z / np.maximum(np.linalg.norm(z, axis=1, keepdims=True), 1e-12)

    def similarities(self, profile: pd.Series | np.ndarray) -> np.ndarray:
        vec = profile[self.features].to_numpy(dtype=float) if isinstance(profile, pd.Series) else profile
        return self._matrix @ self._embed(vec)[0]

    def neighbors(self, profile: pd.Series | np.ndarray, k: int, *, exclude_compound: str) -> list[Neighbor]:
        """Top-k annotated treatments by cosine similarity, never from `exclude_compound` (LOCO)."""
        sims = self.similarities(profile)
        allowed = (self.reference["compound"] != exclude_compound).to_numpy()
        order = [i for i in np.argsort(-sims) if allowed[i]][:k]
        return [Neighbor(compound=self.reference.at[i, "compound"],
                         concentration_uM=float(self.reference.at[i, "concentration_uM"]),
                         moa=str(self.reference.at[i, "moa"]), similarity=float(sims[i]),
                         thumbnail_field_id=self.reference.at[i, "thumbnail_field_id"]) for i in order]

    @property
    def moa_classes(self) -> list[str]:
        return sorted(self.reference["moa"].astype(str).unique())


def nsc_baseline(atlas: Atlas) -> pd.DataFrame:
    """Not-same-compound 1-NN: predict each annotated treatment's MoA from its nearest other-compound treatment."""
    rows = []
    for _, t in atlas.reference.iterrows():
        nn = atlas.neighbors(t, k=1, exclude_compound=t["compound"])[0]
        rows.append({"compound": t["compound"], "concentration_uM": t["concentration_uM"], "moa": t["moa"],
                     "predicted": nn.moa, "neighbor": nn.compound, "similarity": nn.similarity})
    return pd.DataFrame(rows)


def compound_level(pred: pd.DataFrame) -> pd.DataFrame:
    """Majority vote over a compound's concentrations; ties broken by summed similarity."""
    rows = []
    for compound, grp in pred.groupby("compound"):
        votes = grp.groupby("predicted").agg(n=("predicted", "size"), s=("similarity", "sum"))
        best = votes.sort_values(["n", "s"], ascending=False).index[0]
        rows.append({"compound": compound, "moa": grp["moa"].iloc[0], "predicted": best})
    return pd.DataFrame(rows)
