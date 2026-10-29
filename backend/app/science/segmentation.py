"""Nuclei and cell segmentation (spec §3).

Path B, classical: nuclei from DNA (Gaussian -> Otsu -> distance-transform
watershed -> size filter); cell bodies by watershed on tubulin+actin seeded
from the nuclei, so cell label k always belongs to nucleus label k.

Path A, SAM 3.1: concept prompts on the display PNGs (see app/sam.py); label
images are upscaled to the measurement grid, and SAM cells are relabelled to
the nucleus whose centroid they contain so features can pair them.

Which path runs per object type comes from Settings (nuclei_segmenter /
cell_segmenter); every Segmentation records which one produced its masks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage import filters, measure, morphology, segmentation
from skimage.feature import peak_local_max

from app.config import Segmenter, Settings, get_settings

# Pixel sizes at the native 1024x1280 grid. DMSO nuclei are ~300-900 px.
NUC_SIGMA = 2.0
NUC_MIN_AREA = 60
NUC_MAX_AREA = 8000
NUC_PEAK_MIN_DISTANCE = 7
NUC_PEAK_FOOTPRINT = 13
CELL_SIGMA = 2.0
CELL_THRESHOLD_FACTOR = 0.5  # cytoplasm foreground = intensity > factor * Otsu
CELL_MAX_RADIUS = 80  # px a cell may extend beyond its nucleus

SAM_PROMPTS = {"nuclei": ("dna", "nucleus"), "cells": ("composite", "cell")}


@dataclass
class Segmentation:
    nuclei: np.ndarray  # int32 labels, 0 = background
    cells: np.ndarray  # int32 labels; cell k contains nucleus k (0 where no cell)
    nuclei_method: Segmenter
    cells_method: Segmenter
    cost_usd: float = 0.0


def segment_nuclei_classical(dna: np.ndarray) -> np.ndarray:
    smooth = filters.gaussian(dna, sigma=NUC_SIGMA, preserve_range=True)
    mask = smooth > filters.threshold_otsu(smooth)
    mask = ndi.binary_fill_holes(morphology.remove_small_objects(mask, max_size=NUC_MIN_AREA - 1))
    dist = ndi.distance_transform_edt(mask)
    dist = filters.gaussian(dist, sigma=1.0, preserve_range=True)  # one peak per round nucleus
    peaks = peak_local_max(dist, min_distance=NUC_PEAK_MIN_DISTANCE, footprint=np.ones((NUC_PEAK_FOOTPRINT,) * 2),
                           labels=measure.label(mask), exclude_border=False)
    markers = np.zeros(dna.shape, dtype=np.int32)
    markers[tuple(peaks.T)] = np.arange(1, len(peaks) + 1)
    labels = segmentation.watershed(-dist, markers, mask=mask)
    return size_filter(labels, NUC_MIN_AREA, NUC_MAX_AREA)


def size_filter(labels: np.ndarray, min_area: int, max_area: int) -> np.ndarray:
    areas = np.bincount(labels.ravel())
    keep = (areas >= min_area) & (areas <= max_area)
    keep[0] = False
    out, _, _ = segmentation.relabel_sequential(np.where(keep[labels], labels, 0))
    return out.astype(np.int32)


def segment_cells_classical(nuclei: np.ndarray, tubulin: np.ndarray, actin: np.ndarray) -> np.ndarray:
    cyto = filters.gaussian(np.clip(tubulin, 0, None) + np.clip(actin, 0, None), sigma=CELL_SIGMA,
                            preserve_range=True)
    foreground = (cyto > CELL_THRESHOLD_FACTOR * filters.threshold_otsu(cyto)) | (nuclei > 0)
    # Keep cells from flooding empty background far from any nucleus.
    near = ndi.distance_transform_edt(nuclei == 0) <= CELL_MAX_RADIUS
    cells = segmentation.watershed(-cyto, nuclei, mask=foreground & near)
    return cells.astype(np.int32)


def _upscale_labels(labels: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if labels.shape == shape:
        return labels.astype(np.int32)
    im = Image.fromarray(labels.astype(np.int32)).resize((shape[1], shape[0]), Image.NEAREST)
    return np.asarray(im, dtype=np.int32)


def pair_cells_to_nuclei(nuclei: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """Relabel a free cell mask so cell k is the region holding nucleus k's centroid.

    A cell that holds several nucleus centroids goes to the first; nuclei with no
    containing cell get no cell (label absent).
    """
    out = np.zeros_like(nuclei)
    taken: set[int] = set()
    for region in measure.regionprops(nuclei):
        y, x = (int(round(v)) for v in region.centroid)
        cell = int(cells[y, x])
        if cell and cell not in taken:
            taken.add(cell)
            out[cells == cell] = region.label
    out[nuclei > 0] = nuclei[nuclei > 0]  # a nucleus always lies inside its own cell
    return out


def segment_sam(field_id: str, png_path: Path, prompt: str, image_name: str, shape: tuple[int, int],
                settings: Settings) -> tuple[np.ndarray, float]:
    from app import sam  # network client; imported lazily so the classical path has no API dependency

    result = sam.segment(png_path, prompt, field_id, image_name, settings)
    return _upscale_labels(result.labels, shape), result.cost_usd


def segment_field(field_id: str, channels: dict[str, np.ndarray], pngs: dict[str, Path] | None = None,
                  nuclei_method: Segmenter | None = None, cells_method: Segmenter | None = None,
                  settings: Settings | None = None) -> Segmentation:
    """channels: float32 arrays 'dna', 'tubulin', 'actin'; pngs: display PNG paths (needed for SAM)."""
    settings = settings or get_settings()
    nuclei_method = nuclei_method or settings.nuclei_segmenter
    cells_method = cells_method or settings.cell_segmenter
    shape = channels["dna"].shape
    cost = 0.0

    if nuclei_method == "classical":
        nuclei = segment_nuclei_classical(channels["dna"])
    else:
        image, prompt = SAM_PROMPTS["nuclei"]
        nuclei, c = segment_sam(field_id, pngs[image], prompt, image, shape, settings)
        nuclei = size_filter(nuclei, NUC_MIN_AREA, NUC_MAX_AREA)
        cost += c

    if cells_method == "classical":
        cells = segment_cells_classical(nuclei, channels["tubulin"], channels["actin"])
    else:
        image, prompt = SAM_PROMPTS["cells"]
        free, c = segment_sam(field_id, pngs[image], prompt, image, shape, settings)
        cells = pair_cells_to_nuclei(nuclei, free)
        cost += c
    return Segmentation(nuclei, cells, nuclei_method, cells_method, cost)


def load_channels(data_path: Path, row) -> dict[str, np.ndarray]:
    return {ch: np.load(data_path / row[f"npy_{ch}"]) for ch in ("dna", "tubulin", "actin")}


def png_paths(data_path: Path, row) -> dict[str, Path]:
    return {name: data_path / row[f"png_{name}"] for name in ("dna", "tubulin", "actin", "composite")}
