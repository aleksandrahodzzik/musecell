// The single source of run state for the UI: one EventSource per run, typed events, reconnect, reducer.
//
// The server names every SSE event (`event: tool_call`), so `onmessage` never fires; we listen per type.
// The browser reconnects by itself after a dropped connection and sends Last-Event-ID. If the EventSource gives
// up (readyState CLOSED) we reopen it with ?after=<last seq>, backing off, and dedupe by seq in the reducer.

import { useEffect, useReducer, useRef } from "react";
import { initialRunState, runReducer, type RunState } from "./runState";
import { STATIC, openStaticReplay } from "./staticDemo";
import { EVENT_TYPES, type RunEvent } from "./types";

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];

export function useRunEvents(runId: string): RunState & { retry: () => void } {
  const [state, dispatch] = useReducer(runReducer, runId, initialRunState);
  const lastSeq = useRef(0);
  const finished = useRef(false);
  const attempt = useRef(0);
  const retryRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    dispatch({ type: "reset", runId });
    lastSeq.current = 0;
    finished.current = false;
    attempt.current = 0;
    let es: EventSource | null = null;
    let timer: number | undefined;
    let disposed = false;

    if (STATIC) { // no server: replay the recording in the browser (lib/staticDemo.ts)
      dispatch({ type: "connection", connection: "open" });
      const dispose = openStaticReplay(runId, (event) => {
        lastSeq.current = event.seq;
        dispatch({ type: "event", event });
      }, () => dispatch({ type: "connection", connection: "not_found" }));
      retryRef.current = () => undefined;
      return () => { disposed = true; dispose(); };
    }

    const open = () => {
      if (disposed) return;
      const url = `/api/runs/${encodeURIComponent(runId)}/events${lastSeq.current ? `?after=${lastSeq.current}` : ""}`;
      es = new EventSource(url);
      es.onopen = () => {
        attempt.current = 0;
        dispatch({ type: "connection", connection: "open" });
      };
      for (const type of EVENT_TYPES) {
        es.addEventListener(type, (msg) => {
          // "error" is also EventSource's own connection-error event, which carries no data
          if (!(msg instanceof MessageEvent) || typeof msg.data !== "string") return;
          const event = JSON.parse(msg.data) as RunEvent;
          if (event.seq <= lastSeq.current) return;
          lastSeq.current = event.seq;
          dispatch({ type: "event", event });
          if (event.type === "run_finished") {
            finished.current = true;
            es?.close();
          }
        });
      }
      es.onerror = () => {
        if (finished.current || disposed) return;
        if (es?.readyState === EventSource.CONNECTING) {
          dispatch({ type: "connection", connection: "reconnecting" });
          return;
        }
        es?.close();
        if (lastSeq.current === 0 && attempt.current >= 1) {
          dispatch({ type: "connection", connection: "not_found" }); // a 404 closes the EventSource at once
          return;
        }
        if (attempt.current >= BACKOFF_MS.length) {
          dispatch({ type: "connection", connection: "lost" });
          return;
        }
        dispatch({ type: "connection", connection: "reconnecting" });
        timer = window.setTimeout(open, BACKOFF_MS[attempt.current++]);
      };
    };

    retryRef.current = () => {
      attempt.current = 0;
      window.clearTimeout(timer);
      es?.close();
      dispatch({ type: "connection", connection: "connecting" });
      open();
    };
    open();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      es?.close();
    };
  }, [runId]);

  return { ...state, retry: () => retryRef.current() };
}
