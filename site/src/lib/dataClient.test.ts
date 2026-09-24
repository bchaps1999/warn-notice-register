import { afterEach, describe, expect, it, vi } from "vitest";
import { employerShardFor, fetchJson, shardFor } from "./dataClient";

afterEach(() => vi.unstubAllGlobals());

describe("site data routes", () => {
  it("uses the same UTF-8 employer shards as the Python exporter", () => {
    expect(employerShardFor("Coca Cola")).toBe("/data/employers/a0.json");
    expect(employerShardFor("Café International")).toBe("/data/employers/21.json");
    expect(employerShardFor("東京工業")).toBe("/data/employers/fa.json");
    expect(shardFor("ab123456")).toBe("/data/notices/ab.json");
  });

  it("caches successful data and retries a failed fetch", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ notices: 2 }) })
      .mockResolvedValueOnce({ ok: false, status: 503 })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ notices: 3 }) });
    vi.stubGlobal("fetch", fetchMock);

    const good = "/data/test-success.json";
    expect(await fetchJson(good)).toEqual({ notices: 2 });
    expect(await fetchJson(good)).toEqual({ notices: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const retry = "/data/test-retry.json";
    await expect(fetchJson(retry)).rejects.toThrow("503 loading");
    expect(await fetchJson(retry)).toEqual({ notices: 3 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
