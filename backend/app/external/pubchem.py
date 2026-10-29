"""PubChem PUG-REST: compound name -> CID, IUPAC name, SMILES, synonyms, 2D structure URL (spec §5 tool 8).

Verified 2026-10-29: the property `SMILES` (isomeric) and `ConnectivitySMILES` replace the older
`IsomericSMILES` / `CanonicalSMILES` names; an unknown name answers HTTP 404.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from app.external.cached_http import CachedJSONClient, RateLimiter

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PROPERTIES = "Title,IUPACName,SMILES,ConnectivitySMILES,InChIKey,MolecularFormula,MolecularWeight"
LIMITER = RateLimiter(per_second=4)  # PubChem asks for <= 5 req/s


class PubChemCompound(BaseModel):
    cid: int
    title: str | None
    iupac_name: str | None
    smiles: str | None
    inchikey: str | None
    molecular_formula: str | None
    molecular_weight: float | None
    synonyms: list[str]
    structure_png_url: str
    url: str


class PubChemClient:
    def __init__(self, cache_dir: Path | None, http: httpx.AsyncClient | None = None):
        self._api = CachedJSONClient("pubchem", cache_dir, LIMITER, http)

    async def synonyms(self, cid: int, limit: int = 50) -> list[str]:
        body = await self._api.get_json(f"{BASE}/compound/cid/{cid}/synonyms/JSON")
        if not body:
            return []
        return body["InformationList"]["Information"][0].get("Synonym", [])[:limit]

    async def lookup(self, name: str) -> PubChemCompound | None:
        """First CID PubChem returns for `name`, or None when PubChem knows no such name."""
        return await self._compound(
            await self._api.get_json(f"{BASE}/compound/name/{quote(name, safe='')}/property/{PROPERTIES}/JSON"))

    async def lookup_smiles(self, smiles: str) -> PubChemCompound | None:
        """Compound for a structure (SMILES as a query parameter: it may contain '/' and '#')."""
        return await self._compound(
            await self._api.get_json(f"{BASE}/compound/smiles/property/{PROPERTIES}/JSON", {"smiles": smiles}))

    async def _compound(self, body: dict | None) -> PubChemCompound | None:
        if not body or not body.get("PropertyTable", {}).get("Properties"):
            return None
        p = body["PropertyTable"]["Properties"][0]
        if not p.get("CID"):  # structure search answers CID 0 for an unknown structure
            return None
        cid = int(p["CID"])
        mw = p.get("MolecularWeight")
        return PubChemCompound(
            cid=cid, title=p.get("Title"), iupac_name=p.get("IUPACName"),
            smiles=p.get("SMILES") or p.get("ConnectivitySMILES"), inchikey=p.get("InChIKey"),
            molecular_formula=p.get("MolecularFormula"), molecular_weight=float(mw) if mw else None,
            synonyms=await self.synonyms(cid),
            structure_png_url=f"{BASE}/compound/cid/{cid}/PNG",
            url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}")

    async def aclose(self) -> None:
        await self._api.aclose()
