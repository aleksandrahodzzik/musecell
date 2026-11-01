"""Open-world eval (pre-registered, docs/DECISIONS.md "Open-world eval, pre-registered").

The specimen's whole mechanism class is left out of the reference library (leave-one-MoA-out), so the NSC vote and
any well-choice rule cannot name it by construction. The run's final call is the Scientist's own conclusion, and the
Scientist and the PI reviewer are told that the library may not contain the mechanism (one prompt change).
"""

from __future__ import annotations

from app.agent.specimens import Specimen, SpecimenCatalog
from app.science.atlas import Atlas

OPEN_WORLD_VERSION = "v5-open"  # model_ids["agent"] of these runs
REFERENCE_LIBRARY = "leave-one-MoA-out"  # model_ids["reference_library"]; the UI badge reads it


def held_out_well_keys(catalog: SpecimenCatalog, specimen: Specimen) -> set[str]:
    """Wells of the other compounds of the specimen's class (the specimen's own wells stay: the tools query them)."""
    return {w.well_key for sp in catalog.specimens.values()
            if sp.moa == specimen.moa and sp.compound != specimen.compound for w in sp.wells}


def restricted_atlas(atlas: Atlas, catalog: SpecimenCatalog, specimen: Specimen) -> Atlas:
    """The reference library of one open-world run: no reference treatment of the specimen's class, no well vector of
    another compound of that class. Every tool and the protocol call read it through ToolContext.atlas."""
    return atlas.without_moa(specimen.moa, held_out_well_keys(catalog, specimen))
