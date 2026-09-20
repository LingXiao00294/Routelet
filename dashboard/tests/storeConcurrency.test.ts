import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createPinia, setActivePinia } from "pinia";
import type { AppConfig, CallsPage, Summary } from "../src/api/types";
import { useCallsStore } from "../src/stores/calls";
import { useAppStore } from "../src/stores/app";
import { useConfigStore } from "../src/stores/config";
import { useMetricsStore } from "../src/stores/metrics";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function callsPage(page: number): CallsPage {
  return {
    data: [],
    total: 3,
    page,
    size: 1,
    pages: 3,
  };
}

function summary(totalCalls: number): Summary {
  return {
    total_calls: totalCalls,
    success_count: totalCalls,
    error_count: 0,
    success_rate: 100,
    total_input_tokens: totalCalls,
    total_output_tokens: totalCalls,
    total_cache_read: 0,
    total_cache_write: 0,
    total_cost_usd: totalCalls,
    avg_latency_ms: totalCalls,
  };
}

function metricBatch(marker: number, fail = false): Response[] {
  return [
    fail
      ? jsonResponse({ detail: `metrics-${marker}-failed` }, 500)
      : jsonResponse(summary(marker)),
    jsonResponse([
      {
        provider: "provider",
        model: `real-${marker}`,
        count: marker,
        success_count: marker,
        total_input_tokens: marker,
        total_output_tokens: marker,
        total_cost_usd: marker,
      },
    ]),
    jsonResponse([
      {
        virtual_model: `virtual-${marker}`,
        count: marker,
        success_count: marker,
        total_input_tokens: marker,
        total_output_tokens: marker,
        total_cost_usd: marker,
      },
    ]),
    jsonResponse([{ provider: "provider", count: marker, success_count: marker }]),
    jsonResponse([]),
  ];
}

function validConfig(host = "127.0.0.1"): AppConfig {
  return {
    server: {
      host,
      port: 9456,
      log_level: "info",
      log_file: "",
      log_max_bytes: 10_485_760,
      log_backup_count: 0,
    },
    router: {
      failure_threshold: 5,
      recovery_timeout: 600,
      mode: "failover",
    },
    providers: {
      provider: {
        type: "anthropic",
        api_key: "test-key",
        base_url: "https://example.test/anthropic",
        timeout_seconds: 120,
        failure_threshold: null,
        recovery_timeout: null,
        max_concurrent: 0,
        max_queue: 0,
        queue_wait_timeout: 30,
        rate_limit_cooldown: 30,
        models: { real: {} },
      },
    },
    models: virtualModels(),
  };
}

function virtualModels(): AppConfig["models"] {
  return {
    virtual: {
      pinned_model: null,
      models: [{ provider: "provider", model: "real" }],
    },
  };
}

const originalFetch = globalThis.fetch;

