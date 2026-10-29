"""ChEMBL REST: exact molecule match + curated mechanisms of action with target names (spec §5 tool 8).

Molecule matching (deviation from the spec's `/molecule/search`, docs/DECISIONS.md): the free-text search
ranks unrelated molecules first (ALLN -> an oxalate enzyme entry, taxol -> CHEMBL306601), so we only accept
an exact, case-insensitive preferred name, then an exact synonym, then the PubChem InChIKey. No match -> None.

Verified 2026-10-29 (ChEMBL_37): mechanisms are often stored on a salt form, so
`mechanism.json?molecule_chembl_id=CHEMBL90555` (vincristine) returns 0 records while
`parent_molecule_chembl_id=CHEMBL90555` returns "Tubulin inhibitor". We therefore filter by the parent id,
falling back to the matched molecule's own id. Research compounds without a curated mechanism (e.g.
nocodazole, CHEMBL9514) get an empty list, never a filled-in one.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import BaseModel

from app.external.cached_http import CachedJSONClient, RateLimiter

BASE = "https://www.ebi.ac.uk/chembl/api/data"
LIMITER = RateLimiter(per_second=4)


class ChemblMechanism(BaseModel):
    mechanism_of_action: str
    action_type: str | None
    target_chembl_id: str | None
    target_name: str | None
    target_type: str | None


class ChemblMolecule(BaseModel):
    molecule_chembl_id: str
    pref_name: str | None
    matched_by: str  # "pref_name" | "synonym" | "inchikey"
    max_phase: float | None
    mechanisms: list[ChemblMechanism]
    url: str


class ChemblClient:
    def __init__(self, cache_dir: Path | None, http: httpx.AsyncClient | None = None):
        self._api = CachedJSONClient("chembl", cache_dir, LIMITER, http, timeout_s=60.0)

    async def _target(self, target_id: str) -> dict:
        return await self._api.get_json(f"{BASE}/target/{target_id}.json") or {}

    async def mechanisms(self, parent_id: str, molecule_id: str | None = None) -> list[ChemblMechanism]:
        """Curated mechanisms of every form of `parent_id`; the molecule's own id only if that finds none."""
        queries = [("parent_molecule_chembl_id", parent_id)]
        if molecule_id and molecule_id != parent_id:
            queries.append(("molecule_chembl_id", molecule_id))
        records: list[dict] = []
        for field, cid in queries:
            body = await self._api.get_json(f"{BASE}/mechanism.json", {field: cid, "limit": 50})
            records = (body or {}).get("mechanisms", [])
            if records:
                break
        out: list[ChemblMechanism] = []
        seen: set[tuple] = set()
        for m in records:
            key = (m.get("mechanism_of_action"), m.get("target_chembl_id"))
            if key in seen or not m.get("mechanism_of_action"):
                continue
            seen.add(key)
            target = await self._target(m["target_chembl_id"]) if m.get("target_chembl_id") else {}
            out.append(ChemblMechanism(mechanism_of_action=m["mechanism_of_action"],
                                       action_type=m.get("action_type"),
                                       target_chembl_id=m.get("target_chembl_id"),
                                       target_name=target.get("pref_name"),
                                       target_type=target.get("target_type")))
        return out

    async def find_molecule(self, names: list[str], inchikey: str | None = None) -> tuple[dict, str] | None:
        filters = [("pref_name", "pref_name__iexact", n) for n in names]
        filters += [("synonym", "molecule_synonyms__molecule_synonym__iexact", n) for n in names]
        if inchikey:
            filters.append(("inchikey", "molecule_structures__standard_inchi_key", inchikey))
        for how, field, value in filters:
            body = await self._api.get_json(f"{BASE}/molecule.json", {field: value, "limit": 5})
            molecules = (body or {}).get("molecules", [])
            if molecules:
                return molecules[0], how
        return None

    async def lookup(self, names: str | list[str], inchikey: str | None = None) -> ChemblMolecule | None:
        """Exactly matching molecule (by any of `names`, then InChIKey) with its curated mechanisms."""
        found = await self.find_molecule([names] if isinstance(names, str) else names, inchikey)
        if found is None:
            return None
        mol, how = found
        parent = (mol.get("molecule_hierarchy") or {}).get("parent_chembl_id") or mol["molecule_chembl_id"]
        phase = mol.get("max_phase")
        return ChemblMolecule(molecule_chembl_id=mol["molecule_chembl_id"], pref_name=mol.get("pref_name"), matched_by=how,
                              max_phase=float(phase) if phase is not None else None,
                              mechanisms=await self.mechanisms(parent, mol["molecule_chembl_id"]),
                              url=f"https://www.ebi.ac.uk/chembl/explore/compound/{mol['molecule_chembl_id']}")

    async def aclose(self) -> None:
        await self._api.aclose()
