"""NCBI E-utilities: PubMed esearch + esummary (spec §5 tool 9).

Etiquette: every request carries `tool=musecell` and, when NCBI_EMAIL is set, `email=`; at most 3 requests
per second across the process (NCBI's limit without an API key). Responses are cached on disk; the
e-mail is kept out of the cache key and the cache files.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import BaseModel

from app.external.cached_http import CachedJSONClient, RateLimiter

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "musecell"
LIMITER = RateLimiter(per_second=3)
MAX_RESULTS = 20


class PubMedArticle(BaseModel):
    pmid: str
    title: str
    journal: str | None
    year: int | None
    authors: list[str]
    url: str


class PubMedSearch(BaseModel):
    query: str
    total_count: int
    articles: list[PubMedArticle]


def _year(pubdate: str | None) -> int | None:
    head = (pubdate or "")[:4]
    return int(head) if head.isdigit() else None


class PubMedClient:
    def __init__(self, cache_dir: Path | None, email: str | None = None, http: httpx.AsyncClient | None = None):
        self._api = CachedJSONClient("pubmed", cache_dir, LIMITER, http)
        self._etiquette = {"tool": TOOL, **({"email": email} if email else {})}

    async def _get(self, endpoint: str, params: dict) -> dict:
        body = await self._api.get_json(f"{BASE}/{endpoint}", {**params, **self._etiquette}, cache_params=params)
        return body or {}

    async def search(self, query: str, max_results: int = 5) -> PubMedSearch:
        """Relevance-sorted PubMed hits with title, journal, year. Only PMIDs NCBI actually returned."""
        max_results = max(1, min(max_results, MAX_RESULTS))
        found = (await self._get("esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json",
                                                  "retmax": max_results, "sort": "relevance"})).get("esearchresult", {})
        ids = found.get("idlist", [])
        articles: list[PubMedArticle] = []
        if ids:
            result = (await self._get("esummary.fcgi", {"db": "pubmed", "id": ",".join(ids),
                                                        "retmode": "json"})).get("result", {})
            for pmid in ids:
                doc = result.get(pmid)
                if not doc or doc.get("error"):
                    continue
                articles.append(PubMedArticle(
                    pmid=pmid, title=doc.get("title", "").strip(), journal=doc.get("source"),
                    year=_year(doc.get("pubdate")), authors=[a["name"] for a in doc.get("authors", [])[:6]],
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"))
        return PubMedSearch(query=query, total_count=int(found.get("count", 0) or 0), articles=articles)

    async def aclose(self) -> None:
        await self._api.aclose()
