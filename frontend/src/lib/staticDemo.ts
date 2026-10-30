// Static demo build (VITE_STATIC=1, published on GitHub Pages): there is no server. Reads become files under
// data/, written by scripts/build_static_demo.py from the real server's own answers, and a replay runs here in the
// browser with the same pacing, speed limits and "Replay of a live run" block as backend/app/runs/replay.py.

import type { RunEvent } from "./types";

export const STATIC = import.meta.env.VITE_STATIC === "1";
const BASE = import.meta.env.BASE_URL; // ends with "/"
const MIN_SPEED = 0.25, MAX_SPEED = 20, MAX_GAP_S = 60; // as in runs/replay.py

export const dataUrl = (file: string) => `${BASE}data/${file}`;

/** Static replay ids carry their source run and speed: "replay_<run_id>~<speed>". */
export const sourceOf = (runId: string) => (runId.startsWith("replay_") ? runId.slice(7).split("~")[0] : runId);
const speedOf = (runId: string) => {
  const s = parseFloat(runId.split("~")[1] ?? "");
  return Number.isFinite(s) ? Math.min(MAX_SPEED, Math.max(MIN_SPEED, s)) : 2;
};

/** The data file that answers a GET to the API, or null if the static demo has none. */
export function staticPath(path: string): string | null {
  const p = path.split("?")[0];
  const simple: Record<string, string> = {
    "/api/health": "health.json", "/api/specimens": "specimens.json", "/api/recorded": "recorded.json",
    "/api/gallery": "gallery.json",
  };
  if (simple[p]) return dataUrl(simple[p]);
  let m = p.match(/^\/api\/eval\/(\w+)$/);
  if (m) return dataUrl(`eval/${m[1]}.json`);
  m = p.match(/^\/api\/runs\/([^/]+)\/dossier\.(md|json)$/);
  if (m) return dataUrl(`runs/${sourceOf(decodeURIComponent(m[1]))}.dossier.${m[2]}`);
  m = p.match(/^\/api\/runs\/([^/]+)$/);
  if (m) return dataUrl(`runs/${sourceOf(decodeURIComponent(m[1]))}.json`);
  m = p.match(/^\/api\/images\/(.+)$/);
  if (m) return dataUrl(`images/${m[1]}.png`);
  return null;
}

const running = new Map<string, () => void>();

/** POSTs the static demo understands: start a replay, stop it. Anything else is a live-run action. */
export function staticPost(path: string): unknown | undefined {
  const [p, query] = path.split("?");
  let m = p.match(/^\/api\/runs\/([^/]+)\/replay$/);
  if (m) {
    const speed = new URLSearchParams(query ?? "").get("speed") ?? "2";
    return { run_id: `replay_${decodeURIComponent(m[1])}~${speed}` };
  }
  m = p.match(/^\/api\/runs\/([^/]+)\/stop$/);
  if (m) {
    running.get(decodeURIComponent(m[1]))?.();
    return { stopped: decodeURIComponent(m[1]) };
  }
  return undefined;
}

/**
 * Replays a recorded run in the browser: the recorded events unchanged, renumbered from 1, with `run_started`
 * carrying the replay block. Returns a disposer. A missing recording calls onMissing.
 */
export function openStaticReplay(runId: string, onEvent: (e: RunEvent) => void, onMissing: () => void): () => void {
  const source = sourceOf(runId);
  const speed = speedOf(runId);
  let timer: number | undefined;
  let cancelled = false;
  let finished = false;
  let seq = 0;
  const emit = (type: RunEvent["type"], data: unknown) => {
    if (type === "run_finished") finished = true;
    onEvent({ seq: ++seq, type, ts: new Date().toISOString(), run_id: runId, data } as RunEvent);
  };

  fetch(dataUrl(`runs/${source}.events.json`))
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((events: RunEvent[]) => {
      if (cancelled) return;
      const started = events.find((e) => e.type === "run_started");
      const info = { replay_of: source, recorded_at: events[0]?.ts ?? null,
                     model_ids: (started?.data as { model_ids?: unknown })?.model_ids ?? {} };
      let i = 0;
      const step = () => {
        if (cancelled) return;
        const e = events[i++];
        emit(e.type, e.type === "run_started" ? { ...(e.data as object), replay: info } : e.data);
        if (i >= events.length) {
          if (!finished) emit("run_finished", { status: "failed", error: "The recording ends here." });
          return;
        }
        const gap = Math.min(MAX_GAP_S, Math.max(0, (Date.parse(events[i].ts) - Date.parse(e.ts)) / 1000)) / speed;
        timer = window.setTimeout(step, gap * 1000);
      };
      step();
    })
    .catch(() => !cancelled && onMissing());

  running.set(runId, () => {
    if (finished || cancelled) return;
    window.clearTimeout(timer);
    emit("error", { message: "Replay stopped.", fatal: true });
    emit("run_finished", { status: "failed", error: "Replay stopped." });
  });
  return () => {
    cancelled = true;
    window.clearTimeout(timer);
    running.delete(runId);
  };
}
