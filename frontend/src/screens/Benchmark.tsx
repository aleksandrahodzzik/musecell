// Benchmark (spec §8 screen 5): reads eval/results.json (scripts/evaluate.py, Phase 6) and eval/baseline.json
// as written by the scripts. Nothing here computes or rounds a result beyond display formatting.

import { EmptyState, ErrorNote, Loading } from "../components/States";
import { dateOnly, modelName, usd } from "../lib/format";
import { useFetch } from "../lib/useFetch";

/** The shape scripts/evaluate.py writes (docs/DECISIONS.md, Phase 5). Unknown fields are ignored. */
export interface ResultRow {
  method: string;
  label: string;
  budget: number | null; // wells per compound; null = all wells (a reference line)
  accuracy: number;
  correct?: number;
  n?: number;
  mean_wells?: number | null;
  mean_cost_usd?: number | null;
  mean_latency_s?: number | null;
  citation_validity?: number | null;
  atlas?: string; // agent rows: the atlas feature space the agent searched
  space?: string; // baseline rows: "hand-crafted features" | "ResNet50 + TVN"
  nsc_on_chosen_wells?: number | null; // agent rows: NSC on exactly the wells the agent chose
  split_accuracy?: { dev?: number; test?: number };
  scientist_accuracy?: number | null; // agent v5+: the Scientist's own call (the final call is the protocol's)
}
/** scripts/well_choice.py: label-free well-choice rules vs the agent, same vote, paired bootstrap. */
interface Tally { correct: number; n: number }
interface Diff { mean: number; ci95: [number, number] }
export interface WellChoice {
  budget: number;
  n: number;
  best_rule: string;
  rows: ({ name: string } & Record<string, Tally | string>)[];
  differences: ({ vs: string; best_rule: boolean } & Record<string, Diff | string | boolean>)[];
}
export interface Results {
  well_choice?: WellChoice | null;
  generated_at?: string;
  models?: Record<string, string>;
  n_compounds?: number;
  rows: ResultRow[];
  blinding_leak_test?: string;
  notes?: string[];
}
interface Baseline {
  method: string;
  date: string;
  cosine: { treatment_accuracy: number; n_treatments: number; compound_accuracy: number; n_compounds: number };
  per_class: Record<string, { accuracy: number; n: number }>;
}

const pct = (x?: number | null) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const num = (x?: number | null, d = 1) => (x == null ? "—" : x.toFixed(d));

export default function Benchmark() {
  const results = useFetch<Results>("/api/eval/results");
  const baseline = useFetch<Baseline>("/api/eval/baseline");

  return (
    <div className="mx-auto max-w-[1040px] px-6 py-8 flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <p className="tag text-graphite">Evaluation · leave one compound out</p>
        <h1 className="display stop text-[clamp(56px,8vw,120px)]">Bench<span className="accent">mark</span></h1>
        <p className="text-[17px] text-graphite max-w-[72ch] leading-relaxed">
          Mechanism-of-action accuracy on the 38 annotated BBBC021 compounds, each held out from the reference atlas
          (leave-one-compound-out). Every number on this page is read from the files the evaluation scripts write.
        </p>
      </div>

      {results.data && <Finding rows={results.data.rows} wellChoice={results.data.well_choice} />}

      <section className="panel p-5 flex flex-col gap-3" aria-labelledby="agent-h">
        <h2 id="agent-h" className="font-serif text-[20px] font-semibold">Agent against baselines, by budget</h2>
        {results.loading && <Loading label="Loading eval/results.json…" />}
        {results.error?.status === 404 && (
          <EmptyState title="No benchmark results yet.">
            Run <code className="text-ink">python scripts/evaluate.py</code> to write eval/results.json. This page shows
            it as written; the classical baseline below is already measured.
          </EmptyState>
        )}
        {results.error && results.error.status !== 404 && <ErrorNote title="Could not read eval/results.json.">{results.error.message}</ErrorNote>}
        {results.data && <ResultsView r={results.data} />}
      </section>

      <section className="panel p-5 flex flex-col gap-3" aria-labelledby="baseline-h">
        <h2 id="baseline-h" className="font-serif text-[20px] font-semibold">Classical baseline: nearest reference compound</h2>
        {baseline.loading && <Loading label="Loading eval/baseline.json…" />}
        {baseline.error && <ErrorNote title="Could not read eval/baseline.json.">{baseline.error.message} Run python scripts/baseline_nsc.py to create it.</ErrorNote>}
        {baseline.data && <BaselineView b={baseline.data} />}
      </section>
    </div>
  );
}