beforeEach(() => setActivePinia(createPinia()));
afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("request generations", () => {
  test("app startup settles partial failures and marks data stale", async () => {
    globalThis.fetch = ((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/health") {
        return Promise.resolve(jsonResponse({ detail: "router unavailable" }, 503));
      }
      if (path === "/api/config") {
        return Promise.resolve(jsonResponse({ detail: "config unavailable" }, 502));
      }
      if (path === "/api/circuit-breaker") {
        return Promise.resolve(jsonResponse({ provider: "closed" }));
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useAppStore();

    await expect(store.loadInitialState()).resolves.toBe(true);

    expect(store.healthy).toBe(false);
    expect(store.config).toBeNull();
    expect(store.configError).toContain("config unavailable");
    expect(store.circuit).toEqual({ provider: "closed" });
    expect(store.staleData).toBe(true);
  });

  test("app silent config and circuit loaders report failures", async () => {
    globalThis.fetch = ((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/config") {
        return Promise.resolve(jsonResponse({ detail: "config failed" }, 502));
      }
      if (path === "/api/circuit-breaker") {
        return Promise.resolve(jsonResponse({ detail: "circuit failed" }, 502));
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useAppStore();

    await expect(store.loadConfig(true)).resolves.toBe(false);
    await expect(store.loadCircuit(true)).resolves.toBe(false);

    expect(store.configError).toContain("config failed");
    expect(store.config).toBeNull();
    expect(store.circuit).toEqual({});
  });

  test("app stores keep the newest global health, config, and circuit results", async () => {
    const health = [deferred<Response>(), deferred<Response>()];
    const configs = [deferred<Response>(), deferred<Response>()];
    const circuits = [deferred<Response>(), deferred<Response>()];
    const indexes = { health: 0, config: 0, circuit: 0 };
    globalThis.fetch = ((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/health") return health[indexes.health++].promise;
      if (path === "/api/config") return configs[indexes.config++].promise;
      if (path === "/api/circuit-breaker") return circuits[indexes.circuit++].promise;
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useAppStore();

    const first = [store.checkHealth(), store.loadConfig(), store.loadCircuit()];
    const second = [store.checkHealth(), store.loadConfig(), store.loadCircuit()];
    health[1].resolve(jsonResponse({ status: "ok" }));
    configs[1].resolve(jsonResponse(validConfig("newest")));
    circuits[1].resolve(jsonResponse({ provider: "open" }));
    await Promise.all(second);

    health[0].resolve(jsonResponse({ status: "down" }));
    configs[0].resolve(jsonResponse(validConfig("stale")));
    circuits[0].resolve(jsonResponse({ provider: "closed" }));
    await Promise.all(first);

    expect(store.healthy).toBe(true);
    expect(store.config?.server.host).toBe("newest");
    expect(store.circuit).toEqual({ provider: "open" });
    expect(store.configLoading).toBe(false);
  });

  test("calls keeps the newest page when responses and errors finish in reverse order", async () => {
    const requests = [deferred<Response>(), deferred<Response>(), deferred<Response>()];
    let requestIndex = 0;
    globalThis.fetch = (() => requests[requestIndex++].promise) as typeof fetch;
    const store = useCallsStore();

    const first = store.fetchList({ page: 1, size: 1 });
    const second = store.fetchList({ page: 2, size: 1 });
    const third = store.fetchList({ page: 3, size: 1 });

    requests[2].resolve(jsonResponse(callsPage(3)));
    await third;
    expect(store.page?.page).toBe(3);
    expect(store.loading).toBe(false);

    requests[1].resolve(jsonResponse({ detail: "old request failed" }, 500));
    await second;
    requests[0].resolve(jsonResponse(callsPage(1)));
    await first;

    expect(store.page?.page).toBe(3);
    expect(store.error).toBeNull();
    expect(store.loading).toBe(false);
  });

  test("calls keeps loading while the newest request is still pending", async () => {
    const requests = [deferred<Response>(), deferred<Response>()];
    let requestIndex = 0;
    globalThis.fetch = (() => requests[requestIndex++].promise) as typeof fetch;
    const store = useCallsStore();

    const oldRequest = store.fetchList({ page: 1, size: 1 });
    const newestRequest = store.fetchList({ page: 2, size: 1 });
    requests[0].resolve(jsonResponse(callsPage(1)));
    await oldRequest;
    expect(store.loading).toBe(true);

    requests[1].resolve(jsonResponse(callsPage(2)));
    await newestRequest;
    expect(store.loading).toBe(false);
  });

  test("calls publishes slow responses despite overlapping polls and initial loading", async () => {
    const requests = [deferred<Response>(), deferred<Response>()];
    let requestCount = 0;
    globalThis.fetch = (() => requests[requestCount++].promise) as typeof fetch;
    const store = useCallsStore();

    for (let batch = 0; batch < 2; batch += 1) {
      const pending = [store.fetchList({ page: 1, size: 1 }, batch > 0)];
      for (let tick = 0; tick < 3; tick += 1) {
        pending.push(store.fetchList({ size: 1, page: 1 }, true));
      }
      expect(requestCount).toBe(batch + 1);
      expect(store.loading).toBe(batch === 0);
      requests[batch].resolve(jsonResponse({ ...callsPage(1), total: batch + 10 }));
      await Promise.all(pending);
      expect(store.page?.total).toBe(batch + 10);
      expect(store.loading).toBe(false);
    }
  });

  test("a manual calls refresh joining a silent poll still reports failure", async () => {
    const response = deferred<Response>();
    let requestCount = 0;
    globalThis.fetch = (() => {
      requestCount += 1;
      return response.promise;
    }) as typeof fetch;
    const store = useCallsStore();
    const poll = store.fetchList({ page: 1 }, true);
    const manual = store.fetchList({ page: 1 });
    expect(requestCount).toBe(1);
    expect(store.loading).toBe(true);
    const manualResult = manual.catch((error: unknown) => error);
    response.resolve(jsonResponse({ detail: "slow poll failed" }, 503));
    const manualError = await manualResult;
    expect(manualError).toBeInstanceOf(Error);
    expect((manualError as Error).message).toBe("slow poll failed");
    await poll;
    expect(store.error).toBe("slow poll failed");
    expect(store.loading).toBe(false);
  });

  test("metrics publishes slow batches while repeated polls share the current range", async () => {
    const requests = Array.from({ length: 10 }, () => deferred<Response>());
    let requestCount = 0;
    globalThis.fetch = (() => requests[requestCount++].promise) as typeof fetch;
    const store = useMetricsStore();

    for (let batch = 0; batch < 2; batch += 1) {
      const pending = [store.refresh(batch > 0)];
      for (let tick = 0; tick < 3; tick += 1) pending.push(store.refresh(true));
      expect(requestCount).toBe((batch + 1) * 5);
      metricBatch(batch + 10).forEach((response, index) => {
        requests[batch * 5 + index].resolve(response);
      });
      await Promise.all(pending);
      expect(store.summary?.total_calls).toBe(batch + 10);
      expect(store.loading).toBe(false);
      expect(store.loadedOnce).toBe(true);
    }
  });

  test("a manual metrics refresh joining a silent batch still reports failure", async () => {
    const requests = Array.from({ length: 5 }, () => deferred<Response>());
    let requestCount = 0;
    globalThis.fetch = (() => requests[requestCount++].promise) as typeof fetch;
    const store = useMetricsStore();
    const poll = store.refresh(true);
    const manual = store.refresh();
    expect(requestCount).toBe(5);
    expect(store.loading).toBe(true);
    const manualResult = manual.catch((error: unknown) => error);
    metricBatch(1, true).forEach((response, index) => requests[index].resolve(response));
    const manualError = await manualResult;
    expect(manualError).toBeInstanceOf(Error);
    expect((manualError as Error).message).toBe("metrics-1-failed");
    await poll;
    expect(store.error).toBe("metrics-1-failed");
    expect(store.loading).toBe(false);
  });

  test("metrics keeps the newest range when three batches finish newest-first", async () => {
    const requests = Array.from({ length: 15 }, () => deferred<Response>());
    let requestIndex = 0;
    globalThis.fetch = (() => requests[requestIndex++].promise) as typeof fetch;
    const store = useMetricsStore();

    const sevenDays = store.setDays(7);
    const thirtyDays = store.setDays(30);
    const ninetyDays = store.setDays(90);

    metricBatch(90).forEach((response, index) => requests[10 + index].resolve(response));
    await ninetyDays;
    metricBatch(30, true).forEach((response, index) => requests[5 + index].resolve(response));
    await thirtyDays;
    metricBatch(7).forEach((response, index) => requests[index].resolve(response));
    await sevenDays;

    expect(store.days).toBe(90);
    expect(store.summary?.total_calls).toBe(90);
    expect(store.byRealModel[0]?.model).toBe("real-90");
    expect(store.daily).toHaveLength(90);
    expect(store.error).toBeNull();
    expect(store.loadedOnce).toBe(true);
  });

  test.each(["load", "save", "mode"] as const)(
    "config %s keeps providers and model references from one complete snapshot",
    async (phase) => {
      function snapshot(providerName: string): AppConfig {
        const config = validConfig();
        config.providers = { [providerName]: config.providers.provider };
        config.models.virtual.models = [{ provider: providerName, model: "real" }];
        return config;
      }

      const initial = snapshot("initial-provider");
      const updated = snapshot("updated-provider");
      const unrelatedModels = snapshot("unrelated-provider").models;
      let backend = initial;
      let configGets = 0;
      let modelGets = 0;
      const puts: AppConfig[] = [];
      globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/config" && init?.method === "PUT") {
          const payload = JSON.parse(String(init.body)) as AppConfig;
          puts.push(payload);
          backend = phase === "save" ? updated : payload;
          return Promise.resolve(jsonResponse({ status: "ok" }));
        }
        if (path === "/api/config") {
          configGets += 1;
          if (phase === "mode" && configGets === 2) backend = updated;
          return Promise.resolve(jsonResponse(backend));
        }
        if (path === "/api/config/models") {
          modelGets += 1;
          // An external write between separate GETs yields a different generation.
          return Promise.resolve(jsonResponse(
            phase === "load" || configGets > 1 ? unrelatedModels : initial.models,
          ));
        }
        throw new Error(`Unexpected request: ${path}`);
      }) as typeof fetch;
      const store = useConfigStore();
      await store.load();
      if (phase === "save") {
        store.draft!.server.host = "saved-host";
        await expect(store.save()).resolves.toBe(true);
      } else if (phase === "mode") {
        await expect(store.setRouterMode("sticky")).resolves.toBe(true);
        expect(puts[0].models.virtual.models).toEqual(updated.models.virtual.models);
        expect(puts[0].router.mode).toBe("sticky");
      }

      expect(store.models.virtual.models).toEqual(backend.models.virtual.models);
      expect(Object.keys(store.draft!.providers)).toEqual(Object.keys(backend.providers));
      expect(store.validate()).toBe(true);
      expect(store.dirty).toBe(false);
      expect(modelGets).toBe(0);
      expect(configGets).toBe(phase === "load" ? 1 : phase === "save" ? 2 : 3);
      expect(puts).toHaveLength(phase === "load" ? 0 : 1);
    },
  );

  test("config load does not replace a draft edited after the request starts", async () => {
    const refreshConfig = deferred<Response>();
    let configGets = 0;
    globalThis.fetch = ((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/config") {
        configGets += 1;
        return configGets === 1
          ? Promise.resolve(jsonResponse(validConfig()))
          : refreshConfig.promise;
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();

    const refresh = store.load();
    store.draft!.server.host = "locally-edited";
    refreshConfig.resolve(jsonResponse(validConfig("remote-update")));
    await refresh;

    expect(store.draft?.server.host).toBe("locally-edited");
    expect(store.dirty).toBe(true);
    expect(store.error).toBeNull();
    expect(store.loading).toBe(false);
  });

  test("config save starts a post-PUT load instead of reusing a pre-save request", async () => {
    const staleConfig = deferred<Response>();
    const freshConfig = deferred<Response>();
    let configGets = 0;
    let putBody: AppConfig | null = null;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/config" && init?.method === "PUT") {
        putBody = JSON.parse(String(init.body)) as AppConfig;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (path === "/api/config") {
        configGets += 1;
        if (configGets === 1) return Promise.resolve(jsonResponse(validConfig()));
        return configGets === 2 ? staleConfig.promise : freshConfig.promise;
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    store.draft!.server.host = "saved-value";

    const preSaveLoad = store.load();
    const save = store.save();
    for (let attempt = 0; attempt < 20 && configGets < 3; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }

    expect(putBody?.server.host).toBe("saved-value");
    expect(configGets).toBe(3);
    freshConfig.resolve(jsonResponse(validConfig("saved-value")));
    await save;

    staleConfig.resolve(jsonResponse(validConfig("stale-value")));
    await preSaveLoad;

    expect(store.draft?.server.host).toBe("saved-value");
    expect(store.dirty).toBe(false);
    expect(store.error).toBeNull();
    expect(store.loading).toBe(false);
  });

  test("config save remains committed when its post-PUT reload fails", async () => {
    let configGets = 0;
    let putCount = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/config/models") {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      if (path === "/api/config" && init?.method === "PUT") {
        putCount += 1;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (path === "/api/config") {
        configGets += 1;
        return Promise.resolve(
          configGets === 1
            ? jsonResponse(validConfig())
            : jsonResponse({ detail: "post-save reload failed" }, 502),
        );
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    store.draft!.server.host = "saved-despite-reload-error";

    await expect(store.save()).resolves.toBe(false);

    expect(putCount).toBe(1);
    expect(store.draft?.server.host).toBe("saved-despite-reload-error");
    expect(store.dirty).toBe(false);
    expect(store.error).toContain("post-save reload failed");
    expect(store.saving).toBe(false);
  });

  test("a newly saved provider can preserve its key after the reload fails", async () => {
    const puts: AppConfig[] = [];
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        puts.push(JSON.parse(String(init.body)) as AppConfig);
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (puts.length) {
        return Promise.resolve(jsonResponse({ detail: "post-save reload failed" }, 502));
      }
      return Promise.resolve(jsonResponse(
        String(input).endsWith("/models") ? virtualModels() : validConfig(),
      ));
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    store.addProvider("added");
    store.draft!.providers.added.base_url = "https://added.test";
    store.draft!.providers.added.api_key = "new-provider-secret";

    await expect(store.save()).resolves.toBe(false);
    expect(store.dirty).toBe(false);
    expect(store.buildPayload().providers.added.api_key).toBe("");
    store.draft!.providers.added.api_key = "";
    store.draft!.server.host = "next-edit";
    await expect(store.save()).resolves.toBe(false);

    expect(puts).toHaveLength(2);
    expect(puts[0].providers.added.api_key).toBe("new-provider-secret");
    expect(puts[1].providers.added.api_key).toBe("");
    expect(store.fieldErrors).toEqual({});
    expect(store.dirty).toBe(false);
  });

  test("acknowledging saved keys preserves newer edits and omits already persisted secrets", async () => {
    const put = deferred<Response>();
    let configGets = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return put.promise;
      if (String(input).endsWith("/models")) {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      configGets += 1;
      return Promise.resolve(jsonResponse(validConfig()));
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    store.addProvider("added");
    store.draft!.providers.added.base_url = "https://added.test";
    store.draft!.providers.added.api_key = "saved-added-secret";
    store.draft!.providers.provider.api_key = "saved-original-secret";

    const save = store.save();
    store.draft!.server.host = "newer-host";
    store.draft!.providers.provider.api_key = "newer-original-secret";
    put.resolve(jsonResponse({ status: "ok" }));
    await save;

    expect(configGets).toBe(1);
    expect(store.draft?.server.host).toBe("newer-host");
    expect(store.buildPayload().providers.provider.api_key).toBe("newer-original-secret");
    expect(store.buildPayload().providers.added.api_key).toBe("");
    store.draft!.providers.added.api_key = "";
    expect(store.validate()).toBe(true);
    expect(store.dirty).toBe(true);
  });

  test("mode switch keeps the persisted mode when reconciliation fails", async () => {
    let configGets = 0;
    let putMode: string | null = null;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/config/models") {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      if (path === "/api/config" && init?.method === "PUT") {
        putMode = (JSON.parse(String(init.body)) as AppConfig).router.mode;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (path === "/api/config") {
        configGets += 1;
        return Promise.resolve(
          configGets <= 2
            ? jsonResponse(validConfig())
            : jsonResponse({ detail: "mode reconciliation failed" }, 502),
        );
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();

    await expect(store.setRouterMode("sticky")).resolves.toBe(false);

    expect(putMode).toBe("sticky");
    expect(store.draft?.router.mode).toBe("sticky");
    expect(store.dirty).toBe(false);
    expect(store.error).toContain("mode reconciliation failed");
    expect(store.saving).toBe(false);
  });

  test("mode switch writes when only the cached editor already has the requested mode", async () => {
    let backend = validConfig();
    const puts: AppConfig[] = [];
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        backend = JSON.parse(String(init.body)) as AppConfig;
        puts.push(backend);
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      return Promise.resolve(jsonResponse(
        String(input).endsWith("/models") ? backend.models : backend,
      ));
    }) as typeof fetch;
    const store = useConfigStore();
    const app = useAppStore();
    await store.load();
    backend.router.mode = "sticky";
    await app.loadConfig(true);
    expect(store.draft?.router.mode).toBe("failover");
    expect(app.mode).toBe("sticky");

    await expect(app.setMode("failover")).resolves.toBe(true);

    expect(puts).toHaveLength(1);
    expect(backend.router.mode).toBe("failover");
    expect(app.mode).toBe("failover");
    expect(store.saving).toBe(false);
  });

  test("mode switch preserves external settings absent from the cached editor", async () => {
    let backend = validConfig();
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        backend = JSON.parse(String(init.body)) as AppConfig;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      return Promise.resolve(jsonResponse(
        String(input).endsWith("/models") ? backend.models : backend,
      ));
    }) as typeof fetch;
    const store = useConfigStore();
    const app = useAppStore();
    await store.load();
    await app.loadConfig();
    backend.providers.external = { ...backend.providers.provider };
    backend.server.log_backup_count = 8;

    await expect(app.setMode("sticky")).resolves.toBe(true);

    expect(backend.router.mode).toBe("sticky");
    expect(backend.providers.external).toBeDefined();
    expect(backend.server.log_backup_count).toBe(8);
    expect(store.dirty).toBe(false);
  });

  test("mode switch reserves saving while loading and preserves edits made during that load", async () => {
    const freshConfig = deferred<Response>();
    let configGets = 0;
    let putCount = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        putCount += 1;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (String(input).endsWith("/models")) {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      configGets += 1;
      return configGets === 1
        ? Promise.resolve(jsonResponse(validConfig()))
        : freshConfig.promise;
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    const switching = store.setRouterMode("sticky").catch((error: unknown) => error);
    expect(store.saving).toBe(true);
    store.draft!.server.host = "edited-during-mode-load";
    await expect(store.save()).rejects.toThrow("配置正在保存");
    await expect(store.setRouterMode("sticky")).rejects.toThrow("配置正在保存");
    freshConfig.resolve(jsonResponse(validConfig("remote-host")));
    const switchError = await switching;

    expect(switchError).toBeInstanceOf(Error);
    expect((switchError as Error).message).toContain("未保存更改");
    expect(putCount).toBe(0);
    expect(store.draft?.server.host).toBe("edited-during-mode-load");
    expect(store.draft?.router.mode).toBe("failover");
    expect(store.dirty).toBe(true);
    expect(store.saving).toBe(false);
  });

  test("mode switch refuses to write from its cache if the fresh read fails", async () => {
    let configGets = 0;
    let putCount = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        putCount += 1;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (String(input).endsWith("/models")) {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      configGets += 1;
      return Promise.resolve(configGets === 1
        ? jsonResponse(validConfig())
        : jsonResponse({ detail: "latest config unavailable" }, 503));
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();

    await expect(store.setRouterMode("sticky")).rejects.toThrow("latest config unavailable");

    expect(putCount).toBe(0);
    expect(store.draft?.router.mode).toBe("failover");
    expect(store.error).toBe("latest config unavailable");
    expect(store.saving).toBe(false);
  });

  test("an already persisted mode needs no PUT after the fresh read and releases saving", async () => {
    let backend = validConfig();
    let putCount = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        putCount += 1;
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      return Promise.resolve(jsonResponse(
        String(input).endsWith("/models") ? backend.models : backend,
      ));
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    backend = { ...backend, router: { ...backend.router, mode: "sticky" } };

    await expect(store.setRouterMode("sticky")).resolves.toBe(true);

    expect(putCount).toBe(0);
    expect(store.draft?.router.mode).toBe("sticky");
    expect(store.dirty).toBe(false);
    expect(store.saving).toBe(false);
  });

  test("app mode reflects a persisted switch when both reloads fail", async () => {
    let configGets = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/config/models") {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      if (path === "/api/config" && init?.method === "PUT") {
        return Promise.resolve(jsonResponse({ status: "ok" }));
      }
      if (path === "/api/config") {
        configGets += 1;
        return Promise.resolve(
          configGets === 1
            ? jsonResponse(validConfig())
            : jsonResponse({ detail: `reload-${configGets}-failed` }, 502),
        );
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const app = useAppStore();
    app.config = validConfig();

    await expect(app.setMode("sticky")).resolves.toBe(false);

    expect(app.mode).toBe("sticky");
    expect(app.staleData).toBe(true);
    expect(app.configError).toContain("reload-3-failed");
  });

  test("config rejects a concurrent save without sending a second PUT", async () => {
    const put = deferred<Response>();
    let putCount = 0;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/config/models") {
        return Promise.resolve(jsonResponse(virtualModels()));
      }
      if (path === "/api/config" && init?.method === "PUT") {
        putCount += 1;
        return put.promise;
      }
      if (path === "/api/config") {
        return Promise.resolve(jsonResponse(validConfig()));
      }
      throw new Error(`Unexpected request: ${path}`);
    }) as typeof fetch;
    const store = useConfigStore();
    await store.load();
    store.draft!.server.host = "first-edit";

    const firstSave = store.save();
    store.draft!.server.host = "newer-edit";

    expect(store.saving).toBe(true);
    await expect(store.save()).rejects.toThrow("配置正在保存");
    await expect(store.setRouterMode("sticky")).rejects.toThrow("配置正在保存");
    expect(putCount).toBe(1);

    put.resolve(jsonResponse({ status: "ok" }));
    await firstSave;

    expect(store.saving).toBe(false);
    expect(store.draft?.server.host).toBe("newer-edit");
    expect(store.dirty).toBe(true);
  });
});
