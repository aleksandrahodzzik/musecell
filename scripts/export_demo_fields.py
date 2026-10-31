"""Ship cell masks and measurements for the demo fields, so mask outlines and cell hover work without the data.

For every field in demo/thumbnails/ (the fields the recorded demo runs show) this writes, from the local
segmentation cache in DATA_DIR:
  demo/fields/<field_id>.cells.json   the /api/fields/<id>/cells answer (centroids, measurements, flags)
  demo/fields/<field_id>.labels.gz    the /api/fields/<id>/labels body: gzip of [cells | nuclei] uint16 LE, half size
A replay-only server and the static demo answer those two endpoints from these files. Small (~tens of KB a field).

Usage: python scripts/export_demo_fields.py   (needs DATA_DIR with the atlas cache; run after record_demo_runs.py)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import fields  # noqa: E402
from app.runs.manager import RunManager  # noqa: E402

THUMBS = ROOT / "demo" / "thumbnails"
OUT = ROOT / "demo" / "fields"


async def main() -> int:
    manager = RunManager()
    await manager.startup(check_models=False)
    try:
        if manager.catalog is None:
            print(manager.data_error)
            return 1
        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True)
        ids = sorted(p.name.removesuffix(".composite.png") for p in THUMBS.glob("f_*.composite.png"))
        done = 0
        for fid in ids:
            table = fields.cell_table(manager.settings.cells_dir, fid, manager.reference)
            maps = fields.label_maps(manager.settings.cells_dir, fid)
            if table is None or maps is None:
                print(f"  {fid}: no segmentation cache, skipped")
                continue
            body, w, h = maps
            table.update(label_width=w, label_height=h)
            (OUT / f"{fid}.cells.json").write_text(json.dumps(table, separators=(",", ":")), encoding="utf-8")
            (OUT / f"{fid}.labels.gz").write_bytes(body)
            done += 1
        size = sum(f.stat().st_size for f in OUT.iterdir()) / 1e6
        print(f"demo/fields: {done} of {len(ids)} fields, {size:.1f} MB")
        return 0
    finally:
        await manager.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
