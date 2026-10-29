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
}
interface Results {
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
    <div className="mx-auto max-w-[1040px] px-6 py-7 flex flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-serif text-[28px] leading-tight font-semibold">Benchmark</h1>
        <p className="font-serif text-[16px] text-graphite max-w-[72ch] leading-snug">
          Mechanism-of-action accuracy on the 38 annotated BBBC021 compounds, each held out from the reference atlas
          (leave-one-compound-out). Every number on this page is read from the files the evaluation scripts write.
        </p>
      </div>

      <section className="panel p-4 flex flex-col gap-3" aria-labelledby="agent-h">
        <h2 id="agent-h" className="text-sm font-medium">Agent against baselines, by budget</h2>
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

      <section className="panel p-4 flex flex-col gap-3" aria-labelledby="baseline-h">
        <h2 id="baseline-h" className="text-sm font-medium">Classical baseline: nearest reference compound</h2>
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
          <p className="text-[28px] font-medium leading-none">{pct(c.compound_accuracy)}</p>
          <p className="label mt-1">compound level · {Math.round(c.compound_accuracy * c.n_compounds)} of {c.n_compounds}</p>
        </div>
        <div>
          <p className="text-[28px] font-medium leading-none">{pct(c.treatment_accuracy)}</p>
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

/** Accuracy against wells per compound. Methods with a budget are lines; "all wells" methods are reference lines. */
function AccuracyChart({ rows }: { rows: ResultRow[] }) {
  const W = 640, H = 240, L = 44, R = 170, T = 12, B = 30;
  const series = new Map<string, ResultRow[]>();
  rows.forEach((x) => series.set(x.label, [...(series.get(x.label) ?? []), x]));
  const budgets = rows.map((x) => x.budget).filter((b): b is number => b != null);
  if (!budgets.length) return null;
  const [b0, b1] = [Math.min(...budgets), Math.max(...budgets)];
  const sx = (b: number) => L + ((b - b0) / (b1 - b0 || 1)) * (W - L - R);
  const sy = (a: number) => T + (1 - a) * (H - T - B);
  const ticks = Array.from(new Set(budgets)).sort((a, b) => a - b);
  // end-of-line labels, nudged apart vertically so close values stay readable
  const labels = [...series.entries()].map(([label, pts], i) => {
    const withBudget = pts.filter((p) => p.budget != null).sort((a, b) => a.budget! - b.budget!);
    const end = withBudget[withBudget.length - 1];
    return { label, i, x: end ? sx(end.budget!) + 8 : W - R + 6, y: sy(end ? end.accuracy : pts[0].accuracy) + 4 };
  }).sort((a, b) => a.y - b.y);
  labels.forEach((l, k) => {
    if (k > 0 && l.y - labels[k - 1].y < 13) l.y = labels[k - 1].y + 13;
  });
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
          const color = i === 0 ? "#17202A" : "#5B6672";
          const dash = DASH[i % DASH.length];
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
              <path d={d} fill="none" stroke={color} strokeWidth={i === 0 ? 2 : 1.5} strokeDasharray={dash} />
              {sorted.map((p) => <circle key={p.budget} cx={sx(p.budget!)} cy={sy(p.accuracy)} r={3} fill={color} />)}
            </g>
          );
        })}
        {labels.map((l) => <text key={l.label} x={l.x} y={l.y} fontSize={11} fill={l.i === 0 ? "#17202A" : "#5B6672"}>{l.label}</text>)}
      </svg>
    </figure>
  );
}
