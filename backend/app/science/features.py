"""Per-cell, per-field and per-well features (spec §3).

Intensities are the per-plate DMSO-normalised .npy values (1st-99.8th pct of
DMSO pixels -> 0..1, unclipped), so absolute thresholds mean the same thing on
every plate.

Pipeline: cell_features (one row per segmented nucleus/cell) -> field_features
(counts and phenotype fractions, needs a DMSO reference) -> well_profile
(median over cells + mean of field features) -> normalize_to_dmso (robust z vs
DMSO wells of the same plate) -> treatment_profiles (median over replicate wells).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from skimage import measure
from skimage.feature import graycomatrix

CELL_FEATURES = [
    "nuc_area", "nuc_dna_integrated", "nuc_dna_mean", "nuc_eccentricity", "nuc_solidity", "nuc_form_factor",
    "nuc_major_axis", "cell_area", "cell_nuc_area_ratio", "cell_roundness", "cell_eccentricity", "cell_solidity",
    "cell_major_axis", "tub_mean", "tub_std", "tub_glcm_contrast", "act_mean", "act_std", "act_glcm_contrast",
    "act_tub_ratio",
]
FIELD_FEATURES = ["cell_count", "frac_mitotic_like", "frac_large_nucleus", "frac_multinucleate_proxy"]
PROFILE_FEATURES = CELL_FEATURES + FIELD_FEATURES

GLCM_LEVELS = 32  # level 0 is reserved for pixels outside the cell
GLCM_MAX = 2.0  # normalised intensity mapped to the top level
EPS = 1e-6


@dataclass(frozen=True)
class Reference:
    """DMSO medians of the per-cell quantities the phenotype fractions are relative to."""

    nuc_area: float
    nuc_dna_mean: float
    # mitotic-like: compact, bright, small nucleus
    mitotic_area_max: float = 0.6
    mitotic_dna_min: float = 1.5
    mitotic_solidity_min: float = 0.9
    # polyploid proxy
    large_area_min: float = 2.0

    def to_dict(self) -> dict:
        return asdict(self)


def _form_factor(area: np.ndarray, perimeter: np.ndarray) -> np.ndarray:
    return 4 * np.pi * area / np.maximum(perimeter, EPS) ** 2


def _quantize(image: np.ndarray) -> np.ndarray:
    return 1 + np.floor(np.clip(image, 0, GLCM_MAX) / GLCM_MAX * (GLCM_LEVELS - 2)).astype(np.int32)


def glcm_contrast(image: np.ndarray, mask: np.ndarray) -> float:
    """GLCM contrast (distance 1, 0 and 90 degrees) over pixel pairs that both lie inside the mask.

    Reference implementation with skimage; glcm_contrast_all computes the same for every label at once.
    """
    q = _quantize(image).astype(np.uint8)
    q[~mask] = 0
    glcm = graycomatrix(q, [1], [0, np.pi / 2], levels=GLCM_LEVELS, symmetric=True)[1:, 1:].sum(axis=(2, 3))
    total = glcm.sum()
    if total == 0:
        return 0.0
    i, j = np.indices(glcm.shape)
    return float(((i - j) ** 2 * glcm).sum() / total * (GLCM_MAX / (GLCM_LEVELS - 2)) ** 2)


def glcm_contrast_all(image: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """GLCM contrast per label (index = label), from right and down neighbour pairs within the same label."""
    q = _quantize(image)
    n = int(labels.max()) + 1
    total, count = np.zeros(n), np.zeros(n)
    for a, b, qa, qb in ((labels[:, :-1], labels[:, 1:], q[:, :-1], q[:, 1:]),
                         (labels[:-1, :], labels[1:, :], q[:-1, :], q[1:, :])):
        same = (a == b) & (a > 0)
        total += np.bincount(a[same], weights=((qa[same] - qb[same]) ** 2).astype(float), minlength=n)
        count += np.bincount(a[same], minlength=n)
    return np.where(count > 0, total / np.maximum(count, 1), 0.0) * (GLCM_MAX / (GLCM_LEVELS - 2)) ** 2


def cell_features(nuclei: np.ndarray, cells: np.ndarray, channels: dict[str, np.ndarray]) -> pd.DataFrame:
    """One row per nucleus that has a cell. `cells` label k must contain nucleus label k."""
    dna, tub, act = channels["dna"], channels["tubulin"], channels["actin"]
    nuc = measure.regionprops_table(
        nuclei, intensity_image=dna,
        properties=("label", "area", "intensity_mean", "eccentricity", "solidity", "perimeter",
                    "axis_major_length", "centroid"))
    nuc = pd.DataFrame(nuc).set_index("label")
    cel = measure.regionprops_table(
        cells, intensity_image=np.stack([tub, act], axis=-1),
        properties=("label", "area", "eccentricity", "solidity", "perimeter", "axis_major_length", "bbox",
                    "intensity_mean", "intensity_std"))
    cel = pd.DataFrame(cel).set_index("label")
    common = nuc.index.intersection(cel.index)
    nuc, cel = nuc.loc[common], cel.loc[common]

    # Touching nuclei (one connected DNA-positive component split by the watershed): clumping / multinucleation proxy.
    components = measure.label(nuclei > 0)
    comp_of = pd.Series(components[nuclei > 0], index=nuclei[nuclei > 0]).groupby(level=0).first()
    comp_size = comp_of.map(comp_of.value_counts())

    h, w = nuclei.shape
    df = pd.DataFrame(index=common)
    df["nuc_area"] = nuc["area"]
    df["nuc_dna_mean"] = nuc["intensity_mean"]
    df["nuc_dna_integrated"] = nuc["area"] * nuc["intensity_mean"]
    df["nuc_eccentricity"] = nuc["eccentricity"]
    df["nuc_solidity"] = nuc["solidity"]
    df["nuc_form_factor"] = _form_factor(nuc["area"].to_numpy(), nuc["perimeter"].to_numpy())
    df["nuc_major_axis"] = nuc["axis_major_length"]
    df["cell_area"] = cel["area"]
    df["cell_nuc_area_ratio"] = cel["area"] / nuc["area"]
    df["cell_roundness"] = _form_factor(cel["area"].to_numpy(), cel["perimeter"].to_numpy())
    df["cell_eccentricity"] = cel["eccentricity"]
    df["cell_solidity"] = cel["solidity"]
    df["cell_major_axis"] = cel["axis_major_length"]
    df["tub_mean"] = cel["intensity_mean-0"]
    df["tub_std"] = cel["intensity_std-0"]
    df["act_mean"] = cel["intensity_mean-1"]
    df["act_std"] = cel["intensity_std-1"]
    df["act_tub_ratio"] = cel["intensity_mean-1"] / np.maximum(cel["intensity_mean-0"], EPS)

    df["tub_glcm_contrast"] = glcm_contrast_all(tub, cells)[common]
    df["act_glcm_contrast"] = glcm_contrast_all(act, cells)[common]

    df["centroid_y"] = nuc["centroid-0"]
    df["centroid_x"] = nuc["centroid-1"]
    df["touches_border"] = ((cel["bbox-0"] == 0) | (cel["bbox-1"] == 0) | (cel["bbox-2"] == h) | (cel["bbox-3"] == w))
    df["nuc_in_cluster"] = comp_size.reindex(common).fillna(1).to_numpy() > 1
    df.index.name = "label"
    return df.reset_index()


def reference_from_dmso(dmso_cells: pd.DataFrame) -> Reference:
    inner = dmso_cells[~dmso_cells["touches_border"]]
    return Reference(nuc_area=float(inner["nuc_area"].median()), nuc_dna_mean=float(inner["nuc_dna_mean"].median()))


def phenotype_flags(cells: pd.DataFrame, ref: Reference) -> pd.DataFrame:
    return pd.DataFrame({
        "mitotic_like": (cells["nuc_area"] < ref.mitotic_area_max * ref.nuc_area)
        & (cells["nuc_dna_mean"] > ref.mitotic_dna_min * ref.nuc_dna_mean)
        & (cells["nuc_solidity"] > ref.mitotic_solidity_min),
        "large_nucleus": cells["nuc_area"] > ref.large_area_min * ref.nuc_area,
        "multinucleate_proxy": cells["nuc_in_cluster"],
    }, index=cells.index)


def field_features(cells: pd.DataFrame, ref: Reference) -> dict[str, float]:
    """cell_count counts every nucleus; fractions use cells not cut by the image border."""
    inner = cells[~cells["touches_border"]]
    flags = phenotype_flags(inner, ref)
    n = len(inner)
    return {
        "cell_count": float(len(cells)),
        "frac_mitotic_like": float(flags["mitotic_like"].mean()) if n else np.nan,
        "frac_large_nucleus": float(flags["large_nucleus"].mean()) if n else np.nan,
        "frac_multinucleate_proxy": float(flags["multinucleate_proxy"].mean()) if n else np.nan,
    }


def well_profile(cells_per_field: list[pd.DataFrame], ref: Reference) -> pd.Series:
    """Median over the well's (non-border) cells for cell features; mean over fields for field features."""
    fields = pd.DataFrame([field_features(c, ref) for c in cells_per_field])
    pooled = pd.concat(cells_per_field, ignore_index=True)
    pooled = pooled[~pooled["touches_border"]]
    profile = pooled[CELL_FEATURES].median() if len(pooled) else pd.Series(np.nan, index=CELL_FEATURES)
    profile = pd.concat([profile, fields[FIELD_FEATURES].mean()])
    profile["n_cells"] = float(len(pooled))
    return profile


