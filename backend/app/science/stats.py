"""Statistics the agent and UI read numbers from (spec §3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel
from scipy import stats


class DosePoint(BaseModel):
    concentration_uM: float
    median: float
    n_wells: int


class DoseResponse(BaseModel):
    feature: str
    points: list[DosePoint]
    spearman_rho: float | None
    p_value: float | None
    n_wells: int
    note: str | None = None


class FeatureDeviation(BaseModel):
    feature: str
    z: float
    direction: str  # "up" | "down"
    value: float | None = None
    dmso_median: float | None = None


def dose_response(feature: str, acquired_wells: pd.DataFrame) -> DoseResponse:
    """Per-concentration medians of `feature` and Spearman rho of feature vs concentration over wells.

    `acquired_wells`: one row per acquired well with 'concentration_uM' and the feature (normalised z).
    """
    df = acquired_wells[["concentration_uM", feature]].dropna()
    points = [DosePoint(concentration_uM=float(c), median=float(g[feature].median()), n_wells=len(g))
              for c, g in df.groupby("concentration_uM", sort=True)]
    if len(points) < 2 or len(df) < 3:
        return DoseResponse(feature=feature, points=points, spearman_rho=None, p_value=None, n_wells=len(df),
                            note="Need at least 3 wells over 2 concentrations for a dose trend.")
    if df[feature].nunique() == 1:
        return DoseResponse(feature=feature, points=points, spearman_rho=None, p_value=None, n_wells=len(df),
                            note="Feature is constant across the acquired wells.")
    res = stats.spearmanr(df["concentration_uM"], df[feature])
    return DoseResponse(feature=feature, points=points, spearman_rho=float(res.statistic),
                        p_value=float(res.pvalue), n_wells=len(df))


def compare_to_dmso(profile: pd.Series, k: int = 8, raw: pd.Series | None = None,
                    dmso_median: pd.Series | None = None) -> list[FeatureDeviation]:
    """Top-k features by |z| of a DMSO-normalised profile, with raw values when given."""
    z = profile.astype(float).dropna()
    top = z.reindex(z.abs().sort_values(ascending=False).index[:k])

    def opt(series: pd.Series | None, name: str) -> float | None:
        if series is None or name not in series or pd.isna(series[name]):
            return None
        return float(series[name])

    return [FeatureDeviation(feature=name, z=float(v), direction="up" if v > 0 else "down",
                             value=opt(raw, name), dmso_median=opt(dmso_median, name))
            for name, v in top.items() if np.isfinite(v)]
