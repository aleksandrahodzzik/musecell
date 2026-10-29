"""NSC (nearest other compound) MoA baseline under LOCO (spec §3, §6).

Each MoA-annotated compound-concentration takes the MoA of its most similar
treatment profile from a different compound (1-NN, cosine). Reports
treatment-level accuracy (the published NSC protocol) and compound-level
accuracy (majority vote over the compound's concentrations), with and without
PCA whitening on DMSO, plus the confusion matrix of the default variant.
The default (no whitening) is the spec's primary similarity; the whitened
variant is reported but not selected on these numbers.

Usage: python scripts/build_atlas.py; python scripts/baseline_nsc.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings  # noqa: E402
from app.science.atlas import Atlas, compound_level, nsc_baseline  # noqa: E402

OUT = ROOT / "eval" / "baseline.json"


def evaluate(atlas: Atlas) -> tuple[dict, pd.DataFrame]:
    pred = nsc_baseline(atlas)
    comp = compound_level(pred)
    return {
        "treatment_accuracy": round(float((pred["moa"] == pred["predicted"]).mean()), 4),
        "n_treatments": len(pred),
        "compound_accuracy": round(float((comp["moa"] == comp["predicted"]).mean()), 4),
        "n_compounds": len(comp),
    }, pred


def main() -> None:
    settings = get_settings()
    ref = json.loads((settings.atlas_dir / "reference.json").read_text())
    plain = Atlas.load(whiten=False)
    results, pred = evaluate(plain)
    whitened, _ = evaluate(Atlas.load(whiten=True))

    classes = plain.moa_classes
    cm = pd.crosstab(pd.Categorical(pred["moa"], classes), pd.Categorical(pred["predicted"], classes),
                     rownames=["true"], colnames=["predicted"], dropna=False)
    short = {c: c[:12] for c in classes}
    pd.set_option("display.width", 250)
    print("Confusion matrix (treatment level, cosine, no whitening; rows = true MoA):")
    print(cm.rename(index=short, columns=short).to_string())
    per_class = pred.assign(ok=pred["moa"] == pred["predicted"]).groupby("moa")["ok"].agg(["mean", "size"])
    print("\nPer-class accuracy:")
    print(per_class.round(3).to_string())
    print(f"\nNSC 1-NN, LOCO, cosine:          treatment {results['treatment_accuracy']:.3f} "
          f"(n={results['n_treatments']}), compound {results['compound_accuracy']:.3f} (n={results['n_compounds']})")
    print(f"NSC 1-NN, LOCO, DMSO-whitened:   treatment {whitened['treatment_accuracy']:.3f}, "
          f"compound {whitened['compound_accuracy']:.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "method": "NSC 1-NN (not-same-compound), leave-one-compound-out, cosine on robust-z profiles",
        "date": date.today().isoformat(),
        "segmentation": {"nuclei": ref["nuclei_segmenter"], "cells": ref["cell_segmenter"]},
        "n_features": len(plain.features),
        "features": plain.features,
        "cosine": results,
        "cosine_dmso_whitened": whitened,
        "per_class": {m: {"accuracy": round(float(r["mean"]), 4), "n": int(r["size"])}
                      for m, r in per_class.iterrows()},
        "confusion_matrix": {"classes": classes, "rows_true_cols_predicted": cm.to_numpy().tolist()},
        "predictions": pred.round(4).to_dict(orient="records"),
    }, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