def normalize_to_dmso(wells: pd.DataFrame, features: list[str] = PROFILE_FEATURES,
                      mad_floor_fraction: float = 0.5) -> pd.DataFrame:
    """Robust z-score of every well vs the DMSO wells of its own plate.

    z = (x - median_DMSO_plate) / (1.4826 * MAD_DMSO_plate). With only ~6 DMSO
    wells per plate a plate's MAD can be near zero, so the scale is floored at
    `mad_floor_fraction` x the median per-plate MAD of that feature over all plates.
    `wells` needs columns 'plate', 'role' ('control' = DMSO) and the features.
    """
    dmso = wells[wells["role"] == "control"]
    med = dmso.groupby("plate")[features].median()
    mad = dmso.groupby("plate")[features].agg(lambda s: (s - s.median()).abs().median()) * 1.4826
    floor = mad.median() * mad_floor_fraction
    scale = mad.clip(lower=floor, axis=1).clip(lower=EPS)
    out = wells.copy()
    out[features] = (wells[features] - med.loc[wells["plate"]].to_numpy()) / scale.loc[wells["plate"]].to_numpy()
    return out


def treatment_profiles(norm_wells: pd.DataFrame, features: list[str] = PROFILE_FEATURES) -> pd.DataFrame:
    """Median over replicate wells per (compound, concentration). DMSO is excluded."""
    treated = norm_wells[norm_wells["role"] == "treated"]
    keys = ["compound", "concentration_uM"]
    prof = treated.groupby(keys)[features].median()
    meta = treated.groupby(keys).agg(moa=("moa", "first"), moa_annotated_conc=("moa_annotated_conc", "first"),
                                     n_wells=("well_key", "nunique"), thumbnail_field_id=("thumbnail_field_id", "first"))
    return meta.join(prof).reset_index()
