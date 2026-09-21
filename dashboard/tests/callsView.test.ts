import { afterEach, beforeEach, expect, test } from "bun:test";
import * as vue from "vue";
import * as pinia from "pinia";
import { compileScript, parse } from "vue/compiler-sfc";
import type { CallsPage, ModelStatReal } from "../src/api/types";
import { useCallsStore } from "../src/stores/calls";
import { useMetricsStore } from "../src/stores/metrics";
import { useRefreshStore } from "../src/stores/refresh";
import { useAutoRefresh } from "../src/composables/useAutoRefresh";
import * as format from "../src/utils/format";

// Compile the production setup script so watchers, lifecycle hooks, stores, and
// refresh registration run together without duplicating the page's load logic.
const source = await Bun.file(new URL("../src/views/Calls.vue", import.meta.url)).text();
const script = compileScript(parse(source).descriptor, { id: "calls-view-test" });
const compiled = new Bun.Transpiler({ loader: "ts" })
  .transformSync(script.content)
  .replace(/import\s+([\s\S]*?)\s+from\s+"([^"]+)";/g, (_, binding: string, path: string) => {
    const pattern = binding.trim().startsWith("{")
      ? binding.replace(/\bas\b/g, ":")
      : `{ default: ${binding} }`;
    return `const ${pattern} = modules[${JSON.stringify(path)}];`;
  })
  .replace("export default", "return");
const createComponent = new Function("modules", compiled);

const renderer = vue.createRenderer<object, object>({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert() {},
  remove() {},
  setText() {},
  setElementText() {},
  parentNode: () => null,
  nextSibling: () => null,
  patchProp() {},
});

function page(pageNumber = 1, pages = 1): CallsPage {
  return { data: [], total: pages * 50, page: pageNumber, size: 50, pages };
}

function modelStat(model: string): ModelStatReal {
  return {
    provider: "provider",
    model,
    count: 1,
    success_count: 1,
    total_input_tokens: 0,
    total_output_tokens: 0,
    total_cost_usd: 0,
  };
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status });
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await vue.nextTick();
}

const originalFetch = globalThis.fetch;
const cleanups: Array<() => Promise<void>> = [];

beforeEach(() => pinia.setActivePinia(pinia.createPinia()));
afterEach(async () => {
  for (const cleanup of cleanups.splice(0)) await cleanup();
  globalThis.fetch = originalFetch;
});

function mountCalls(query: Record<string, string> = { page: "1", model: "old-model" }) {
  const route = vue.reactive({ query });
  const redirects: Record<string, string>[] = [];
  const calls: Array<{ url: string; resolve: (value: Response) => void }> = [];
  const requested: string[] = [];
  const catalog = { model: "fresh-model", failing: false };
  const store = useCallsStore();
  const metrics = useMetricsStore();
  metrics.loadedOnce = true;
  metrics.byRealModel = [modelStat("cached-model")];
  const refresh = useRefreshStore();
  refresh.setIntervalSec(0);
  globalThis.fetch = ((input: RequestInfo | URL) => {
    const path = String(input);
    requested.push(path);
    if (path.startsWith("/api/calls?")) {
      return new Promise<Response>((resolve) => calls.push({ url: path, resolve }));
    }
    if (path === "/api/metrics/by-real-model") {
      return Promise.resolve(catalog.failing
        ? json({ detail: "catalog unavailable" }, 503)
        : json([modelStat(catalog.model)]));
    }
    if (path === "/api/metrics/summary") return Promise.resolve(json({ total_calls: 1 }));
    if (path.startsWith("/api/metrics/")) return Promise.resolve(json([]));
    throw new Error(`Unexpected request: ${path}`);
  }) as typeof fetch;
  const component = createComponent({
    vue,
    pinia,
    "vue-router": {
      useRoute: () => route,
      useRouter: () => ({
        replace: ({ query: next }: { query: Record<string, string> }) => {
          redirects.push(next);
          return Promise.resolve();
        },
      }),
    },
    "@/stores/calls": { useCallsStore },
    "@/stores/metrics": { useMetricsStore },
    "@/utils/format": format,
    "@/composables/useAutoRefresh": { useAutoRefresh },
    "@/components/CallDetail.vue": { default: {} },
  });
  const app = renderer.createApp({ ...component, render: () => null });
  app.mount({});
  let unmounted = false;
  function unmount() {
    if (unmounted) return;
    app.unmount();
    unmounted = true;
  }
  cleanups.push(async () => {
    unmount();
    for (const call of calls) call.resolve(json(page()));
    await flush();
  });
  return { route, redirects, calls, requested, catalog, store, metrics, refresh, unmount };
}

test("a stale Calls load cannot redirect a newer filter using an old out-of-range page", async () => {
  const view = mountCalls();
  expect(view.calls).toHaveLength(1);
  view.route.query = { page: "10", model: "old-model" };
  await vue.nextTick();
  view.calls[1].resolve(json(page(10, 5)));
  await flush();
  expect(view.redirects).toEqual([{ page: "5", model: "old-model" }]);

  view.route.query = { page: "1", model: "new-model" };
  await vue.nextTick();
  view.calls[0].resolve(json(page()));
  await flush();

  expect(view.calls[2].url).toContain("model=new-model");
  expect(view.redirects).toEqual([{ page: "5", model: "old-model" }]);
  view.calls[2].resolve(json(page()));
  await flush();
});

test("unmount invalidates pending Calls redirects and unregisters refresh", async () => {
  const view = mountCalls({ page: "10" });
  view.unmount();
  view.route.query = { page: "1", model: "other-page-filter" };
  view.calls[0].resolve(json(page(10, 5)));
  await flush();
  expect(view.redirects).toEqual([]);
  const requests = view.requested.length;
  expect(await view.refresh.runHandlers()).toBe(false);
  expect(view.requested).toHaveLength(requests);
});

test("the current Calls load still redirects an out-of-range page", async () => {
  const view = mountCalls({ page: "10", status: "error" });
  view.calls[0].resolve(json(page(10, 5)));
  await flush();
  expect(view.redirects).toEqual([{ page: "5", status: "error" }]);
});

test("Calls refreshes cached model choices on mount and every refresh cycle", async () => {
  const view = mountCalls();
  view.calls[0].resolve(json(page()));
  await flush();
  expect(view.metrics.byRealModel[0].model).toBe("fresh-model");

  for (const model of ["first-new-model", "second-new-model"]) {
    view.catalog.model = model;
    const refreshing = view.refresh.runHandlers();
    await vue.nextTick();
    view.calls.at(-1)!.resolve(json(page()));
    expect(await refreshing).toBe(false);
    expect(view.metrics.byRealModel[0].model).toBe(model);
  }
});

test("a Calls catalog refresh failure is reported and a later cycle recovers", async () => {
  const view = mountCalls();
  view.calls[0].resolve(json(page()));
  await flush();
  view.catalog.failing = true;
  const failing = view.refresh.runHandlers();
  await vue.nextTick();
  view.calls.at(-1)!.resolve(json(page()));
  expect(await failing).toBe(true);

  view.catalog.failing = false;
  view.catalog.model = "recovered-model";
  const recovering = view.refresh.runHandlers();
  await vue.nextTick();
  view.calls.at(-1)!.resolve(json(page()));
  expect(await recovering).toBe(false);
  expect(view.metrics.byRealModel[0].model).toBe("recovered-model");
});
