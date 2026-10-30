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

/** The verdict lands like a stamp: a short press from slightly larger, part of the same orchestrated sequence. */
function Stamp({ at, children }: { at: number; children: ReactNode }) {
  const still = useReducedMotion();
  return (
    <motion.div initial={still ? false : { opacity: 0, scale: 1.25, rotate: -4 }} animate={{ opacity: 1, scale: 1, rotate: 0 }}
                transition={{ delay: at, duration: 0.28, ease: [0.3, 0, 0.2, 1] }}>
      {children}
    </motion.div>
  );
}

export default function Unblinding({ runId, specimenLabel, unblinded: u, verdict, report, probability }: Props) {
  const still = useReducedMotion();

  return (
    <div className="relative h-full overflow-y-auto bg-night bg-[radial-gradient(70%_60%_at_30%_0%,rgba(255,244,228,0.08),transparent_70%)] text-haze">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden"><div className="grain" /></div>
      <div className="relative max-w-[920px] mx-auto px-9 py-9 flex flex-col gap-7">
        <div>
          <motion.p className="tag text-mist" initial={still ? false : { opacity: 1 }} animate={{ opacity: 0.85 }}>
            {specimenLabel} was
          </motion.p>
          <motion.h2
            className="display stop text-white mt-3 break-words"
            style={{ fontSize: "clamp(52px, 6.4vw, 112px)" }}
            initial={still ? false : { opacity: 0, filter: "blur(14px)", letterSpacing: "0.02em" }}
            animate={{ opacity: 1, filter: "blur(0px)", letterSpacing: "-0.058em" }}
            transition={{ duration: 0.6, ease }}
          >
            {u.compound}
          </motion.h2>
          {u.identity_check && <p className="text-xs text-mist mt-3">Identity check: {u.identity_check}</p>}
          {!u.structure_url && <p className="text-xs text-mist">No public structure to show for this compound.</p>}
        </div>

        <div className={`grid gap-5 items-start ${u.structure_url ? "grid-cols-[128px_1fr] xl:grid-cols-[168px_1fr]" : "grid-cols-1"}`}>
          {u.structure_url && (
            <Beat at={0.3}>
              <figure className="bg-white rounded-panel p-2 shadow-[0_20px_40px_-20px_rgba(0,0,0,0.9)]">
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
              <Beat at={0.6}><h3 className="text-sm font-medium text-white">ChEMBL mechanism</h3></Beat>
              {u.chembl_mechanisms.length ? (
                <div className="mt-1 flex flex-col gap-0.5">
                  {u.chembl_mechanisms.map((m, i) => (
                    <Beat key={i} at={0.7 + i * 0.08}>
                      <p className="text-sm text-haze">
                        {m.mechanism_of_action}
                        <span className="text-mist">{m.target_name ? ` · target ${m.target_name}` : ""}{m.action_type ? ` · ${m.action_type.toLowerCase()}` : ""}</span>
                      </p>
                    </Beat>
                  ))}
                </div>
              ) : (
                <Beat at={0.7}>
                  <p className="text-sm text-mist mt-1">
                    No curated mechanism in ChEMBL{u.chembl_id ? ` (${u.chembl_id})` : ""}.
                  </p>
                </Beat>
              )}
            </div>

            {verdict && (
              <Stamp at={1.0}>
                <div className={`-rotate-[1.2deg] rounded-[22px] border-2 p-6 bg-white/[0.035] ${verdict.correct ? "border-verdict-yes-on-night shadow-[0_0_40px_-12px_rgba(95,191,143,0.45)]" : "border-verdict-no-on-night shadow-[0_0_40px_-12px_rgba(224,112,104,0.45)]"}`}>
                  <p className={`flex items-center gap-2.5 display text-[clamp(26px,2.4vw,36px)] tracking-[-0.045em] ${verdict.correct ? "text-verdict-yes-on-night" : "text-verdict-no-on-night"}`}>
                    {verdict.correct ? <Check aria-hidden className="h-7 w-7" /> : <X aria-hidden className="h-7 w-7" />}
                    {verdict.correct ? "MuseCell's call matches" : "MuseCell's call does not match"}
                  </p>
                  <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-5 gap-y-1 text-[14.5px]">
                    {verdict.call_source === "protocol" && verdict.protocol ? (
                      <>
                        <dt className="text-mist">Final call</dt>
                        <dd className="text-white">{verdict.agent_moa}<span className="text-mist"> · nearest-neighbour vote over the {verdict.protocol.wells} wells the agent chose ({verdict.protocol.votes}/{verdict.protocol.wells})</span></dd>
                        <dt className="text-mist">Scientist's call</dt>
                        <dd className="text-white">{verdict.scientist_moa}<span className="text-mist">
                          {probability != null && ` · probability ${probability.toFixed(2)}`} · {verdict.scientist_moa === verdict.agent_moa ? "agrees" : `dissents, ${verdict.scientist_correct ? "right" : "wrong"}`}</span></dd>
                      </>
                    ) : (
                      <>
                        <dt className="text-mist">Agent's call</dt>
                        <dd className="text-white">{verdict.agent_moa}{probability != null && <span className="text-mist"> · probability {probability.toFixed(2)} in its conclusion</span>}</dd>
                      </>
                    )}
                    <dt className="text-mist">Annotated mechanism</dt>
                    <dd className="text-white">{verdict.truth_moa} <span className="text-mist">· BBBC021</span></dd>
                  </dl>
                  <p className="text-[11px] text-mist mt-2">Compared by code, not judged by a model.</p>
                </div>
              </Stamp>
            )}
          </div>
        </div>

        {report ? (
          <motion.section initial={still ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}
                          className="flex flex-col gap-2 border-t border-line pt-4">
            <div className="flex items-center gap-3">
              <h3 className="font-serif text-[20px] font-semibold text-white">Reflection</h3>
              <span className="label">{report.citations_label}</span>
              <a className="btn h-7 text-xs ml-auto" href={href({ name: "dossier", runId })}>
                <FileText aria-hidden className="h-3.5 w-3.5" /> Open dossier
              </a>
            </div>
            {report.reflection.text.split(/\n\n+/).map((p, i) => (
              <p key={i} className="font-serif text-[16.5px] leading-[1.65] text-haze">{p}</p>
            ))}
          </motion.section>
        ) : (
          <p className="text-sm text-mist border-t border-line pt-4">
            The agent is checking the literature and writing its reflection.
          </p>
        )}
      </div>
    </div>
  );
}
