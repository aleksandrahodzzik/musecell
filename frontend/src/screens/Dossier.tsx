// Dossier (spec §8 screen 4): a printable page built from runs/<id>.dossier.json, which the backend writes by code.
// Export links download the Markdown and JSON files themselves.

import { ArrowLeft, Check, Download, Printer, X } from "lucide-react";
import type { ReactNode } from "react";
import { EmptyState, ErrorNote, Loading } from "../components/States";
import { imageUrl } from "../lib/api";
import { dateTime, featureName, modelName, prob, signed, toolName, uM, usd } from "../lib/format";
import { href } from "../lib/router";
import type { Article, Conclusion, Deviation, Hypothesis, Reflection, Review, Unblinded, Verdict } from "../lib/types";
import { useFetch } from "../lib/useFetch";

interface DossierDoc {
  run_id: string;
  mode: string;
  specimen_id: string;
  question: string;
  budget: { total: number; used: number };
  experiments: { evidence_id: string; well_id: string; concentration_uM: number; replicate: number; cells_measured: number; top_deviations: Deviation[]; image_ids: string[] }[];
  steps: { step: number; tool: string; rationale: string; evidence_id: string | null; error: string | null; started_at: string }[];
  hypothesis_history: { step: number; hypotheses: Hypothesis[] }[];
  conclusions: Conclusion[];
  review: Review | null;
  unblinding: Unblinded | null;
  verdict: Verdict | null;
  reflection: Reflection | null;
  citations: Article[];
  citations_label: string;
  cost_usd: number;
  model_ids: Record<string, string>;
  turns: number;
  model_calls: unknown[];
  disclaimer: string;
}

interface Snapshot {
  started_at: string;
  evidence: { evidence_id: string; kind: string; summary: string; data: Record<string, any> }[]; // eslint-disable-line @typescript-eslint/no-explicit-any
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="print-break flex flex-col gap-2 border-t border-rule pt-4">
      <h2 className="font-serif text-[20px] font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export default function Dossier({ runId, labels }: { runId: string; labels: Record<string, string> }) {
  const doc = useFetch<DossierDoc>(`/api/runs/${encodeURIComponent(runId)}/dossier.json`);
  const snap = useFetch<Snapshot>(`/api/runs/${encodeURIComponent(runId)}`);

  if (doc.loading) return <Loading label="Loading the dossier…" />;
  if (doc.error || !doc.data) {
    return (
      <div className="max-w-[640px] mx-auto p-8">
        {doc.error?.status === 404 ? (
          <div className="panel">
            <EmptyState title="No dossier for this run yet." action={<a className="btn" href={href({ name: "run", runId })}>Back to the run</a>}>
              The dossier is written when the run finishes, after the unblinding and the literature check.
            </EmptyState>
          </div>
        ) : (
          <ErrorNote title="Could not load the dossier.">{doc.error?.message}</ErrorNote>
        )}
      </div>
    );
  }

  const d = doc.data;
  const label = labels[d.specimen_id] ?? "Specimen";
  const final = d.conclusions[d.conclusions.length - 1];
  const doseResponses = (snap.data?.evidence ?? []).filter((e) => e.kind === "dose_response");
  const cited = new Map((d.reflection?.citations ?? []).map((c) => [c.pmid, c.claim]));

