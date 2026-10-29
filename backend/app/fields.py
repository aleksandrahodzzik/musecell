"""Per-field segmentation for the microscope view: cell measurements (hover) and label maps (masks, hit-testing).

Read from the atlas cache (`DATA_DIR/processed/cells/<nuclei>-<cells>/<field_id>.{parquet,npz}`), so every number
the UI shows on hover is the one `science.features.cell_features` computed. Field ids are opaque; nothing here
names a compound.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.science.features import Reference, phenotype_flags

LABEL_STEP = 2  # label maps are sent at half resolution (640×512 for a 1280×1024 field)
CELL_COLUMNS = ["nuc_area", "nuc_dna_mean", "nuc_dna_integrated", "nuc_solidity", "cell_area", "cell_roundness",
                "tub_mean", "act_mean", "act_tub_ratio"]


def load_reference(atlas_dir: Path) -> Reference | None:
    path = atlas_dir / "reference.json"
    if not path.exists():
        return None
    ref = json.loads(path.read_text(encoding="utf-8"))
    return Reference(nuc_area=ref["nuc_area"], nuc_dna_mean=ref["nuc_dna_mean"])


def cell_table(cells_dir: Path, field_id: str, ref: Reference | None) -> dict | None:
    """Cells of one field: label, centroid (fraction of width/height), measurements, phenotype flags."""
    table, masks = cells_dir / f"{field_id}.parquet", cells_dir / f"{field_id}.npz"
    if not table.exists() or not masks.exists():
        return None
    cells = pd.read_parquet(table)
    with np.load(masks) as z:
        h, w = z["cells"].shape
    flags = phenotype_flags(cells, ref) if ref is not None else pd.DataFrame(index=cells.index)
    out = []
    for i, row in cells.iterrows():
        item = {"label": int(row["label"]), "x": round(float(row["centroid_x"]) / w, 5),
                "y": round(float(row["centroid_y"]) / h, 5), "touches_border": bool(row["touches_border"])}
        item.update({c: round(float(row[c]), 4) for c in CELL_COLUMNS})
        item["flags"] = [name for name in flags.columns if bool(flags.at[i, name])]
        out.append(item)
    return {"field_id": field_id, "width": w, "height": h, "label_step": LABEL_STEP, "cells": out}


def label_maps(cells_dir: Path, field_id: str) -> tuple[bytes, int, int] | None:
    """gzip of [cells | nuclei] as little-endian uint16, each (h/2)×(w/2), row-major. Returns (body, w, h)."""
    path = cells_dir / f"{field_id}.npz"
    if not path.exists():
        return None
    with np.load(path) as z:
        cells = z["cells"][::LABEL_STEP, ::LABEL_STEP].astype("<u2")
        nuclei = z["nuclei"][::LABEL_STEP, ::LABEL_STEP].astype("<u2")
    h, w = cells.shape
    return gzip.compress(cells.tobytes() + nuclei.tobytes(), compresslevel=5), w, h