function BaselineView({ b }: { b: Baseline }) {
  const c = b.cosine;
  const classes = Object.entries(b.per_class).sort((x, y) => y[1].accuracy - x[1].accuracy);
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-graphite">{b.method} · {dateOnly(b.date)}</p>
      <div className="flex gap-8">
        <div>
          <p className="font-serif text-[34px] font-semibold leading-none">{pct(c.compound_accuracy)}</p>
          <p className="label mt-1">compound level · {Math.round(c.compound_accuracy * c.n_compounds)} of {c.n_compounds}</p>
        </div>
        <div>
          <p className="font-serif text-[34px] font-semibold leading-none">{pct(c.treatment_accuracy)}</p>
          <p className="label mt-1">treatment level · {Math.round(c.treatment_accuracy * c.n_treatments)} of {c.n_treatments}</p>
        </div>
      </div>
      <table className="text-sm max-w-[560px]">
        <caption className="text-left label pb-1">Treatment-level accuracy per mechanism class</caption>
        <thead className="text-xs text-graphite"><tr><th className="text-left font-normal">Class</th><th className="text-right font-normal">Treatments</th><th className="text-right font-normal">Accuracy</th><th className="w-[140px]" /></tr></thead>
        <tbody>
          {classes.map(([moa, v]) => (
            <tr key={moa} className="border-t border-rule">
              <td className="py-1">{moa}</td>
              <td className="py-1 text-right">{v.n}</td>
              <td className="py-1 text-right">{pct(v.accuracy)}</td>
              <td className="py-1 pl-3"><div className="h-1.5 rounded-full bg-ink/10"><div className="h-1.5 rounded-full bg-ink" style={{ width: `${v.accuracy * 100}%` }} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultsView({ r }: { r: Results }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-graphite">
        {r.generated_at && <>Generated {dateOnly(r.generated_at)} · </>}
        {r.models && Object.entries(r.models).map(([k, v]) => `${k} ${modelName(v)}`).join(" · ")}
        {r.n_compounds != null && <> · {r.n_compounds} compounds</>}
        {r.blinding_leak_test && <> · blinding-leak test: {r.blinding_leak_test}</>}
      </p>
      <AccuracyChart rows={r.rows} />
      <table className="text-sm w-full">
        <thead className="text-xs text-graphite">
          <tr>
            <th className="text-left font-normal">Method</th><th className="text-right font-normal">Budget</th>
            <th className="text-right font-normal">Accuracy</th><th className="text-right font-normal">Correct</th>
            <th className="text-right font-normal">Mean wells</th><th className="text-right font-normal">Mean cost</th>
            <th className="text-right font-normal">Mean latency</th><th className="text-right font-normal">Citations valid</th>
          </tr>
        </thead>
        <tbody>
          {r.rows.map((x, i) => (
            <tr key={i} className="border-t border-rule">
              <td className="py-1">{x.label}</td>
              <td className="py-1 text-right">{x.budget ?? "all"}</td>
              <td className="py-1 text-right">{pct(x.accuracy)}</td>
              <td className="py-1 text-right">{x.correct != null && x.n != null ? `${x.correct} of ${x.n}` : "—"}</td>
              <td className="py-1 text-right">{num(x.mean_wells)}</td>
              <td className="py-1 text-right">{x.mean_cost_usd == null ? "—" : usd(x.mean_cost_usd)}</td>
              <td className="py-1 text-right">{x.mean_latency_s == null ? "—" : `${x.mean_latency_s.toFixed(0)} s`}</td>
              <td className="py-1 text-right">{pct(x.citation_validity)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.notes?.map((n, i) => <p key={i} className="text-sm text-graphite">{n}</p>)}
    </div>
  );
}

const DASH = ["", "5 4", "1.5 3", "8 3 2 3"];
const isAgent = (pts: ResultRow[]) => pts.some((p) => p.method === "agent");

/** The headline: the agent and both baselines at the largest budget the eval ran, read from the rows. */
export function Finding({ rows, wellChoice }: { rows: ResultRow[]; wellChoice?: WellChoice | null }) {
  const agent = rows.filter((x) => x.method === "agent" && x.budget != null);
  if (!agent.length) return null;
  const b = Math.max(...agent.map((x) => x.budget!));
  const a = agent.find((x) => x.budget === b)!;
  // compare against the baseline in the agent's own atlas space (deep features from agent v4 on)
  const suffix = a.atlas?.startsWith("ResNet50") && rows.some((x) => x.method === "nsc_random_deep") ? "_deep" : "";
  const rand = rows.find((x) => x.method === "nsc_random" + suffix && x.budget === b);
  const all = rows.find((x) => x.method === "nsc_all" + suffix);
  const below = rand && a.accuracy < rand.accuracy;
  const chosen: ResultRow | undefined = a.nsc_on_chosen_wells != null
    ? { ...a, accuracy: a.nsc_on_chosen_wells, correct: Math.round(a.nsc_on_chosen_wells * (a.n ?? 0)) } : undefined;
  const protocol = a.scientist_accuracy != null; // v5+: the final call is the NSC vote over the agent's wells
  const scientist: ResultRow | undefined = protocol
    ? { ...a, accuracy: a.scientist_accuracy!, correct: Math.round(a.scientist_accuracy! * (a.n ?? 0)) } : undefined;
  // the best label-free rule, scored with the same vote (present for v5+, where the agent's lever is the wells)
  const wc = wellChoice && wellChoice.budget === b ? wellChoice : null;
  const ruleTally = wc?.rows.find((r) => r.name === wc.best_rule)?.all as Tally | undefined;
  const rule: ResultRow | undefined = ruleTally
    ? { ...a, accuracy: ruleTally.correct / ruleTally.n, correct: ruleTally.correct, n: ruleTally.n } : undefined;
  const vsRule = wc?.differences.find((d) => d.best_rule)?.all as Diff | undefined;
  const vsRandom = wc?.differences.find((d) => d.vs.startsWith("random"))?.all as Diff | undefined;
  const pp = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}`;
  const tiles: [string, ResultRow | undefined, string][] = protocol ? [
    ["MuseCell", a, `final call from the ${b} wells the agent chose`],
    ...(rule ? [[`Fixed rule: ${wc!.best_rule}`, rule, `${b} wells, no model, same vote`] as [string, ResultRow, string]]
      : [["The Scientist alone", scientist, "its own conclusion, same runs"] as [string, ResultRow | undefined, string]]),
    ["NSC on random wells", rand, `${b} wells drawn at random`],
    ["NSC on all wells", all, "every well of the compound"],
  ] : [
    ["MuseCell agent", a, `its own call, ${b} wells per compound`],
    ["NSC on the agent's wells", chosen, `the ${b} wells the agent chose`],
    ["NSC on random wells", rand, `${b} wells drawn at random`],
    ["NSC on all wells", all, "every well of the compound"],
  ];
  const test = a.split_accuracy?.test;
  return (
    <section aria-label="Headline result" className="dark instrument relative overflow-hidden rounded-frame p-7 lg:p-10 flex flex-col gap-8 text-haze">
      <div aria-hidden className="absolute inset-0 overflow-hidden"><div className="grain" /></div>
      {rand && (
        <p className="relative display text-[clamp(24px,2.4vw,34px)] leading-[1.1] tracking-[-0.04em] text-white max-w-[34ch]">
          {protocol && vsRule && vsRandom ? (
            <>From {b} wells the agent chose, MuseCell is right on {a.correct} of {a.n} compounds
              {test != null && <> ({pct(test)} on held-out ones)</>}. A fixed rule, the {wc!.best_rule}, does{" "}
              {vsRule.ci95[0] > 0 ? "worse" : vsRule.ci95[1] < 0 ? "better" : "as well within noise"}; against {b} random
              wells the difference is {pp(vsRandom.mean)} points (95% interval {pp(vsRandom.ci95[0])} to {pp(vsRandom.ci95[1])}).
              {scientist && <> The Scientist's own conclusion: {pct(scientist.accuracy)}.</>}</>
          ) : protocol ? (
            <>From {b} wells chosen by the agent, MuseCell is {below ? "below" : "above"} the nearest-neighbour baseline on {b} random
              wells{all && <> and {Math.round(((all.accuracy - a.accuracy) * (a.n ?? 0)))} compound{Math.round((all.accuracy - a.accuracy) * (a.n ?? 0)) === 1 ? "" : "s"} short of using every well</>}
              {test != null && <>; {pct(test)} on held-out compounds</>}.</>
          ) : (
            <>At {b} wells the agent's own call is {below ? "below" : "at or above"} the nearest-neighbour baseline on {b} random wells
              {chosen && rand && chosen.accuracy > rand.accuracy
                ? `; the ${b} wells it chooses lift that baseline to ${pct(chosen.accuracy)}${all && chosen.accuracy >= all.accuracy ? ", as high as using every well" : ""}.`
                : "."}</>
          )}
        </p>
      )}
      <div className="relative grid grid-cols-2 lg:grid-cols-4 gap-6">
        {tiles.map(([name, row, sub], i) => row && (
          <div key={i} className={`flex flex-col gap-1 ${i ? "border-l border-line pl-6" : ""}`}>
            <span className="tag text-mist">{name}</span>
            <span className={`display text-[clamp(48px,5vw,88px)] mt-2 ${i === 0 ? "text-lime" : "text-haze"}`}>
              {pct(row.accuracy)}
            </span>
            <span className="text-xs text-mist">{sub}{row.correct != null && row.n != null ? ` · ${row.correct} of ${row.n} correct` : ""}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Accuracy against wells per compound. Methods with a budget are lines; "all wells" methods are reference lines. */
function AccuracyChart({ rows }: { rows: ResultRow[] }) {
  const W = 720, H = 250, L = 44, R = 210, T = 12, B = 30;
  const series = new Map<string, ResultRow[]>();
  rows.forEach((x) => series.set(x.label, [...(series.get(x.label) ?? []), x]));
  const budgets = rows.map((x) => x.budget).filter((b): b is number => b != null);
  if (!budgets.length) return null;
  const [b0, b1] = [Math.min(...budgets), Math.max(...budgets)];
  const sx = (b: number) => L + ((b - b0) / (b1 - b0 || 1)) * (W - L - R);
  const sy = (a: number) => T + (1 - a) * (H - T - B);
  const ticks = Array.from(new Set(budgets)).sort((a, b) => a - b);
  // end-of-line labels, nudged apart vertically so close values stay readable; a series that stops before the
  // largest budget (e.g. open weights, run at one budget only) gets one short line beside its last point instead
  const all = [...series.entries()].map(([label, pts], i) => {
    const withBudget = pts.filter((p) => p.budget != null).sort((a, b) => a.budget! - b.budget!);
    const end = withBudget[withBudget.length - 1];
    const inside = !!end && end.budget! < b1;
    return { label, i, lead: isAgent(pts), inside, x: end ? sx(end.budget!) + (inside ? 8 : 10) : W - R + 6,
             y: sy(end ? end.accuracy : pts[0].accuracy) + 4 };
  });
  const labels = all.filter((l) => !l.inside).sort((a, b) => a.y - b.y);
  labels.forEach((l, k) => {
    if (k > 0 && l.y - labels[k - 1].y < 13) l.y = labels[k - 1].y + 13;
  });
  labels.push(...all.filter((l) => l.inside));
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[720px]" role="img" aria-label="Accuracy against wells per compound for each method">
        {[0, 0.25, 0.5, 0.75, 1].map((a) => (
          <g key={a}>
            <line x1={L} x2={W - R} y1={sy(a)} y2={sy(a)} stroke="#17202A" strokeOpacity={0.08} />
            <text x={L - 6} y={sy(a) + 4} textAnchor="end" fontSize={11} fill="#5B6672">{a * 100}%</text>
          </g>
        ))}
        {ticks.map((b) => <text key={b} x={sx(b)} y={H - B + 14} textAnchor="middle" fontSize={11} fill="#5B6672">{b}</text>)}
        <text x={(L + W - R) / 2} y={H - 2} textAnchor="middle" fontSize={11} fill="#5B6672">wells per compound</text>
        {[...series.entries()].map(([label, pts], i) => {
          const lead = isAgent(pts);
          const color = lead ? "#17202A" : "#5B6672";
          const dash = lead ? "" : DASH[1 + (i % (DASH.length - 1))];
          const flat = pts.every((p) => p.budget == null);
          if (flat) {
            const y = sy(pts[0].accuracy);
            return (
              <g key={label}>
                <line x1={L} x2={W - R} y1={y} y2={y} stroke={color} strokeDasharray={dash || "2 3"} strokeWidth={1.25} />
              </g>
            );
          }
          const sorted = pts.filter((p) => p.budget != null).sort((a, b) => a.budget! - b.budget!);
          const d = sorted.map((p, j) => `${j ? "L" : "M"}${sx(p.budget!)},${sy(p.accuracy)}`).join(" ");
          return (
            <g key={label}>
              <path d={d} fill="none" stroke={color} strokeWidth={lead ? 2.5 : 1.25} strokeDasharray={dash} />
              {sorted.map((p) => <circle key={p.budget} cx={sx(p.budget!)} cy={sy(p.accuracy)} r={lead ? 4 : 2.5} fill={lead ? "#17202A" : "#F6F8F9"} stroke={color} strokeWidth={1.5} />)}
            </g>
          );
        })}
        {labels.map((l) => {
          const [name, model] = l.inside ? [l.label.replace(/^MuseCell agent, /, "")] : l.label.split(/ (?=\()/);
          return (
            <text key={l.label} x={l.x} y={l.y} fontSize={11.5} fontWeight={l.lead ? 600 : 400} fill={l.lead ? "#17202A" : "#5B6672"}>
              {name}
              {model && <tspan x={l.x} dy={13} fontSize={10.5} fontWeight={400} fill="#5B6672">{model}</tspan>}
            </text>
          );
        })}
      </svg>
    </figure>
  );
}
