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
    billing: Literal["usd", "credits"] = "usd"  # "credits": paid from provider credits; still logged and capped


# Open weights (spec §4 stretch). The hackathon Baseten key only works with this exact id (DECISIONS, Stretch).
OPEN_WEIGHTS_MODEL = "meta-models/muse-spark-open-1026"


# USD per 1M tokens (spec §4, dev.meta.ai pricing). A model without a price is refused by LLMClient.
PRICES: dict[str, Price] = {
    "muse-spark-1.3": Price(input_per_m=1.25, output_per_m=4.25),
    "muse-spark-1.3-contributor": Price(input_per_m=0.10, output_per_m=0.20),
    "muse-wonder-1.0": Price(input_per_m=4.00, output_per_m=20.00),
    # baseten.co/pricing, 2026-10-30 (Muse Spark Open). Paid from hackathon credits.
    OPEN_WEIGHTS_MODEL: Price(input_per_m=1.25, output_per_m=4.25, billing="credits"),
}


class ProviderConfig(BaseModel):
    base_url: str
    key_env: str  # name of the Settings field holding the key
    label: str
    open_model: str | None = None  # the open-weights id this provider serves; None = not verified, refused
    max_images: int = 50  # images per request


# PROVIDER=meta runs every role on the Meta Model API. Any other provider moves only the Scientist and the
# Microscopist to that provider's open-weights model; the PI reviewer (muse-wonder-1.0) and SAM (sam-3.1) stay
# on the Meta API. Fireworks/Together are not probed (no credits), so they are refused until verified.
PROVIDERS: dict[str, ProviderConfig] = {
    "meta": ProviderConfig(base_url="https://api.meta.ai/v1", key_env="model_api_key", label="Meta Model API"),
    "baseten": ProviderConfig(base_url="https://inference.baseten.co/v1", key_env="baseten_api_key",
                              label="Baseten", open_model=OPEN_WEIGHTS_MODEL,
                              max_images=8),  # scripts/probe_baseten.py: 9 images -> HTTP 400
    "fireworks": ProviderConfig(base_url="https://api.fireworks.ai/inference/v1", key_env="fireworks_api_key",
                                label="Fireworks"),
    "together": ProviderConfig(base_url="https://api.together.xyz/v1", key_env="together_api_key",
                               label="Together"),
}
OPEN_ROLES = ("scientist", "microscopist")  # roles a non-Meta provider takes over


def provider_of(model: str) -> str:
    """Which provider serves a model id. Routing is by id, so a run can mix providers."""
    for name, cfg in PROVIDERS.items():
        if cfg.open_model == model:
            return name
    return "meta"

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
    def role_models(self) -> dict[str, str]:
        """Effective model id per role. A non-Meta provider replaces SCIENTIST_MODEL/MICROSCOPIST_MODEL with its
        open-weights id; the reviewer and the segmenter always stay on the Meta API."""
        roles = {"scientist": self.scientist_model, "microscopist": self.microscopist_model,
                 "reviewer": self.reviewer_model, "segmenter": self.segmenter_model}
        if self.provider != "meta":
            open_model = PROVIDERS[self.provider].open_model
            if open_model is None:
                raise ValueError(f"PROVIDER={self.provider} is not verified (no probed model id). Use meta or baseten.")
            roles.update({r: open_model for r in OPEN_ROLES})
        return roles

    @property
    def scientist_id(self) -> str:
        return self.role_models["scientist"]

    @property
    def microscopist_id(self) -> str:
        return self.role_models["microscopist"]

    def for_provider(self, provider: str) -> "Settings":
        """A copy for one run on another provider (the start screen's switch)."""
        return self.model_copy(update={"provider": provider})

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
