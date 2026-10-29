// Choose a specimen (spec §8 screen 1), darkfield design: the start screen is the instrument. A real BBBC021 field
// sits in the eyepiece; specimens are sealed glass slides; the budget is a row of wells.

import { Lock, Play, RefreshCw, Shuffle } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type React from "react";
import { EmptyState, ErrorNote, Loading } from "../components/States";
import { ApiError, postJSON } from "../lib/api";
import { dateTime, modelName, usd } from "../lib/format";
import { CHANNELS, CHANNEL_LABEL, compose, loadPixels, type Channel, type FieldPixels } from "../lib/fieldImage";
import { navigate } from "../lib/router";
import type { Health, ModelIds, RecordedRun, SpecimenCard } from "../lib/types";
import { useFetch } from "../lib/useFetch";
import { SWATCH } from "../components/run/Microscope";

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
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
      {parts.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-graphite dark:text-mist">{k}</dt>
          <dd className="text-ink dark:text-haze">{v}</dd>
        </div>
      ))}
    </dl>
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
  const recordedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!selected && specimens.data?.length) setSelected(specimens.data[0].specimen_id);
  }, [specimens.data, selected]);

  useEffect(() => {
    if (showRecorded) recordedRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [showRecorded]);

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
  const chosen = specimens.data?.find((s) => s.specimen_id === selected);

  return (
    <div className="mx-auto max-w-[1180px] px-8 pt-7 pb-10 flex flex-col gap-8 text-haze">
      <div className="grid grid-cols-[minmax(0,1fr)_400px] gap-12 items-start">
        <div className="flex flex-col gap-5 min-w-0">
          <div className="flex flex-col gap-3">
            <h1 className="font-serif text-[46px] leading-[1.04] font-semibold tracking-[-0.015em] text-white max-w-[19ch]">
              Name the mechanism before you know the compound.
            </h1>
            <p className="font-serif text-[16.5px] leading-[1.6] text-mist max-w-[64ch]">
              Each slide holds one compound from the BBBC021 screen (MCF-7 cells, 24 h) and its dose series. The agent
              chooses which wells to image, measures the cells, and commits to a mechanism. Only after a second model
              has reviewed the call is the slide unsealed and checked against chemistry databases and the literature.
            </p>
          </div>

          <section aria-labelledby="specimens-h" className="flex flex-col gap-2.5">
            <div className="flex items-center gap-3">
              <h2 id="specimens-h" className="text-sm font-medium text-white">
                Specimens{specimens.data?.length ? <span className="text-mist font-normal"> · {specimens.data.length} sealed</span> : null}
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
                  recorded run.
                </EmptyState>
              </div>
            )}
            {!!specimens.data?.length && (
              <div ref={rowRef} role="radiogroup" aria-label="Specimen"
                   className="flex gap-3 overflow-x-auto pb-3 pt-1 px-1 -mx-1 snap-x">
                {specimens.data.map((s) => (
                  <Slide key={s.specimen_id} s={s} on={s.specimen_id === selected} onKeyDown={onCardKey}
                         onClick={() => setSelected(s.specimen_id)} />
                ))}
              </div>
            )}
          </section>

          <section className="flex flex-col gap-1.5">
            <BudgetWells budget={budget} setBudget={setBudget} />
            <p className="text-[12px] text-mist">
              Each well the agent images costs one unit. Looking again at wells it already has is free.
            </p>
          </section>

          <div className="flex flex-wrap items-center gap-3">
            <button className="btn-primary h-11 px-5 text-[15px]" disabled={!live || !selected || starting} onClick={start}>
              {starting ? "Starting…" : `Start blinded run${chosen ? ` on ${chosen.label}` : ""}`}
            </button>
            <button className="btn h-11 px-4 text-[15px]" aria-expanded={showRecorded} onClick={() => setShowRecorded((v) => !v)}>
              Watch a recorded run
            </button>
          </div>

          {health && !live && (
            <ErrorNote title="Live runs are off on this server.">
              {offReasons.map((r) => <p key={r}>{r}</p>)}
            </ErrorNote>
          )}
          {startError && <ErrorNote title="The run did not start.">{startError}</ErrorNote>}
        </div>

        <aside className="flex flex-col gap-5 pt-2">
          <Eyepiece />
          {health && (
            <div className="border-t border-line pt-4">
              <ModelLine ids={health.model_ids} />
            </div>
          )}
        </aside>
      </div>

      {showRecorded && <div ref={recordedRef}><RecordedRuns /></div>}
    </div>
  );
}

