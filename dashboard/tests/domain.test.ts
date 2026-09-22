import { describe, expect, test } from "bun:test";
import {
  affectedRoutes,
  canonical,
  cleanConfig,
  clone,
  newProvider,
  normalizeConfig,
  refKey,
  removeCatalogEntry,
  validateConfig,
} from "../src/domain/config";
import {
  csvCell,
  fillDays,
  latency,
  money,
  parseAttempts,
} from "../src/domain/format";
import { EventDecoder } from "../src/services/stream";

function config() {
  return normalizeConfig({
    providers: {
      a: {
        ...newProvider(),
        api_key: "secret-key",
        base_url: "https://a.test",
        models: { shared: { input_price_per_million: 0 }, other: {} },
      },
      b: {
        ...newProvider(),
        api_key: "secret-key",
        base_url: "https://b.test",
        models: { shared: {} },
      },
    },
    models: {
      mixed: {
        models: [
          { provider: "a", model: "shared" },
          { provider: "b", model: "shared" },
        ],
        pinned_model: { provider: "a", model: "shared" },
      },
      only: {
        models: [{ provider: "a", model: "shared" }],
        pinned_model: { provider: "a", model: "shared" },
      },
    },
  });
}
describe("configuration contracts", () => {
  test("removing a catalog model cleans references and pins atomically without affecting a namesake", () => {
    const value = config();
    expect(affectedRoutes(value, "a", "shared")).toEqual(["mixed", "only"]);
    removeCatalogEntry(value, "a", "shared");
    expect(Object.keys(value.models)).toEqual(["mixed"]);
    expect(value.models.mixed.pinned_model).toEqual({
      provider: "b",
      model: "shared",
    });
    expect(value.providers.a.models.other).toEqual({});
    expect(value.providers.b.models.shared).toEqual({});
    expect(validateConfig(value)).toEqual([]);
  });
  test("failover removal clears pin and keeps survivor order", () => {
    const value = config();
    value.router.mode = "failover";
    value.models.mixed.models.push({ provider: "a", model: "other" });
    removeCatalogEntry(value, "a", "shared");
    expect(value.models.mixed.pinned_model).toBeUndefined();
    expect(value.models.mixed.models).toEqual([
      { provider: "b", model: "shared" },
      { provider: "a", model: "other" },
    ]);
  });
  test("empty workspace is a valid intentional configuration", () =>
    expect(validateConfig(normalizeConfig({}))).toEqual([]));
  test("identity is structured, including slashes and punctuation", () => {
    expect(refKey({ provider: "a/b", model: "c" })).not.toBe(
      refKey({ provider: "a", model: "b/c" }),
    );
  });
  test("validation rejects duplicates, invalid pins, deleted refs and non-finite prices", () => {
    const value = config();
    value.models.mixed.models.push(clone(value.models.mixed.models[0]));
    value.models.only.pinned_model = { provider: "missing", model: "x" };
    value.providers.a.models.shared.output_price_per_million = Infinity;
    expect(validateConfig(value).join(" ")).toContain("候选模型重复");
    expect(validateConfig(value).join(" ")).toContain("固定模型");
    expect(validateConfig(value).join(" ")).toContain("价格无效");
    delete value.providers.a;
    expect(validateConfig(value).join(" ")).toContain("不存在");
  });
  test("URLs reject whitespace, credentials, query and non-http protocols", () => {
    for (const url of [
      "https://a.test /path",
      "https://user:pass@a.test",
      "https://a.test?q=1",
      "file:///tmp",
      "https://a.test/#a",
    ]) {
      const value = config();
      value.providers.a.base_url = url;
      expect(
        validateConfig(value).some((error) => error.includes("HTTP(S)")),
      ).toBe(true);
    }
  });
  test("blank prices differ from zero and transient server flags are stripped", () => {
    const value = config();
    value.providers.a.has_key = true;
    value.providers.a.api_key_unresolved = false;
    value.providers.a.models.shared.cache_read_price_per_million = null;
    const clean = cleanConfig(value);
    expect(clean.providers.a.has_key).toBeUndefined();
    expect(clean.providers.a.api_key_unresolved).toBeUndefined();
    expect(clean.providers.a.models.shared.input_price_per_million).toBe(0);
    expect(
      clean.providers.a.models.shared.cache_read_price_per_million,
    ).toBeNull();
    expect(value.providers.a.has_key).toBe(true);
  });
  test("snapshot comparison ignores object key order, preserves priority order", () => {
    expect(canonical({ b: 2, a: 1 })).toBe(canonical({ a: 1, b: 2 }));
    expect(canonical([1, 2])).not.toBe(canonical([2, 1]));
  });
  test("existing blank keys are preserved but new providers require credentials", () => {
    const value = config();
    value.providers.a.api_key = "";
    expect(validateConfig(value, config())).toEqual([]);
    expect(
      validateConfig(value).some((error) => error.includes("API Key")),
    ).toBe(true);
  });
});
describe("honest metric presentation", () => {
  test("null, overflowing and zero costs have distinct presentation", () => {
    expect(money(null)).toBe("—");
    expect(money(Infinity)).toBe("—");
    expect(money(0)).toBe("$0.0000");
    expect(latency(-1)).toBe("—");
    expect(latency(0)).toBe("0 ms");
  });
  test("daily gaps use UTC calendar days, including month boundaries", () => {
    const rows = fillDays([], 3, new Date("2026-03-01T01:00:00+08:00"));
    expect(rows.map((row) => row.day)).toEqual([
      "2026-02-26",
      "2026-02-27",
      "2026-02-28",
    ]);
    expect(rows.every((row) => row.count === 0)).toBe(true);
  });
  test("malformed historical failover rows do not poison useful details", () => {
    const result = parseAttempts(
      JSON.stringify([
        null,
        {},
        "bad",
        { provider: "a", model: 2, latency_ms: -2, error: "" },
        { provider: "b", model: "x", error: "timeout", latency_ms: 123 },
      ]),
    );
    expect(result).toHaveLength(2);
    expect(result[0].error).toBe("");
    expect(result[0].model).toBeUndefined();
    expect(result[0].latency_ms).toBeUndefined();
    expect(result[1].latency_ms).toBe(123);
    expect(parseAttempts("{bad")).toEqual([]);
  });
  test("CSV quotes embedded delimiters and neutralizes spreadsheet formulas", () => {
    expect(csvCell('a,"b"')).toBe('"a,""b"""');
    expect(csvCell("=HYPERLINK(1)")).toBe('"\'=HYPERLINK(1)"');
    expect(csvCell(null)).toBe('""');
  });
});
describe("stream framing", () => {
  test("handles BOM, split CRLF, comments and multiline data", () => {
    const decoder = new EventDecoder();
    expect(decoder.push("\uFEFFevent: message_start\r")).toEqual([]);
    expect(decoder.push("\ndata: first\r\ndata: second\r\n\r")).toEqual([]);
    expect(decoder.push("\n: heartbeat\n\n")).toEqual([
      { event: "message_start", data: "first\nsecond" },
    ]);
  });
  test("buffers split JSON and drains final events", () => {
    const decoder = new EventDecoder();
    expect(decoder.push('event: content_block_delta\ndata: {"delta":')).toEqual(
      [],
    );
    expect(decoder.push('{"text":"你好"}}\n\n')).toEqual([
      { event: "content_block_delta", data: '{"delta":{"text":"你好"}}' },
    ]);
    expect(decoder.push('data: {"type":"message_stop"}', true)).toHaveLength(1);
  });
});
