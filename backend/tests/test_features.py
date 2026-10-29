"""Features on a synthetic field with known blobs, plus normalisation and stats."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.science import features as F
from app.science.segmentation import segment_cells_classical, segment_field, segment_nuclei_classical
from app.science.stats import compare_to_dmso, dose_response

SHAPE = (300, 400)
# (y, x, nucleus radius, cell radius, DNA intensity)
BLOBS = [(80, 80, 14, 34, 0.8), (90, 250, 18, 40, 0.6), (220, 150, 11, 30, 1.0), (210, 320, 16, 36, 0.7)]


def disc(cy: int, cx: int, r: float) -> np.ndarray:
    yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1]]
    return (yy - cy) ** 2 + (xx - cx) ** 2 <= r ** 2


@pytest.fixture(scope="module")
def field() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(0)
    dna = np.zeros(SHAPE, np.float32)
    cyto = np.zeros(SHAPE, np.float32)
    for y, x, rn, rc, inten in BLOBS:
        dna[disc(y, x, rn)] = inten
        cyto[disc(y, x, rc)] = 0.5
    noise = lambda: rng.normal(0, 0.01, SHAPE).astype(np.float32)  # noqa: E731
    return {"dna": dna + noise(), "tubulin": cyto + noise(), "actin": cyto * 0.8 + noise()}


@pytest.fixture(scope="module")
def cells(field) -> pd.DataFrame:
    seg = segment_field("f_synthetic", field, nuclei_method="classical", cells_method="classical")
    return F.cell_features(seg.nuclei, seg.cells, field).sort_values("centroid_x").reset_index(drop=True)


def test_counts(field):
    nuclei = segment_nuclei_classical(field["dna"])
    cells = segment_cells_classical(nuclei, field["tubulin"], field["actin"])
    assert nuclei.max() == len(BLOBS)
    assert set(np.unique(cells)) - {0} == set(np.unique(nuclei)) - {0}


def test_areas_and_intensities(cells):
    truth = sorted(BLOBS, key=lambda b: b[1])
    assert len(cells) == len(truth)
    for (_, row), (y, x, rn, rc, inten) in zip(cells.iterrows(), truth):
        # Gaussian smoothing before a global Otsu moves a bright nucleus's edge out by up to ~1.5 px.
        assert np.pi * (rn - 0.5) ** 2 <= row["nuc_area"] <= np.pi * (rn + 1.5) ** 2
        # Cytoplasm threshold is 0.5 x Otsu on a smoothed image: the edge moves out ~1.3 px (sigma 2).
        assert row["cell_area"] == pytest.approx(np.pi * (rc + 1.3) ** 2, rel=0.04)
        # The dark rim dilutes the mean but adds ~0 to integrated DNA (the DNA-content proxy).
        assert row["nuc_dna_integrated"] == pytest.approx(inten * np.pi * rn ** 2, rel=0.05)
        assert row["nuc_dna_integrated"] == pytest.approx(row["nuc_area"] * row["nuc_dna_mean"])
        assert row["nuc_dna_mean"] <= inten * 1.01
        assert row["centroid_y"] == pytest.approx(y, abs=1.5)
        assert row["cell_roundness"] > 0.85 and row["nuc_eccentricity"] < 0.3
        assert row["act_tub_ratio"] == pytest.approx(0.8, rel=0.05)
    assert not cells["touches_border"].any()
    assert not cells["nuc_in_cluster"].any()
    assert set(F.CELL_FEATURES) <= set(cells.columns)


def test_field_features_with_reference(cells):
    ref = F.Reference(nuc_area=float(np.pi * 16 ** 2), nuc_dna_mean=0.5)
    ff = F.field_features(cells, ref)
    assert ff["cell_count"] == 4
    # Blob at x=150: area pi*11^2 < 0.6 * pi*16^2 and DNA 1.0 > 1.5 * 0.5 -> mitotic-like; 1 of 4.
    assert ff["frac_mitotic_like"] == pytest.approx(0.25)
    assert ff["frac_large_nucleus"] == 0.0


def test_touching_nuclei_flagged():
    dna = np.zeros(SHAPE, np.float32)
    dna[disc(150, 180, 15) | disc(150, 206, 15)] = 1.0
    nuclei = segment_nuclei_classical(dna)
    assert nuclei.max() == 2
    cells = segment_cells_classical(nuclei, dna, dna)
    df = F.cell_features(nuclei, cells, {"dna": dna, "tubulin": dna, "actin": dna})
    assert df["nuc_in_cluster"].all()


def test_robust_z_uses_same_plate_dmso():
    rows = []
    for plate, base in [("p1", 10.0), ("p2", 100.0)]:
        for v in [-1, 0, 1, 2, -2]:
            rows.append({"plate": plate, "role": "control", "x": base + v})
        rows.append({"plate": plate, "role": "treated", "x": base + 6})
    wells = pd.DataFrame(rows)
    z = F.normalize_to_dmso(wells, features=["x"])
    treated = z[z["role"] == "treated"]["x"].to_numpy()
    # median 0, MAD 1 -> scale 1.4826 on both plates; the plate offset must cancel.
    assert treated == pytest.approx([6 / 1.4826, 6 / 1.4826])


def test_dose_response_and_compare():
    wells = pd.DataFrame({"concentration_uM": [0.1, 0.1, 1, 1, 10, 10], "f": [0.0, 0.2, 1.0, 1.1, 3.0, 2.5]})
    dr = dose_response("f", wells)
    assert dr.spearman_rho == pytest.approx(1.0, abs=0.1) and dr.p_value < 0.05
    assert [p.median for p in dr.points] == pytest.approx([0.1, 1.05, 2.75])
    short = dose_response("f", wells.head(2))
    assert short.spearman_rho is None and short.note
    dev = compare_to_dmso(pd.Series({"a": 0.5, "b": -4.0, "c": 2.0}), k=2)
    assert [(d.feature, d.direction) for d in dev] == [("b", "down"), ("c", "up")]
