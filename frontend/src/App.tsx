import { useMemo } from "react";
import { Footer, Nav, Page } from "./components/Shell";
import { StainTintFilter } from "./components/StainTint";
import { ErrorNote } from "./components/States";
import { useRoute } from "./lib/router";
import type { Health, RecordedRun, SpecimenCard } from "./lib/types";
import { useFetch } from "./lib/useFetch";
import Benchmark from "./screens/Benchmark";
import ChooseSpecimen from "./screens/ChooseSpecimen";
import Dossier from "./screens/Dossier";
import RunView from "./screens/RunView";

export default function App() {
  const route = useRoute();
  const health = useFetch<Health>("/api/health");
  const specimens = useFetch<SpecimenCard[]>("/api/specimens");
  const recorded = useFetch<RecordedRun[]>(route.name === "home" ? null : "/api/recorded");

  // opaque specimen id → "Specimen 7" (from this server's catalog, else from the recorded runs)
  const labels = useMemo(() => {
    const out: Record<string, string> = {};
    recorded.data?.forEach((r) => r.specimen_label && (out[r.specimen_id] = r.specimen_label));
    specimens.data?.forEach((s) => (out[s.specimen_id] = s.label));
    return out;
  }, [specimens.data, recorded.data]);

  const liveNote = health.data ? (health.data.live_runs ? "Live runs available" : "Replays only on this server") : undefined;

  return (
    <div className="h-screen flex flex-col bg-night text-ink">
      <StainTintFilter />
      <Nav route={route} liveNote={liveNote} live={health.data?.live_runs} />
      {health.error && (
        <div className="px-4 pt-3">
          <ErrorNote title="The MuseCell server is not reachable." action={<button className="btn" onClick={health.reload}>Try again</button>}>
            Start it with <code className="text-ink">python -m uvicorn app.main:app --app-dir backend --port 8787</code>, then try again.
          </ErrorNote>
        </div>
      )}
      {route.name === "run" ? (
        <main className="flex-1 min-h-0"><RunView key={route.runId} runId={route.runId} labels={labels} /></main>
      ) : (
        <Page dark={route.name === "home"}>
          {route.name === "home" && <ChooseSpecimen health={health.data} />}
          {route.name === "dossier" && <Dossier runId={route.runId} labels={labels} />}
          {route.name === "benchmark" && <Benchmark />}
        </Page>
      )}
      <Footer compact />
    </div>
  );
}
