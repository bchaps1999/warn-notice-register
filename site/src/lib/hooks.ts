import { useEffect, useState } from "react";
import { employerShardFor, fetchJson, shardFor } from "./dataClient";
import type {
  EmployerDetail, Meta, National, NoticeDetail, NoticeIndex, StateData,
} from "./types";

interface Loaded<T> {
  data: T | null;
  error: string | null;
}

function useJson<T>(path: string | null): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null });
  useEffect(() => {
    if (path === null) return;
    let live = true;
    setState({ data: null, error: null });
    fetchJson<T>(path)
      .then((data) => live && setState({ data, error: null }))
      .catch((e) => live && setState({ data: null, error: String(e) }));
    return () => {
      live = false;
    };
  }, [path]);
  return state;
}

export const useMeta = () => useJson<Meta>("/data/meta.json");
export const useNational = () => useJson<National>("/data/national.json");
export const useStateData = (xx: string | undefined) =>
  useJson<StateData>(xx ? `/data/states/${xx.toLowerCase()}.json` : null);
export const useIndex = () => useJson<NoticeIndex>("/data/index.json");

export function useEmployer(key: string | undefined): Loaded<EmployerDetail> {
  const shard = useJson<Record<string, EmployerDetail>>(
    key ? employerShardFor(key) : null
  );
  if (!key || shard.data === null) return { data: null, error: shard.error };
  const rec = shard.data[key];
  return rec
    ? { data: rec, error: null }
    : { data: null, error: "Employer not found" };
}

export function useNotice(key: string | undefined): Loaded<NoticeDetail> {
  const shard = useJson<Record<string, NoticeDetail>>(key ? shardFor(key) : null);
  if (!key || shard.data === null) return { data: null, error: shard.error };
  const match = Object.entries(shard.data).find(([full]) => full.startsWith(key));
  return match
    ? { data: match[1], error: null }
    : { data: null, error: "Notice not found" };
}
