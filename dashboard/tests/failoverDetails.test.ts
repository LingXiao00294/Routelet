import { expect, test } from "bun:test";
import { compile } from "vue";
import { parse } from "vue/compiler-sfc";
import * as format from "../src/utils/format";

const first = { provider: "first", model: "model-a", error: "overloaded", latency_ms: 12.5 };
const second = { provider: "second", model: "model-b", error: "timeout", latency_ms: 0 };

test("failover parsing preserves valid entries and their order", () => {
  expect(format.parseFailover(JSON.stringify([first, second]))).toEqual([first, second]);
});

test("failover parsing ignores null, arrays, and primitive entries", () => {
  const raw = JSON.stringify([null, [], true, 42, "bad", first, [second], false, second]);
  expect(format.parseFailover(raw)).toEqual([first, second]);
});

test("failover parsing validates identity and error fields while retaining empty exception messages", () => {
  const emptyMessage = { provider: "timeout-provider", model: "model", error: "" };
  const raw = JSON.stringify([
    {},
    { provider: "provider", model: "model" },
    { ...first, provider: 12 },
    { ...first, provider: " " },
    { ...first, model: null },
    { ...first, model: "" },
    { ...first, error: {} },
    { ...first, error: null },
    first,
    emptyMessage,
    second,
  ]);
  expect(format.parseFailover(raw)).toEqual([first, emptyMessage, second]);
});

test("failover parsing retains only finite non-negative numeric latency", () => {
  const withoutLatency = { provider: "provider", model: "model", error: "failure" };
  for (const latency of ["-1", "1e400", "-1e400", '"10"', "null", "true", "{}", "[]"]) {
    const raw = `[{"provider":"provider","model":"model","error":"failure","latency_ms":${latency}}]`;
    expect(format.parseFailover(raw)).toEqual([withoutLatency]);
  }
  expect(format.parseFailover(JSON.stringify([withoutLatency, second]))).toEqual([
    withoutLatency,
    second,
  ]);
});

test.each([null, "", "not JSON", "null", "{}", "42", '"text"'])(
  "failover parsing tolerates invalid top-level data %s",
  (raw) => expect(format.parseFailover(raw)).toEqual([]),
);

test("a malformed failover entry does not break the production call detail template", async () => {
  const source = await Bun.file(new URL("../src/components/CallDetail.vue", import.meta.url)).text();
  const template = parse(source).descriptor.template!.content;
  const render = compile(template);
  const failover = format.parseFailover(JSON.stringify([first, null, second]));
  const context = {
    ...format,
    record: {
      id: "call",
      timestamp: "2026-01-01T00:00:00Z",
      virtual_model: "virtual",
      provider_name: null,
      provider_type: null,
      provider_model: null,
      provider_url: null,
      attempt: 2,
      status: "error",
      latency_ms: 20,
      request_body: "{}",
      response_body: "{}",
    },
    loading: false,
    error: null,
    failover,
    providerLabel: "未命中（见 Failover 链）",
    $emit() {},
  };
  expect(() => render(context, [])).not.toThrow();
  expect(failover).toEqual([first, second]);
});
