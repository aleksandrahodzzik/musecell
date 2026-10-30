"""Deep features for every field: ImageNet-pretrained ResNet50, one forward pass per stain channel.

Each channel PNG (already contrast-normalised per plate by preprocess.py) is downscaled to 512 px on the long
side, repeated to 3 channels, ImageNet-normalised, and the 2048-d global-average-pooled activation is kept.
A field gets 3 x 2048 = 6144 numbers (DNA, tubulin, actin). No MoA label is used anywhere: the network was
trained on ImageNet only, so these features cannot be fitted to the evaluation compounds.

Output: DATA_DIR/processed/embeddings/resnet50/<field_id>.npy (float16, 6144), resumable.
Needs the optional ML dependencies: pip install -r backend/requirements-ml.txt (CPU torch).

Usage: python scripts/embed_fields.py [--limit N] [--threads 12]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings  # noqa: E402

CHANNELS = ("dna", "tubulin", "actin")
LONG_SIDE = 512
BATCH = 12


def main() -> int:
    import torch
    from torchvision.models import ResNet50_Weights, resnet50

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int)
    p.add_argument("--threads", type=int, default=12)
    args = p.parse_args()
    torch.set_num_threads(args.threads)

    settings = get_settings()
    index = pd.read_parquet(settings.index_path)
    out_dir = settings.data_path / "processed" / "embeddings" / "resnet50"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for r in index.itertuples() if not (out_dir / f"{r.field_id}.npy").exists()]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(index)} fields, {len(todo)} to embed -> {out_dir}")

    model = resnet50(weights=ResNet50_Weights.IMAGENET1K_V2)
    model.fc = torch.nn.Identity()
    model.eval()
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

    def load(row, ch: str) -> torch.Tensor:
        with Image.open(settings.data_path / getattr(row, f"png_{ch}")) as im:
            im = im.convert("L")
            scale = LONG_SIDE / max(im.size)
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.BILINEAR)
            x = torch.from_numpy(np.asarray(im, dtype=np.float32) / 255.0)
        return x.unsqueeze(0).repeat(3, 1, 1)

    t0 = time.perf_counter()
    with torch.inference_mode():
        for start in range(0, len(todo), BATCH):
            rows = todo[start:start + BATCH]
            feats = {}
            for ch in CHANNELS:
                x = torch.stack([load(r, ch) for r in rows])
                feats[ch] = model((x - mean) / std).numpy()
            for i, r in enumerate(rows):
                vec = np.concatenate([feats[ch][i] for ch in CHANNELS]).astype(np.float16)
                np.save(out_dir / f"{r.field_id}.npy", vec)
            done = start + len(rows)
            if done % (BATCH * 10) == 0 or done == len(todo):
                rate = done / (time.perf_counter() - t0)
                print(f"  {done}/{len(todo)} fields, {rate:.2f}/s, ~{(len(todo) - done) / rate / 60:.0f} min left",
                      flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
