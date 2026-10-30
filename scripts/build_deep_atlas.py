"""Deep-feature reference atlas: ResNet50 (ImageNet) field embeddings + TVN, for similarity search only.

Input: DATA_DIR/processed/embeddings/resnet50/<field_id>.npy (scripts/embed_fields.py).
Steps (no MoA label is used; chosen on the dev split, checked once on test — docs/DECISIONS.md, "Deep atlas"):
  1. well vector = mean of its fields' 6144-d embeddings;
  2. per plate, subtract the mean of that plate's DMSO wells;
  3. Typical Variation Normalization (Ando et al. 2017): SVD of all centred DMSO wells, keep the components that
     explain 99 % of DMSO variance, scale each by its DMSO standard deviation (whitening);
  4. treatment profile = median over replicate wells per (compound, concentration).
Output: DATA_DIR/processed/atlas_resnet50_tvn/{wells.parquet, treatments.parquet, meta.json}. Treatments keep the
metadata columns of the hand-crafted atlas (annotation, thumbnails), so `Atlas` uses them unchanged.

Usage: python scripts/build_deep_atlas.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings  # noqa: E402

VAR_KEEP = 0.99


def main() -> int:
    s = get_settings()
    emb = s.data_path / "processed" / "embeddings" / "resnet50"
    out = s.deep_atlas_dir
    idx = pd.read_parquet(s.index_path)
    idx["well_key"] = idx["plate"] + "/" + idx["well"]
    wells = idx.groupby("well_key").agg(plate=("plate", "first"), role=("role", "first"),
                                        fields=("field_id", list)).reset_index()
    missing = [f for fs in wells["fields"] for f in fs if not (emb / f"{f}.npy").exists()]
    if missing:
        print(f"{len(missing)} fields have no embedding; run scripts/embed_fields.py first.")
        return 1
    x = np.stack([np.mean([np.load(emb / f"{f}.npy").astype(np.float32) for f in fs], axis=0)
                  for fs in wells["fields"]])
    dmso = (wells["role"] == "control").to_numpy()
    for _, rows in wells.groupby("plate").groups.items():
        rows = np.asarray(list(rows))
        x[rows] -= x[rows[dmso[rows]]].mean(axis=0)
    d = x[dmso]
    _, sv, vt = np.linalg.svd(d - d.mean(axis=0), full_matrices=False)
    var = sv ** 2
    keep = int(np.searchsorted(np.cumsum(var) / var.sum(), VAR_KEEP) + 1)
    z = (x @ vt[:keep].T) / (sv[:keep] / np.sqrt(len(d) - 1) + 1e-6)
    cols = [f"d{i:03d}" for i in range(keep)]

    out.mkdir(parents=True, exist_ok=True)
    well_df = pd.DataFrame(z.astype(np.float32), columns=cols)
    well_df.insert(0, "well_key", wells["well_key"].to_numpy())
    well_df.to_parquet(out / "wells.parquet", index=False)

    # treatment profiles, keyed like the hand-crafted atlas (which carries annotation and thumbnails)
    norm = pd.read_parquet(s.atlas_dir / "wells_norm.parquet")[["well_key", "compound", "concentration_uM", "role"]]
    w = well_df.merge(norm, on="well_key")
    w = w[w["role"] == "treated"]
    prof = w.groupby(["compound", "concentration_uM"])[cols].median().reset_index()
    meta = pd.read_parquet(s.atlas_dir / "treatments.parquet")
    meta = meta[[c for c in meta.columns if c in ("compound", "concentration_uM", "moa", "moa_annotated_conc",
                                                   "n_wells", "thumbnail_field_id")]]
    treatments = meta.merge(prof, on=["compound", "concentration_uM"], how="inner")
    treatments.to_parquet(out / "treatments.parquet", index=False)
    info = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": "torchvision resnet50 "
            "IMAGENET1K_V2, global average pool, per channel (dna, tubulin, actin), 512 px", "tvn_components": keep,
            "tvn_variance_kept": VAR_KEEP, "dmso_wells": int(dmso.sum()), "wells": len(well_df),
            "treatments": len(treatments), "annotated": int(treatments["moa_annotated_conc"].sum())}
    (out / "meta.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(json.dumps(info, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
