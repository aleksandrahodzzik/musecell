// Notebook timeline (spec §8): every event in order. The agent's stated reasons ("Why") are its tool rationales,
// never hidden reasoning. Tool results show the numbers the backend computed.

import { AlertTriangle, BookOpen, Check, ChevronDown, ChevronRight, ExternalLink, FileText, Loader2, MessageSquareQuote, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import FieldThumb from "../FieldThumb";
import { CHANNEL_LABEL, type Channel } from "../../lib/fieldImage";
import { featureName, modelName, prob, signed, toolName, uM } from "../../lib/format";
import { href } from "../../lib/router";
import type { Entry, RunState } from "../../lib/runState";
import type { Article, Deviation, ToolCallData, ToolResultData } from "../../lib/types";
import { SWATCH } from "./Microscope";

interface Props {
  run: RunState;
  focusEvidence?: { id: string; n: number };
  onSelectWell: (wellId: string) => void;
}

export default function Notebook({ run, focusEvidence, onSelectWell }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true); // follow new entries unless the reader scrolled up

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [run.entries.length, run.working, run.entries[run.entries.length - 1]]);

  useEffect(() => {
    if (!focusEvidence) return;
    const el = document.getElementById(`ev-${focusEvidence.id}`);
    if (el) {
      pinned.current = false;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.focus({ preventScroll: true });
    }
  }, [focusEvidence]);

  const onScroll = () => {
    const el = scroller.current!;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const visible = run.entries.filter((e) => !(e.kind === "tool" && e.call.name === "submit_conclusion" && e.result?.ok));
  const last = run.entries[run.entries.length - 1];
  const awaitingReview = last?.kind === "conclusion" && !run.finished;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-4 h-9 border-b border-rule shrink-0 bg-sheet">
        <h2 className="text-sm font-medium">Notebook</h2>
        <span className="label">{run.entries.filter((e) => e.kind === "tool").length} steps · turn {run.turn} of {run.started?.max_turns ?? 20}</span>
      </div>
      <div ref={scroller} onScroll={onScroll} className="flex-1 min-h-0 overflow-y-auto px-4 py-2" aria-live="polite" aria-relevant="additions">
        {visible.length === 0 && !run.working && (
          <p className="text-sm text-graphite py-2">The notebook fills in as the agent works. Every step shows what it did and why.</p>
        )}
        <ol className="flex flex-col">
          {visible.map((e) => (
            <li key={e.key} className="border-b border-rule last:border-0 py-2">
              <EntryView entry={e} onSelectWell={onSelectWell} run={run} />
            </li>
          ))}
        </ol>
        {run.working && !awaitingReview && (
          <p className="flex items-center gap-2 py-2 text-sm text-graphite" role="status">
            <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            {run.phase === "unblinded" ? "Scientist is reading the literature" : "Scientist is deciding the next step"} · turn {run.turn}
          </p>
        )}
        {awaitingReview && (
          <p className="flex items-center gap-2 py-2 text-sm text-graphite" role="status">
            <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            Reviewer ({modelName(run.started?.model_ids.reviewer)}) is reading the evidence ledger
          </p>
        )}
      </div>
    </div>
  );
}

function Meta({ children }: { children: ReactNode }) {
  return <p className="flex items-center gap-1.5 text-xs text-graphite">{children}</p>;
}

