// "Open-world eval": shown wherever a run comes from the pre-registered open-world eval (agent v5-open), in which the
// specimen's whole mechanism class was left out of the reference library and the Scientist's own call is final.
// Derived from the run's model_ids (reference_library); every other run shows nothing.

import { Globe } from "lucide-react";
import { isOpenWorld } from "../lib/format";
import type { ModelIds } from "../lib/types";

export default function OpenWorldBadge({ ids, className = "" }: { ids?: ModelIds | null; className?: string }) {
  if (!isOpenWorld(ids)) return null;
  return (
    <span role="note"
          title="Open-world eval: every compound of this specimen's mechanism class was left out of the reference library, so the NSC vote cannot name it; the final call is the Scientist's own"
          className={`chip h-7 px-2.5 border-white/40 bg-white/[0.07] text-white whitespace-nowrap shrink-0 ${className}`}>
      <Globe aria-hidden className="h-3.5 w-3.5 shrink-0" />
      Open-world eval
    </span>
  );
}
