// The unblinding (spec §8 screen 3): the app's one orchestrated animation, about 1.2 s. The specimen label
// resolves to the compound, the PubChem structure fades in, ChEMBL mechanism lines appear, then the verdict.
// With prefers-reduced-motion everything is shown at once.

import { motion, useReducedMotion, type Variants } from "framer-motion";
import { Check, FileText, X } from "lucide-react";
import type { ReactNode } from "react";
import { href } from "../../lib/router";
import type { EventPayloads, Unblinded, Verdict } from "../../lib/types";

interface Props {
  runId: string;
  specimenLabel: string;
  unblinded: Unblinded;
  verdict?: Verdict;
  report?: EventPayloads["report_ready"];
  probability?: number;
}

const ease = [0.2, 0.7, 0.2, 1] as const;
const beat = (delay: number, duration = 0.3): Variants => ({
  hidden: { opacity: 0, y: 6 },
  shown: { opacity: 1, y: 0, transition: { delay, duration, ease } },
});

function Beat({ at, children, className }: { at: number; children: ReactNode; className?: string }) {
  const still = useReducedMotion();
  return (
    <motion.div variants={beat(at)} initial={still ? false : "hidden"} animate="shown" className={className}>
      {children}
    </motion.div>
  );
}

export default function Unblinding({ runId, specimenLabel, unblinded: u, verdict, report, probability }: Props) {
  const still = useReducedMotion();

  return (
    <div className="h-full overflow-y-auto bg-sheet">
      <div className="max-w-[780px] mx-auto px-6 py-3 flex flex-col gap-3">
        <div>
          <motion.p className="text-sm text-graphite" initial={still ? false : { opacity: 1 }} animate={{ opacity: 0.8 }}>
            {specimenLabel} was
          </motion.p>
          <motion.h2
            className="font-serif text-[30px] leading-tight font-semibold"
            initial={still ? false : { opacity: 0, filter: "blur(6px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            transition={{ duration: 0.35, ease }}
          >
            {u.compound}
          </motion.h2>
          {u.identity_check && <p className="text-xs text-graphite mt-0.5">Identity check: {u.identity_check}</p>}
          {!u.structure_url && <p className="text-xs text-graphite">No public structure to show for this compound.</p>}
        </div>

        <div className={`grid gap-5 items-start ${u.structure_url ? "grid-cols-[150px_1fr]" : "grid-cols-1"}`}>
          {u.structure_url && (
            <Beat at={0.3}>
              <figure className="bg-white border border-rule rounded-panel p-1.5">
                <img src={u.structure_url} alt={`2D structure of ${u.compound} from PubChem`} className="w-full aspect-square object-contain" />
                <figcaption className="text-[11px] text-graphite mt-0.5">
                  PubChem CID{" "}
                  <a className="underline decoration-ink/30" href={`https://pubchem.ncbi.nlm.nih.gov/compound/${u.pubchem_cid}`} target="_blank" rel="noreferrer">
                    {u.pubchem_cid}
                  </a>
                </figcaption>
              </figure>
            </Beat>
          )}

          <div className="flex flex-col gap-4">
            <div>
              <Beat at={0.6}><h3 className="text-sm font-medium">ChEMBL mechanism</h3></Beat>
              {u.chembl_mechanisms.length ? (
                <div className="mt-1 flex flex-col gap-0.5">
                  {u.chembl_mechanisms.map((m, i) => (
                    <Beat key={i} at={0.7 + i * 0.08}>
                      <p className="text-sm">
                        {m.mechanism_of_action}
                        <span className="text-graphite">{m.target_name ? ` · target ${m.target_name}` : ""}{m.action_type ? ` · ${m.action_type.toLowerCase()}` : ""}</span>
                      </p>
                    </Beat>
                  ))}
                </div>
              ) : (
                <Beat at={0.7}>
                  <p className="text-sm text-graphite mt-1">
                    No curated mechanism in ChEMBL{u.chembl_id ? ` (${u.chembl_id})` : ""}.
                  </p>
                </Beat>
              )}
            </div>

            {verdict && (
              <Beat at={1.0}>
                <div className={`rounded-panel border-2 p-3 ${verdict.correct ? "border-verdict-yes" : "border-verdict-no"}`}>
                  <p className={`flex items-center gap-1.5 font-medium ${verdict.correct ? "text-verdict-yes" : "text-verdict-no"}`}>
                    {verdict.correct ? <Check aria-hidden className="h-5 w-5" /> : <X aria-hidden className="h-5 w-5" />}
                    {verdict.correct ? "The agent's call matches" : "The agent's call does not match"}
                  </p>
                  <dl className="mt-1.5 grid grid-cols-[150px_1fr] gap-y-0.5 text-sm">
                    <dt className="text-graphite">Agent's call</dt>
                    <dd>{verdict.agent_moa}{probability != null && <span className="text-graphite"> · probability {probability.toFixed(2)}</span>}</dd>
                    <dt className="text-graphite">Annotated mechanism</dt>
                    <dd>{verdict.truth_moa} <span className="text-graphite">· BBBC021</span></dd>
                  </dl>
                  <p className="text-[11px] text-graphite mt-1.5">Compared by code, not judged by a model.</p>
                </div>
              </Beat>
            )}
          </div>
        </div>

        {report ? (
          <motion.section initial={still ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}
                          className="flex flex-col gap-2 border-t border-rule pt-3">
            <div className="flex items-center gap-3">
              <h3 className="text-sm font-medium">Reflection</h3>
              <span className="label">{report.citations_label}</span>
              <a className="btn h-7 text-xs ml-auto" href={href({ name: "dossier", runId })}>
                <FileText aria-hidden className="h-3.5 w-3.5" /> Open dossier
              </a>
            </div>
            {report.reflection.text.split(/\n\n+/).map((p, i) => (
              <p key={i} className="font-serif text-[16px] leading-relaxed">{p}</p>
            ))}
          </motion.section>
        ) : (
          <p className="text-sm text-graphite border-t border-rule pt-3">
            The agent is checking the literature and writing its reflection.
          </p>
        )}
      </div>
    </div>
  );
}