function EntryView({ entry: e, onSelectWell, run }: { entry: Entry; onSelectWell: (id: string) => void; run: RunState }) {
  switch (e.kind) {
    case "note":
      return (
        <div className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Scientist</span>
          <p className="font-serif text-[15px] leading-snug whitespace-pre-line">{e.text}</p>
        </div>
      );
    case "tool":
      return <ToolEntry call={e.call} result={e.result} step={e.step} onSelectWell={onSelectWell} />;
    case "conclusion":
      return (
        <div className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Conclusion</span>
          <div className="flex flex-col gap-1">
            <p className="text-sm">
              <span className="font-medium">{e.conclusion.top_moa}</span>
              <span className="text-graphite"> · agent's probability {prob(e.conclusion.probability)}</span>
              {!!e.conclusion.revision && <span className="text-graphite"> · revised after review</span>}
            </p>
            <p className="font-serif text-[15px] leading-snug">{e.conclusion.rationale}</p>
            <p className="text-xs text-graphite">
              Key evidence: {e.conclusion.key_evidence_ids.map((id) => <EvLink key={id} id={id} />)}
            </p>
            <p className="text-xs text-graphite">
              Next experiment in a real lab: <span className="text-ink">{e.conclusion.next_experiment_if_this_were_a_real_lab}</span>
            </p>
          </div>
        </div>
      );
    case "review":
      return (
        <div className="flex gap-3 rounded-r-panel bg-white border border-rule border-l-[3px] border-l-ink -mx-2 px-2 py-2.5 shadow-[0_6px_18px_-12px_rgba(23,32,42,0.5)]">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5 flex gap-1"><MessageSquareQuote aria-hidden className="h-3.5 w-3.5" />Reviewer</span>
          <div className="flex flex-col gap-1">
            <p className="text-sm">
              <span className="font-medium">{e.review.verdict === "approve" ? "Approved the conclusion" : "Asked for one more experiment"}</span>
              <span className="text-graphite"> · {modelName(e.review.model ?? run.started?.model_ids.reviewer)}</span>
            </p>
            <p className="font-serif italic text-[15.5px] leading-snug">“{e.review.critique}”</p>
            {e.review.requested_experiment && (
              <p className="text-xs text-graphite">Requested: <span className="text-ink">{e.review.requested_experiment}</span></p>
            )}
          </div>
        </div>
      );
    case "unblinded":
      return (
        <div className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Unblinded</span>
          <p className="text-sm">
            The specimen is <span className="font-medium">{e.unblinded.compound}</span>
            {e.unblinded.identity_check && <span className="text-graphite"> · {e.unblinded.identity_check}</span>}
          </p>
        </div>
      );
    case "protocol":
      return (
        <div className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Protocol</span>
          <p className="text-sm">
            <span className="font-medium">{e.protocol.final ? "Final call" : "NSC vote"}: {e.protocol.moa}</span>
            <span className="text-graphite"> · nearest-neighbour vote over the {e.protocol.wells} wells the agent chose ({e.protocol.votes}/{e.protocol.wells}) · </span>
            {e.protocol.agrees ? <span>the Scientist agrees</span>
              : <span>the Scientist dissents ({e.protocol.scientist_moa})</span>}
          </p>
        </div>
      );
    case "verdict":
      return (
        <div className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Verdict</span>
          <p className={`text-sm flex items-center gap-1.5 ${e.verdict.correct ? "text-verdict-yes" : "text-verdict-no"}`}>
            {e.verdict.correct ? <Check aria-hidden className="h-4 w-4" /> : <X aria-hidden className="h-4 w-4" />}
            {e.verdict.correct
              ? `Correct: ${e.verdict.agent_moa} is the annotated mechanism.`
              : `Incorrect: the call was ${e.verdict.agent_moa}; the annotated mechanism is ${e.verdict.truth_moa}.`}
            {e.verdict.call_source === "protocol" && e.verdict.scientist_moa !== e.verdict.agent_moa &&
              <span className="text-graphite">The Scientist's own call ({e.verdict.scientist_moa}) was {e.verdict.scientist_correct ? "right" : "wrong"}.</span>}
            <span className="text-graphite">Computed by code, not by a model.</span>
          </p>
        </div>
      );
    case "report":
      return (
        <div className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Dossier</span>
          <p className="text-sm flex items-center gap-3">
            <span>Reflection and dossier written · {e.report.citations_label}</span>
            <a className="btn h-7 text-xs" href={href({ name: "dossier", runId: run.runId })}>
              <FileText aria-hidden className="h-3.5 w-3.5" /> Open dossier
            </a>
          </p>
        </div>
      );
    case "error":
      return (
        <div role="alert" className="flex gap-3">
          <span className="w-[76px] shrink-0 font-mono text-[11px] text-ink pt-0.5 flex gap-1"><AlertTriangle aria-hidden className="h-3.5 w-3.5" />{e.fatal ? "Stopped" : "Error"}</span>
          <p className="text-sm font-medium">{e.message}</p>
        </div>
      );
  }
}

function EvLink({ id }: { id: string }) {
  return (
    <a href={`#ev-${id}`} onClick={(ev) => {
      ev.preventDefault();
      document.getElementById(`ev-${id}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }} className="chip mr-1 hover:border-ink/40">{id}</a>
  );
}

function argLine(call: ToolCallData): string {
  const a = call.args as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  switch (call.name) {
    case "acquire_well":
      return `dose ${Number(a.concentration_index) + 1}, replicate ${a.replicate}`;
    case "describe_phenotype":
      return `${(a.field_ids ?? []).length} fields`;
    case "compare_to_reference_atlas":
      return `${(a.well_ids ?? []).length} wells, ${a.k} nearest`;
    case "dose_response":
      return featureName(String(a.feature));
    case "search_literature":
      return `“${a.query}”`;
    case "lookup_compound":
      return String(a.name);
    default:
      return "";
  }
}

function ToolEntry({ call, result, step, onSelectWell }: { call: ToolCallData; result?: ToolResultData; step: number; onSelectWell: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const detail = result?.ok ? <ToolDetail result={result} onSelectWell={onSelectWell} open={open} /> : null;
  const hasMore = result?.ok && ["acquire_well", "describe_phenotype", "compare_to_reference_atlas", "search_literature"].includes(result.name);
  return (
    <div id={result?.evidence_id ? `ev-${result.evidence_id}` : undefined} tabIndex={-1} className="flex gap-3 scroll-mt-4">
      <span className="w-[76px] shrink-0 font-mono text-[11px] text-graphite pt-0.5">Step {step}</span>
      <div className="flex flex-col gap-1 min-w-0 flex-1">
        <p className="text-sm">
          <span className="font-semibold">{toolName(call.name)}</span>
          {argLine(call) && <span className="text-graphite"> · {argLine(call)}</span>}
          {result?.evidence_id && <span className="text-graphite"> · <span className="ids">{result.evidence_id}</span></span>}
        </p>
        {call.rationale && (
          <p className="font-serif text-[15px] leading-snug"><span className="font-sans text-xs text-graphite mr-1.5">Why</span>{call.rationale}</p>
        )}
        {!result && (
          <Meta><Loader2 aria-hidden className="h-3 w-3 animate-spin" />{call.name === "describe_phenotype" ? "Microscopist is looking at the fields" : "Running"}</Meta>
        )}
        {result && !result.ok && (
          <p className="text-sm flex gap-1.5"><AlertTriangle aria-hidden className="h-3.5 w-3.5 mt-0.5" />The tool refused: {result.error}</p>
        )}
        {result?.ok && !["update_hypotheses"].includes(result.name) && (
          <p className="text-sm"><span className="text-xs text-graphite mr-1.5">Result</span>{resultLine(result)}</p>
        )}
        {detail}
        {hasMore && (
          <button className="self-start text-xs text-graphite hover:text-ink flex items-center gap-0.5" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? <ChevronDown aria-hidden className="h-3 w-3" /> : <ChevronRight aria-hidden className="h-3 w-3" />}
            {open ? "Less" : "Details"}
          </button>
        )}
      </div>
    </div>
  );
}

function resultLine(r: ToolResultData): string {
  const d = r.data ?? {};
  switch (r.name) {
    case "acquire_well":
      return `${d.segmentation?.cells_measured ?? "?"} cells measured at ${uM(d.concentration_uM)}`;
    case "update_hypotheses":
      return "";
    default:
      return r.summary;
  }
}

function DeviationChip({ d }: { d: Deviation }) {
  return (
    <span className="chip" title={`${d.feature}: ${d.value} vs DMSO ${d.dmso_median}`}>
      {featureName(d.feature)} <span className="text-graphite">z {signed(d.z)}</span>
    </span>
  );
}

function ToolDetail({ result: r, onSelectWell, open }: { result: ToolResultData; onSelectWell: (id: string) => void; open: boolean }) {
  const d = r.data ?? {};
  switch (r.name) {
    case "acquire_well": {
      const devs = (d.top_deviations ?? []) as Deviation[];
      return (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1">{devs.slice(0, open ? 8 : 4).map((x) => <DeviationChip key={x.feature} d={x} />)}</div>
          <div className="flex gap-1">
            {(d.field_ids as string[]).map((f, i) => (
              <button key={f} onClick={() => onSelectWell(d.well_id)} className="bg-darkfield on-dark"
                      aria-label={`Show well field ${i + 1} in the microscope`}>
                <FieldThumb imageId={`${f}.composite`} alt={`Field ${i + 1} of this well, composite of DNA, tubulin and actin`}
                            width={64} height={51} className="stain-tint block h-[51px] w-16 object-cover" />
              </button>
            ))}
          </div>
          {open && (
            <table className="text-xs mt-1 max-w-[520px]">
              <thead><tr className="text-graphite"><th className="text-left font-normal">Feature</th><th className="text-right font-normal">Well</th><th className="text-right font-normal">DMSO median</th><th className="text-right font-normal">z</th></tr></thead>
              <tbody>
                {devs.map((x) => (
                  <tr key={x.feature}><td>{featureName(x.feature)}</td><td className="text-right">{x.value}</td><td className="text-right">{x.dmso_median}</td><td className="text-right">{signed(x.z)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      );
    }
    case "describe_phenotype": {
      const rep = d.report ?? {};
      const obs = (rep.observations ?? []) as { channel: string; finding: string; agrees_with_measurements: boolean }[];
      const cands = (rep.candidate_mechanisms ?? []) as { moa: string; why: string }[];
      return (
        <div className="flex flex-col gap-1 text-sm">
          <p className="text-xs text-graphite">Microscopist ({modelName(d.model)}) compared the fields with a same-plate DMSO control. Cell density: {rep.cell_density} than DMSO.</p>
          <ul className="flex flex-col gap-1">
            {obs.slice(0, open ? obs.length : 2).map((o, i) => (
              <li key={i} className="flex gap-2">
                <span className="w-[74px] shrink-0 text-xs flex items-center gap-1 self-start pt-0.5">
                  {o.channel in SWATCH && <span aria-hidden className={`h-2 w-2 rounded-full ${SWATCH[o.channel as Channel]}`} />}
                  {CHANNEL_LABEL[o.channel as Channel] ?? "Composite"}
                </span>
                <span className="font-serif text-[14px] leading-snug">{o.finding}
                  <span className="font-sans text-xs text-graphite"> · {o.agrees_with_measurements ? "agrees with the measurements" : "does not agree with the measurements"}</span>
                </span>
              </li>
            ))}
          </ul>
          {open && cands.length > 0 && (
            <ul className="flex flex-col gap-0.5 mt-1">
              {cands.map((c) => <li key={c.moa} className="text-xs"><span className="font-medium">{c.moa}</span> <span className="text-graphite">{c.why}</span></li>)}
            </ul>
          )}
          {open && rep.uncertainty && <p className="text-xs text-graphite">Uncertainty: {rep.uncertainty}</p>}
        </div>
      );
    }
    case "compare_to_reference_atlas": {
      if (!open) return null;
      const nb = (d.neighbors ?? []) as { compound: string; concentration_uM: number; moa: string; similarity: number }[];
      return (
        <div className="text-xs">
          <p className="text-graphite mb-1">Reference compounds, {d.similarity}; {d.excluded} excluded.</p>
          <table className="max-w-[560px] w-full">
            <thead><tr className="text-graphite"><th className="text-left font-normal">Reference</th><th className="text-left font-normal">Mechanism</th><th className="text-right font-normal">Similarity</th></tr></thead>
            <tbody>
              {nb.map((n, i) => <tr key={i}><td>{n.compound} · {uM(n.concentration_uM)}</td><td>{n.moa}</td><td className="text-right">{n.similarity.toFixed(3)}</td></tr>)}
            </tbody>
          </table>
        </div>
      );
    }
    case "dose_response": {
      const pts = (d.points ?? []) as { concentration_uM: number; median: number }[];
      return pts.length >= 2 ? <Sparkline points={pts} /> : null;
    }
    case "search_literature": {
      const arts = (d.articles ?? []) as Article[];
      return (
        <ul className="flex flex-col gap-0.5 text-xs">
          {arts.slice(0, open ? arts.length : 2).map((a) => (
            <li key={a.pmid} className="flex gap-1.5">
              <BookOpen aria-hidden className="h-3 w-3 mt-0.5 shrink-0 text-graphite" />
              <span>
                <a className="underline decoration-ink/30 hover:decoration-ink" href={a.url} target="_blank" rel="noreferrer">{a.title}</a>
                <span className="text-graphite"> {a.journal} {a.year} · PMID {a.pmid}</span>
                <ExternalLink aria-hidden className="inline h-3 w-3 ml-1 text-graphite" />
              </span>
            </li>
          ))}
        </ul>
      );
    }
    default:
      return null;
  }
}

/** Robust z of one feature against log dose, from the dose_response tool's points. */
function Sparkline({ points }: { points: { concentration_uM: number; median: number }[] }) {
  const W = 160, H = 36, P = 4;
  const xs = points.map((p) => Math.log10(p.concentration_uM));
  const ys = points.map((p) => p.median);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(0, ...ys), Math.max(0, ...ys)];
  const sx = (x: number) => P + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * P);
  const sy = (y: number) => H - P - ((y - y0) / (y1 - y0 || 1)) * (H - 2 * P);
  const path = points.map((_, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join(" ");
  return (
    <span className="flex items-center gap-2 text-xs text-graphite">
      <svg width={W} height={H} role="img" aria-label="Robust z against log dose for the acquired wells">
        <line x1={P} x2={W - P} y1={sy(0)} y2={sy(0)} stroke="currentColor" strokeOpacity={0.35} strokeDasharray="2 2" />
        <path d={path} fill="none" stroke="#17202A" strokeWidth={1.5} />
        {points.map((_, i) => <circle key={i} cx={sx(xs[i])} cy={sy(ys[i])} r={2} fill="#17202A" />)}
      </svg>
      z vs log dose · dashed line = DMSO
    </span>
  );
}
