import { Lock, Minus, Play, Plus, Shuffle } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { EmptyState, ErrorNote, Loading } from "../components/States";
import { ApiError, postJSON } from "../lib/api";
import { dateTime, modelName, usd } from "../lib/format";
import { navigate } from "../lib/router";
import type { Health, ModelIds, RecordedRun, SpecimenCard } from "../lib/types";
import { useFetch } from "../lib/useFetch";

const MIN_BUDGET = 2;
const MAX_BUDGET = 8;
const SPEEDS = [1, 2, 4];

export function ModelLine({ ids }: { ids: ModelIds }) {
  const parts = [
    ["Scientist", modelName(ids.scientist)],
    ["Microscopist", ids.microscopist ? `${modelName(ids.microscopist)} vision` : undefined],
    ["Reviewer", modelName(ids.reviewer)],
    ["Segmentation", modelName(ids.segmentation)],
  ].filter(([, v]) => v);
  return (
    <p className="text-sm text-graphite">
      {parts.map(([k, v], i) => (
        <span key={k}>
          {i > 0 && <span aria-hidden> · </span>}
          {k}: <span className="text-ink">{v}</span>
        </span>
      ))}
    </p>
  );
}

export default function ChooseSpecimen({ health }: { health?: Health; healthError?: ApiError }) {
  const specimens = useFetch<SpecimenCard[]>("/api/specimens");
  const [selected, setSelected] = useState<string | null>(null);
  const [budget, setBudget] = useState(4);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [showRecorded, setShowRecorded] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!selected && specimens.data?.length) setSelected(specimens.data[0].specimen_id);
  }, [specimens.data, selected]);

  const pickRandom = () => {
    const list = specimens.data ?? [];
    if (!list.length) return;
    const s = list[Math.floor(Math.random() * list.length)];
    setSelected(s.specimen_id);
    rowRef.current?.querySelector<HTMLElement>(`[data-id="${s.specimen_id}"]`)?.scrollIntoView({ block: "nearest", inline: "center" });
  };

  // radiogroup keyboard pattern: one tab stop, arrow keys move the selection
  const onCardKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const list = specimens.data ?? [];
    const i = list.findIndex((s) => s.specimen_id === selected);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    const j = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : step ? Math.min(list.length - 1, Math.max(0, i + step)) : -1;
    if (j < 0) return;
    e.preventDefault();
    setSelected(list[j].specimen_id);
    const el = rowRef.current?.querySelector<HTMLElement>(`[data-id="${list[j].specimen_id}"]`);
    el?.focus();
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };

  const start = async () => {
    if (!selected) return;
    setStarting(true);
    setStartError(null);
    try {
      const { run_id } = await postJSON<{ run_id: string }>("/api/runs", { specimen_id: selected, budget, mode: "blinded" });
      navigate({ name: "run", runId: run_id });
    } catch (e) {
      setStartError((e as ApiError).message);
      setStarting(false);
    }
  };

  const live = health?.live_runs ?? false;
  const offReasons = [health?.data_error, health?.model_error].filter(Boolean) as string[];

  return (
    <div className="mx-auto max-w-[1040px] px-6 pt-7 pb-6 flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-serif text-[28px] leading-tight font-semibold">Choose a specimen</h1>
        <p className="font-serif text-[16px] text-graphite max-w-[70ch] leading-snug">
          Each specimen is one compound from the BBBC021 screen (MCF-7 cells, 24 h) with its dose series. The agent sees
          images and measurements only. The compound and its mechanism stay sealed until it has committed to a
          conclusion and a second model has reviewed it.
        </p>
      </div>

      <section aria-labelledby="specimens-h" className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <h2 id="specimens-h" className="text-sm font-medium">
            Specimens{specimens.data?.length ? <span className="text-graphite font-normal"> · {specimens.data.length} sealed</span> : null}
          </h2>
          {!!specimens.data?.length && (
            <button className="btn h-7 text-xs ml-auto" onClick={pickRandom}>
              <Shuffle aria-hidden className="h-3.5 w-3.5" /> Pick one at random
            </button>
          )}
        </div>
        {specimens.loading && <Loading label="Loading specimens…" />}
        {specimens.error && <ErrorNote title="Could not load specimens.">{specimens.error.message}</ErrorNote>}
        {specimens.data && specimens.data.length === 0 && (
          <div className="panel">
            <EmptyState title="No specimens on this machine.">
              Live runs need the BBBC021 subset under DATA_DIR (see the README quickstart). You can still watch a
              recorded run below.
            </EmptyState>
          </div>
        )}
        {!!specimens.data?.length && (
          <div ref={rowRef} role="radiogroup" aria-label="Specimen" className="flex gap-2.5 overflow-x-auto pb-2 snap-x">
            {specimens.data.map((s) => {
              const on = s.specimen_id === selected;
              return (
                <button
                  key={s.specimen_id}
                  data-id={s.specimen_id}
                  role="radio"
                  aria-checked={on}
                  tabIndex={on ? 0 : -1}
                  onKeyDown={onCardKey}
                  onClick={() => setSelected(s.specimen_id)}
                  className={`snap-start shrink-0 w-[150px] text-left rounded-panel p-3 bg-sheet transition-colors ${
                    on ? "border-2 border-ink" : "border border-rule hover:border-ink/40 m-px"
                  }`}
                >
                  <span className="block font-serif text-[17px] font-semibold">{s.label}</span>
                  <span className="block text-xs text-graphite mt-0.5">{s.doses} doses · {s.wells} wells</span>
                  <span className="mt-3 flex items-center gap-1 text-xs text-graphite">
                    <Lock aria-hidden className="h-3 w-3" /> Mechanism hidden
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </section>

      <section className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="flex flex-col gap-1.5">
          <span id="budget-label" className="text-sm font-medium">Budget</span>
          <div className="flex items-center gap-1" role="group" aria-labelledby="budget-label">
            <button className="btn w-8 px-0 justify-center" aria-label="Fewer wells" disabled={budget <= MIN_BUDGET}
                    onClick={() => setBudget((b) => Math.max(MIN_BUDGET, b - 1))}>
              <Minus aria-hidden className="h-4 w-4" />
            </button>
            <output aria-live="polite" className="w-[76px] text-center text-sm">{budget} wells</output>
            <button className="btn w-8 px-0 justify-center" aria-label="More wells" disabled={budget >= MAX_BUDGET}
                    onClick={() => setBudget((b) => Math.min(MAX_BUDGET, b + 1))}>
              <Plus aria-hidden className="h-4 w-4" />
            </button>
          </div>
        </div>
        <p className="text-sm text-graphite max-w-[46ch] pb-1">
          Each well the agent images costs one unit. Looking again at wells it already has is free.
        </p>
      </section>

      {health && <ModelLine ids={health.model_ids} />}

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={!live || !selected || starting} onClick={start}>
          {starting ? "Starting…" : "Start blinded run"}
        </button>
        <button className="btn h-9" aria-expanded={showRecorded} onClick={() => setShowRecorded((v) => !v)}>
          Watch a recorded run
        </button>
      </div>

      {health && !live && (
        <ErrorNote title="Live runs are off on this server.">
          {offReasons.map((r) => <p key={r}>{r}</p>)}
        </ErrorNote>
      )}
      {startError && <ErrorNote title="The run did not start.">{startError}</ErrorNote>}

      {showRecorded && <RecordedRuns />}
    </div>
  );
}

function RecordedRuns() {
  const recorded = useFetch<RecordedRun[]>("/api/recorded");
  const [speed, setSpeed] = useState(2);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const play = async (runId: string) => {
    setBusy(runId);
    setError(null);
    try {
      const r = await postJSON<{ run_id: string }>(`/api/runs/${encodeURIComponent(runId)}/replay?speed=${speed}`);
      navigate({ name: "run", runId: r.run_id });
    } catch (e) {
      setError((e as ApiError).message);
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="recorded-h" className="panel p-4 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h2 id="recorded-h" className="text-sm font-medium">Recorded runs</h2>
        <span className="label">Real runs, replayed with their original pacing. Labelled as replays throughout.</span>
        <label className="ml-auto flex items-center gap-2 text-xs text-graphite">
          Speed
          <select value={speed} onChange={(e) => setSpeed(+e.target.value)}
                  className="h-7 rounded-control border border-rule bg-white px-1.5 text-xs text-ink">
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </label>
      </div>
      {recorded.loading && <Loading label="Loading recorded runs…" />}
      {recorded.error && <ErrorNote title="Could not list recorded runs.">{recorded.error.message}</ErrorNote>}
      {error && <ErrorNote title="The replay did not start.">{error}</ErrorNote>}
      {recorded.data?.length === 0 && (
        <EmptyState title="No recorded runs on this server.">
          Finish a live run and it becomes replayable here. Recorded demo runs ship in demo/runs/.
        </EmptyState>
      )}
      {!!recorded.data?.length && (
        <ul className="flex flex-col divide-y divide-ink/10">
          {recorded.data.map((r) => (
            <li key={r.run_id} className="flex items-center gap-4 py-2 text-sm">
              <span className="font-serif font-semibold w-[110px]">{r.specimen_label ?? "Specimen"}</span>
              <span className="text-graphite">Recorded {dateTime(r.recorded_at)}</span>
              <span className="text-graphite">{r.budget_used ?? "?"} of {r.budget_total} wells</span>
              <span className="text-graphite">{usd(r.cost_usd)}</span>
              <span className="text-graphite truncate">{modelName(r.model_ids.scientist)} · {modelName(r.model_ids.reviewer)}</span>
              {r.status !== "finished" && <span className="chip">Ended early</span>}
              <button className="btn ml-auto" disabled={busy !== null} onClick={() => play(r.run_id)}
                      aria-label={`Replay ${r.specimen_label ?? "run"} recorded ${dateTime(r.recorded_at)}`}>
                <Play aria-hidden className="h-3.5 w-3.5" /> {busy === r.run_id ? "Starting…" : "Replay"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
