// Choose a specimen (spec §8 screen 1). Design v2: an editorial landing on bone paper with dark instrument frames.
// A real BBBC021 field sits in the eyepiece (top right of the hero); specimens are sealed glass slides; the budget
// is a row of wells.

import { Lock, Play, RefreshCw, Shuffle } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type React from "react";
import HowItWorks from "../components/HowItWorks";
import { Reveal } from "../components/Shell";
import { EmptyState, ErrorNote, Loading } from "../components/States";
import { ApiError, imageUrl, postJSON } from "../lib/api";
import type { Results } from "./Benchmark";
import OpenWeightsBadge from "../components/OpenWeightsBadge";
import { PROVIDER_LABEL, dateTime, isOpenWeights, modelName, providerOf, usd } from "../lib/format";
import { CHANNELS, CHANNEL_LABEL, compose, loadPixels, type Channel, type FieldPixels } from "../lib/fieldImage";
import { navigate } from "../lib/router";
import type { Health, ModelIds, RecordedRun, SpecimenCard } from "../lib/types";
import { useFetch } from "../lib/useFetch";
import { SWATCH } from "../components/run/Microscope";

const MIN_BUDGET = 2;
const MAX_BUDGET = 8;
const SPEEDS = [1, 2, 4];

// Providers the switch offers, in order. A provider is selectable when /api/health lists it (key set, model id verified).
const PROVIDER_OPTIONS = [
  { provider: "meta", title: "Meta Model API", sub: "Muse Spark 1.3" },
  { provider: "baseten", title: "Open weights", sub: "Muse Spark Open via Baseten" },
];

