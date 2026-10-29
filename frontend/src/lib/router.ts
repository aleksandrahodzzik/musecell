// Hash routes, so FastAPI's static mount can serve the SPA without a catch-all: #/, #/run/<id>, #/dossier/<id>, #/benchmark.
import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "run"; runId: string }
  | { name: "dossier"; runId: string }
  | { name: "benchmark" };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "run" && parts[1]) return { name: "run", runId: parts[1] };
  if (parts[0] === "dossier" && parts[1]) return { name: "dossier", runId: parts[1] };
  if (parts[0] === "benchmark") return { name: "benchmark" };
  return { name: "home" };
}

export const href = (r: Route) =>
  r.name === "home" ? "#/" : r.name === "benchmark" ? "#/benchmark" : `#/${r.name}/${encodeURIComponent(r.runId)}`;

export const navigate = (r: Route) => {
  window.location.hash = href(r);
};

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
