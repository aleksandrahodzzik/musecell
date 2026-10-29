"""External clients: offline behaviour (cache, 404, retries, NCBI etiquette) and a live check for nocodazole."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from app.external.cached_http import CachedJSONClient, ExternalAPIError, RateLimiter
from app.external.chembl import ChemblClient
from app.external.pubchem import PubChemClient
from app.external.pubmed import PubMedClient


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_cache_404_and_retry(tmp_path, monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr("app.external.cached_http.asyncio.sleep", no_sleep)
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "missing" in req.url.path:
            return httpx.Response(404)
        if len([u for u in seen if "flaky" in u]) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async def go():
        api = CachedJSONClient("t", tmp_path, RateLimiter(100), mock(handler))
        assert await api.get_json("https://x/flaky") == {"ok": True}  # 503 then 200
        assert await api.get_json("https://x/flaky") == {"ok": True}  # cached
        assert await api.get_json("https://x/missing") is None
        assert await api.get_json("https://x/missing") is None
        return api

    api = asyncio.run(go())
    assert api.requests_sent == 3 and api.cache_hits == 2


def test_client_error_not_retried(tmp_path):
    async def go():
        api = CachedJSONClient("t", tmp_path, RateLimiter(100), mock(lambda r: httpx.Response(400, text="bad")))
        with pytest.raises(ExternalAPIError, match="HTTP 400"):
            await api.get_json("https://x/a")
        return api
    assert asyncio.run(go()).requests_sent == 1


def test_rate_limiter_spaces_requests():
    async def go():
        lim = RateLimiter(per_second=20)
        t0 = time.monotonic()
        await asyncio.gather(*(lim.wait() for _ in range(5)))
        return time.monotonic() - t0
    assert asyncio.run(go()) >= 4 / 20 - 0.01


def test_pubmed_etiquette_params_and_email_kept_out_of_cache(tmp_path):
    reqs = []

    def handler(req):
        reqs.append(req)
        if "esearch" in req.url.path:
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["123"]}})
        return httpx.Response(200, json={"result": {"uids": ["123"], "123": {
            "title": "A paper.", "source": "J Cell Biol", "pubdate": "1999 Jan", "authors": [{"name": "Doe J"}]}}})

    async def go():
        c = PubMedClient(tmp_path, email="someone@example.org", http=mock(handler))
        return await c.search("nocodazole", 3)

    res = asyncio.run(go())
    assert [a.pmid for a in res.articles] == ["123"] and res.articles[0].year == 1999
    assert res.articles[0].url == "https://pubmed.ncbi.nlm.nih.gov/123/"
    for r in reqs:
        assert r.url.params["tool"] == "musecell" and r.url.params["email"] == "someone@example.org"
    cached = " ".join(p.read_text() for p in (tmp_path / "http" / "pubmed").glob("*.json"))
    assert "someone@example.org" not in cached and "nocodazole" in cached


def test_chembl_exact_match_only_and_parent_mechanisms(tmp_path):
    seen = []

    def handler(req):
        p, q = req.url.path, dict(req.url.params)
        seen.append(q)
        if p.endswith("molecule.json"):
            if q.get("molecule_synonyms__molecule_synonym__iexact") == "wantedine":
                return httpx.Response(200, json={"molecules": [
                    {"molecule_chembl_id": "CHEMBL2", "pref_name": "SALT", "max_phase": "4.0",
                     "molecule_hierarchy": {"parent_chembl_id": "CHEMBL3"}}]})
            return httpx.Response(200, json={"molecules": []})
        if p.endswith("mechanism.json"):
            if q.get("parent_molecule_chembl_id") == "CHEMBL3":
                return httpx.Response(200, json={"mechanisms": [{"mechanism_of_action": "Tubulin inhibitor",
                                                                 "action_type": "INHIBITOR",
                                                                 "target_chembl_id": "CHEMBL9"}]})
            return httpx.Response(200, json={"mechanisms": []})
        return httpx.Response(200, json={"pref_name": "Tubulin", "target_type": "PROTEIN COMPLEX GROUP"})

    client = ChemblClient(tmp_path, http=mock(handler))
    mol = asyncio.run(client.lookup("wantedine"))
    assert mol.molecule_chembl_id == "CHEMBL2" and mol.matched_by == "synonym" and mol.max_phase == 4.0
    assert [(m.mechanism_of_action, m.target_name) for m in mol.mechanisms] == [("Tubulin inhibitor", "Tubulin")]
    assert asyncio.run(client.lookup("nothing-like-it", inchikey="AAAA-BBBB-N")) is None  # no fuzzy fallback
    assert {"molecule_structures__standard_inchi_key": "AAAA-BBBB-N", "limit": "5"} in seen


# --- live ---------------------------------------------------------------------------------------

@pytest.mark.slow
def test_live_nocodazole(tmp_path):
    async def go():
        pubchem, chembl, pubmed = PubChemClient(tmp_path), ChemblClient(tmp_path), PubMedClient(tmp_path)
        try:
            return await asyncio.gather(pubchem.lookup("nocodazole"), chembl.lookup("nocodazole"),
                                        pubmed.search("nocodazole microtubule", 3),
                                        pubchem.lookup("definitely-not-a-compound-xyz"))
        finally:
            for c in (pubchem, chembl, pubmed):
                await c.aclose()

    pc, ch, pm, missing = asyncio.run(go())
    assert pc.cid == 4122 and pc.molecular_formula == "C14H11N3O3S" and pc.smiles
    assert "nocodazole" in [s.casefold() for s in pc.synonyms]
    assert pc.structure_png_url.endswith("/cid/4122/PNG")
    assert ch.molecule_chembl_id == "CHEMBL9514" and ch.matched_by == "pref_name"
    assert isinstance(ch.mechanisms, list)  # ChEMBL has no curated mechanism for nocodazole (2026-10-29)
    assert pm.total_count > 100 and len(pm.articles) == 3 and all(a.pmid.isdigit() and a.title for a in pm.articles)
    assert missing is None
    cached = [json.loads(p.read_text()) for p in (tmp_path / "http" / "pubchem").glob("*.json")]
    assert any(c["body"] is None for c in cached) and any(c["body"] for c in cached)  # 404 and hits both cached
