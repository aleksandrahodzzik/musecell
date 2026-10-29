"""Guardrails: MoA vocabulary + renormalisation, citation validator, blinding-leak scanner."""

from __future__ import annotations

import pytest

from app.agent.guardrails import (
    BlindingScanner,
    GuardrailError,
    canonical_moa,
    filter_pmids,
    load_moa_classes,
    normalize_hypotheses,
    validate_citations,
)
from app.agent.schemas import Hypothesis

CLASSES = ["Eg5 inhibitors", "Microtubule destabilizers", "Microtubule stabilizers"]
EVIDENCE = {"e1", "e2", "e3"}


def h(moa, p, sup=(), con=()):
    return Hypothesis(moa=moa, probability=p, supporting_evidence_ids=list(sup), contradicting_evidence_ids=list(con))


# --- MoA + probabilities --------------------------------------------------------------------------

def test_moa_classes_read_from_csv(tmp_path):
    csv = tmp_path / "moa.csv"
    csv.write_text('compound,concentration,moa\nA,1,"Eg5 inhibitors"\nB,1,"DNA damage"\nDMSO,0,DMSO\nA,3,"Eg5 inhibitors"\n')
    assert load_moa_classes(csv) == ["DNA damage", "Eg5 inhibitors"]


def test_canonical_moa_is_case_insensitive_but_strict():
    assert canonical_moa("eg5 INHIBITORS ", CLASSES) == "Eg5 inhibitors"
    with pytest.raises(GuardrailError, match="Unknown MoA"):
        canonical_moa("Eg5 inhibitor", CLASSES)


def test_probabilities_renormalised_and_sorted():
    out = normalize_hypotheses([h("Eg5 inhibitors", 1), h("microtubule destabilizers", 3)], CLASSES, EVIDENCE)
    assert [x.moa for x in out] == ["Microtubule destabilizers", "Eg5 inhibitors"]
    assert [x.probability for x in out] == pytest.approx([0.75, 0.25])
    assert sum(x.probability for x in out) == pytest.approx(1.0)


@pytest.mark.parametrize("hyps, match", [
    ([h("Kinase inhibitors", 1)], "Unknown MoA"),
    ([h("Eg5 inhibitors", 1), h("eg5 inhibitors", 1)], "twice"),
    ([h("Eg5 inhibitors", -0.1)], ">= 0"),
    ([h("Eg5 inhibitors", float("nan"))], "finite"),
    ([h("Eg5 inhibitors", 0), h("Microtubule stabilizers", 0)], "sum to 0"),
    ([h("Eg5 inhibitors", 1, sup=["e9"])], "Unknown evidence"),
    ([h("Eg5 inhibitors", 1, con=["x"])], "Unknown evidence"),
    ([], "at least one"),
])
def test_bad_hypotheses_rejected(hyps, match):
    with pytest.raises(GuardrailError, match=match):
        normalize_hypotheses(hyps, CLASSES, EVIDENCE)


# --- citations ------------------------------------------------------------------------------------

def test_only_pmids_returned_in_run_survive():
    text = ("Nocodazole depolymerises microtubules (PMID: 111). Also see PMID 222 and "
            "https://pubmed.ncbi.nlm.nih.gov/333/ plus [PMID:444].")
    check = validate_citations(text, allowed_pmids=["111", "333"])
    assert check.cited == ["111", "222", "333", "444"]
    assert check.verified == ["111", "333"]
    assert check.removed == ["222", "444"]
    assert "222" not in check.text and "444" not in check.text
    assert "PMID: 111" in check.text and "pubmed.ncbi.nlm.nih.gov/333" in check.text
    assert check.label == "Citations verified: 2/4"


def test_no_citations():
    check = validate_citations("No references.", [])
    assert check.cited == [] and check.text == "No references." and check.label == "Citations verified: 0/0"


def test_filter_pmids_structured():
    assert filter_pmids(["1", 2, "3"], ["2", "3"]) == ["2", "3"]


# --- blinding leaks -------------------------------------------------------------------------------

@pytest.fixture
def scanner():
    return BlindingScanner(["nocodazole", "Oncodazole", "R 17,934", "NSC-238159", "31430-18-9", "R 1",
                            "Week1_22123", "COC(=O)NC1=NC2"])


@pytest.mark.parametrize("payload", [
    "Treated with NOCODAZOLE at 1 uM",
    {"data": {"neighbors": [{"compound": "oncodazole"}]}},
    {"nocodazole": 1},  # in a key
    ["ok", ["deep", {"x": "the compound R17934 was used"}]],  # separators removed
    "code R-17,934 here",
    "nsc 238159",
    "31430 18 9",
    "plate week1-22123 well E10",
    {"smiles": "COC(=O)NC1=NC2=C(N1)C=C"},
])
def test_scanner_finds_leaks(scanner, payload):
    assert scanner.scan(payload), payload


@pytest.mark.parametrize("payload", [
    {"specimen_id": "s_1a2b3c4d5e", "field_ids": ["f_7c1a00112233"], "moa": "Microtubule destabilizers"},
    "R 1 is too short to be a term",  # terms < 4 chars are ignored
    "nocodazolex is a different word",  # word boundary
    {"concentration_uM": 1.0, "z": 3.1, "ok": True, "none": None},
])
def test_scanner_ignores_clean_payloads(scanner, payload):
    assert scanner.scan(payload) == []


def test_assert_clean_raises_with_path(scanner):
    with pytest.raises(GuardrailError, match=r"\$\.a\[1\]\.b"):
        scanner.assert_clean({"a": [0, {"b": "Nocodazole"}]})
