"""Preprocess fetched BBBC021 fields (spec §2).

For every planned field whose 3 TIFFs are on disk:
  - per-plate, per-channel robust contrast: 1st / 99.8th percentile of the
    plate's DMSO fields (stored in DATA_DIR/processed/contrast.json);
  - float32 .npy per channel, (raw - lo) / (hi - lo), not clipped (for measurement);
  - 8-bit PNGs, max side 1024: composite (DNA->blue, tubulin->green, actin->red)
    and one grayscale PNG per channel (clipped to [0, 1]).
Writes data/index.parquet (repo, gitignored) with opaque field ids
`f_<12 hex>` = salted SHA-256 of plate/well/site. The salt lives in
DATA_DIR/field_id_salt (never committed) so ids can't be reversed from the
public metadata. Idempotent: fields whose outputs exist are skipped.

Usage: python scripts/preprocess.py [--workers 6] [--examples]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile
from PIL import Image
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_paths import INDEX_PATH, ROOT, data_dir  # noqa: E402

CHANNELS = {"dna": "file_dapi", "tubulin": "file_tubulin", "actin": "file_actin"}
COMPOSITE_RGB = ("actin", "tubulin", "dna")  # R, G, B
PCT_LO, PCT_HI = 1.0, 99.8
MAX_SIDE = 1024
PIXEL_SAMPLE = 200_000  # per DMSO field per channel, for the percentile estimate
EXAMPLE_DIR = ROOT / "docs" / "probe"


def field_salt(root: Path) -> str:
    path = root / "field_id_salt"
    if not path.exists():
        path.write_text(secrets.token_hex(16))
    return path.read_text().strip()


def field_id(salt: str, plate: str, well: str, site: int) -> str:
    return "f_" + hashlib.sha256(f"{salt}/{plate}/{well}/{site}".encode()).hexdigest()[:12]


def raw_file(root: Path, plate: str, name: str) -> Path:
    return root / "raw" / plate / name


def load_planned(root: Path) -> pd.DataFrame:
    fields = pd.read_csv(root / "plan_fields.csv")
    present = [all(raw_file(root, r.plate, getattr(r, col)).exists() for col in CHANNELS.values())
               for r in fields.itertuples()]
    fields["present"] = present
    return fields


def plate_contrast(root: Path, plate_fields: pd.DataFrame) -> dict[str, list[float]]:
    rng = np.random.default_rng(0)
    control = plate_fields[plate_fields["role"] == "control"]
    if control.empty:
        raise SystemExit(f"plate {plate_fields['plate'].iloc[0]} has no DMSO fields")
    out = {}
    for ch, col in CHANNELS.items():
        samples = []
        for name in control[col]:
            img = tifffile.imread(raw_file(root, control["plate"].iloc[0], name)).ravel()
            samples.append(rng.choice(img, size=min(PIXEL_SAMPLE, img.size), replace=False))
        lo, hi = np.percentile(np.concatenate(samples), [PCT_LO, PCT_HI])
        out[ch] = [float(lo), float(hi)]
    return out


def to_png(img01: np.ndarray) -> Image.Image:
    im = Image.fromarray((np.clip(img01, 0, 1) * 255).round().astype(np.uint8))  # L or RGB by shape
    scale = MAX_SIDE / max(im.size)
    if scale < 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return im


def output_paths(fid: str) -> dict[str, str]:
    """Paths relative to DATA_DIR."""
    base = f"processed/fields/{fid}"
    paths = {f"npy_{ch}": f"{base}/{ch}.npy" for ch in CHANNELS}
    paths |= {f"png_{ch}": f"{base}/{ch}.png" for ch in CHANNELS}
    paths["png_composite"] = f"{base}/composite.png"
    return paths


def process_field(job: dict) -> dict:
    root = Path(job["root"])
    paths = output_paths(job["field_id"])
    if all((root / p).exists() for p in paths.values()):
        return {"field_id": job["field_id"], "skipped": True}
    (root / paths["png_composite"]).parent.mkdir(parents=True, exist_ok=True)
    norm = {}
    for ch in CHANNELS:
        raw = tifffile.imread(raw_file(root, job["plate"], job["files"][ch]))
        lo, hi = job["contrast"][ch]
        norm[ch] = ((raw.astype(np.float32) - lo) / (hi - lo)).astype(np.float32)
        np.save(root / paths[f"npy_{ch}"], norm[ch])
        to_png(norm[ch]).save(root / paths[f"png_{ch}"], optimize=True)
    rgb = np.stack([norm[ch] for ch in COMPOSITE_RGB], axis=-1)
    to_png(rgb).save(root / paths["png_composite"], optimize=True)
    return {"field_id": job["field_id"], "skipped": False, "shape": list(norm["dna"].shape)}


def build_jobs(root: Path, fields: pd.DataFrame, contrast: dict, salt: str) -> list[dict]:
    return [
        {
            "root": str(root),
            "field_id": field_id(salt, r.plate, r.well, r.site),
            "plate": r.plate,
            "files": {ch: getattr(r, col) for ch, col in CHANNELS.items()},
            "contrast": contrast[r.plate],
        }
        for r in fields.itertuples()
    ]


def build_index(fields: pd.DataFrame, salt: str) -> pd.DataFrame:
    idx = pd.DataFrame({
        "field_id": [field_id(salt, p, w, s) for p, w, s in zip(fields["plate"], fields["well"], fields["site"])],
        "plate": fields["plate"],
        "well": fields["well"],
        "field": fields["site"].astype("int16"),
        "compound": fields["compound"],
        "concentration_uM": fields["concentration_uM"].astype("float64"),
        "moa": fields["moa"].astype("string"),
        "moa_annotated_conc": fields["moa_annotated_conc"].astype(bool),
        "replicate": fields["replicate"].astype("int16"),
        "role": fields["role"],
    })
    paths = pd.DataFrame([output_paths(fid) for fid in idx["field_id"]])
    for ch, col in CHANNELS.items():
        idx[f"raw_{ch}"] = [f"raw/{p}/{n}" for p, n in zip(fields["plate"], fields[col])]
    return pd.concat([idx.reset_index(drop=True), paths], axis=1)


def example_rows(index: pd.DataFrame, n_moa: int = 5) -> list[pd.Series]:
    """DMSO + n_moa different MoAs; highest MoA-labelled dose, field 1 of replicate 1."""
    picks = [index[(index["role"] == "control") & (index["field"] == 1)].iloc[0]]
    labelled = index[index["moa_annotated_conc"] & (index["field"] == 1)]
    preferred = ["Microtubule stabilizers", "Microtubule destabilizers", "Actin disruptors", "Eg5 inhibitors",
                 "DNA damage", "Aurora kinase inhibitors", "Protein synthesis"]
    for moa in [m for m in preferred if m in set(labelled["moa"])][:n_moa]:
        rows = labelled[labelled["moa"] == moa].sort_values(["concentration_uM", "replicate"], ascending=[False, True])
        picks.append(rows.iloc[0])
    return picks


def example_label(row: pd.Series) -> str:
    label = "DMSO" if row["role"] == "control" else f"{row['moa']}_{row['compound']}_{row['concentration_uM']:g}uM"
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in label)


def write_examples(root: Path, index: pd.DataFrame, n_moa: int = 5) -> list[Path]:
    EXAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for row in example_rows(index, n_moa):
        dest = EXAMPLE_DIR / f"example_{example_label(row)}.jpg"
        Image.open(root / row["png_composite"]).convert("RGB").save(dest, quality=90)
        out.append(dest)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--examples", action="store_true", help="write 6 example composites to docs/probe/")
    args = parser.parse_args()

    root = data_dir()
    fields = load_planned(root)
    complete = fields.groupby("plate")["present"].all()
    ready = complete[complete].index
    if len(ready) < len(complete):
        print(f"skipping {len(complete) - len(ready)} plates with missing TIFFs (re-run after fetch)")
    fields = fields[fields["plate"].isin(ready)].reset_index(drop=True)

    contrast_path = root / "processed" / "contrast.json"
    contrast_path.parent.mkdir(parents=True, exist_ok=True)
    contrast = json.loads(contrast_path.read_text()) if contrast_path.exists() else {}
    for plate in tqdm([p for p in ready if p not in contrast], desc="DMSO contrast", unit="plate"):
        contrast[plate] = plate_contrast(root, fields[fields["plate"] == plate])
        contrast_path.write_text(json.dumps(contrast, indent=1))

    salt = field_salt(root)
    jobs = build_jobs(root, fields, contrast, salt)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(tqdm(pool.map(process_field, jobs, chunksize=4), total=len(jobs), desc="fields", unit="field"))
    print(f"fields processed: {sum(not r['skipped'] for r in results)}, already done: {sum(r['skipped'] for r in results)}")

    index = build_index(fields, salt)
    if index["field_id"].duplicated().any():
        raise SystemExit("field_id collision — increase hash length")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    index.to_parquet(INDEX_PATH, index=False)
    print(f"wrote {INDEX_PATH} ({len(index)} fields, {index.groupby(['plate', 'well']).ngroups} wells, "
          f"{index.loc[index['role'] == 'treated', 'compound'].nunique()} compounds, {index['moa'].nunique()} MoA classes)")

    if args.examples:
        for path in write_examples(root, index):
            print(f"example: {path.relative_to(ROOT)} ({path.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
