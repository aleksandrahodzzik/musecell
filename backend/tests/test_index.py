"""Integrity of data/index.parquet built by scripts/preprocess.py.

Skipped (not failed) when the index or DATA_DIR metadata isn't present, e.g. on
a fresh clone in replay mode.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import INDEX_PATH, data_dir  # noqa: E402

FIELD_ID_RE = re.compile(r"^f_[0-9a-f]{12}$")
PATH_COLS = ["npy_dna", "npy_tubulin", "npy_actin", "png_dna", "png_tubulin", "png_actin", "png_composite"]


@pytest.fixture(scope="module")
def index() -> pd.DataFrame:
    if not INDEX_PATH.exists():
        pytest.skip("data/index.parquet not built (run scripts/preprocess.py)")
    return pd.read_parquet(INDEX_PATH)


@pytest.fixture(scope="module")
def moa_table() -> pd.DataFrame:
    path = data_dir() / "metadata" / "BBBC021_v1_moa.csv"
    if not path.exists():
        pytest.skip("BBBC021_v1_moa.csv not downloaded (run scripts/fetch_bbbc021.py)")
    return pd.read_csv(path)


def test_every_annotated_compound_has_images(index, moa_table):
    annotated = set(moa_table.loc[moa_table["compound"] != "DMSO", "compound"])
    labelled = index[index["moa_annotated_conc"]]
    have = set(labelled.groupby("compound")["concentration_uM"].nunique().loc[lambda s: s >= 1].index)
    assert annotated - have == set(), f"annotated compounds without images: {sorted(annotated - have)}"


def test_moa_matches_ground_truth(index, moa_table):
    truth = moa_table[moa_table["compound"] != "DMSO"].groupby("compound")["moa"].first()
    treated = index[index["role"] == "treated"]
    assert (treated["moa"] == treated["compound"].map(truth)).all()
    assert index.loc[index["role"] == "control", "moa"].isna().all()
    assert treated["moa"].nunique() == truth.nunique()


def test_every_plate_has_dmso(index):
    control_plates = set(index.loc[index["role"] == "control", "plate"])
    assert set(index["plate"]) - control_plates == set()


def test_field_ids_unique_and_opaque(index):
    assert index["field_id"].is_unique
    assert index["field_id"].map(lambda f: bool(FIELD_ID_RE.match(f))).all()
    names = {c.lower() for c in index["compound"]}
    for col in PATH_COLS:
        paths = index[col].str.lower()
        assert not paths.map(lambda p: any(n in p for n in names)).any(), f"compound name in {col}"


def test_output_files_exist(index):
    root = data_dir()
    sample = index.sample(n=min(50, len(index)), random_state=0)
    missing = [row[col] for _, row in sample.iterrows() for col in PATH_COLS if not (root / row[col]).exists()]
    assert missing == []
