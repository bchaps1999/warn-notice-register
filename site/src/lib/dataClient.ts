// Data files are immutable per deploy; cache fetched JSON for the session.
const cache = new Map<string, Promise<unknown>>();

export function fetchJson<T>(path: string): Promise<T> {
  let hit = cache.get(path);
  if (!hit) {
    hit = fetch(path).then((r) => {
      if (!r.ok) throw new Error(`${r.status} loading ${path}`);
      return r.json();
    });
    cache.set(path, hit);
    hit.catch(() => cache.delete(path)); // don't cache failures
  }
  return hit as Promise<T>;
}

export const shardFor = (key: string) => `/data/notices/${key.slice(0, 2)}.json`;
