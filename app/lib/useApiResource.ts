"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/app/lib/api";
import { fetchResource, peekResource } from "@/app/lib/resourceCache";

type ResourceState<T> = { path: string | null; data: T | null; loading: boolean; error: string };

export function useApiResource<T>(path: string | null) {
  const [state, setState] = useState<ResourceState<T>>(() => ({
    path, data: peekResource<T>(path), loading: Boolean(path && peekResource<T>(path) === null), error: "",
  }));
  const requestId = useRef(0);

  const reload = useCallback(async () => {
    if (!path) return;
    const id = ++requestId.current;
    setState((previous) => ({ path, data: previous.path === path ? previous.data : peekResource<T>(path),
      loading: !(previous.path === path && previous.data !== null), error: "" }));
    try {
      const data = await fetchResource(path, () => api<T>(path), true);
      if (requestId.current === id) setState({ path, data, loading: false, error: "" });
    } catch (requestError) {
      if (requestId.current === id) setState((previous) => ({ ...previous, data: [401, 403, 404].includes((requestError as { status?: number })?.status ?? 0) ? null : previous.data, loading: false, error: formatApiError(requestError) }));
    }
  }, [path]);

  useEffect(() => {
    const id = ++requestId.current;
    let active = true;
    Promise.resolve().then(async () => {
      if (!active) return;
      const cached = peekResource<T>(path);
      setState({ path, data: cached, loading: Boolean(path && cached === null), error: "" });
      if (!path) return;
      try {
        const data = await fetchResource(path, () => api<T>(path));
        if (active && requestId.current === id) setState({ path, data, loading: false, error: "" });
      } catch (error) {
        if (active && requestId.current === id) setState((previous) => ({ ...previous, data: [401, 403, 404].includes((error as { status?: number })?.status ?? 0) ? null : previous.data, loading: false, error: formatApiError(error) }));
      }
    });
    return () => { active = false; requestId.current += 1; };
  }, [path]);

  const setData = useCallback((value: T | null | ((previous: T | null) => T | null)) => {
    setState((previous) => ({ ...previous, path, data: typeof value === "function"
      ? (value as (previous: T | null) => T | null)(previous.data) : value }));
  }, [path]);
  const current = state.path === path ? state : {
    path, data: peekResource<T>(path), loading: Boolean(path && peekResource<T>(path) === null), error: "",
  };
  return { data: current.data, loading: current.loading, error: current.error, reload, setData };
}
