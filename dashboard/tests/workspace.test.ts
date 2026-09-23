import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createPinia, setActivePinia } from "pinia";
import { clone, newProvider, normalizeConfig } from "../src/domain/config";
import { useWorkspace } from "../src/state/workspace";
import { useTelemetry } from "../src/state/telemetry";
import { ApiError, request, responseError } from "../src/services/http";
const originalFetch = globalThis.fetch;
beforeEach(() => setActivePinia(createPinia()));
afterEach(() => {
  globalThis.fetch = originalFetch;
});
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const config = () =>
  normalizeConfig({
    providers: {
      main: {
        ...newProvider(),
        api_key: "sk-****abcd",
        has_key: true,
        base_url: "https://a.test",
        models: { fast: {} },
      },
    },
  });
describe("configuration publication", () => {
  test("deduplicates loads and never overwrites in-memory edits", async () => {
    const data = config();
    let reads = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    globalThis.fetch = (async () => {
      reads++;
      await gate;
      return response(data);
    }) as typeof fetch;
    const store = useWorkspace();
    const first = store.load(),
      second = store.load();
    release();
    await Promise.all([first, second]);
    expect(reads).toBe(1);
    store.draft!.router.mode = "failover";
    await store.load();
    expect(store.draft!.router.mode).toBe("failover");
    expect(store.dirty).toBe(true);
  });
  test("blocks overwriting a remotely changed snapshot", async () => {
    let server = config(),
      writes = 0;
    globalThis.fetch = (async (_url, init) => {
      if (init?.method === "PUT") writes++;
      return response(server);
    }) as typeof fetch;
    const store = useWorkspace();
    await store.load();
    store.draft!.router.mode = "failover";
    server = clone(server);
    server.server.port = 9555;
    expect(await store.save()).toBe(false);
    expect(writes).toBe(0);
    expect(store.conflict).toBe(true);
    expect(store.dirty).toBe(true);
  });
  test("failed write retains draft and original snapshot", async () => {
    globalThis.fetch = (async (_url, init) =>
      init?.method === "PUT"
        ? response({ detail: "invalid route" }, 400)
        : response(config())) as typeof fetch;
    const store = useWorkspace();
    await store.load();
    store.draft!.router.mode = "failover";
    expect(await store.save()).toBe(false);
    expect(store.error).toBe("invalid route");
    expect(store.dirty).toBe(true);
    expect(store.base!.router.mode).toBe("sticky");
  });
  test("committed write remains committed if reload fails, and plaintext secrets are cleared", async () => {
    let written = false;
    let submitted: Record<string, unknown> | undefined;
    globalThis.fetch = (async (_url, init) => {
      if (init?.method === "PUT") {
        written = true;
        submitted = JSON.parse(String(init.body));
        return response({ status: "ok" });
      }
      if (written) return response({ detail: "offline" }, 503);
      return response(config());
    }) as typeof fetch;
    const store = useWorkspace();
    await store.load();
    store.draft!.providers.main.api_key = "new-secret-key";
    expect(await store.save()).toBe(true);
    expect(store.dirty).toBe(false);
    expect(JSON.stringify(store.$state)).not.toContain("new-secret-key");
    expect(JSON.stringify(submitted)).not.toContain("has_key");
    expect(store.error).toContain("已成功发布");
  });
  test("save is serialized while a write is in flight", async () => {
    let server = config(),
      writes = 0,
      release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    globalThis.fetch = (async (_url, init) => {
      if (init?.method === "PUT") {
        writes++;
        await gate;
        server = JSON.parse(String(init.body));
        return response({ status: "ok" });
      }
      return response(server);
    }) as typeof fetch;
    const store = useWorkspace();
    await store.load();
    store.draft!.router.mode = "failover";
    const first = store.save();
    expect(await store.save()).toBe(false);
    release();
    expect(await first).toBe(true);
    expect(writes).toBe(1);
  });
});
describe("network behavior", () => {
  test("partial telemetry failures retain stale values without discarding healthy endpoints", async () => {
    const failed = new Set<string>();
    let version = 1;
    globalThis.fetch = (async (url) => {
      const path = String(url);
      if (failed.has(path))
        return response({ detail: "temporarily unavailable" }, 503);
      if (path === "/health") return response({ status: "ok" });
      if (path === "/api/metrics/summary")
        return response({ total_calls: version });
      if (path === "/api/circuit-breaker")
        return response({ main: version === 1 ? "open" : "closed" });
      return response([]);
    }) as typeof fetch;
    const store = useTelemetry();
    await store.refresh();
    expect(store.summary?.total_calls).toBe(1);
    failed.add("/api/metrics/summary");
    version = 2;
    await store.refresh();
    expect(store.connected).toBe(true);
    expect(store.summary?.total_calls).toBe(1);
    expect(store.circuits.main).toBe("closed");
    expect(store.error).toContain("汇总统计");
    failed.clear();
    failed.add("/health");
    await store.refresh();
    expect(store.connected).toBe(false);
    expect(store.summary?.total_calls).toBe(2);
    expect(store.error).toContain("健康检查");
    failed.clear();
    await store.refresh();
    expect(store.connected).toBe(true);
    expect(store.error).toBe("");
  });
  test("renders nested API errors and non-JSON responses", async () => {
    globalThis.fetch = (async () =>
      response({ error: { message: "upstream failed" } }, 502)) as typeof fetch;
    await expect(request("/api/test")).rejects.toThrow("upstream failed");
    globalThis.fetch = (async () =>
      new Response("<html>offline</html>", { status: 503 })) as typeof fetch;
    await expect(request("/api/test")).rejects.toThrow("HTTP 503");
  });
  test("uses the same error envelope priority for raw responses", async () => {
    const body = {
      detail: "validation failed",
      error: { message: "upstream failed" },
    };
    const rawResponse = response(body, 422);
    const parsed = responseError(rawResponse, await rawResponse.text());
    expect(parsed).toBeInstanceOf(ApiError);
    expect(parsed.status).toBe(422);
    expect(parsed.message).toBe("validation failed");
    globalThis.fetch = (async () => response(body, 422)) as typeof fetch;
    await expect(request("/api/test")).rejects.toThrow("validation failed");
  });
  test("timeout cancels a slow read", async () => {
    globalThis.fetch = ((_url: unknown, init: RequestInit) =>
      new Promise((_resolve, reject) =>
        init.signal!.addEventListener("abort", () =>
          reject(init.signal!.reason),
        ),
      )) as typeof fetch;
    await expect(request("/api/test", {}, 5)).rejects.toThrow("超时");
  });
  test("metrics refresh deduplicates overlapping batches", async () => {
    let reads = 0,
      release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    globalThis.fetch = (async (url) => {
      reads++;
      await gate;
      return response(String(url) === "/health" ? { status: "ok" } : []);
    }) as typeof fetch;
    const store = useTelemetry();
    const first = store.refresh(),
      second = store.refresh();
    release();
    await Promise.all([first, second]);
    expect(reads).toBe(7);
    expect(store.connected).toBe(true);
  });
});
