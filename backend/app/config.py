"""Runtime settings, read from backend/.env (gitignored) and the environment.

Only what Phase 2 needs lives here so far: data location, the SAM model id and
which segmenter produces nuclei and cell masks. Empty values in .env (e.g.
`DATA_DIR=`) fall back to the defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent

Segmenter = Literal["classical", "sam"]

# USD per image (spec §4).
SAM_PRICE_PER_IMAGE = 2.50 / 1000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_ignore_empty=True, extra="ignore")

    model_api_key: SecretStr | None = None
    model_base_url: str = "https://api.meta.ai/v1"
    segmenter_model: str = "sam-3.1"
    data_dir: str = "~/musecell-data"

    # Which segmenter produces each object type (Phase-2 bake-off, docs/DECISIONS.md).
    nuclei_segmenter: Segmenter = "classical"
    cell_segmenter: Segmenter = "classical"

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir).expanduser()
        return (path if path.is_absolute() else ROOT / path).resolve()

    @property
    def index_path(self) -> Path:
        return ROOT / "data" / "index.parquet"

    @property
    def atlas_dir(self) -> Path:
        return self.data_path / "processed" / "atlas"


@lru_cache
def get_settings() -> Settings:
    return Settings()