export function ModelLine({ ids }: { ids: ModelIds }) {
  // On open weights the run is mixed: name the provider of every model so nobody mistakes it for all-open.
  const via = (p: string) => (isOpenWeights(ids) ? ` · ${PROVIDER_LABEL[p] ?? p}` : "");
  const parts = [
    ["Scientist", modelName(ids.scientist) + via(providerOf(ids))],
    ["Microscopist", ids.microscopist ? `${modelName(ids.microscopist)} vision${via(providerOf(ids))}` : undefined],
    ["Reviewer", modelName(ids.reviewer) + via("meta")],
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
  const [provider, setProvider] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!selected && specimens.data?.length) setSelected(specimens.data[0].specimen_id);
  }, [specimens.data, selected]);

  useEffect(() => {
    if (!provider && health) setProvider(providerOf(health.model_ids));
  }, [health, provider]);

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
      const { run_id } = await postJSON<{ run_id: string }>("/api/runs", { specimen_id: selected, budget, mode: "blinded", provider: provider ?? undefined });
      navigate({ name: "run", runId: run_id });
    } catch (e) {
      setStartError((e as ApiError).message);
      setStarting(false);
    }
  };

  const live = health?.live_runs ?? false;
  const offReasons = [health?.data_error, health?.model_error].filter(Boolean) as string[];
  const chosen = specimens.data?.find((s) => s.specimen_id === selected);
  const runIds = health?.providers?.find((p) => p.provider === provider)?.model_ids ?? health?.model_ids;
  const sealed = specimens.data?.length ?? 0;
  const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="flex flex-col">
      {/* ── Hero: a dark rounded frame on the paper. The eyepiece stays top right. ───────────────────────── */}
      <section aria-labelledby="hero-h" className="dark relative mx-2.5 rounded-frame overflow-hidden instrument text-haze min-h-[calc(100vh-106px)] flex flex-col">
        <DriftingField />
        <div aria-hidden className="absolute inset-0 overflow-hidden"><div className="grain" /></div>
        <div aria-hidden className="absolute inset-0 bg-[linear-gradient(180deg,rgb(11_10_9/0.35),rgb(11_10_9/0.05)_45%,rgb(11_10_9/0.75))]" />
        <div className="relative flex-1 flex flex-col p-5 sm:p-6 lg:p-9">
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_auto] gap-8 items-start">
            <div className="flex flex-col min-w-0 pt-2">
              <p aria-hidden className="display text-haze -ml-[0.04em] whitespace-nowrap" style={{ fontSize: "clamp(72px, 12.4vw, 210px)" }}>
                MuseCell
              </p>
              <h1 id="hero-h" className="display text-[clamp(28px,3.4vw,50px)] leading-[1.02] tracking-[-0.05em] mt-4 max-w-[17ch]">
                Name the mechanism <span className="accent text-lime text-[1.12em]">before</span> you know the compound.
              </h1>
              <p className="tag mt-6 self-start inline-flex items-center gap-2.5 rounded-full bg-white/[0.06] ring-1 ring-white/10 px-3.5 py-2 backdrop-blur">
                <span aria-hidden className={`h-[7px] w-[7px] rounded-full ${live ? "bg-lime animate-[pulse-lime_1.8s_ease-out_infinite]" : "bg-mist/60"}`} />
                {live ? <>Live · <b className="font-medium text-lime">{sealed}</b> sealed specimens</> : "Replays on this server"}
              </p>
              <HeadlineNumber />
            </div>
            <aside className="pt-2 justify-self-center lg:justify-self-auto"><Eyepiece /></aside>
          </div>

          <div aria-hidden className="hidden lg:flex justify-between mt-auto pt-8 mb-7 text-mist/70">
            {[0, 1, 2, 3].map((i) => <span key={i} className="relative h-[15px] w-[15px] before:absolute before:left-[7px] before:top-0 before:h-[15px] before:w-px before:bg-current after:absolute after:top-[7px] after:left-0 after:h-px after:w-[15px] after:bg-current" />)}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,0.9fr)_auto] gap-8 items-end mt-10 lg:mt-0">
            <p className="text-[clamp(18px,1.5vw,22px)] leading-[1.25] tracking-[-0.025em] font-medium max-w-[34ch]">
              An AI scientist built on Meta's models.{" "}
              <span className="text-mist">It picks which wells of a sealed BBBC021 compound to image, measures every cell,
              defends its call to a reviewer model, and only then is the slide unsealed and checked.</span>
            </p>
            <ol className="text-[15px] font-medium leading-[1.6]">
              {["Blinded acquisition", "Peer review by a second model", "Unblinding against PubChem · ChEMBL", "A dossier with verified citations"].map((t, i) => (
                <li key={t} className="flex items-baseline gap-2.5 lg:whitespace-nowrap">
                  <span className="font-mono text-[11px] text-mist">{String(i + 1).padStart(2, "0")}</span>{t}
                </li>
              ))}
            </ol>
            <div className="flex flex-col items-start lg:items-end gap-2.5">
              <button className="btn-primary h-12 text-[15px] pl-5 pr-11" onClick={() => go("bench")}>Choose a specimen</button>
              <button className="btn h-10 px-4" onClick={() => go("recorded")}>
                <Play aria-hidden className="h-3.5 w-3.5" /> Watch a recorded run
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── The bench: choose a slide, a budget and the models ─────────────────────────────────────────── */}
      <section id="bench" aria-labelledby="bench-h" className="scroll-mt-4 px-5 lg:px-9 pt-28 pb-6">
        <Reveal className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,380px)] gap-6 md:gap-12 items-end mb-12">
          <div>
            <p className="tag text-graphite mb-4">01 — The bench</p>
            <h2 id="bench-h" className="display stop text-[clamp(56px,9vw,140px)]">Start a <span className="accent">run</span></h2>
          </div>
          <p className="text-graphite text-[17px] leading-relaxed max-w-[36ch]">
            Each slide holds one compound from the BBBC021 screen (MCF-7 cells, 24 h) and its dose series. Which one it is
            stays sealed until the agent has committed.
          </p>
        </Reveal>
      </section>
      <Reveal className="dark relative mx-2.5 rounded-frame overflow-hidden instrument text-haze">
        <div aria-hidden className="absolute inset-0 overflow-hidden"><div className="grain" /></div>
        <div className="relative grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-10 lg:gap-12 p-5 sm:p-6 lg:p-10">
          <div className="flex flex-col gap-7 min-w-0">
            <section aria-labelledby="specimens-h" className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <h3 id="specimens-h" className="tag text-mist">
                  Specimens{sealed ? <> · <span className="text-haze">{sealed} sealed</span></> : null}
                </h3>
                {!!sealed && (
                  <button className="btn h-8 text-xs ml-auto" onClick={pickRandom}>
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
              {!!sealed && (
                <div ref={rowRef} role="radiogroup" aria-label="Specimen"
                     className="flex gap-3 overflow-x-auto pb-3 pt-1 px-1 -mx-1 snap-x">
                  {specimens.data!.map((s) => (
                    <Slide key={s.specimen_id} s={s} on={s.specimen_id === selected} onKeyDown={onCardKey}
                           onClick={() => setSelected(s.specimen_id)} />
                  ))}
                </div>
              )}
            </section>

            <section className="flex flex-col gap-1.5">
              <BudgetWells budget={budget} setBudget={setBudget} />
              <p className="text-[12.5px] text-mist">
                Each well the agent images costs one unit. Looking again at wells it already has is free.
              </p>
            </section>

            {health && <ProviderSwitch health={health} provider={provider} setProvider={setProvider} />}

            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button className="btn-primary h-12 pl-5 pr-11 text-[15px]" disabled={!live || !selected || starting} onClick={start}>
                {starting ? "Starting…" : `Start blinded run${chosen ? ` on ${chosen.label}` : ""}`}
              </button>
            </div>

            {health && !live && (
              <ErrorNote title="Live runs are off on this server.">
                {offReasons.map((r) => <p key={r}>{r}</p>)}
              </ErrorNote>
            )}
            {startError && <ErrorNote title="The run did not start.">{startError}</ErrorNote>}
          </div>

          <aside className="flex flex-col gap-4 border-t lg:border-t-0 lg:border-l border-line pt-6 lg:pt-0 lg:pl-8">
            <p className="tag text-mist">Who does what</p>
            {runIds && (
              <>
                <OpenWeightsBadge ids={runIds} className="self-start" />
                <ModelLine ids={runIds} />
              </>
            )}
            <p className="text-[12.5px] leading-relaxed text-mist mt-auto">
              Every model call is metered and capped per run. A blinding guard checks every message for the compound's
              identity before it reaches a model or this page.
            </p>
          </aside>
        </div>
      </Reveal>

      {/* ── Recorded runs ─────────────────────────────────────────────────────────────────────────────── */}
      <section id="recorded" aria-labelledby="recorded-h" className="scroll-mt-4 px-5 lg:px-9 pt-28 pb-6">
        <Reveal className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,380px)] gap-6 md:gap-12 items-end mb-12">
          <div>
            <p className="tag text-graphite mb-4">02 — Replays</p>
            <h2 id="recorded-h" className="display stop text-[clamp(56px,9vw,140px)]">Watch it <span className="accent">think</span></h2>
          </div>
          <p className="text-graphite text-[17px] leading-relaxed max-w-[36ch]">
            Real live runs, replayed event by event with their original pacing, and labelled as replays throughout.
          </p>
        </Reveal>
      </section>
      <Reveal className="dark mx-2.5 rounded-frame overflow-hidden instrument text-haze">
        <RecordedRuns />
      </Reveal>

      <HowItWorks ids={health?.model_ids} />

      {/* ── The giant wordmark that closes the page ──────────────────────────────────────────────────── */}
      <div aria-hidden className="px-5 lg:px-9 pt-24 overflow-hidden">
        <p className="display whitespace-nowrap -ml-[0.04em] leading-[0.8] translate-y-[12%]" style={{ fontSize: "clamp(80px, 21vw, 360px)" }}>MuseCell</p>
      </div>
    </div>
  );
}

