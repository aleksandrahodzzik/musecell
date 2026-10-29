"""One wrapper for every chat-model call (spec §4).

- Provider config (base url, key, model ids) from Settings / backend/.env.
- `check_models()` calls GET /v1/models at startup and refuses configured ids that do not exist.
- Retries with exponential backoff on 429 / 5xx / connection errors / timeouts; other errors fail at once.
- Token usage -> USD cost from the price table in config; every call is recorded on the run's
  CostMeter and in a daily ledger on disk. A run over its cap, or a day over the daily cap, is refused
  *before* the next call is sent.
- Spark reasons by default and reasoning tokens count toward max_tokens (Phase 0): empty content with
  finish_reason "length" is an error, never a valid answer.

The key is read from Settings and never logged.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import openai
from pydantic import BaseModel, Field

from app.config import PRICES, PROVIDERS, Settings, get_settings

DEFAULT_MAX_TOKENS = 8000  # generous: reasoning tokens share this budget
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0


class LLMError(RuntimeError):
    """A model call failed; the message is safe to show in the UI."""


class CostCapExceeded(LLMError):
    pass


class ModelCheckError(LLMError):
    pass


class CallRecord(BaseModel):
    model: str
    purpose: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    attempts: int = 1
    finish_reason: str | None = None


class ChatResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    message: Any  # openai ChatCompletionMessage (keeps tool_calls for the loop)
    record: CallRecord

    @property
    def content(self) -> str:
        return self.message.content or ""


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        raise LLMError(f"No price for model '{model}' in app/config.py PRICES; refusing to call it unmetered.")
    return input_tokens * price.input_per_m / 1e6 + output_tokens * price.output_per_m / 1e6


class CostMeter(BaseModel):
    """Per-run cost accounting with a hard cap."""

    run_id: str
    cap_usd: float
    calls: list[CallRecord] = Field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    def add(self, record: CallRecord) -> None:
        self.calls.append(record)


class DailyLedger:
    """Append-only JSONL of every call's cost, one file per UTC day: DATA_DIR/cache/costs/<date>.jsonl."""

    def __init__(self, directory: Path, cap_usd: float):
        self.directory = directory
        self.cap_usd = cap_usd

    def _path(self, day: date | None = None) -> Path:
        return self.directory / f"{(day or datetime.now(timezone.utc).date()).isoformat()}.jsonl"

    def total_today(self) -> float:
        path = self._path()
        if not path.exists():
            return 0.0
        return sum(json.loads(line)["cost_usd"] for line in path.read_text().splitlines() if line.strip())

    def add(self, run_id: str, record: CallRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        line = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id, **record.model_dump()}
        with self._path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):  # timeout is a connection error subclass
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _explain(exc: Exception, model: str) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return "Meta API rejected the key. Check MODEL_API_KEY in backend/.env."
    if isinstance(exc, openai.NotFoundError):
        return f"Model '{model}' was not found by the API. Check the model ids in backend/.env."
    if isinstance(exc, openai.APIStatusError):
        return f"Model API returned HTTP {exc.status_code} for {model}: {str(exc)[:300]}"
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return f"Could not reach the model API for {model} ({type(exc).__name__}). Check the network and retry."
    return f"Model call to {model} failed: {type(exc).__name__}: {str(exc)[:300]}"


