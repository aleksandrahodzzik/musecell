// Run view (spec §8 screen 2): header, plate map | microscope (or unblinding) | hypotheses, notebook below.
// Live runs and replays use exactly this screen; a replay only adds its permanent badge in the header.

import { useEffect, useRef, useState } from "react";
import HypothesisBoard from "../components/run/HypothesisBoard";
import Microscope from "../components/run/Microscope";
import Notebook from "../components/run/Notebook";
import PlateMap from "../components/run/PlateMap";
import RunHeader from "../components/run/RunHeader";
import Unblinding from "../components/run/Unblinding";
import { ErrorNote, Loading } from "../components/States";
import { shortId } from "../lib/format";
import { href } from "../lib/router";
import { useRunEvents } from "../lib/useRunEvents";

export default function RunView({ runId, labels }: { runId: string; labels: Record<string, string> }) {
  const run = useRunEvents(runId);
  const [picked, setPicked] = useState<string | undefined>();
  const [tab, setTab] = useState<"microscope" | "unblinding">("microscope");
  const [focus, setFocus] = useState<{ id: string; n: number } | undefined>();
  const switched = useRef(false);

  // follow the agent: each newly acquired well takes over the microscope
  useEffect(() => setPicked(undefined), [run.wells.length]);
  // the unblinding takes the centre once, when it happens
  useEffect(() => {
    if (run.unblinded && !switched.current) {
      switched.current = true;
      setTab("unblinding");
    }
  }, [run.unblinded]);

  if (run.connection === "not_found") {
    return (
      <div className="max-w-[640px] mx-auto p-8">
        <ErrorNote title="This run is not on the server."
                   action={<a className="btn" href={href({ name: "home" })}>Back to specimens</a>}>
          Live runs are kept in memory until the server restarts. Start a new run, or replay a recorded one.
        </ErrorNote>
      </div>
    );
  }
  if (!run.started) {
    return run.connection === "lost" ? (
      <div className="max-w-[640px] mx-auto p-8">
        <ErrorNote title="Lost the connection to the server." action={<button className="btn" onClick={run.retry}>Try again</button>}>
          Check that the backend is still running on port 8787.
        </ErrorNote>
      </div>
    ) : <Loading label="Connecting to the run…" />;
  }

  const label = labels[run.started.specimen_id] ?? `Specimen ${shortId(run.started.specimen_id)}`;
  const selectedId = picked ?? run.wells[run.wells.length - 1]?.well_id;
  const well = run.wells.find((w) => w.well_id === selectedId);
  const finalConclusion = run.conclusions[run.conclusions.length - 1];

  const onEvidence = (id: string) => {
    const w = run.wells.find((x) => x.evidence_id === id);
    if (w) {
      setPicked(w.well_id);
      setTab("microscope");
    }
    setFocus((f) => ({ id, n: (f?.n ?? 0) + 1 }));
  };
  const onSelectWell = (wellId: string) => {
    setPicked(wellId);
    setTab("microscope");
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <RunHeader run={run} label={label} />
      {run.connection === "lost" && (
        <div className="px-4 pt-2">
          <ErrorNote title="Lost the connection to the server." action={<button className="btn" onClick={run.retry}>Reconnect</button>}>
            The run may still be going on the server. Reconnecting resumes from the last event received.
          </ErrorNote>
        </div>
      )}
      <div className={`shrink-0 ${tab === "unblinding" ? "h-[66%]" : "h-[56%]"} min-h-[320px] grid grid-cols-[214px_minmax(0,1fr)_268px] border-b border-rule`}>
        <section aria-label="Plate map" className="bg-sheet border-r border-rule min-h-0">
          <PlateMap doses={run.doses} nDoses={run.started.doses} wells={run.wells} selected={selectedId} onSelect={onSelectWell} />
        </section>
        <section aria-label={tab === "microscope" ? "Microscope" : "Unblinding"} className="flex flex-col min-h-0 min-w-0">
          {run.unblinded && (
            <div role="tablist" aria-label="Centre view" className="flex gap-1 px-3 pt-1.5 bg-sheet border-b border-rule">
              {(["unblinding", "microscope"] as const).map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                        className={`px-2.5 h-7 text-xs rounded-t-control border-b-2 ${tab === t ? "border-ink text-ink font-medium" : "border-transparent text-graphite hover:text-ink"}`}>
                  {t === "unblinding" ? "Unblinding" : "Microscope"}
                </button>
              ))}
            </div>
          )}
          <div className="flex-1 min-h-0">
            {tab === "unblinding" && run.unblinded ? (
              <Unblinding runId={runId} specimenLabel={label} unblinded={run.unblinded} verdict={run.verdict}
                          report={run.report} probability={finalConclusion?.probability} />
            ) : (
              <Microscope well={well} />
            )}
          </div>
        </section>
        <section aria-label="Hypotheses" className="bg-sheet border-l border-rule min-h-0">
          <HypothesisBoard hypotheses={run.hypotheses} conclusion={finalConclusion} verdict={run.verdict} onEvidence={onEvidence} />
        </section>
      </div>
      <section aria-label="Notebook" className="flex-1 min-h-0 bg-sheet">
        <Notebook run={run} focusEvidence={focus} onSelectWell={onSelectWell} />
      </section>
    </div>
  );
}
