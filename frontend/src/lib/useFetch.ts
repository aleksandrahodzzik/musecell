import { useCallback, useEffect, useState } from "react";
import { ApiError, getJSON } from "./api";

export interface FetchState<T> {
  data?: T;
  error?: ApiError;
  loading: boolean;
  reload: () => void;
}

export function useFetch<T>(path: string | null): FetchState<T> {
  const [state, setState] = useState<{ data?: T; error?: ApiError; loading: boolean }>({ loading: !!path });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    getJSON<T>(path)
      .then((data) => live && setState({ data, loading: false }))
      .catch((error: ApiError) => live && setState({ error, loading: false }));
    return () => {
      live = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