/**
 * Behind the hero: one real BBBC021 field, heavily blurred, drifting slowly (a 60 s loop). Decorative only; stops
 * under prefers-reduced-motion. Uses a shipped thumbnail, so it works in replay-only mode too.
 */
function DriftingField() {
  const gallery = useFetch<string[]>("/api/gallery");
  const id = gallery.data?.length ? gallery.data[Math.min(7, gallery.data.length - 1)] : null;
  if (!id) return null;
  return (
    <div aria-hidden className="absolute inset-0 overflow-hidden">
      <img src={imageUrl(id)} alt="" className="absolute -inset-[15%] h-[130%] w-[130%] max-w-none object-cover opacity-[0.5] blur-[16px] saturate-[1.3]
                                              animate-[drift_60s_ease-in-out_infinite_alternate] motion-reduce:animate-none" />
    </div>
  );
}

/** The headline result on the first screen, read from eval/results.json (never typed in by hand). */
function HeadlineNumber() {
  const results = useFetch<Results>("/api/eval/results");
  const rows = results.data?.rows ?? [];
  const agent = rows.filter((r) => r.method === "agent" && r.budget != null);
  if (!agent.length) return null;
  const a = agent.reduce((x, y) => (y.budget! > x.budget! ? y : x));
  const test = a.split_accuracy?.test;
  const deep = a.atlas?.startsWith("ResNet50") ? "_deep" : "";
  const rand = rows.find((r) => r.method === "nsc_random" + deep && r.budget === a.budget);
  return (
    <a href="#/benchmark" className="group mt-5 self-start flex flex-col sm:flex-row items-start sm:items-center gap-2 sm:gap-5 rounded-[18px] bg-white/[0.06] ring-1 ring-white/10 px-5 py-3.5 backdrop-blur hover:bg-white/[0.09] transition-colors">
      <span className="display text-lime text-[44px] leading-none">{(a.accuracy * 100).toFixed(1)}%</span>
      <span className="flex flex-col text-[13px] leading-snug">
        <span className="text-haze font-medium">
          right mechanism from {a.budget} wells the agent chose
        </span>
        <span className="text-mist">
          {a.correct}/{a.n} compounds{test != null && <> · {(test * 100).toFixed(0)}% on held-out ones</>}
          {rand && <> · random wells {(rand.accuracy * 100).toFixed(1)}%</>}
        </span>
      </span>
      <span aria-hidden className="hidden sm:inline text-mist group-hover:text-white transition-colors">→</span>
    </a>
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

/** Who runs the Scientist + Microscopist: the Meta Model API or open weights (Muse Spark Open on Baseten). */
function ProviderSwitch({ health, provider, setProvider }: {
  health: Health; provider: string | null; setProvider: (p: string) => void;
}) {
  const usable = new Set((health.providers ?? []).map((p) => p.provider));
  const why = (p: string) => health.provider_notes?.[p] ?? (p === "baseten" ? "Needs BASETEN_API_KEY in backend/.env." : "");
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const list = PROVIDER_OPTIONS.filter((o) => usable.has(o.provider));
    const i = list.findIndex((o) => o.provider === provider);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step || i < 0) return;
    e.preventDefault();
    const next = list[(i + step + list.length) % list.length];
    setProvider(next.provider);
    (e.currentTarget.parentElement?.querySelector(`[data-provider="${next.provider}"]`) as HTMLElement | null)?.focus();
  };
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3">
        <span id="provider-label" className="text-sm font-medium text-white">Models</span>
        <div role="radiogroup" aria-labelledby="provider-label" className="flex items-center gap-1 rounded-full bg-night-2 border border-line p-1 self-start">
          {PROVIDER_OPTIONS.map((o) => {
            const on = o.provider === provider;
            const ok = usable.has(o.provider);
            return (
              <button key={o.provider} data-provider={o.provider} role="radio" aria-checked={on} tabIndex={on ? 0 : -1}
                      disabled={!ok} onClick={() => setProvider(o.provider)} onKeyDown={onKey}
                      title={ok ? undefined : why(o.provider)}
                      className={`h-10 sm:h-9 px-3.5 rounded-full flex flex-col items-start justify-center leading-tight whitespace-nowrap transition-colors
                        ${on ? "bg-white/[0.1] border border-white/40" : "border border-transparent hover:border-white/20"}
                        ${ok ? "" : "opacity-40 cursor-not-allowed"}`}>
                <span className={`text-[12.5px] font-medium ${on ? "text-white" : "text-haze"}`}>{o.title}</span>
                <span className="text-[10.5px] text-mist">{o.sub}</span>
              </button>
            );
          })}
        </div>
      </div>
      <p className="text-[12px] text-mist">
        Open weights moves the Scientist and the Microscopist to Muse Spark Open on Baseten. The PI reviewer stays
        Muse Wonder 1.0 on the Meta Model API.{!usable.has("baseten") && <> Not available on this server: {why("baseten")}</>}
      </p>
    </section>
  );
}

