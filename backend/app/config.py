"""Runtime settings, read from backend/.env (gitignored) and the environment.

Data location, model ids, provider, price table and cost caps. Empty values in
.env (e.g. `DATA_DIR=`) fall back to the defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent

Segmenter = Literal["classical", "sam"]
Provider = Literal["meta", "baseten", "fireworks", "together"]

# USD per image (spec §4).
SAM_PRICE_PER_IMAGE = 2.50 / 1000


class Price(BaseModel):
    input_per_m: float  # USD per 1M input tokens
    output_per_m: float  # USD per 1M output tokens (reasoning tokens are billed as output)


# USD per 1M tokens (spec §4, dev.meta.ai pricing). A model without a price is refused by LLMClient.
PRICES: dict[str, Price] = {
    "muse-spark-1.3": Price(input_per_m=1.25, output_per_m=4.25),
    "muse-spark-1.3-contributor": Price(input_per_m=0.10, output_per_m=0.20),
    "muse-wonder-1.0": Price(input_per_m=4.00, output_per_m=20.00),
}


class ProviderConfig(BaseModel):
    base_url: str
    key_env: str  # name of the Settings field holding the key


# Open-weights providers are the spec's stretch goal; only "meta" is exercised so far.
PROVIDERS: dict[str, ProviderConfig] = {
    "meta": ProviderConfig(base_url="https://api.meta.ai/v1", key_env="model_api_key"),
    "baseten": ProviderConfig(base_url="https://inference.baseten.co/v1", key_env="baseten_api_key"),
    "fireworks": ProviderConfig(base_url="https://api.fireworks.ai/inference/v1", key_env="fireworks_api_key"),
    "together": ProviderConfig(base_url="https://api.together.xyz/v1", key_env="together_api_key"),
}

MAX_BUDGET_WELLS = 8  # spec §5 hard guardrail
MAX_MODEL_TURNS = 20


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_ignore_empty=True, extra="ignore")

    provider: Provider = "meta"
    model_api_key: SecretStr | None = None
    baseten_api_key: SecretStr | None = None
    fireworks_api_key: SecretStr | None = None
    together_api_key: SecretStr | None = None
    model_base_url: str = "https://api.meta.ai/v1"

    scientist_model: str = "muse-spark-1.3"
    microscopist_model: str = "muse-spark-1.3"
    reviewer_model: str = "muse-wonder-1.0"
    segmenter_model: str = "sam-3.1"
    allow_contributor: bool = False

    model_timeout_s: float = 180.0
    model_max_retries: int = 4
    run_cost_cap_usd: float = 1.00
    daily_cost_cap_usd: float = 20.00

    ncbi_email: str | None = None
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

    @property
    def moa_csv(self) -> Path:
        return self.data_path / "metadata" / "BBBC021_v1_moa.csv"

    @property
    def cache_dir(self) -> Path:
        return self.data_path / "cache"

    @property
    def cells_dir(self) -> Path:
        return self.data_path / "processed" / "cells" / f"{self.nuclei_segmenter}-{self.cell_segmenter}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