/** A sealed specimen as a glass slide: frosted label on the left, the coverslip on the right. */
function Slide({ s, on, onClick, onKeyDown }: {
  s: SpecimenCard; on: boolean; onClick: () => void; onKeyDown: (e: KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const n = s.label.replace(/^Specimen\s*/, "");
  return (
    <button
      data-id={s.specimen_id}
      role="radio"
      aria-checked={on}
      aria-label={`${s.label}, ${s.doses} doses, ${s.wells} wells, mechanism hidden`}
      tabIndex={on ? 0 : -1}
      onKeyDown={onKeyDown}
      onClick={onClick}
      className={`group snap-start shrink-0 w-[208px] h-[72px] flex rounded-[5px] overflow-hidden text-left transition-all duration-200
        ${on
          ? "ring-1 ring-white/80 shadow-[0_0_0_4px_rgba(255,255,255,0.08),0_12px_30px_-10px_rgba(0,0,0,0.9)] -translate-y-0.5"
          : "ring-1 ring-white/[0.12] hover:ring-white/30"}`}
    >
      <span className={`w-[78px] shrink-0 flex flex-col justify-center px-2.5 transition-colors ${on ? "bg-sheet" : "bg-paper/85 group-hover:bg-paper"}`}>
        <span className="text-[10px] text-graphite leading-none">Specimen</span>
        <span className="font-serif text-[26px] font-semibold leading-none text-ink mt-1">{n}</span>
      </span>
      <span className="relative flex-1 bg-gradient-to-br from-white/[0.07] to-white/[0.02] px-3.5 py-2.5 flex flex-col justify-between">
        <span aria-hidden className="absolute inset-1.5 rounded-[2px] border border-white/[0.07]" />
        <span className="relative text-[11px] text-haze">{s.doses} doses · {s.wells} wells</span>
        <span className="relative flex items-center gap-1 text-[11px] text-mist">
          <Lock aria-hidden className="h-3 w-3" /> Sealed
        </span>
      </span>
    </button>
  );
}

/** Budget as wells: click a well to set the budget; −/+ keys work through the buttons. */
function BudgetWells({ budget, setBudget }: { budget: number; setBudget: (n: number) => void }) {
  return (
    <div className="flex items-center gap-4">
      <span id="budget-label" className="text-sm font-medium text-white">Budget</span>
      <div role="radiogroup" aria-labelledby="budget-label" className="flex items-center gap-1.5 rounded-full bg-night-2 border border-line px-2.5 py-2">
        {Array.from({ length: MAX_BUDGET }, (_, i) => {
          const n = i + 1;
          const filled = n <= budget;
          const disabled = n < MIN_BUDGET;
          return (
            <button
              key={n}
              role="radio"
              aria-checked={n === budget}
              aria-label={`${n} wells`}
              disabled={disabled}
              tabIndex={n === budget ? 0 : -1}
              onClick={() => setBudget(n)}
              onKeyDown={(e) => {
                const d = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
                if (!d) return;
                e.preventDefault();
                const next = Math.min(MAX_BUDGET, Math.max(MIN_BUDGET, budget + d));
                setBudget(next);
                (e.currentTarget.parentElement?.children[next - 1] as HTMLElement | undefined)?.focus();
              }}
              className={`h-5 w-5 rounded-full transition-all duration-150 ${
                filled ? "bg-haze shadow-[0_0_10px_rgba(255,255,255,0.35)]" : "well hover:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.3)]"
              } ${disabled ? "cursor-default" : ""}`}
            />
          );
        })}
      </div>
      <output aria-live="polite" className="text-sm text-white tabular-nums w-[60px]">{budget} wells</output>
    </div>
  );
}

/**
 * A real field from the recorded runs, seen through an eyepiece with a reticle. Move the pointer to pan across the
 * field, switch the three stains on and off, or click for another field.
 */
function Eyepiece() {
  const gallery = useFetch<string[]>("/api/gallery");
  const ids = gallery.data ?? [];
  const startAt = useMemo(() => Math.floor(Math.random() * 1000), []);
  const [k, setK] = useState(0);
  const [enabled, setEnabled] = useState<Record<Channel, boolean>>({ dna: true, tubulin: true, actin: true });
  const [px, setPx] = useState<FieldPixels | null>(null);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const canvas = useRef<HTMLCanvasElement>(null);
  const id = ids.length ? ids[(startAt + k) % ids.length] : null;
  const fieldId = id?.replace(".composite", "");

  useEffect(() => {
    if (!fieldId) return;
    let live = true;
    setPx(null);
    loadPixels(fieldId).then((p) => live && setPx(p)).catch(() => live && setPx(null));
    return () => { live = false; };
  }, [fieldId]);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !px) return;
    c.width = px.width;
    c.height = px.height;
    c.getContext("2d")!.putImageData(compose(px, enabled), 0, 0);
  }, [px, enabled]);

  const onMove = (e: React.PointerEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setPan({ x: ((e.clientX - r.left) / r.width - 0.5) * -2, y: ((e.clientY - r.top) / r.height - 0.5) * -2 });
  };

  return (
    <figure className="flex flex-col items-center gap-3">
      <button
        onClick={() => setK((x) => x + 1)}
        onPointerMove={onMove}
        onPointerLeave={() => setPan({ x: 0, y: 0 })}
        disabled={ids.length < 2}
        aria-label="Look at another field"
        className="group relative h-[360px] w-[360px] rounded-full bg-darkfield shadow-[0_0_0_1px_rgba(255,255,255,0.14),0_0_0_10px_#0E1217,0_0_0_11px_rgba(255,255,255,0.08),0_40px_80px_-20px_rgba(0,0,0,0.9)] overflow-hidden"
      >
        <span aria-hidden={!px} className={`absolute inset-0 flex items-center justify-center transition-[opacity,transform] duration-500 ease-out ${px ? "opacity-100" : "opacity-0"}`}
              style={{ transform: `translate(${pan.x * 12}%, ${pan.y * 12}%) scale(1.4)` }}>
          <canvas ref={canvas} role="img" aria-label="A field of MCF-7 cells from BBBC021 with the selected stains"
                  className="h-full w-auto max-w-none" />
        </span>
        <Reticle />
        <span aria-hidden className="absolute inset-0 rounded-full bg-[radial-gradient(circle_at_50%_50%,transparent_58%,rgba(0,0,0,0.55)_80%,#000_100%)]" />
        <span className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-1 rounded-full bg-black/60 px-2.5 py-1 text-[11px] text-haze opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity">
          <RefreshCw aria-hidden className="h-3 w-3" /> Another field
        </span>
      </button>
      <figcaption className="flex flex-col items-center gap-2 text-center">
        <span role="group" aria-label="Stains" className="flex items-center gap-1.5">
          {CHANNELS.map((c) => (
            <button key={c} aria-pressed={enabled[c]} onClick={() => setEnabled((e) => ({ ...e, [c]: !e[c] }))}
                    className={`flex items-center gap-1.5 h-7 px-2.5 rounded-full border text-[12px] transition-colors ${
                      enabled[c] ? "border-white/30 text-white bg-white/[0.06]" : "border-line text-mist hover:text-haze"}`}>
              <span aria-hidden className={`h-2 w-2 rounded-full ${SWATCH[c]} ${enabled[c] ? "shadow-[0_0_8px_currentColor]" : "opacity-30"}`} />
              {CHANNEL_LABEL[c]}
            </button>
          ))}
        </span>
        <span className="text-[11px] text-mist">
          A real BBBC021 field from a recorded run{fieldId ? <> · <span className="ids">{fieldId}</span></> : null}
        </span>
      </figcaption>
    </figure>
  );
}