class LLMClient:
    def __init__(self, settings: Settings | None = None, ledger: DailyLedger | None = None,
                 http_client: httpx.AsyncClient | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.settings = settings or get_settings()
        provider = PROVIDERS[self.settings.provider]
        base_url = self.settings.model_base_url if self.settings.provider == "meta" else provider.base_url
        key = getattr(self.settings, provider.key_env)
        if key is None:
            raise LLMError(f"{provider.key_env.upper()} is empty. Put your key in backend/.env "
                           f"(provider '{self.settings.provider}').")
        # Retries are ours (backoff + logging), so the SDK's own are off.
        self._client = openai.AsyncOpenAI(base_url=base_url, api_key=key.get_secret_value(),
                                          timeout=self.settings.model_timeout_s, max_retries=0,
                                          http_client=http_client)
        self.ledger = ledger or DailyLedger(self.settings.cache_dir / "costs", self.settings.daily_cost_cap_usd)
        self._sleep = sleep

    @property
    def configured_models(self) -> list[str]:
        s = self.settings
        return sorted({s.scientist_model, s.microscopist_model, s.reviewer_model, s.segmenter_model})

    async def check_models(self, required: list[str] | None = None) -> list[str]:
        """GET /v1/models; raise ModelCheckError if any configured id is missing. Returns the available ids."""
        required = required or self.configured_models
        try:
            page = await self._client.models.list()
        except Exception as exc:  # noqa: BLE001 - surfaced with a readable message
            raise ModelCheckError(_explain(exc, "models.list")) from exc
        available = sorted(m.id for m in page.data)
        missing = [m for m in required if m not in available]
        if missing:
            raise ModelCheckError(f"Configured model id(s) {missing} not offered by the API. "
                                  f"Available: {available}. Fix backend/.env; model ids are never guessed.")
        return available

    def _check_allowed(self, model: str, meter: CostMeter) -> None:
        if model.endswith("-contributor") and not self.settings.allow_contributor:
            raise LLMError(f"'{model}' lets Meta train on prompts; set ALLOW_CONTRIBUTOR=true to use it "
                           "(public-data eval runs only).")
        cost_of(model, 0, 0)  # raises if unpriced
        if meter.total_usd >= meter.cap_usd:
            raise CostCapExceeded(f"Run cost cap reached: ${meter.total_usd:.4f} of ${meter.cap_usd:.2f}. "
                                  "Raise RUN_COST_CAP_USD in backend/.env to allow more.")
        today = self.ledger.total_today()
        if today >= self.ledger.cap_usd:
            raise CostCapExceeded(f"Daily cost cap reached: ${today:.4f} of ${self.ledger.cap_usd:.2f}. "
                                  "Raise DAILY_COST_CAP_USD in backend/.env or wait until tomorrow (UTC).")

    async def chat(self, *, model: str, messages: list[dict], meter: CostMeter, purpose: str,
                   tools: list[dict] | None = None, response_format: dict | None = None,
                   reasoning_effort: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> ChatResult:
        self._check_allowed(model, meter)
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"  # the only value the Meta API accepts
        if response_format:
            kwargs["response_format"] = response_format
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        t0 = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                if not _retryable(exc) or attempt > self.settings.model_max_retries:
                    raise LLMError(_explain(exc, model)) from exc
                delay = _retry_after(exc) or min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
                await self._sleep(delay * (1 + 0.25 * random.random()))

        usage = response.usage
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        choice = response.choices[0]
        record = CallRecord(
            model=model, purpose=purpose,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            reasoning_tokens=(getattr(details, "reasoning_tokens", 0) or 0) if details else 0,
            seconds=round(time.perf_counter() - t0, 3), attempts=attempt, finish_reason=choice.finish_reason)
        record.cost_usd = cost_of(model, record.input_tokens, record.output_tokens)
        meter.add(record)
        self.ledger.add(meter.run_id, record)

        message = choice.message
        if not (message.content or "").strip() and not message.tool_calls:
            if choice.finish_reason == "length":
                raise LLMError(f"{model} used all {max_tokens} tokens (incl. {record.reasoning_tokens} reasoning) "
                               "without answering. Raise max_tokens for this call.")
            raise LLMError(f"{model} returned an empty answer (finish_reason={choice.finish_reason}).")
        return ChatResult(message=message, record=record)

    async def chat_json(self, *, model: str, messages: list[dict], meter: CostMeter, purpose: str,
                        schema_name: str, schema: dict, **kwargs: Any) -> tuple[dict, CallRecord]:
        """Strict structured output; returns the parsed JSON object."""
        fmt = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
        result = await self.chat(model=model, messages=messages, meter=meter, purpose=purpose,
                                 response_format=fmt, **kwargs)
        try:
            return json.loads(result.content), result.record
        except json.JSONDecodeError as exc:
            raise LLMError(f"{model} returned invalid JSON for {schema_name}: {result.content[:200]}") from exc

    async def aclose(self) -> None:
        await self._client.close()
