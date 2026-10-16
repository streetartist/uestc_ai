// Tab-local read cache. Never cache on the server or persist account data.
type Entry = { data?: unknown; updatedAt: number; pending?: Promise<unknown> };
const entries = new Map<string, Entry>();
const freshForMs = 15_000;
const retainForMs = 60_000;
let generation = 0;

export function isCacheableResource(path: string) {
  return /^\/(?:competitions(?:\/[^/?]+(?:\/tracks\/[^/?]+)?)?|problems(?:\/[^/?]+)?|content(?:\/[^/?]+)?)(?:\?.*)?$/.test(path)
    || /^\/me\/(?:teams|registrations|submissions)$/.test(path);
}

export function clearResourceCache() {
  generation += 1;
  entries.clear();
}

export function peekResource<T>(path: string | null): T | null {
  if (typeof window === "undefined" || !path || !isCacheableResource(path)) return null;
  const entry = entries.get(path);
  return entry?.data !== undefined && Date.now() - entry.updatedAt < retainForMs ? entry.data as T : null;
}

export function fetchResource<T>(path: string, loader: () => Promise<T>, force = false): Promise<T> {
  if (typeof window === "undefined" || !isCacheableResource(path)) return loader();
  const previous = entries.get(path);
  if (previous?.pending) return previous.pending as Promise<T>;
  if (!force && previous?.data !== undefined && Date.now() - previous.updatedAt < freshForMs) return Promise.resolve(previous.data as T);
  const version = generation;
  const entry: Entry = { data: previous?.data, updatedAt: previous?.updatedAt ?? 0 };
  const pending = Promise.resolve().then(loader).then((data) => {
    if (generation === version && entries.get(path) === entry) { entry.data = data; entry.updatedAt = Date.now(); }
    return data;
  }).finally(() => {
    if (entries.get(path) === entry) { entry.pending = undefined; if (entry.data === undefined) entries.delete(path); }
  });
  entry.pending = pending;
  entries.set(path, entry);
  if (entries.size > 100) {
    for (const [key, value] of entries) {
      if (key !== path && !value.pending) { entries.delete(key); break; }
    }
  }
  return pending;
}
