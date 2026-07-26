// Data files are immutable per deploy; cache fetched JSON for the session.
const cache = new Map<string, Promise<unknown>>();

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

export function fetchJson<T>(path: string): Promise<T> {
  let hit = cache.get(path);
  if (!hit) {
    hit = fetch(BASE + path).then((r) => {
      if (!r.ok) throw new Error(`${r.status} loading ${path}`);
      return r.json();
    });
    cache.set(path, hit);
    hit.catch(() => cache.delete(path)); // don't cache failures
  }
  return hit as Promise<T>;
}

export const shardFor = (key: string) => `/data/notices/${key.slice(0, 2)}.json`;

// FNV-1a low byte, mirroring the site exporter's employer sharding.
export function employerShardFor(key: string): string {
  let h = 2166136261;
  const bytes = new TextEncoder().encode(key);
  for (const b of bytes) h = Math.imul(h ^ b, 16777619) >>> 0;
  return `/data/employers/${(h & 0xff).toString(16).padStart(2, "0")}.json`;
}
