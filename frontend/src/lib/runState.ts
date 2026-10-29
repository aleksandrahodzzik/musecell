// Run state as a pure reducer over SSE events (spec §5). Events are applied once, in seq order.

import type {
  CallCost, Conclusion, Dose, EventPayloads, Hypothesis, ReplayInfo, Review, RunEvent, ToolCallData,
  ToolResultData, Unblinded, Verdict, WellData,
} from "./types";

export type Phase = "connecting" | "blinded" | "review" | "unblinded" | "finished" | "failed";
export type Connection = "connecting" | "open" | "reconnecting" | "lost" | "closed" | "not_found";

export type Entry =
  | { kind: "note"; key: string; seq: number; ts: string; turn: number; text: string; phase: string }
  | { kind: "tool"; key: string; seq: number; ts: string; step: number; call: ToolCallData; result?: ToolResultData }
  | { kind: "conclusion"; key: string; seq: number; ts: string; conclusion: Conclusion }
  | { kind: "review"; key: string; seq: number; ts: string; review: Review }
  | { kind: "unblinded"; key: string; seq: number; ts: string; unblinded: Unblinded }
  | { kind: "verdict"; key: string; seq: number; ts: string; verdict: Verdict }
  | { kind: "report"; key: string; seq: number; ts: string; report: EventPayloads["report_ready"] }
  | { kind: "error"; key: string; seq: number; ts: string; message: string; fatal: boolean };

export interface AcquiredWell extends WellData {
  order: number; // 1-based acquisition order
  evidence_id: string;
}

export interface RunState {
  runId: string;
  connection: Connection;
  lastSeq: number;
  phase: Phase;
  started?: EventPayloads["run_started"];
  replay?: ReplayInfo;
  turn: number;
  working: boolean; // a model turn started and has not produced output yet
  entries: Entry[];
  toolIndex: Record<string, number>; // call_id → entries index
  evidence: Record<string, ToolResultData>;
  doses: Dose[];
  wells: AcquiredWell[];
  budget: { total: number; used: number };
  hypotheses: Hypothesis[];
  history: { step: number; hypotheses: Hypothesis[] }[];
  conclusions: Conclusion[];
  review?: Review;
  unblinded?: Unblinded;
  verdict?: Verdict;
  report?: EventPayloads["report_ready"];
  cost: { usd: number; calls: number; last?: CallCost };
  finished?: EventPayloads["run_finished"];
  fatalError?: string;
}

export const initialRunState = (runId: string): RunState => ({
  runId, connection: "connecting", lastSeq: 0, phase: "connecting", turn: 0, working: false, entries: [],
  toolIndex: {}, evidence: {}, doses: [], wells: [], budget: { total: 0, used: 0 }, hypotheses: [], history: [],
  conclusions: [], cost: { usd: 0, calls: 0 },
});

export type RunAction =
  | { type: "event"; event: RunEvent }
  | { type: "connection"; connection: Connection }
  | { type: "reset"; runId: string };

function applyEvent(s: RunState, e: RunEvent): RunState {
  const base = { key: `${e.type}-${e.seq}`, seq: e.seq, ts: e.ts };
  const next: RunState = { ...s, lastSeq: e.seq };
  switch (e.type) {
    case "run_started":
      return {
        ...next, started: e.data, replay: e.data.replay, phase: "blinded",
        budget: { total: e.data.budget_total, used: 0 },
      };
    case "thinking": {
      const phase = (["blinded", "review", "unblinded"].includes(e.data.phase) ? e.data.phase : s.phase) as Phase;
      if (!e.data.note) return { ...next, turn: e.data.turn, working: true, phase };
      return {
        ...next, turn: e.data.turn, working: false, phase,
        entries: [...s.entries, { ...base, kind: "note", turn: e.data.turn, text: e.data.note, phase: e.data.phase }],
      };
    }
    case "tool_call": {
      const step = s.entries.filter((x) => x.kind === "tool").length + 1;
      return {
        ...next, working: false,
        toolIndex: { ...s.toolIndex, [e.data.call_id]: s.entries.length },
        entries: [...s.entries, { ...base, kind: "tool", step, call: e.data }],
      };
    }
    case "tool_result": {
      const r = e.data;
      const entries = [...s.entries];
      const i = s.toolIndex[r.call_id];
      if (i !== undefined && entries[i]?.kind === "tool") entries[i] = { ...(entries[i] as Extract<Entry, { kind: "tool" }>), result: r };
      const evidence = r.evidence_id ? { ...s.evidence, [r.evidence_id]: r } : s.evidence;
      let { doses, wells } = s;
      if (r.ok && r.name === "list_available_experiments" && r.data?.doses) doses = r.data.doses as Dose[];
      if (r.ok && r.name === "acquire_well" && r.data && r.evidence_id) {
        wells = [...wells, { ...(r.data as WellData), order: wells.length + 1, evidence_id: r.evidence_id }];
      }
      return { ...next, entries, evidence, doses, wells };
    }
    case "hypotheses_updated":
      return { ...next, hypotheses: e.data.hypotheses, history: [...s.history, { step: e.data.step, hypotheses: e.data.hypotheses }] };
    case "budget_updated":
      return { ...next, budget: { total: e.data.budget_total, used: e.data.budget_used } };
    case "conclusion":
      return {
        ...next, phase: "review", conclusions: [...s.conclusions, e.data],
        entries: [...s.entries, { ...base, kind: "conclusion", conclusion: e.data }],
      };
    case "review":
      return { ...next, review: e.data, entries: [...s.entries, { ...base, kind: "review", review: e.data }] };
    case "unblinded":
      return { ...next, phase: "unblinded", unblinded: e.data, entries: [...s.entries, { ...base, kind: "unblinded", unblinded: e.data }] };
    case "verdict":
      return { ...next, verdict: e.data, entries: [...s.entries, { ...base, kind: "verdict", verdict: e.data }] };
    case "report_ready":
      return { ...next, report: e.data, entries: [...s.entries, { ...base, kind: "report", report: e.data }] };
    case "cost_updated":
      return { ...next, cost: { usd: e.data.cost_usd, calls: e.data.calls, last: e.data.last } };
    case "error":
      return {
        ...next, working: false, fatalError: e.data.fatal ? e.data.message : s.fatalError,
        entries: [...s.entries, { ...base, kind: "error", message: e.data.message, fatal: !!e.data.fatal }],
      };
    case "run_finished":
      return {
        ...next, working: false, finished: e.data, connection: "closed",
        phase: e.data.status === "finished" ? "finished" : "failed",
        cost: e.data.cost_usd != null ? { ...s.cost, usd: e.data.cost_usd } : s.cost,
      };
    default:
      return next;
  }
}

export function runReducer(s: RunState, a: RunAction): RunState {
  switch (a.type) {
    case "reset":
      return initialRunState(a.runId);
    case "connection":
      if (s.connection === "closed" && a.connection !== "closed") return s; // the run is over; ignore late noise
      return { ...s, connection: a.connection };
    case "event":
      if (a.event.seq <= s.lastSeq) return s; // a resumed stream can repeat events
      return applyEvent(s, a.event);
  }
}
