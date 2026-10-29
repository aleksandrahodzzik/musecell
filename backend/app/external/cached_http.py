"""Cached, rate-limited JSON GET shared by the PubChem, ChEMBL and PubMed clients.

Cache: DATA_DIR/cache/http/<source>/<sha256 of url+params>.json, successful responses only, no expiry
(runs stay reproducible and we never re-query for the same thing). A 404 is cached as "not found"
because PubChem answers unknown names with 404. 429/5xx/network errors are retried with backoff.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "MuseCell/0.1 (cell-biology research tool)"
MAX_ATTEMPTS = 4


class ExternalAPIError(RuntimeError):
    """An external database call failed; the message is safe to show in the UI."""


class RateLimiter:
    """At most `per_second` request starts per second, shared by every caller of this instance.

    Instances live at module level (one per API), so the lock is re-created when a new event loop uses it.
    """

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next = 0.0

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._lock, self._loop = asyncio.Lock(), loop
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


class CachedJSONClient:
    def __init__(self, source: str, cache_dir: Path | None, limiter: RateLimiter,
                 http: httpx.AsyncClient | None = None, timeout_s: float = 30.0):
        self.source = source
        self.cache_dir = cache_dir / "http" / source if cache_dir else None
        self.limiter = limiter
        self._http = http or httpx.AsyncClient(timeout=timeout_s, headers={"User-Agent": USER_AGENT},
                                               follow_redirects=True)
        self.requests_sent = 0
        self.cache_hits = 0

    def _key(self, url: str, params: dict | None) -> str:
        blob = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    async def get_json(self, url: str, params: dict | None = None,
                       cache_params: dict | None = None) -> dict[str, Any] | None:
        """GET -> parsed JSON, or None on 404. `cache_params` replaces `params` in the cache key
        (so etiquette params such as an e-mail address do not change or appear in the key)."""
        path = self.cache_dir / f"{self._key(url, cache_params if cache_params is not None else params)}.json" \
            if self.cache_dir else None
        if path and path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["body"]

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self.limiter.wait()
            try:
                self.requests_sent += 1
                r = await self._http.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise ExternalAPIError(f"{self.source}: could not reach {url} ({type(exc).__name__}).") from exc
            else:
                if r.status_code == 404:
                    body = None
                    break
                if r.status_code == 200:
                    try:
                        body = r.json()
                    except ValueError as exc:
                        raise ExternalAPIError(f"{self.source}: non-JSON answer from {url}.") from exc
                    break
                if r.status_code != 429 and r.status_code < 500:
                    raise ExternalAPIError(f"{self.source}: HTTP {r.status_code} from {url}: {r.text[:200]}")
                if attempt == MAX_ATTEMPTS:
                    raise ExternalAPIError(f"{self.source}: HTTP {r.status_code} from {url} after {attempt} tries.")
            await asyncio.sleep(min(8.0, 0.5 * 2 ** attempt))

        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "params": cache_params if cache_params is not None else params,
                                        "body": body}), encoding="utf-8")
        return body

    async def aclose(self) -> None:
        await self._http.aclose()
