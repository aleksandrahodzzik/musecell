"""LOCO: the atlas must never return the query compound as its own neighbour."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.science.atlas import Atlas, nsc_baseline

FEATURES = ["a", "b", "c"]


@pytest.fixture
def atlas() -> Atlas:
    rows = [
        # compound X has the profile closest to everything; it must still be excluded for itself.
        ("X", 1.0, "M1", [1.0, 0.0, 0.0]), ("X", 3.0, "M1", [1.0, 0.05, 0.0]),
        ("Y", 1.0, "M1", [0.9, 0.2, 0.0]),
        ("Z", 1.0, "M2", [0.0, 1.0, 0.0]), ("W", 1.0, "M2", [0.1, 0.9, 0.1]),
        ("unlabelled", 5.0, None, [1.0, 0.0, 0.0]),
    ]
    df = pd.DataFrame([{"compound": c, "concentration_uM": conc, "moa": m, "moa_annotated_conc": m is not None,
                        "thumbnail_field_id": f"f_{i:012x}", **dict(zip(FEATURES, v))}
                       for i, (c, conc, m, v) in enumerate(rows)])
    return Atlas(df, features=FEATURES)


def test_neighbors_exclude_query_compound(atlas):
    query = pd.Series({"a": 1.0, "b": 0.0, "c": 0.0})
    hits = atlas.neighbors(query, k=10, exclude_compound="X")
    assert hits and all(h.compound != "X" for h in hits)
    assert hits[0].compound == "Y"
    assert [h.similarity for h in hits] == sorted([h.similarity for h in hits], reverse=True)
    assert all(h.compound != "unlabelled" for h in hits)  # only MoA-annotated treatments are references


def test_exclude_compound_is_required(atlas):
    with pytest.raises(TypeError):
        atlas.neighbors(pd.Series({"a": 1.0, "b": 0.0, "c": 0.0}), 3)  # type: ignore[call-arg]


def test_nsc_never_matches_self(atlas):
    pred = nsc_baseline(atlas)
    assert (pred["neighbor"] != pred["compound"]).all()
    assert (pred["moa"] == pred["predicted"]).all()


def test_whitening_runs(atlas):
    dmso = pd.DataFrame(np.random.default_rng(0).normal(size=(30, 3)), columns=FEATURES)
    w = Atlas(atlas.reference.assign(moa_annotated_conc=True), features=FEATURES, dmso_wells=dmso, whiten=True)
    assert all(h.compound != "Z" for h in w.neighbors(pd.Series({"a": 0, "b": 1, "c": 0}), 5, exclude_compound="Z"))


def test_deep_atlas_loads_well_vectors_and_stays_loco(tmp_path):
    """Deep atlas (scripts/build_deep_atlas.py layout): vectors per well, d000.. columns detected, LOCO kept."""
    from app.science.atlas import Atlas
    rng = np.random.default_rng(0)
    cols = [f"d{i:03d}" for i in range(6)]
    a, b = rng.normal(size=6), rng.normal(size=6)
    tr = pd.DataFrame([{"compound": c, "concentration_uM": 1.0, "moa": m, "moa_annotated_conc": True, "n_wells": 2,
                        "thumbnail_field_id": f"f_{i}", **dict(zip(cols, v + rng.normal(scale=.05, size=6)))}
                       for i, (c, m, v) in enumerate([("x", "A", a), ("y", "A", a), ("z", "B", b)])])
    tr.to_parquet(tmp_path / "treatments.parquet", index=False)
    pd.DataFrame([{"well_key": "P/w1", **dict(zip(cols, a))}]).to_parquet(tmp_path / "wells.parquet", index=False)
    atlas = Atlas.load_deep(tmp_path)
    assert atlas.space == "resnet50_tvn" and atlas.features == cols
    vec = atlas.well_vector("P/w1")
    assert vec.shape == (6,)
    hit = atlas.neighbors(vec, 1, exclude_compound="x")[0]
    assert hit.compound == "y" and hit.moa == "A"  # its own compound "x" is never returned
