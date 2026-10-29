// Hypothesis board (spec §8): the agent's current probabilities; bars move on update; a row opens its evidence.
// After the unblinding the verdict (computed by code) marks the agent's call and the annotated mechanism.

import { Check, X } from "lucide-react";
import { useState } from "react";
import { prob } from "../../lib/format";
import type { Conclusion, Hypothesis, Verdict } from "../../lib/types";

interface Props {
  hypotheses: Hypothesis[];
  conclusion?: Conclusion;
  verdict?: Verdict;
  onEvidence: (evidenceId: string) => void;
}

export default function HypothesisBoard({ hypotheses, conclusion, verdict, onEvidence }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = [...hypotheses].sort((a, b) => b.probability - a.probability);
  const truthMissing = verdict && !rows.some((h) => h.moa === verdict.truth_moa);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-3 h-10 border-b border-rule bg-sheet">
        <h2 className="text-sm font-medium">Hypotheses</h2>
        {rows.length > 0 && <span className="label">agent's probabilities</span>}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2">
        {rows.length === 0 && (
          <p className="text-xs text-graphite pt-1">
            No hypotheses yet. The agent posts its first board after it has looked at some wells.
          </p>
        )}
        <ul className="flex flex-col gap-0.5">
          {rows.map((h, i) => {
            const isCall = verdict ? h.moa === verdict.agent_moa : conclusion?.top_moa === h.moa;
            const isTruth = verdict?.truth_moa === h.moa;
            const expanded = open === h.moa;
            return (
              <li key={h.moa} className={i === 0 ? "pb-2 mb-1 border-b border-rule" : ""}>
                <button
                  className="w-full text-left rounded-control px-1.5 py-1.5 hover:bg-white/70"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : h.moa)}
                >
                  {i === 0 ? (
                    <span className="flex items-end gap-2">
                      <span className="flex-1 font-serif text-[17px] leading-tight font-semibold">{h.moa}</span>
                      <span className="font-serif text-[34px] leading-none font-semibold tracking-tight">{prob(h.probability)}</span>
                    </span>
                  ) : (
                    <span className="flex items-baseline gap-2 text-[13px]">
                      <span className="flex-1 truncate">{h.moa}</span>
                      <span className="text-graphite">{prob(h.probability)}</span>
                    </span>
                  )}
                  <span className={`block rounded-full bg-ink/10 overflow-hidden ${i === 0 ? "mt-2 h-2" : "mt-1 h-1"}`}>
                    <span
                      className={`block h-full rounded-full transition-[width] duration-500 ease-out ${i === 0 ? "bg-ink" : "bg-graphite/60"}`}
                      style={{ width: `${Math.max(1, h.probability * 100)}%` }}
                    />
                  </span>
                  {(isCall || isTruth) && (
                    <span className="mt-1 flex flex-wrap gap-x-3 text-[11px]">
                      {isCall && conclusion && (!verdict || Math.abs(conclusion.probability - h.probability) >= 0.005) && (
                        <span className="text-graphite">
                          {verdict ? "Conclusion" : "Submitted conclusion"}
                          {Math.abs(conclusion.probability - h.probability) >= 0.005 ? ` stated ${prob(conclusion.probability)}` : ""}
                        </span>
                      )}
                      {isCall && verdict && (
                        <span className={`flex items-center gap-1 ${verdict.correct ? "text-verdict-yes" : "text-verdict-no"}`}>
                          {verdict.correct ? <Check aria-hidden className="h-3 w-3" /> : <X aria-hidden className="h-3 w-3" />}
                          Agent's call · {verdict.correct ? "matches the annotation" : "does not match"}
                        </span>
                      )}
                      {isTruth && !isCall && (
                        <span className="flex items-center gap-1 text-verdict-yes">
                          <Check aria-hidden className="h-3 w-3" /> Annotated mechanism
                        </span>
                      )}
                    </span>
                  )}
                </button>
                {expanded && (
                  <div className="px-1.5 pb-1.5 text-[11px] text-graphite flex flex-col gap-1">
                    <EvidenceIds label="Supported by" ids={h.supporting_evidence_ids} onEvidence={onEvidence} />
                    <EvidenceIds label="Against" ids={h.contradicting_evidence_ids} onEvidence={onEvidence} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {truthMissing && (
          <p className="mt-2 flex items-center gap-1 text-[11px] text-verdict-yes">
            <Check aria-hidden className="h-3 w-3" /> Annotated mechanism: {verdict!.truth_moa} (not on the board)
          </p>
        )}
      </div>
    </div>
  );
}

function EvidenceIds({ label, ids, onEvidence }: { label: string; ids: string[]; onEvidence: (id: string) => void }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {label}:
      {ids.length === 0 && <span>none cited</span>}
      {ids.map((id) => (
        <button key={id} className="chip hover:border-ink/40" onClick={() => onEvidence(id)} aria-label={`Show evidence ${id} in the notebook`}>
          {id}
        </button>
      ))}
    </span>
  );
}
