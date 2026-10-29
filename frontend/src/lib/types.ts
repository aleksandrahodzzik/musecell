// Event and payload types, mirrored from backend/app/agent/schemas.py and the event payloads in agent/loop.py.

export type ModelIds = Partial<Record<"scientist" | "microscopist" | "reviewer" | "segmentation", string>> &
  Record<string, string>;

export interface Hypothesis {
  moa: string;
  probability: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
}

export interface Deviation {
  feature: string;
  z: number;
  direction: "up" | "down";
  value: number;
  dmso_median: number;
}

export interface Dose {
  concentration_index: number;
  concentration_uM: number;
  replicates: number;
  acquired_replicates: number[];
}

export interface WellData {
  well_id: string;
  concentration_index: number;
  concentration_uM: number;
  replicate: number;
  field_ids: string[];
  dmso_field_ids: string[];
  segmentation?: { cells_measured: number; nuclei_method: string; cells_method: string };
  profile_z?: Record<string, number>;
  top_deviations: Deviation[];
}

export interface Article {
  pmid: string;
  title: string;
  journal: string;
  year: number | null;
  authors: string[];
  url: string;
}

export interface ChemblMechanism {
  mechanism_of_action: string;
  action_type?: string | null;
  target_name?: string | null;
  target_chembl_id?: string | null;
}

export interface Citation {
  pmid: string;
  claim: string;
}

export interface Reflection {
  text: string;
  what_was_right: string[];
  what_was_wrong: string[];
  citations: Citation[];
  next_experiment: string;
  citations_cited?: string[];
  citations_removed?: string[];
}

export interface Conclusion {
  revision?: number;
  top_moa: string;
  probability: number;
  key_evidence_ids: string[];
  next_experiment_if_this_were_a_real_lab: string;
  rationale: string;
}

export interface Review {
  round?: number;
  model?: string;
  verdict: "approve" | "request_experiment";
  critique: string;
  requested_experiment: string | null;
}

export interface Unblinded {
  compound: string;
  pubchem_cid: number | null;
  structure_url: string | null;
  chembl_mechanisms: ChemblMechanism[];
  identity_check: string | null;
  chembl_id?: string | null;
  evidence_id?: string;
}

export interface Verdict {
  correct: boolean;
  truth_moa: string;
  agent_moa: string;
}

export interface ReplayInfo {
  replay_of: string;
  recorded_at: string | null;
  model_ids: ModelIds;
}

export interface CallCost {
  purpose: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  seconds: number;
}

export interface ToolCallData {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
  rationale: string;
}

export interface ToolResultData {
  call_id: string;
  name: string;
  ok: boolean;
  error: string | null;
  evidence_id: string | null;
  kind: string | null;
  summary: string;
  image_ids: string[];
  data: Record<string, any> | null; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface EventPayloads {
  run_started: {
    specimen_id: string;
    mode: "blinded" | "open";
    budget_total: number;
    model_ids: ModelIds;
    moa_classes: string[];
    max_turns: number;
    doses: number;
    replay?: ReplayInfo;
  };
  thinking: { turn: number; phase: string; note?: string };
  tool_call: ToolCallData;
  tool_result: ToolResultData;
  hypotheses_updated: { step: number; evidence_id: string; hypotheses: Hypothesis[] };
  budget_updated: { budget_total: number; budget_used: number; budget_left: number; acquired_well_ids: string[] };
  conclusion: Conclusion;
  review: Review;
  unblinded: Unblinded;
  verdict: Verdict;
  report_ready: {
    files: { markdown: string; json: string };
    citations_label: string;
    citations_verified: string[];
    citations_removed: string[];
    reflection: Reflection;
  };
  cost_updated: { cost_usd: number; calls: number; last?: CallCost };
  error: { message: string; fatal?: boolean };
  run_finished: {
    status: "finished" | "failed";
    error: string | null;
    cost_usd?: number;
    turns?: number;
    budget_used?: number;
    verdict?: Verdict | null;
  };
}

export type EventType = keyof EventPayloads;
export const EVENT_TYPES = [
  "run_started", "thinking", "tool_call", "tool_result", "hypotheses_updated", "budget_updated", "conclusion",
  "review", "unblinded", "verdict", "report_ready", "cost_updated", "error", "run_finished",
] as const satisfies readonly EventType[];

export type RunEvent = {
  [K in EventType]: { type: K; run_id: string; seq: number; ts: string; data: EventPayloads[K] };
}[EventType];

// REST shapes
export interface Health {
  ok: boolean;
  live_runs: boolean;
  data: boolean;
  data_error: string | null;
  model_error: string | null;
  model_ids: ModelIds;
  specimens: number;
}

export interface SpecimenCard {
  specimen_id: string;
  label: string;
  doses: number;
  wells: number;
}

export interface RecordedRun {
  run_id: string;
  source: "demo" | "local";
  recorded_at: string;
  specimen_id: string;
  specimen_label: string | null;
  mode: string;
  budget_total: number;
  budget_used: number | null;
  status: string;
  cost_usd: number | null;
  model_ids: ModelIds;
}

export interface CellRow {
  label: number;
  x: number;
  y: number;
  touches_border: boolean;
  nuc_area: number;
  nuc_dna_mean: number;
  nuc_dna_integrated: number;
  nuc_solidity: number;
  cell_area: number;
  cell_roundness: number;
  tub_mean: number;
  act_mean: number;
  act_tub_ratio: number;
  flags: string[];
}

export interface CellTable {
  field_id: string;
  width: number;
  height: number;
  label_step: number;
  cells: CellRow[];
}
