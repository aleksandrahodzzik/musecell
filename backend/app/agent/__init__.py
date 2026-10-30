"""Scientist agent, tools, reviewer and dossier."""

# Recorded in every run's model_ids as "agent"; runs without it are v1.
# v1: Phases 4-6 and the open-weights stretch. v2: the atlas tool returns the NSC vote over all acquired
# wells and the prompt says how to weigh it (docs/DECISIONS.md, "Agent v2"); eval stopped at 19/38.
# v3: v2 + the blinded phase must not name any compound (two v2 runs were stopped by the blinding guard).
# v3.1: v3 + the PI reviewer must not name any compound either (one v3 eval review was stopped by the guard).
# v4: v3.1 + the atlas searches ResNet50 (ImageNet) + TVN embeddings instead of the 24 hand-crafted features
#     (chosen on the dev split, checked once on test; docs/DECISIONS.md, "Deep atlas").
AGENT_VERSION = "v4"
