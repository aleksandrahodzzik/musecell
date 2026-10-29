import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { FileText, History, Loader2, Lock, Square, Unlock } from "lucide-react";
import { useState } from "react";
import { postJSON } from "../../lib/api";
import { dateOnly, modelName, usd } from "../../lib/format";
import { href } from "../../lib/router";
import type { Phase, RunState } from "../../lib/runState";

const PHASE: Record<Phase, string> = {
  connecting: "Connecting",
  blinded: "Blinded",
  review: "Under review",
  unblinded: "Unblinded",
  finished: "Finished",
  failed: "Stopped",
};

/** The run's instrument bar: specimen (or, after the unblinding, the compound), budget wells, cost, replay badge. */
export default function RunHeader({ run, label }: { run: RunState; label: string }) {
  const still = useReducedMotion();
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const { total, used } = run.budget;
  const done = !!run.finished;
  const replay = run.replay;
  const sealed = !run.unblinded && run.started?.mode !== "open";

  const stop = async () => {
    setStopping(true);
    setStopError(null);
    try {
      await postJSON(`/api/runs/${encodeURIComponent(run.runId)}/stop`);
    } catch (e) {
      setStopError((e as Error).message);
    }
    setStopping(false);
  };

  return (
    <div className="dark shrink-0 flex items-center gap-6 px-4 h-14 border-b border-line bg-night-2 text-haze">
      <div className="flex items-center gap-3 min-w-0">
        <AnimatePresence mode="wait" initial={false}>
          {run.unblinded ? (
            <motion.span key="name" className="flex items-baseline gap-2 min-w-0"
                         initial={still ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
              <span className="font-serif text-[22px] font-semibold text-white truncate">{run.unblinded.compound}</span>
              <span className="text-xs text-mist whitespace-nowrap">was {label}</span>
            </motion.span>
          ) : (
            <motion.span key="label" className="font-serif text-[22px] font-semibold text-white whitespace-nowrap"
                         exit={still ? undefined : { opacity: 0, y: -4 }} transition={{ duration: 0.2 }}>
              {label}
            </motion.span>
          )}
        </AnimatePresence>
        <span className="chip h-6 px-2">
          {sealed ? <Lock aria-hidden className="h-3 w-3" /> : <Unlock aria-hidden className="h-3 w-3" />}
          {run.phase === "blinded" || run.phase === "connecting" ? (run.started?.mode === "open" ? "Open" : "Blinded") : PHASE[run.phase]}
        </span>
      </div>

      {total > 0 && (
        <div className="flex items-center gap-2.5 text-sm" aria-label={`Budget: ${used} of ${total} wells used`}>
          <span aria-hidden className="flex gap-1.5">
            {Array.from({ length: total }, (_, i) => (
              <span key={i} className={`h-3 w-3 rounded-full transition-all duration-300 ${
                i < used ? "bg-haze shadow-[0_0_8px_rgba(255,255,255,0.45)]" : "well"}`} />
            ))}
          </span>
          <span className="text-white">{used} <span className="text-mist">of {total} wells</span></span>
        </div>
      )}
      <span className="text-sm" title={`${run.cost.calls} model calls`}>
        <span className="text-mist">Cost </span><span className="text-white">{usd(run.cost.usd)}</span>
      </span>

      {replay && (
        <span className="chip h-7 px-2.5 border-white/25 text-haze truncate" role="note">
          <History aria-hidden className="h-3.5 w-3.5 shrink-0" />
          Replay of a live run · {dateOnly(replay.recorded_at)} · {[replay.model_ids.scientist, replay.model_ids.reviewer].filter(Boolean).map(modelName).join(", ")}
        </span>
      )}

      <div className="ml-auto flex items-center gap-2 shrink-0">
        {(run.connection === "reconnecting") && (
          <span className="flex items-center gap-1 text-xs text-mist" role="status">
            <Loader2 aria-hidden className="h-3 w-3 animate-spin" /> Reconnecting
          </span>
        )}
        {stopError && <span className="text-xs text-white" role="alert">{stopError}</span>}
        {run.report && (
          <a className="btn-primary h-8" href={href({ name: "dossier", runId: run.runId })}>
            <FileText aria-hidden className="h-3.5 w-3.5" /> Dossier
          </a>
        )}
        {!done && run.started && (
          <button className="btn" onClick={stop} disabled={stopping}>
            <Square aria-hidden className="h-3 w-3 fill-current" /> {stopping ? "Stopping…" : replay ? "Stop replay" : "Stop"}
          </button>
        )}
      </div>
    </div>
  );
}
