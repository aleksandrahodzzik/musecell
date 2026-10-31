// Hypothesis board (spec §8): the agent's current probabilities; bars move on update; a row opens its evidence.
// After the unblinding the verdict (computed by code) marks the agent's call and the annotated mechanism.

import { Check, X } from "lucide-react";
import { useState, type PointerEvent } from "react";
import { prob } from "../../lib/format";
import type { Conclusion, Hypothesis, Verdict } from "../../lib/types";

interface Props {
  hypotheses: Hypothesis[];
  history: { step: number; hypotheses: Hypothesis[] }[];
  conclusion?: Conclusion;
  verdict?: Verdict;
  onEvidence: (evidenceId: string) => void;
}

export default function HypothesisBoard({ hypotheses, history, conclusion, verdict, onEvidence }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [view, setView] = useState<"now" | "time">("now");
  const rows = [...hypotheses].sort((a, b) => b.probability - a.probability);
  const truthMissing = verdict && !rows.some((h) => h.moa === verdict.truth_moa);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-3 h-10 border-b border-rule bg-sheet">
        <h2 className="text-sm font-medium">Hypotheses</h2>
        {rows.length > 0 && history.length < 2 && <span className="label">agent's probabilities</span>}
        {history.length >= 2 && (
          <div role="tablist" aria-label="Board view" className="ml-auto flex rounded-control border border-rule p-0.5 text-[11px]">
            {(["now", "time"] as const).map((v) => (
              <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
                      className={`px-2 h-5 rounded-[4px] ${view === v ? "bg-ink text-sheet" : "text-graphite hover:text-ink"}`}>
                {v === "now" ? "Now" : "Over time"}
              </button>
            ))}
          </div>
        )}
      </div>
      {view === "time" && history.length >= 2 ? <Trajectory history={history} lead={verdict?.agent_moa ?? conclusion?.top_moa ?? rows[0]?.moa} truth={verdict?.truth_moa} /> : (
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
            // the Scientist's own conclusion; from v5 it can differ from the final call (the protocol's vote)
            const isConcl = conclusion?.top_moa === h.moa;
            const dissent = !!verdict && verdict.call_source === "protocol" && isConcl && !isCall;
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
                  {(isCall || isTruth || dissent) && (
                    <span className="mt-1 flex flex-wrap gap-x-3 text-[11px]">
                      {dissent && conclusion && (
                        <span className={verdict!.scientist_correct ? "text-verdict-yes" : "text-verdict-no"}>
                          Scientist's conclusion{Math.abs(conclusion.probability - h.probability) >= 0.005 ? ` stated ${prob(conclusion.probability)}` : ""} · dissents from the vote, {verdict!.scientist_correct ? "right" : "wrong"}
                        </span>
                      )}
                      {isCall && isConcl && conclusion && (!verdict || Math.abs(conclusion.probability - h.probability) >= 0.005) && (
                        <span className="text-graphite">
                          {verdict ? "Conclusion" : "Submitted conclusion"}
                          {Math.abs(conclusion.probability - h.probability) >= 0.005 ? ` stated ${prob(conclusion.probability)}` : ""}
                        </span>
                      )}
                      {isCall && verdict && (
                        <span className={`flex items-center gap-1 ${verdict.correct ? "text-verdict-yes" : "text-verdict-no"}`}>
                          {verdict.correct ? <Check aria-hidden className="h-3 w-3" /> : <X aria-hidden className="h-3 w-3" />}
                          {verdict.call_source === "protocol" ? "Final call (NSC vote)" : "Agent's call"} · {verdict.correct ? "matches the annotation" : "does not match"}
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
      )}
    </div>
  );
}

/**
 * The board over time: one line per mechanism across the agent's board updates. Move the pointer (or use the
 * arrow keys) to read the board as it stood after any update.
 */
function Trajectory({ history, lead, truth }: { history: { step: number; hypotheses: Hypothesis[] }[]; lead?: string; truth?: string }) {
  const [at, setAt] = useState(history.length - 1);
  const W = 256, H = 150, L = 26, R = 8, T = 8, B = 20;
  const n = history.length;
  const sx = (i: number) => L + (i / Math.max(1, n - 1)) * (W - L - R);
  const sy = (p: number) => T + (1 - p) * (H - T - B);
  const classes = Array.from(new Set(history.flatMap((h) => h.hypotheses.map((x) => x.moa))));
  const value = (i: number, moa: string) => history[i].hypotheses.find((x) => x.moa === moa)?.probability ?? 0;
  const peak = (moa: string) => Math.max(...history.map((_, i) => value(i, moa)));
  const shown = classes.filter((m) => m === lead || m === truth || peak(m) >= 0.1);
  const i = Math.min(at, n - 1);
  const board = [...history[i].hypotheses].sort((a, b) => b.probability - a.probability).slice(0, 5);

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    setAt(Math.max(0, Math.min(n - 1, Math.round(((x - L) / (W - L - R)) * (n - 1)))));
  };
  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2 flex flex-col gap-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full touch-none cursor-crosshair outline-none" onPointerMove={onMove}
           tabIndex={0} role="slider" aria-label="Board update" aria-valuemin={1} aria-valuemax={n} aria-valuenow={i + 1}
           aria-valuetext={`Update ${i + 1} of ${n}, after step ${history[i].step}`}
           onKeyDown={(e) => {
             const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
             if (d) { e.preventDefault(); setAt(Math.max(0, Math.min(n - 1, i + d))); }
           }}>
        {[0, 0.5, 1].map((p) => (
          <g key={p}>
            <line x1={L} x2={W - R} y1={sy(p)} y2={sy(p)} stroke="#17202A" strokeOpacity={0.08} />
            <text x={L - 4} y={sy(p) + 3} textAnchor="end" fontSize={9} fill="#5B6672">{p.toFixed(1)}</text>
          </g>
        ))}
        <text x={L} y={H - 4} fontSize={9} fill="#5B6672">update 1</text>
        <text x={W - R} y={H - 4} fontSize={9} fill="#5B6672" textAnchor="end">{n}</text>
        {shown.map((m) => {
          const isLead = m === lead;
          const d = history.map((_, k) => `${k ? "L" : "M"}${sx(k)},${sy(value(k, m))}`).join(" ");
          return <path key={m} d={d} fill="none" stroke={isLead ? "#17202A" : "#5B6672"} strokeOpacity={isLead ? 1 : 0.55}
                       strokeWidth={isLead ? 2.25 : 1.1} strokeDasharray={m === truth && !isLead ? "3 2" : undefined} />;
        })}
        <line x1={sx(i)} x2={sx(i)} y1={T} y2={H - B} stroke="#17202A" strokeOpacity={0.35} />
        {shown.map((m) => <circle key={m} cx={sx(i)} cy={sy(value(i, m))} r={m === lead ? 3.5 : 2.2} fill={m === lead ? "#17202A" : "#5B6672"} />)}
      </svg>
      <p className="text-[11px] text-graphite">
        Board after step {history[i].step} · update {i + 1} of {n}
        {truth && truth !== lead && <> · dashed: annotated mechanism</>}
      </p>
      <ul className="flex flex-col gap-1 text-[12.5px]">
        {board.map((h) => (
          <li key={h.moa} className="flex items-baseline gap-2">
            <span className={`flex-1 truncate ${h.moa === lead ? "font-semibold" : ""}`}>{h.moa}</span>
            <span className="text-graphite font-mono text-[11.5px]">{prob(h.probability)}</span>
          </li>
        ))}
      </ul>
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
