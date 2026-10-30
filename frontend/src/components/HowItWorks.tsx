// The start screen's second half: what one run does, step by step (a timeline whose line draws in as it scrolls into
// view), and the benchmark headline read from eval/results.json. The step text describes the code paths; the
// numbers come from the eval files.

import { ArrowRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { href } from "../lib/router";
import { modelName } from "../lib/format";
import type { ModelIds } from "../lib/types";
import { useFetch } from "../lib/useFetch";
import { Finding, type Results } from "../screens/Benchmark";
import { Reveal } from "./Shell";

interface Step { when: string; title: string; who: string; text: string }

/** Lights the timeline once it is in view: the line grows, then each node fills in turn. */
function useOnce(): [React.RefObject<HTMLDivElement>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) return setOn(true);
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setOn(true); io.disconnect(); } },
                                        { rootMargin: "0px 0px -20% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, on];
}

export default function HowItWorks({ ids }: { ids?: ModelIds }) {
  const results = useFetch<Results>("/api/eval/results");
  const spark = modelName(ids?.scientist ?? "muse-spark-1.3");
  const wonder = modelName(ids?.reviewer ?? "muse-wonder-1.0");
  const [track, on] = useOnce();
  const steps: Step[] = [
    { when: "Before the run", title: "Sealed", who: "Opaque specimen ids",
      text: "The compound hides behind a salted id. A scanner checks every event and every model message for its name, synonyms and identifiers." },
    { when: "Turn by turn", title: "Choose a well", who: `Scientist · ${spark}`,
      text: "Nine strict-schema tools. Each acquired well costs budget, so the agent has to say why this dose or replicate separates its hypotheses." },
    { when: "Every well", title: "Measure and look", who: `Microscopist · ${spark} vision`,
      text: "Every cell is segmented and measured against same-plate DMSO; the vision model says where the pixels agree with the numbers." },
    { when: "Before unsealing", title: "Peer review", who: `PI reviewer · ${wonder}`,
      text: "A second model reads the evidence ledger and approves the call or asks for one more experiment." },
    { when: "After the call", title: "Unseal and check", who: "PubChem · ChEMBL · PubMed",
      text: "Only now do the databases unlock. Code scores the call; the dossier may cite only papers retrieved in this run." },
  ];
  return (
    <>
      <section aria-labelledby="how-h" className="px-5 lg:px-9 pt-28">
        <Reveal className="grid grid-cols-[minmax(0,1fr)_minmax(0,380px)] gap-12 items-end mb-16">
          <div>
            <p className="tag text-graphite mb-4">03 — How a run works</p>
            <h2 id="how-h" className="display text-[clamp(40px,5vw,76px)] leading-[0.98] max-w-[16ch]">
              From sealed slide to <span className="accent">verified dossier</span>.
            </h2>
          </div>
          <p className="text-graphite text-[17px] leading-relaxed max-w-[36ch]">
            Three Meta models, a classical measurement pipeline and three public databases, wired so that the agent has
            to commit before it can look anything up.
          </p>
        </Reveal>
        <div ref={track} className="relative">
          <span aria-hidden className="absolute left-0 right-0 top-[7px] h-px bg-rule" />
          <span aria-hidden className={`absolute left-0 right-0 top-[7px] h-px bg-ink origin-left transition-transform duration-[1600ms] ease-[cubic-bezier(.7,0,.2,1)] ${on ? "scale-x-100" : "scale-x-0"}`} />
          <ol className="grid grid-cols-5 gap-6">
            {steps.map((s, i) => (
              <li key={s.title} className="relative pt-11 pr-3">
                <span aria-hidden style={{ transitionDelay: `${300 + i * 260}ms` }}
                      className={`absolute left-0 top-0 h-[15px] w-[15px] rounded-full shadow-[0_0_0_5px_#EBE6DC] transition-colors duration-300
                        ${on ? "bg-ink" : "bg-paper ring-1 ring-inset ring-ink"}`} />
                <p className="tag text-graphite">{s.when}</p>
                <h3 className="display text-[clamp(24px,2.2vw,32px)] leading-[1.05] tracking-[-0.045em] mt-2 mb-1">{s.title}</h3>
                <p className="text-[13px] font-medium text-ink mb-2">{s.who}</p>
                <p className="text-[14.5px] leading-relaxed text-graphite max-w-[32ch]">{s.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {results.data && (
        <section aria-labelledby="results-h" className="px-5 lg:px-9 pt-28">
          <Reveal className="grid grid-cols-[minmax(0,1fr)_minmax(0,380px)] gap-12 items-end mb-12">
            <div>
              <p className="tag text-graphite mb-4">04 — The benchmark</p>
              <h2 id="results-h" className="display stop text-[clamp(56px,9vw,140px)]">Measured, <span className="accent">not claimed</span></h2>
            </div>
            <div className="flex flex-col gap-4 items-start">
              <p className="text-graphite text-[17px] leading-relaxed max-w-[36ch]">
                38 annotated compounds, each held out of the reference atlas, scored by code against a classical
                nearest-neighbour baseline. The numbers below are read from the eval files.
              </p>
              <a href={href({ name: "benchmark" })} className="group inline-flex items-center gap-1.5 text-[15px] font-medium">
                <span className="relative">The full benchmark<span aria-hidden className="absolute left-0 right-0 -bottom-0.5 h-px bg-current opacity-35 group-hover:opacity-100" /></span>
                <ArrowRight aria-hidden className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </a>
            </div>
          </Reveal>
          <Reveal><Finding rows={results.data.rows} /></Reveal>
        </section>
      )}
    </>
  );
}
