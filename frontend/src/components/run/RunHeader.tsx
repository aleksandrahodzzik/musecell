import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { FileText, History, Loader2, Square } from "lucide-react";
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

export default function RunHeader({ run, label }: { run: RunState; label: string }) {
  const still = useReducedMotion();
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const { total, used } = run.budget;
  const done = !!run.finished;
  const replay = run.replay;

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
    <div className="shrink-0 flex items-center gap-5 px-4 h-12 border-b border-rule bg-sheet">
      <div className="flex items-baseline gap-2 min-w-0">
        <AnimatePresence mode="wait" initial={false}>
          {run.unblinded ? (
            <motion.span key="name" className="font-serif text-[20px] font-semibold truncate"
                         initial={still ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
              {run.unblinded.compound}
              <span className="font-sans text-xs font-normal text-graphite ml-2">was {label}</span>
            </motion.span>
          ) : (
            <motion.span key="label" className="font-serif text-[20px] font-semibold" exit={still ? undefined : { opacity: 0, y: -4 }}
                         transition={{ duration: 0.2 }}>
              {label} <span className="font-sans text-sm font-normal text-graphite">({run.started?.mode === "open" ? "open" : "blinded"})</span>
            </motion.span>
          )}
        </AnimatePresence>
        {run.phase !== "blinded" && <span className="chip">{PHASE[run.phase]}</span>}
      </div>

      {total > 0 && (
        <div className="flex items-center gap-2 text-sm" aria-label={`Budget: ${used} of ${total} wells used`}>
          <span className="text-graphite">Budget</span>
          <span aria-hidden className="flex gap-1">
            {Array.from({ length: total }, (_, i) => (
              <span key={i} className={`h-2.5 w-2.5 rounded-full ${i < used ? "bg-ink" : "border border-ink/40"}`} />
            ))}
          </span>
          <span>{used} of {total} wells</span>
        </div>
      )}
      <span className="text-sm" title={`${run.cost.calls} model calls`}>
        <span className="text-graphite">Cost </span>{usd(run.cost.usd)}
      </span>

      {replay && (
        <span className="chip h-7 px-2 border-ink/50 bg-white" role="note">
          <History aria-hidden className="h-3.5 w-3.5" />
          Replay of a live run · {dateOnly(replay.recorded_at)} · {[replay.model_ids.scientist, replay.model_ids.reviewer].filter(Boolean).map(modelName).join(", ")}
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        {(run.connection === "reconnecting") && (
          <span className="flex items-center gap-1 text-xs text-graphite" role="status">
            <Loader2 aria-hidden className="h-3 w-3 animate-spin" /> Reconnecting
          </span>
        )}
        {stopError && <span className="text-xs text-ink" role="alert">{stopError}</span>}
        {run.report && (
          <a className="btn" href={href({ name: "dossier", runId: run.runId })}>
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
