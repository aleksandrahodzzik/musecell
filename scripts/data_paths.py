"""Shared DATA_DIR resolution for the data scripts.

DATA_DIR comes from the environment / backend/.env. `~` is expanded; relative
paths resolve against the repo root. The data must live outside the repo and
outside OneDrive (spec §2), so we refuse such locations instead of silently
filling a synced folder with gigabytes of TIFFs.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

DEFAULT_DATA_DIR = "~/musecell-data"
INDEX_PATH = ROOT / "data" / "index.parquet"


def data_dir() -> Path:
    raw = os.getenv("DATA_DIR") or DEFAULT_DATA_DIR
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if "onedrive" in str(path).lower():
        raise SystemExit(f"DATA_DIR resolves inside OneDrive ({path}). Set DATA_DIR=~/musecell-data in backend/.env.")
    if path == ROOT or ROOT in path.parents:
        raise SystemExit(f"DATA_DIR resolves inside the repo ({path}). Point it outside the repo.")
    path.mkdir(parents=True, exist_ok=True)
    return path