function Reticle() {
  const ticks = Array.from({ length: 21 }, (_, i) => i - 10);
  return (
    <svg aria-hidden viewBox="0 0 380 380" className="absolute inset-0 h-full w-full pointer-events-none">
      <g stroke="white" strokeOpacity="0.22" strokeWidth="0.75">
        <line x1="190" y1="40" x2="190" y2="340" />
        <line x1="40" y1="190" x2="340" y2="190" />
        {ticks.map((t) => (
          <line key={t} x1={190 + t * 12} x2={190 + t * 12} y1={190 - (t % 5 === 0 ? 7 : 3.5)} y2={190 + (t % 5 === 0 ? 7 : 3.5)} />
        ))}
      </g>
      <circle cx="190" cy="190" r="186" fill="none" stroke="white" strokeOpacity="0.1" />
    </svg>
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
    <section aria-labelledby="recorded-h" className="panel p-5 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h2 id="recorded-h" className="font-serif text-[20px] font-semibold text-white">Recorded runs</h2>
        <span className="label">Real runs, replayed with their original pacing. Labelled as replays throughout.</span>
        <label className="ml-auto flex items-center gap-2 text-xs text-mist">
          Speed
          <select value={speed} onChange={(e) => setSpeed(+e.target.value)}
                  className="h-7 rounded-control border border-line bg-night-3 px-1.5 text-xs text-haze">
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
        <ul className="flex flex-col divide-y divide-white/[0.07]">
          {recorded.data.map((r) => (
            <li key={r.run_id} className="flex items-center gap-5 py-2.5 text-sm">
              <span className="font-serif text-[16px] font-semibold text-white w-[120px]">{r.specimen_label ?? "Specimen"}</span>
              <span className="text-mist">Recorded {dateTime(r.recorded_at)}</span>
              <span className="flex items-center gap-1" aria-label={`${r.budget_used ?? "?"} of ${r.budget_total} wells`}>
                {Array.from({ length: r.budget_total }, (_, i) => (
                  <span key={i} aria-hidden className={`h-2 w-2 rounded-full ${i < (r.budget_used ?? 0) ? "bg-haze" : "well"}`} />
                ))}
                <span className="ml-1.5 text-mist">{r.budget_used ?? "?"} of {r.budget_total} wells</span>
              </span>
              <span className="text-mist">{usd(r.cost_usd)}</span>
              <span className="text-mist truncate">{modelName(r.model_ids.scientist)} · {modelName(r.model_ids.reviewer)}</span>
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
