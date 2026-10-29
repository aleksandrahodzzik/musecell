"""Phase-2 segmentation bake-off: classical vs SAM 3.1 on the 6 probe fields.

Probe fields = the docs/probe/example_*.jpg fields (DMSO + 5 MoAs). For each:
classical nuclei + cells, and SAM with several noun phrases on the DNA PNG and
the composite (Phase 0 showed wording matters). Prints object counts per method,
nucleus agreement with the classical path (F1 at IoU >= 0.5), runtime, and $.
Writes overlays docs/probe/seg_<field>.jpg and docs/probe/segmentation_bakeoff.json.
SAM results are cached on disk, so re-runs are free.

Usage: python scripts/segmentation_bakeoff.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw
from skimage import segmentation as skseg

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from app import sam  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.science.segmentation import (  # noqa: E402
    NUC_MAX_AREA, NUC_MIN_AREA, load_channels, png_paths, segment_cells_classical, segment_nuclei_classical,
    size_filter, _upscale_labels,
)
from preprocess import example_label, example_rows  # noqa: E402

OUT = ROOT / "docs" / "probe"
SAM_TRIALS = [("dna", "nucleus"), ("dna", "nuclei"), ("dna", "blob"), ("composite", "cell"), ("composite", "nucleus")]
PANEL_W = 768
COLORS = {"nuclei": (255, 255, 255), "cells": (255, 214, 0)}


def match_f1(ref: np.ndarray, test: np.ndarray, iou_min: float = 0.5) -> float:
    """F1 of one-to-one object matches at IoU >= iou_min (IoU > 0.5 matches are unique)."""
    n_ref, n_test = int(ref.max()), int(test.max())
    if n_ref == 0 or n_test == 0:
        return 0.0
    both = (ref > 0) & (test > 0)
    pairs, inter = np.unique(np.stack([ref[both], test[both]]), axis=1, return_counts=True)
    area_r = np.bincount(ref.ravel(), minlength=n_ref + 1)
    area_t = np.bincount(test.ravel(), minlength=n_test + 1)
    iou = inter / (area_r[pairs[0]] + area_t[pairs[1]] - inter)
    tp = int((iou >= iou_min).sum())
    return 2 * tp / (n_ref + n_test)


def panel(composite: Image.Image, title: str, nuclei: np.ndarray | None, cells: np.ndarray | None) -> Image.Image:
    base = np.asarray(composite.convert("RGB")).copy()
    shape = base.shape[:2]
    for key, labels in (("cells", cells), ("nuclei", nuclei)):
        if labels is None:
            continue
        small = np.asarray(Image.fromarray(labels.astype(np.int32)).resize((shape[1], shape[0]), Image.NEAREST))
        base[skseg.find_boundaries(small, mode="inner")] = COLORS[key]
    im = Image.fromarray(base)
    im = im.resize((PANEL_W, round(im.height * PANEL_W / im.width)), Image.LANCZOS)
    ImageDraw.Draw(im).text((8, 6), title, fill=(255, 255, 255))
    return im


def main() -> None:
    settings = get_settings()
    index = pd.read_parquet(settings.index_path)
    rows, report, cost = [], [], 0.0
    for row in example_rows(index):
        label = example_label(row)
        channels = load_channels(settings.data_path, row)
        pngs = png_paths(settings.data_path, row)
        shape = channels["dna"].shape

        t0 = time.perf_counter()
        nuc_c = segment_nuclei_classical(channels["dna"])
        t_nuc = time.perf_counter() - t0
        cell_c = segment_cells_classical(nuc_c, channels["tubulin"], channels["actin"])
        t_cls = time.perf_counter() - t0
        entry = {"field": label, "field_id": row["field_id"], "classical": {
            "nuclei": int(nuc_c.max()), "cells": int(len(np.unique(cell_c)) - 1),
            "seconds_nuclei": round(t_nuc, 2), "seconds_total": round(t_cls, 2)}, "sam": {}}

        sam_labels = {}
        for image, prompt in SAM_TRIALS:
            t0 = time.perf_counter()
            res = sam.segment(pngs[image], prompt, row["field_id"], image, settings)
            cost += res.cost_usd
            full = _upscale_labels(res.labels, shape)
            filtered = size_filter(full, NUC_MIN_AREA, NUC_MAX_AREA) if prompt != "cell" else full
            sam_labels[(image, prompt)] = filtered
            conf = [c for c in res.confidences if c is not None]
            entry["sam"][f"{image}:{prompt}"] = {
                "objects": int(full.max()), "objects_size_filtered": int(filtered.max()),
                "nucleus_f1_vs_classical": round(match_f1(nuc_c, filtered), 3) if prompt != "cell" else None,
                "median_confidence": round(float(np.median(conf)), 3) if conf else None,
                "seconds": round(time.perf_counter() - t0, 2), "cached": res.cached,
            }
        report.append(entry)

        composite = Image.open(pngs["composite"])
        panels = [panel(composite, "classical: nuclei (white) + cells (yellow)", nuc_c, cell_c),
                  panel(composite, "SAM 3.1 'nucleus' on DNA", sam_labels[("dna", "nucleus")], None),
                  panel(composite, "SAM 3.1 'cell' on composite", None, sam_labels[("composite", "cell")])]
        sheet = Image.new("RGB", (PANEL_W * len(panels), panels[0].height))
        for i, p in enumerate(panels):
            sheet.paste(p, (i * PANEL_W, 0))
        sheet.save(OUT / f"seg_{label}.jpg", quality=85)

        rows.append({"field": label[:34], "cls nuc": entry["classical"]["nuclei"],
                     "cls cell": entry["classical"]["cells"], "cls s": entry["classical"]["seconds_total"],
                     **{f"{k}": v["objects"] for k, v in entry["sam"].items()},
                     "F1 nucleus": entry["sam"]["dna:nucleus"]["nucleus_f1_vs_classical"]})

    pd.set_option("display.width", 220)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\nSAM cost this run: ${cost:.4f} (cached calls are free)")
    (OUT / "segmentation_bakeoff.json").write_text(json.dumps({"fields": report, "sam_cost_usd": cost}, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}/segmentation_bakeoff.json and seg_*.jpg")


if __name__ == "__main__":
    main()
