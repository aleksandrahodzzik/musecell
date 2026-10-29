// The agent's circuit: which model or instrument is working right now, and what each one has cost so far.
// Everything here is derived from the run's events (tool calls and the per-call cost ledger in cost_updated).

import { ChevronDown, Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { modelName, usd } from "../../lib/format";
import type { RunState } from "../../lib/runState";
import type { CallCost } from "../../lib/types";

type NodeId = "scientist" | "microscope" | "microscopist" | "atlas" | "reviewer" | "databases";

interface NodeInfo {
  id: NodeId;
  name: string;
  sub: string;
  stat: string;
  calls: (CallCost & { seq: number })[];
  locked?: boolean;
}

const TOOL_NODE: Record<string, NodeId> = {
  acquire_well: "microscope",
  describe_phenotype: "microscopist",
  compare_to_reference_atlas: "atlas",
  dose_response: "atlas",
  lookup_compound: "databases",
  search_literature: "databases",
};

const ROLE_OF = (purpose: string): NodeId | null =>
  purpose.startsWith("scientist") || purpose === "reflection" ? "scientist"
    : purpose === "microscopist" ? "microscopist" : purpose === "reviewer" ? "reviewer" : null;

/** The node doing work at this moment, or null when the run is idle or over. */
export function activeNode(run: RunState): NodeId | null {
  if (run.finished) return null;
  const tools = run.entries.filter((e) => e.kind === "tool");
  const pending = tools.length ? tools[tools.length - 1] : undefined;
  if (pending?.kind === "tool" && !pending.result) return TOOL_NODE[pending.call.name] ?? "scientist";
  const last = run.entries[run.entries.length - 1];
  if (last?.kind === "conclusion") return "reviewer";
  if (run.working) return "scientist";
  return null;
}

function nodes(run: RunState): NodeInfo[] {
  const ids = run.started?.model_ids;
  const by = (id: NodeId) => run.callLog.filter((c) => ROLE_OF(c.purpose) === id);
  const toolCount = (names: string[]) =>
    run.entries.filter((e) => e.kind === "tool" && names.includes(e.call.name) && e.result?.ok).length;
  const money = (cs: CallCost[]) => cs.length ? `${cs.length} ${cs.length === 1 ? "call" : "calls"} · ${usd(cs.reduce((a, c) => a + c.cost_usd, 0))}` : "no calls yet";
  const sci = by("scientist"), mic = by("microscopist"), rev = by("reviewer");
  const lit = toolCount(["search_literature"]), look = toolCount(["lookup_compound"]);
  return [
    { id: "scientist", name: "Scientist", sub: modelName(ids?.scientist), stat: money(sci), calls: sci },
    { id: "microscope", name: "Microscope", sub: "segmentation", stat: `${run.wells.length} ${run.wells.length === 1 ? "well" : "wells"}`, calls: [] },
    { id: "microscopist", name: "Microscopist", sub: `${modelName(ids?.microscopist)} vision`, stat: money(mic), calls: mic },
    { id: "atlas", name: "Atlas", sub: "statistics", stat: `${toolCount(["compare_to_reference_atlas", "dose_response"])} queries`, calls: [] },
    { id: "reviewer", name: "PI reviewer", sub: modelName(ids?.reviewer), stat: money(rev), calls: rev },
    {
      id: "databases", name: "PubChem · ChEMBL · PubMed", sub: run.unblinded || run.started?.mode === "open" ? "unlocked" : "locked while blinded",
      stat: run.unblinded || run.started?.mode === "open" ? `${look + lit} queries` : "", calls: [], locked: !run.unblinded && run.started?.mode !== "open",
    },
  ];
}

export default function AgentCircuit({ run }: { run: RunState }) {
  const active = activeNode(run);
  const [open, setOpen] = useState<NodeId | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const list = nodes(run);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(null);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div ref={ref} className="dark relative shrink-0 h-12 flex items-center justify-center gap-0 px-3 bg-night border-b border-line overflow-x-auto"
         role="group" aria-label="Who is working">
      {list.map((n, i) => {
        const on = active === n.id;
        return (
          <div key={n.id} className="flex items-center shrink-0">
            {i > 0 && <Wire live={on || active === list[i - 1].id} />}
            <button
              onClick={() => setOpen(open === n.id ? null : n.id)}
              aria-expanded={open === n.id}
              aria-label={`${n.name}, ${n.sub}: ${n.stat}${on ? ", working now" : ""}${n.locked ? ", locked while blinded" : ""}`}
              className={`group flex items-center gap-2 h-9 pl-2.5 pr-3 rounded-full border transition-all duration-300
                ${on ? "border-white/70 bg-white/[0.08] shadow-[0_0_24px_-4px_rgba(255,255,255,0.45)]"
                     : open === n.id ? "border-white/30 bg-white/[0.04]" : "border-line hover:border-white/25"}
                ${n.locked ? "opacity-60" : ""}`}
            >
              <span aria-hidden className="relative flex h-2.5 w-2.5 items-center justify-center">
                {on && <span className="absolute inline-flex h-full w-full rounded-full bg-white opacity-60 animate-ping" />}
                {n.locked ? <Lock className="h-3 w-3 text-mist" /> : (
                  <span className={`relative inline-flex h-2 w-2 rounded-full ${on ? "bg-white" : "bg-mist/40"}`} />
                )}
              </span>
              <span className="flex flex-col items-start leading-tight text-left">
                <span className={`text-[12px] font-medium ${on ? "text-white" : "text-haze"}`}>{n.name}</span>
                <span className="text-[10.5px] text-mist whitespace-nowrap">{n.sub}{n.stat && <> · {n.stat}</>}</span>
              </span>
              {n.calls.length > 0 && <ChevronDown aria-hidden className={`h-3 w-3 text-mist transition-transform ${open === n.id ? "rotate-180" : ""}`} />}
            </button>
          </div>
        );
      })}
      {open && <Details node={list.find((n) => n.id === open)!} />}
    </div>
  );
}