  return (
    <div className="mx-auto max-w-[880px] px-6 py-6 flex flex-col gap-5 bg-sheet print:bg-white min-h-full">
      <div className="no-print flex items-center gap-2">
        <a className="btn" href={href({ name: "run", runId })}><ArrowLeft aria-hidden className="h-3.5 w-3.5" /> Back to the run</a>
        <span className="ml-auto" />
        <button className="btn" onClick={() => window.print()}><Printer aria-hidden className="h-3.5 w-3.5" /> Print</button>
        <a className="btn" href={`/api/runs/${encodeURIComponent(runId)}/dossier.md`} download><Download aria-hidden className="h-3.5 w-3.5" /> Export Markdown</a>
        <a className="btn" href={`/api/runs/${encodeURIComponent(runId)}/dossier.json`} download><Download aria-hidden className="h-3.5 w-3.5" /> Export JSON</a>
      </div>

      <header className="flex flex-col gap-1">
        <p className="text-sm text-graphite">MuseCell dossier · {d.mode} run {d.run_id}{snap.data && <> · {dateTime(snap.data.started_at)}</>}</p>
        <h1 className="font-serif text-[32px] leading-tight font-semibold">
          {d.unblinding?.compound ?? label}
          {d.unblinding && <span className="font-sans text-base font-normal text-graphite ml-2">({label} before unblinding)</span>}
        </h1>
        <p className="text-sm text-graphite">
          Scientist {modelName(d.model_ids.scientist)} · Reviewer {modelName(d.model_ids.reviewer)} · Segmentation {modelName(d.model_ids.segmentation)} ·{" "}
          {d.budget.used} of {d.budget.total} wells · {d.turns} model turns · {d.model_calls.length} model calls · {usd(d.cost_usd)}
        </p>
      </header>

      <Section title="Question">
        <p className="font-serif text-[16px]">{d.question}</p>
      </Section>

      {d.verdict && (
        <div className={`print-break rounded-panel border-2 p-3 ${d.verdict.correct ? "border-verdict-yes" : "border-verdict-no"}`}>
          <p className={`flex items-center gap-1.5 font-medium ${d.verdict.correct ? "text-verdict-yes" : "text-verdict-no"}`}>
            {d.verdict.correct ? <Check aria-hidden className="h-5 w-5" /> : <X aria-hidden className="h-5 w-5" />}
            {d.verdict.correct ? "Correct" : "Incorrect"}: the agent said {d.verdict.agent_moa}
            {final && ` (probability ${prob(final.probability)})`}; the annotated mechanism is {d.verdict.truth_moa}.
          </p>
          <p className="text-xs text-graphite mt-1">Verdict compared by code against the BBBC021 annotation, not judged by a model.</p>
        </div>
      )}

      <Section title="Experiments run">
        <table className="text-sm w-full">
          <thead className="text-xs text-graphite">
            <tr>
              <th className="text-left font-normal w-[64px] pb-1">Well</th><th className="text-left font-normal w-[92px] pb-1">Field 1</th>
              <th className="text-right font-normal w-[72px] pb-1 pr-4">Dose</th><th className="text-right font-normal w-[72px] pb-1 pr-4">Replicate</th>
              <th className="text-right font-normal w-[56px] pb-1 pr-4">Cells</th><th className="text-left font-normal pb-1">Largest changes vs same-plate DMSO (robust z)</th>
            </tr>
          </thead>
          <tbody>
            {d.experiments.map((x, i) => (
              <tr key={x.well_id} className="border-t border-rule align-top">
                <td className="py-1.5">{i + 1}<span className="block text-graphite text-xs">{x.evidence_id}</span></td>
                <td className="py-1.5">
                  {x.image_ids[0] && (
                    <img src={imageUrl(x.image_ids[0])} loading="lazy" width={72} height={58} className="stain-tint bg-darkfield w-[72px] h-[58px] object-cover"
                         alt={`Well ${i + 1}, field 1: composite of DNA, tubulin and actin`} />
                  )}
                </td>
                <td className="py-1.5 pr-4 text-right whitespace-nowrap">{uM(x.concentration_uM)}</td>
                <td className="py-1.5 pr-4 text-right">{x.replicate}</td>
                <td className="py-1.5 pr-4 text-right">{x.cells_measured}</td>
                <td className="py-1.5 text-xs leading-relaxed">
                  {x.top_deviations.map((t) => (
                    <span key={t.feature} className="mr-3 whitespace-nowrap">{featureName(t.feature)} {signed(t.z)}</span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {doseResponses.length > 0 && (
        <Section title="Dose response">
          <ul className="text-sm flex flex-col gap-0.5">
            {doseResponses.map((e) => <li key={e.evidence_id}><span className="text-graphite text-xs mr-2">{e.evidence_id}</span>{e.summary}</li>)}
          </ul>
        </Section>
      )}

      <Section title="Hypothesis history">
        {d.hypothesis_history.length === 0 ? <p className="text-sm text-graphite">The agent never posted a hypothesis board.</p> : (
          <div className="grid grid-cols-3 gap-3">
            {d.hypothesis_history.map((snapH, i) => (
              <div key={i} className="panel bg-white/60 p-2 print-break">
                <p className="text-xs text-graphite mb-1">Board {i + 1} · after step {snapH.step}</p>
                {[...snapH.hypotheses].sort((a, b) => b.probability - a.probability).slice(0, 5).map((h) => (
                  <div key={h.moa} className="text-[11px]">
                    <div className="flex justify-between gap-2"><span className="truncate">{h.moa}</span><span>{prob(h.probability)}</span></div>
                    <div className="h-1 rounded-full bg-ink/10 mb-0.5"><div className="h-1 rounded-full bg-ink" style={{ width: `${h.probability * 100}%` }} /></div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Conclusion">
        {d.conclusions.map((c, i) => (
          <div key={i} className="flex flex-col gap-0.5">
            <p className="text-sm"><span className="font-medium">{c.top_moa}</span> <span className="text-graphite">· probability {prob(c.probability)} · {i === 0 ? "first submission" : "after review"} · key evidence {c.key_evidence_ids.join(", ")}</span></p>
            <p className="font-serif text-[15px]">{c.rationale}</p>
          </div>
        ))}
      </Section>

      {d.review && (
        <Section title="Reviewer critique">
          <p className="text-sm font-medium">{d.review.verdict === "approve" ? "Approved" : "Asked for one more experiment"} · {modelName(d.model_ids.reviewer)}</p>
          <p className="font-serif text-[15px]">{d.review.critique}</p>
          {d.review.requested_experiment && <p className="text-sm text-graphite">Requested: <span className="text-ink">{d.review.requested_experiment}</span></p>}
        </Section>
      )}

      {d.unblinding && (
        <Section title="Unblinding">
          <div className="flex gap-4 items-start">
            {d.unblinding.structure_url && (
              <img src={d.unblinding.structure_url} alt={`2D structure of ${d.unblinding.compound} from PubChem`} className="w-36 h-36 object-contain bg-white border border-rule rounded-panel p-1" />
            )}
            <div className="text-sm flex flex-col gap-0.5">
              <p><span className="text-graphite">Compound</span> {d.unblinding.compound}{d.unblinding.pubchem_cid && <span className="text-graphite"> · PubChem CID {d.unblinding.pubchem_cid}</span>}</p>
              {d.unblinding.identity_check && <p className="text-graphite">Identity check: {d.unblinding.identity_check}</p>}
              <p><span className="text-graphite">ChEMBL</span>{" "}
                {d.unblinding.chembl_mechanisms.length
                  ? d.unblinding.chembl_mechanisms.map((m) => `${m.mechanism_of_action}${m.target_name ? ` (${m.target_name})` : ""}`).join("; ")
                  : "no curated mechanism"}
              </p>
            </div>
          </div>
        </Section>
      )}

      {d.reflection && (
        <Section title="Reflection">
          {d.reflection.text.split(/\n\n+/).map((p, i) => <p key={i} className="font-serif text-[16px] leading-relaxed">{p}</p>)}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div><h3 className="font-medium mb-1">What was right</h3><ul className="list-disc pl-5 flex flex-col gap-1">{d.reflection.what_was_right.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
            <div><h3 className="font-medium mb-1">What was wrong</h3><ul className="list-disc pl-5 flex flex-col gap-1">{d.reflection.what_was_wrong.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          </div>
        </Section>
      )}

      <Section title="Citations">
        <p className="text-sm text-graphite">{d.citations_label}. Only PubMed records returned by this run's own searches can be cited; anything else is removed.</p>
        {d.citations.length === 0 ? <p className="text-sm text-graphite">No citations.</p> : (
          <ol className="list-decimal pl-5 text-sm flex flex-col gap-1">
            {d.citations.map((a) => (
              <li key={a.pmid}>
                <a className="underline decoration-ink/30 hover:decoration-ink" href={a.url} target="_blank" rel="noreferrer">{a.title}</a>{" "}
                <span className="text-graphite">{a.authors.slice(0, 3).join(", ")}{a.authors.length > 3 ? " et al." : ""}. {a.journal} {a.year}. PMID {a.pmid}.</span>
                {cited.get(a.pmid) && <span className="block text-xs text-graphite">Cited for: {cited.get(a.pmid)}</span>}
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Proposed next experiment in a real lab">
        <p className="font-serif text-[16px]">{d.reflection?.next_experiment || final?.next_experiment_if_this_were_a_real_lab || "None proposed."}</p>
      </Section>

      <Section title="Steps">
        <ol className="text-xs flex flex-col gap-0.5">
          {d.steps.map((s) => (
            <li key={s.step}><span className="text-graphite w-10 inline-block">{s.step}</span>{toolName(s.tool)}{s.evidence_id && <span className="text-graphite"> · {s.evidence_id}</span>}{s.error && <span> · refused: {s.error}</span>}{s.rationale && <span className="text-graphite"> · {s.rationale}</span>}</li>
          ))}
        </ol>
      </Section>

      <p className="text-xs text-graphite border-t border-rule pt-3">{d.disclaimer}</p>
    </div>
  );
}
