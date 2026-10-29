"""Hard guardrails (spec §5): MoA vocabulary + probability renormalisation, citation validation,
and the blinding-leak scanner.

These run server-side on every relevant tool call; the model cannot opt out of them.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.agent.schemas import Hypothesis

UNKNOWN_MOA = "unknown"  # allowed only in the Microscopist's candidate list
MIN_TERM_LEN = 4  # shorter synonyms ("R 1") would match unrelated text


class GuardrailError(ValueError):
    """Rejected input; the message goes back to the agent as the tool error."""


# --- MoA vocabulary -------------------------------------------------------------------------------

def load_moa_classes(moa_csv: Path) -> list[str]:
    """The MoA classes as spelled in BBBC021_v1_moa.csv (read at runtime, never hard-coded), DMSO excluded."""
    moa = pd.read_csv(moa_csv)["moa"].dropna().astype(str).str.strip()
    return sorted(m for m in moa.unique() if m.upper() != "DMSO")


def canonical_moa(value: str, classes: Iterable[str]) -> str:
    classes = list(classes)
    by_fold = {c.casefold(): c for c in classes}
    hit = by_fold.get(value.strip().casefold())
    if hit is None:
        raise GuardrailError(f"Unknown MoA '{value}'. Use exactly one of: {classes}.")
    return hit


def normalize_hypotheses(hypotheses: list[Hypothesis], classes: Iterable[str],
                         known_evidence_ids: set[str]) -> list[Hypothesis]:
    """Validate MoA names and evidence ids, reject duplicates, renormalise probabilities to sum 1.

    Returned sorted by probability (descending). Rejections raise GuardrailError with a readable reason.
    """
    if not hypotheses:
        raise GuardrailError("Give at least one hypothesis.")
    classes = list(classes)
    seen: set[str] = set()
    out: list[Hypothesis] = []
    for h in hypotheses:
        moa = canonical_moa(h.moa, classes)
        if moa in seen:
            raise GuardrailError(f"MoA '{moa}' is listed twice; give each MoA once.")
        seen.add(moa)
        if not math.isfinite(h.probability) or h.probability < 0:
            raise GuardrailError(f"Probability for '{moa}' must be a finite number >= 0 (got {h.probability}).")
        unknown = [e for e in h.supporting_evidence_ids + h.contradicting_evidence_ids if e not in known_evidence_ids]
        if unknown:
            raise GuardrailError(f"Unknown evidence id(s) {unknown} for '{moa}'. "
                                 f"Known ids: {sorted(known_evidence_ids, key=_evidence_order)}.")
        out.append(h.model_copy(update={"moa": moa}))
    total = sum(h.probability for h in out)
    if total <= 0:
        raise GuardrailError("Probabilities sum to 0; give at least one positive probability.")
    out = [h.model_copy(update={"probability": h.probability / total}) for h in out]
    return sorted(out, key=lambda h: -h.probability)


def _evidence_order(eid: str) -> tuple[int, str]:
    digits = eid.lstrip("e")
    return (int(digits), eid) if digits.isdigit() else (10**9, eid)


# --- Citations ------------------------------------------------------------------------------------

# "PMID: 123", "PMID 123", "(PMID:123)" and PubMed URLs. Lists such as "PMID 1, 2" are not parsed: write one PMID per mention.
_PMID_PATTERNS = [
    re.compile(r"\[?\(?PMID:?\s*(\d{1,9})\)?\]?", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/(\d{1,9})/?",
               re.IGNORECASE),
]


class CitationCheck(BaseModel):
    text: str  # with unverified citations removed
    cited: list[str]  # every PMID the input cited, in order of first appearance
    verified: list[str]
    removed: list[str]

    @property
    def label(self) -> str:
        return f"Citations verified: {len(self.verified)}/{len(self.cited)}"


def validate_citations(text: str, allowed_pmids: Iterable[str]) -> CitationCheck:
    """Keep only citations of PMIDs that search_literature returned in this run; strip the rest."""
    allowed = {str(p) for p in allowed_pmids}
    found = sorted((m.start(), m.group(1)) for pattern in _PMID_PATTERNS for m in pattern.finditer(text))
    cited = list(dict.fromkeys(pmid for _, pmid in found))

    def strip(m: re.Match) -> str:
        return m.group(0) if m.group(1) in allowed else ""

    clean = text
    for pattern in _PMID_PATTERNS:
        clean = pattern.sub(strip, clean)
    clean = re.sub(r"[ \t]{2,}", " ", re.sub(r"\(\s*\)|\[\s*\]", "", clean))
    verified = [p for p in cited if p in allowed]
    return CitationCheck(text=clean, cited=cited, verified=verified, removed=[p for p in cited if p not in allowed])


def filter_pmids(pmids: Iterable[str], allowed_pmids: Iterable[str]) -> list[str]:
    allowed = {str(p) for p in allowed_pmids}
    return [str(p) for p in pmids if str(p) in allowed]


# --- Blinding leak scanner ------------------------------------------------------------------------

class LeakHit(BaseModel):
    term: str
    path: str  # where in the payload, e.g. "data.neighbors[0].compound"
    excerpt: str


_SEPARATORS = re.compile(r"[\s\-_,/]+")


def _normalize(text: str) -> str:
    return _SEPARATORS.sub(" ", text.casefold())


def _variants(term: str) -> set[str]:
    t = _normalize(term).strip()
    squashed = t.replace(" ", "")
    return {v for v in (t, squashed) if len(v) >= MIN_TERM_LEN}


class BlindingScanner:
    """Finds a blinded compound's identity (name, synonyms, SMILES, plate/well) anywhere in a payload.

    Matching is case-insensitive on word boundaries after runs of spaces, hyphens, underscores, commas and
    slashes are collapsed to one space; each term is also tried with its separators removed
    ("R 17,934", "R-17934" and "R17934" all match each other).
    """

    def __init__(self, terms: Iterable[str]):
        variants: set[str] = set()
        for term in terms:
            if term:
                variants |= _variants(str(term))
        self.terms = sorted(variants, key=len, reverse=True)
        self._patterns = [(t, re.compile(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])")) for t in self.terms]

    def scan_text(self, text: str, path: str = "$") -> list[LeakHit]:
        norm = _normalize(text)
        hits = []
        for term, pattern in self._patterns:
            m = pattern.search(norm)
            if m:
                hits.append(LeakHit(term=term, path=path, excerpt=norm[max(0, m.start() - 30):m.end() + 30]))
        return hits

    def scan(self, payload: Any, path: str = "$") -> list[LeakHit]:
        """Walk dicts / lists / pydantic models / strings; keys are scanned too."""
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="json")
        if isinstance(payload, dict):
            hits = []
            for k, v in payload.items():
                hits += self.scan_text(str(k), f"{path}.<key {k}>")
                hits += self.scan(v, f"{path}.{k}")
            return hits
        if isinstance(payload, (list, tuple)):
            return [h for i, v in enumerate(payload) for h in self.scan(v, f"{path}[{i}]")]
        if isinstance(payload, (str, bytes)):
            text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
            return self.scan_text(text, path)
        if payload is None or isinstance(payload, (bool, int, float)):
            return []
        return self.scan_text(json.dumps(payload, default=str), path)

    def assert_clean(self, payload: Any, what: str = "payload") -> None:
        hits = self.scan(payload)
        if hits:
            first = hits[0]
            raise GuardrailError(f"Blinding leak in {what}: '{first.term}' at {first.path}")
