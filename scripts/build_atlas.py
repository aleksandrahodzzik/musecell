"""Segment every field, measure cells, and build well + treatment profiles (spec §3).

Pass 1 (multiprocessing, cached): per field, segmentation + per-cell features ->
  DATA_DIR/processed/cells/<nuclei>-<cells>/<field_id>.parquet  (cell table)
  DATA_DIR/processed/cells/<nuclei>-<cells>/<field_id>.npz      (nuclei + cell masks)
Pass 2: DMSO reference -> field features -> well profiles -> robust z vs
same-plate DMSO -> treatment profiles. Written to DATA_DIR/processed/atlas/:
  reference.json, field_features.parquet, wells.parquet (raw), wells_norm.parquet,
  treatments.parquet (all compound-concentrations; the atlas uses the MoA-annotated ones).

All fields of the index are processed (a superset of the annotated
compound-concentrations: the agent also acquires unannotated doses).
Segmenters come from Settings (NUCLEI_SEGMENTER / CELL_SEGMENTER) unless overridden.

Usage: python scripts/build_atlas.py [--workers 10] [--annotated-only] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings  # noqa: E402
from app.science import features as F  # noqa: E402
from app.science.segmentation import load_channels, png_paths, segment_field  # noqa: E402


def cells_dir(data_path: Path, nuclei_method: str, cells_method: str) -> Path:
    return data_path / "processed" / "cells" / f"{nuclei_method}-{cells_method}"


def process_field(job: dict) -> dict:
    out_dir = Path(job["out_dir"])
    table = out_dir / f"{job['field_id']}.parquet"
    if table.exists():
        return {"field_id": job["field_id"], "seconds": None}
    t0 = time.perf_counter()
    data_path = Path(job["data_path"])
    channels = load_channels(data_path, job["row"])
    seg = segment_field(job["field_id"], channels, png_paths(data_path, job["row"]),
                        nuclei_method=job["nuclei"], cells_method=job["cells"])
    cells = F.cell_features(seg.nuclei, seg.cells, channels)
    np.savez_compressed(out_dir / f"{job['field_id']}.npz", nuclei=seg.nuclei.astype(np.uint16),
                        cells=seg.cells.astype(np.uint16))
    cells.to_parquet(table, index=False)
    return {"field_id": job["field_id"], "seconds": time.perf_counter() - t0, "cost_usd": seg.cost_usd}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--annotated-only", action="store_true", help="only MoA-annotated doses + DMSO")
    parser.add_argument("--limit", type=int, default=0, help="first N fields only (smoke test)")
    args = parser.parse_args()

    settings = get_settings()
    index = pd.read_parquet(settings.index_path)
    if args.annotated_only:
        index = index[index["moa_annotated_conc"] | (index["role"] == "control")]
    if args.limit:
        index = index.head(args.limit)
    nuc_m, cell_m = settings.nuclei_segmenter, settings.cell_segmenter
    out_dir = cells_dir(settings.data_path, nuc_m, cell_m)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"segmenters: nuclei={nuc_m}, cells={cell_m}; {len(index)} fields; {args.workers} workers")

    jobs = [{"field_id": r["field_id"], "row": r.to_dict(), "data_path": str(settings.data_path),
             "out_dir": str(out_dir), "nuclei": nuc_m, "cells": cell_m} for _, r in index.iterrows()]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(tqdm(pool.map(process_field, jobs, chunksize=2), total=len(jobs), desc="fields", unit="field"))
    wall = time.perf_counter() - t0
    secs = [r["seconds"] for r in results if r["seconds"] is not None]
    if secs:
        print(f"segmented + measured {len(secs)} fields in {wall:.0f} s wall; per field (one worker): "
              f"median {np.median(secs):.2f} s, p90 {np.percentile(secs, 90):.2f} s; "
              f"SAM cost ${sum(r.get('cost_usd', 0) for r in results):.4f}")
    print(f"cached already: {len(results) - len(secs)} fields")

    # Pass 2: profiles.
    cells = {fid: pd.read_parquet(out_dir / f"{fid}.parquet") for fid in tqdm(index["field_id"], desc="load", unit="field")}
    dmso_ids = index.loc[index["role"] == "control", "field_id"]
    ref = F.reference_from_dmso(pd.concat([cells[f] for f in dmso_ids], ignore_index=True))

    meta_cols = ["plate", "well", "compound", "concentration_uM", "moa", "moa_annotated_conc", "replicate", "role"]
    field_rows = [{"field_id": fid, **F.field_features(cells[fid], ref)} for fid in index["field_id"]]
    field_df = index[["field_id", "field"] + meta_cols].merge(pd.DataFrame(field_rows), on="field_id")

    wells = []
    for (plate, well), grp in index.groupby(["plate", "well"], sort=True):
        grp = grp.sort_values("field")
        profile = F.well_profile([cells[f] for f in grp["field_id"]], ref)
        first = grp.iloc[0]
        wells.append({**{c: first[c] for c in meta_cols}, "well_key": f"{plate}/{well}",
                      "field_ids": list(grp["field_id"]), "thumbnail_field_id": first["field_id"],
                      **profile.to_dict()})
    wells = pd.DataFrame(wells)
    wells_norm = F.normalize_to_dmso(wells)
    treatments = F.treatment_profiles(wells_norm)

    atlas = settings.atlas_dir
    atlas.mkdir(parents=True, exist_ok=True)
    (atlas / "reference.json").write_text(json.dumps({**ref.to_dict(), "nuclei_segmenter": nuc_m,
                                                      "cell_segmenter": cell_m}, indent=1))
    field_df.to_parquet(atlas / "field_features.parquet", index=False)
    wells.to_parquet(atlas / "wells.parquet", index=False)
    wells_norm.to_parquet(atlas / "wells_norm.parquet", index=False)
    treatments.to_parquet(atlas / "treatments.parquet", index=False)
    annotated = treatments[treatments["moa_annotated_conc"]]
    print(f"reference: DMSO nucleus area {ref.nuc_area:.0f} px, DNA mean {ref.nuc_dna_mean:.3f}")
    print(f"wells: {len(wells)} ({(wells['role'] == 'control').sum()} DMSO); treatments: {len(treatments)} "
          f"({len(annotated)} MoA-annotated, {annotated['compound'].nunique()} compounds); written to {atlas}")


if __name__ == "__main__":
    main()
