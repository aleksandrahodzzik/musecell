// The start screen's second half: what one run does, step by step, and the benchmark headline read from
// eval/results.json. The step text describes the code paths; the numbers come from the eval files.

import { ArrowRight, BookOpen, Eye, FlaskConical, Lock, MessagesSquare } from "lucide-react";
import type { ReactNode } from "react";
import { href } from "../lib/router";
import { modelName } from "../lib/format";
import type { ModelIds } from "../lib/types";
import { useFetch } from "../lib/useFetch";
import { Finding, type Results } from "../screens/Benchmark";

function Step({ n, icon, title, who, children }: { n: number; icon: ReactNode; title: string; who: string; children: ReactNode }) {
  return (
    <li className="relative flex flex-col gap-2.5 rounded-panel border border-line bg-night-2/70 p-4 pt-5">
      <span className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full border border-white/20 bg-night text-haze">{icon}</span>
        <span className="font-mono text-[11px] text-mist">{String(n).padStart(2, "0")}</span>
      </span>
      <h3 className="font-serif text-[18px] leading-tight font-semibold text-white">{title}</h3>
      <p className="text-[11.5px] text-haze">{who}</p>
      <p className="text-[13px] leading-relaxed text-mist">{children}</p>
    </li>
  );
}

export default function HowItWorks({ ids }: { ids?: ModelIds }) {
  const results = useFetch<Results>("/api/eval/results");
  const spark = modelName(ids?.scientist ?? "muse-spark-1.3");
  const wonder = modelName(ids?.reviewer ?? "muse-wonder-1.0");
  return (
    <section aria-labelledby="how-h" className="flex flex-col gap-6 border-t border-line pt-10">
      <div className="flex items-end gap-6">
        <h2 id="how-h" className="font-serif text-[32px] leading-tight font-semibold text-white max-w-[22ch]">
          One run, from sealed slide to verified dossier
        </h2>
        <p className="ml-auto max-w-[46ch] text-[14px] leading-relaxed text-mist">
          Three Meta models, a classical measurement pipeline and three public databases, wired so that the agent has to
          commit before it can look anything up.
        </p>
      </div>
      <ol className="grid grid-cols-5 gap-3">
        <Step n={1} icon={<Lock className="h-4 w-4" />} title="Sealed" who="Opaque specimen ids">
          The compound is hidden behind a salted id. A scanner checks every event and every model message for its name,
          synonyms and identifiers.
        </Step>
        <Step n={2} icon={<FlaskConical className="h-4 w-4" />} title="Choose the next well" who={`Scientist · ${spark}`}>
          Nine strict-schema tools. Each acquired well costs budget, so the agent has to say why this dose or replicate
          separates its hypotheses.
        </Step>
        <Step n={3} icon={<Eye className="h-4 w-4" />} title="Measure and look" who={`Microscopist · ${spark} vision`}>
          Every cell is segmented and measured against same-plate DMSO. The vision model describes the fields and says
          where the pixels agree with the numbers.
        </Step>
        <Step n={4} icon={<MessagesSquare className="h-4 w-4" />} title="Peer review" who={`PI reviewer · ${wonder}`}>
          A second model reads the evidence ledger and either approves the conclusion or asks for one more experiment.
        </Step>
        <Step n={5} icon={<BookOpen className="h-4 w-4" />} title="Unseal and check" who="PubChem · ChEMBL · PubMed">
          Only now do the databases unlock. Code scores the call; the dossier may cite only papers the agent retrieved in
          this run.
        </Step>
      </ol>
      {results.data && (
        <div className="flex flex-col gap-3">
          <Finding rows={results.data.rows} />
          <a href={href({ name: "benchmark" })} className="self-start flex items-center gap-1.5 text-sm text-haze hover:text-white">
            The full benchmark and where the agent goes wrong <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </a>
        </div>
      )}
    </section>
  );
}
