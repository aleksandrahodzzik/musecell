// "Open weights · Muse Spark Open via Baseten": shown wherever a run's Scientist + Microscopist ran on open weights.
// Derived from the run's model_ids (provider key); runs recorded before the open-weights mode have none and show nothing.

import { PackageOpen } from "lucide-react";
import { isOpenWeights, openWeightsLabel } from "../lib/format";
import type { ModelIds } from "../lib/types";

export default function OpenWeightsBadge({ ids, className = "" }: { ids?: ModelIds | null; className?: string }) {
  if (!isOpenWeights(ids)) return null;
  return (
    <span role="note" title="Scientist and Microscopist on open weights; the PI reviewer stays on the Meta Model API"
          className={`chip h-7 px-2.5 border-white/40 bg-white/[0.07] text-white whitespace-nowrap shrink-0 ${className}`}>
      <PackageOpen aria-hidden className="h-3.5 w-3.5 shrink-0" />
      {openWeightsLabel(ids)}
    </span>
  );
}
