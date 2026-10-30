// REST calls. Errors carry the server's own `detail` (written to the copy rules) so the UI can show it as is.
// In the static demo build (lib/staticDemo.ts) GETs read data files and POSTs are answered in the browser.

import { STATIC, staticPath, staticPost } from "./staticDemo";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (STATIC) {
    if (init?.method === "POST") {
      const answer = staticPost(path);
      if (answer === undefined) throw new ApiError(503, "This static demo replays recorded runs; live runs need the server.");
      return answer as T;
    }
    const file = staticPath(path);
    if (!file) throw new ApiError(404, "Not part of the static demo.");
    path = file;
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "The MuseCell server is not reachable. Start it with: python -m uvicorn app.main:app --app-dir backend --port 8787");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const getJSON = <T>(path: string) => request<T>(path);

export const postJSON = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const imageUrl = (imageId: string) => (STATIC ? staticPath(`/api/images/${imageId}`)! : `/api/images/${imageId}`);

/** Download link for a run's dossier (Markdown or JSON). */
export const dossierUrl = (runId: string, fmt: "md" | "json") => {
  const path = `/api/runs/${encodeURIComponent(runId)}/dossier.${fmt}`;
  return STATIC ? staticPath(path)! : path;
};