/** Budget as wells: click a well to set the budget; −/+ keys work through the buttons. */
function BudgetWells({ budget, setBudget }: { budget: number; setBudget: (n: number) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
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
      <output aria-live="polite" className="text-sm text-white tabular-nums whitespace-nowrap">{budget} wells</output>
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
        className="group relative h-[min(360px,78vw)] w-[min(360px,78vw)] rounded-full bg-darkfield shadow-[0_0_0_1px_rgba(255,255,255,0.14),0_0_0_10px_#0E1217,0_0_0_11px_rgba(255,255,255,0.08),0_40px_80px_-20px_rgba(0,0,0,0.9)] overflow-hidden"
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
    <section aria-label="Recorded runs" className="p-5 lg:p-7 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h3 className="tag text-mist">Recorded runs</h3>
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
          {recorded.data.filter((r) => (r.budget_used ?? 0) > 0).map((r) => ( // nothing to watch before the first well
            <li key={r.run_id} className="flex items-center gap-5 py-2.5 text-sm">
              <span className="font-serif text-[16px] font-semibold text-white w-[110px] shrink-0 whitespace-nowrap">{r.specimen_label ?? "Specimen"}</span>
              <span className="text-mist whitespace-nowrap">Recorded {dateTime(r.recorded_at)}</span>
              <span className="flex items-center gap-1" aria-label={`${r.budget_used ?? "?"} of ${r.budget_total} wells`}>
                {Array.from({ length: r.budget_total }, (_, i) => (
                  <span key={i} aria-hidden className={`h-2 w-2 rounded-full ${i < (r.budget_used ?? 0) ? "bg-haze" : "well"}`} />
                ))}
                <span className="ml-1.5 text-mist whitespace-nowrap">{r.budget_used ?? "?"} of {r.budget_total} wells</span>
              </span>
              <span className="text-mist">{usd(r.cost_usd)}</span>
              <span className="text-mist truncate hidden xl:inline">{modelName(r.model_ids.scientist)} · {modelName(r.model_ids.reviewer)}</span>
              <OpenWeightsBadge ids={r.model_ids} />
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