function Wire({ live }: { live: boolean }) {
  return (
    <span aria-hidden className="relative mx-0.5 h-px w-3.5 bg-white/15 overflow-hidden">
      {live && <span className="absolute inset-y-0 -left-2 w-2 bg-white animate-[wire_0.9s_linear_infinite]" />}
    </span>
  );
}

function Details({ node }: { node: NodeInfo }) {
  const sum = (k: "input_tokens" | "output_tokens" | "reasoning_tokens" | "seconds") => node.calls.reduce((a, c) => a + c[k], 0);
  return (
    <div className="fixed z-30 top-[124px] left-1/2 -translate-x-1/2 w-[560px] max-h-[360px] overflow-y-auto rounded-panel border border-line bg-night-2 shadow-[0_30px_60px_-20px_rgba(0,0,0,0.9)] p-4 text-haze"
         role="dialog" aria-label={`${node.name} calls`}>
      <p className="text-sm text-white font-medium">{node.name} <span className="text-mist font-normal">· {node.sub}</span></p>
      {node.calls.length === 0 ? (
        <p className="text-xs text-mist mt-2">No model calls metered for this part of the run.</p>
      ) : (
        <>
          <p className="text-xs text-mist mt-1">
            {node.calls.length} calls · {sum("input_tokens").toLocaleString("en-US")} tokens in · {sum("output_tokens").toLocaleString("en-US")} out
            ({sum("reasoning_tokens").toLocaleString("en-US")} reasoning) · {sum("seconds").toFixed(0)} s · {usd(node.calls.reduce((a, c) => a + c.cost_usd, 0))}
          </p>
          <table className="mt-3 w-full text-[11.5px]">
            <thead className="text-mist">
              <tr><th className="text-left font-normal pb-1">Call</th><th className="text-right font-normal">In</th><th className="text-right font-normal">Out</th>
                <th className="text-right font-normal">Reasoning</th><th className="text-right font-normal">Time</th><th className="text-right font-normal">Cost</th></tr>
            </thead>
            <tbody className="font-mono">
              {node.calls.map((c) => (
                <tr key={c.seq} className="border-t border-line">
                  <td className="py-1 font-sans">{c.purpose}</td>
                  <td className="py-1 text-right">{c.input_tokens.toLocaleString("en-US")}</td>
                  <td className="py-1 text-right">{c.output_tokens.toLocaleString("en-US")}</td>
                  <td className="py-1 text-right">{c.reasoning_tokens.toLocaleString("en-US")}</td>
                  <td className="py-1 text-right">{c.seconds.toFixed(1)} s</td>
                  <td className="py-1 text-right">{usd(c.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-mist">Metered per call by the backend (token counts from the Meta Model API, prices in backend/app/config.py).</p>
        </>
      )}
    </div>
  );
}
