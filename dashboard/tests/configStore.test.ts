import { beforeEach, describe, expect, test } from "bun:test";
import { createPinia, setActivePinia } from "pinia";
import type { AppConfig } from "../src/api/types";
import { useConfigStore } from "../src/stores/config";

function validConfig(): AppConfig {
  return {
    server: {
      host: "127.0.0.1",
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
      zai: {
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
        models: { existing: {} },
      },
    },
    models: {},
  };
}

beforeEach(() => setActivePinia(createPinia()));

describe("actual-model catalog mutations", () => {
  test("adds, edits, and removes an unreferenced actual model", () => {
    const store = useConfigStore();
    store.draft = validConfig();

    expect(store.addActualModel("zai", " glm-5 ", { input_price_per_million: 1 })).toBe(true);
    expect(store.addActualModel("zai", "glm-5", {})).toBe(false);
    expect(store.updateActualModel("zai", "glm-5", { output_price_per_million: 4 })).toBe(true);
    expect(store.draft.providers.zai.models["glm-5"]).toEqual({ output_price_per_million: 4 });

    store.removeActualModel("zai", "glm-5");
    expect(store.draft.providers.zai.models["glm-5"]).toBeUndefined();
  });

  test("deletes a virtual model when its last actual model is removed", () => {
    const store = useConfigStore();
    store.draft = validConfig();
    store.models = {
      routerA: {
        pinned_model: { provider: "zai", model: "existing" },
        models: [{ provider: "zai", model: "existing" }],
      },
    };
    store.removeActualModel("zai", "existing");
    expect(store.draft.providers.zai.models).toEqual({});
    expect(store.models).toEqual({});
    expect(store.buildPayload().models).toEqual({});
    expect(store.validate()).toBe(true);
  });

  test.each(["sticky", "failover"] as const)(
    "%s deletion cleans every reference and pin while preserving other candidates",
    (mode) => {
      const store = useConfigStore();
      store.draft = validConfig();
      store.draft.router.mode = mode;
      store.draft.providers.other = {
        ...store.draft.providers.zai,
        models: { existing: {}, fallback: {} },
      };
      const deleted = { provider: "zai", model: "existing" };
      const remaining = [
        { provider: "other", model: "existing" },
        { provider: "other", model: "fallback" },
      ];
      store.models = {
        routerA: { models: [deleted, ...remaining], pinned_model: deleted },
        routerB: { models: [...remaining, deleted], pinned_model: remaining[1] },
        stalePin: { models: [...remaining], pinned_model: deleted },
      };

      store.removeActualModel("zai", "existing");

      for (const name of ["routerA", "routerB", "stalePin"]) {
        expect(store.models[name].models).toEqual(remaining);
      }
      expect(store.models.routerA.pinned_model).toEqual(mode === "sticky" ? remaining[0] : null);
      expect(store.models.stalePin.pinned_model).toEqual(mode === "sticky" ? remaining[0] : null);
      expect(store.models.routerB.pinned_model).toEqual(remaining[1]);
      expect(store.draft.providers.other.models.existing).toEqual({});
      expect(store.validate()).toBe(true);
    },
  );

  test("provider deletion removes all its references and allows an empty config", () => {
    const store = useConfigStore();
    store.draft = validConfig();
    store.draft.providers.zai.models.second = {};
    store.models = {
      routerA: {
        models: [
          { provider: "zai", model: "existing" },
          { provider: "zai", model: "second" },
        ],
        pinned_model: { provider: "zai", model: "second" },
      },
    };

    store.removeProvider("zai");

    expect(store.buildPayload().providers).toEqual({});
    expect(store.buildPayload().models).toEqual({});
    expect(store.validate()).toBe(true);
  });

  test("removing a virtual model then replacing its actual model leaves no stale references", () => {
    const store = useConfigStore();
    store.draft = validConfig();
    store.models = {
      routerA: {
        models: [{ provider: "zai", model: "existing" }],
        pinned_model: { provider: "zai", model: "existing" },
      },
    };

    store.removeModel("routerA");
    store.removeActualModel("zai", "existing");
    store.addActualModel("zai", "renamed");

    expect(store.buildPayload().models).toEqual({});
    expect(store.buildPayload().providers.zai.models).toEqual({ renamed: {} });
    expect(store.validate()).toBe(true);
  });

  test("updates an existing actual model by its exact key", () => {
    const store = useConfigStore();
    store.draft = validConfig();
    store.draft.providers.zai.models[" glm-5 "] = { input_price_per_million: 1 };

    expect(
      store.updateActualModel("zai", " glm-5 ", { output_price_per_million: 4 }),
    ).toBe(true);
    expect(store.draft.providers.zai.models[" glm-5 "]).toEqual({
      output_price_per_million: 4,
    });
    expect(store.draft.providers.zai.models["glm-5"]).toBeUndefined();
  });

  test("rejects duplicate and dangling virtual-model references during validation", () => {
    const store = useConfigStore();
    store.draft = validConfig();
    store.models = {
      routerA: {
        pinned_model: null,
        models: [
          { provider: "zai", model: "existing" },
          { provider: "zai", model: "existing" },
          { provider: "zai", model: "missing" },
        ],
      },
    };

    expect(store.validate()).toBe(false);
    expect(store.fieldErrors["models.routerA.ref.1"]).toContain("不能重复");
    expect(store.fieldErrors["models.routerA.ref.2.model"]).toContain("不存在");
  });

  test("matches backend lower bounds for circuit and log settings", () => {
    const store = useConfigStore();
    store.draft = validConfig();
    store.draft.server.log_max_bytes = 0;
    store.draft.router.failure_threshold = 0;
    store.draft.providers.zai.failure_threshold = 0;

    expect(store.validate()).toBe(false);
    expect(store.fieldErrors["server.log_max_bytes"]).toContain("> 0");
    expect(store.fieldErrors["router.failure_threshold"]).toContain("≥ 1");
    expect(store.fieldErrors["providers.zai.failure_threshold"]).toContain("≥ 1");
  });

  test("matches backend provider base URL rules", () => {
    const invalidUrls = [
      "/relative",
      "ftp://provider.test",
      " https://provider.test ",
      "https://provider.test?token=secret",
      "https://provider.test#fragment",
      "https://user:password@provider.test",
    ];

    for (const baseUrl of invalidUrls) {
      const store = useConfigStore();
      store.draft = validConfig();
      store.draft.providers.zai.base_url = baseUrl;

      expect(store.validate()).toBe(false);
      expect(store.fieldErrors["providers.zai.base_url"]).toContain("绝对 HTTP(S) URL");
    }
  });
});
