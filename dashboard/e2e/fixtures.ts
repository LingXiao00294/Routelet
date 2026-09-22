import type { Page } from "@playwright/test";
import { clone, newProvider, normalizeConfig } from "../src/domain/config";
import type { Call, Config } from "../src/domain/types";

export function sampleConfig(): Config {
  return normalizeConfig({
    router: { mode: "failover", failure_threshold: 5, recovery_timeout: 600 },
    providers: {
      Anthropic: {
        ...newProvider(),
        api_key: "sk-a********test",
        has_key: true,
        api_key_unresolved: false,
        base_url: "https://api.anthropic.com",
        models: {
          "claude-sonnet-4-6": {
            input_price_per_million: 3,
            output_price_per_million: 15,
            cache_read_price_per_million: 0.3,
          },
          "claude-haiku-4-5": {
            input_price_per_million: 1,
            output_price_per_million: 5,
          },
        },
      },
      DeepSeek: {
        ...newProvider(),
        api_key: "sk-d********test",
        has_key: true,
        api_key_unresolved: false,
        base_url: "https://api.deepseek.com/anthropic",
        models: {
          "deepseek-v4-pro": {
            input_price_per_million: 1.74,
            output_price_per_million: 3.48,
          },
          "deepseek-v4-flash": {
            input_price_per_million: 0.14,
            output_price_per_million: 0.28,
          },
        },
      },
      "智谱 AI": {
        ...newProvider(),
        api_key: "zai-********test",
        has_key: true,
        api_key_unresolved: false,
        base_url: "https://api.z.ai/api/anthropic",
        models: {
          "glm-5.2": {
            input_price_per_million: 1,
            output_price_per_million: 2,
          },
        },
      },
    },
    models: {
      "coding-assistant": {
        pinned_model: { provider: "Anthropic", model: "claude-sonnet-4-6" },
        models: [
          { provider: "Anthropic", model: "claude-sonnet-4-6" },
          { provider: "DeepSeek", model: "deepseek-v4-pro" },
          { provider: "智谱 AI", model: "glm-5.2" },
        ],
      },
      "fast-response": {
        pinned_model: { provider: "DeepSeek", model: "deepseek-v4-flash" },
        models: [
          { provider: "DeepSeek", model: "deepseek-v4-flash" },
          { provider: "Anthropic", model: "claude-haiku-4-5" },
        ],
      },
      "reasoning-pro": {
        pinned_model: { provider: "DeepSeek", model: "deepseek-v4-pro" },
        models: [
          { provider: "DeepSeek", model: "deepseek-v4-pro" },
          { provider: "智谱 AI", model: "glm-5.2" },
        ],
      },
    },
  });
}
export const summary = {
  total_calls: 24861,
  success_count: 24707,
  error_count: 154,
  success_rate: 99.38,
  total_input_tokens: 14240000,
  total_output_tokens: 3860000,
  total_cache_read: 730000,
  total_cache_write: 120000,
  total_cost_usd: 124.86,
  avg_latency_ms: 1824,
};
export function sampleCalls(): Call[] {
  const models = ["coding-assistant", "fast-response", "reasoning-pro"];
  return Array.from({ length: 47 }, (_, i) => ({
    id:
      (0xa81f0000 + i * 17933).toString(16) +
      "-23fe-4200-aabd-" +
      String(i).padStart(12, "0"),
    timestamp: new Date(Date.now() - i * 137000).toISOString(),
    virtual_model: models[i % 3],
    provider_name: i % 3 === 0 ? "Anthropic" : "DeepSeek",
    provider_model:
      i % 3 === 0
        ? "claude-sonnet-4-6"
        : i % 3 === 1
          ? "deepseek-v4-flash"
          : "deepseek-v4-pro",
    attempt: i % 8 === 4 ? 2 : 1,
    latency_ms: 1240 + i * 29,
    status: i % 9 === 3 ? "error" : "success",
    input_tokens: 1200 + i * 107,
    output_tokens: 320 + i * 33,
    cache_read_tokens: i * 14,
    cache_write_tokens: 0,
    cost_usd: i % 9 === 3 ? null : 0.012 + i * 0.00087,
    input_price_per_million: 3,
    output_price_per_million: 15,
    cache_read_price_per_million: 0,
    cache_write_price_per_million: null,
    request_body: JSON.stringify({
      model: models[i % 3],
      messages: [{ role: "user", content: "这是用于验证界面的模拟请求。" }],
    }),
    response_body: JSON.stringify({
      content: [{ type: "text", text: "模拟响应：路由已成功连接。" }],
    }),
    failover_details: JSON.stringify([
      null,
      {},
      {
        provider: "backup",
        model: "test-model",
        error: "timeout",
        latency_ms: 600,
      },
    ]),
  }));
}
export async function installApi(page: Page, empty = false) {
  const state = {
    config: empty ? normalizeConfig({}) : sampleConfig(),
    writes: [] as Config[],
    calls: empty ? [] : sampleCalls(),
    offline: false,
    lastCalls: "",
    failedSave: false,
  };
  await page.route("**/health", (route) =>
    route.fulfill({
      status: state.offline ? 503 : 200,
      json: { status: state.offline ? "offline" : "ok" },
    }),
  );
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    if (state.offline) {
      await route.fulfill({ status: 503, json: { detail: "测试连接中断" } });
      return;
    }
    if (path === "/api/config") {
      if (route.request().method() === "PUT") {
        if (state.failedSave) {
          await route.fulfill({
            status: 400,
            json: { detail: "模拟配置校验失败" },
          });
          return;
        }
        const submitted = route.request().postDataJSON();
        state.writes.push(clone(submitted));
        state.config = normalizeConfig(submitted);
        for (const provider of Object.values(state.config.providers)) {
          provider.api_key = "sk-n********test";
          provider.has_key = true;
          provider.api_key_unresolved = false;
        }
        await route.fulfill({ json: { status: "ok" } });
        return;
      }
      await route.fulfill({ json: state.config });
      return;
    }
    if (path === "/api/metrics/summary") {
      await route.fulfill({
        json: empty
          ? {
              ...summary,
              total_calls: 0,
              success_count: 0,
              error_count: 0,
              total_cost_usd: 0,
            }
          : summary,
      });
      return;
    }
    if (path === "/api/metrics/daily") {
      const days = Number(url.searchParams.get("days")) || 14;
      const values = [
        380, 550, 460, 730, 650, 800, 970, 700, 860, 1100, 890, 1250, 1160,
        1420,
      ];
      await route.fulfill({
        json: empty
          ? []
          : Array.from({ length: days }, (_, index) => ({
              day: new Date(Date.now() - (days - 1 - index) * 86400000)
                .toISOString()
                .slice(0, 10),
              count: values[index % 14],
              success_count: values[index % 14] - 3,
              input_tokens: 16000,
              output_tokens: 4000,
              cache_read_tokens: 0,
              cache_write_tokens: 0,
              cost_usd: 1.83,
            })),
      });
      return;
    }
    if (path === "/api/metrics/by-model") {
      await route.fulfill({
        json: empty
          ? []
          : ["coding-assistant", "fast-response", "reasoning-pro"].map(
              (name, index) => ({
                virtual_model: name,
                count: [14408, 7585, 2868][index],
                success_count: [14321, 7540, 2846][index],
                total_input_tokens: 150000,
                total_output_tokens: 50000,
                total_cost_usd: [82.32, 14.17, 28.37][index],
              }),
            ),
      });
      return;
    }
    if (path === "/api/metrics/by-real-model") {
      await route.fulfill({
        json: empty
          ? []
          : state.calls.slice(0, 3).map((call) => ({
              provider: call.provider_name,
              model: call.provider_model,
              count: 100,
              success_count: 99,
              total_input_tokens: 1200,
              total_output_tokens: 350,
              total_cost_usd: 4.23,
            })),
      });
      return;
    }
    if (path.startsWith("/api/circuit-breaker")) {
      await route.fulfill({
        json: path.endsWith("/reset")
          ? { status: "ok" }
          : { Anthropic: "closed", DeepSeek: "closed", "智谱 AI": "closed" },
      });
      return;
    }
    if (path === "/api/calls") {
      state.lastCalls = url.search;
      const page = Number(url.searchParams.get("page")) || 1,
        size = Number(url.searchParams.get("size")) || 20;
      const filters = {
        model: "virtual_model",
        provider: "provider_name",
        provider_model: "provider_model",
        status: "status",
      } as const;
      const calls = state.calls.filter((call) =>
        Object.entries(filters).every(
          ([key, field]) =>
            !url.searchParams.get(key) ||
            url.searchParams.get(key) === call[field],
        ),
      );
      await route.fulfill({
        json: {
          data: calls.slice((page - 1) * size, page * size),
          total: calls.length,
          page,
          size,
          pages: Math.max(1, Math.ceil(calls.length / size)),
        },
      });
      return;
    }
    if (path.startsWith("/api/calls/")) {
      const call = state.calls.find((call) => path.endsWith(call.id));
      await route.fulfill({
        status: call ? 200 : 404,
        json: call ?? { detail: "call not found" },
      });
      return;
    }
    await route.fulfill({
      status: 404,
      json: { detail: "Unexpected test API" },
    });
  });
  await page.route("**/v1/messages", async (route) => {
    const body = route.request().postDataJSON();
    if (!body.stream) {
      await route.fulfill({
        json: {
          content: [
            { type: "text", text: "你好，我是通过 Agent Router 连接的模型。" },
          ],
          usage: { input_tokens: 16, output_tokens: 24 },
        },
      });
      return;
    }
    const events = [
      {
        type: "message_start",
        message: { usage: { input_tokens: 16, output_tokens: 0 } },
      },
      {
        type: "content_block_delta",
        delta: { type: "text_delta", text: "你好，" },
      },
      {
        type: "content_block_delta",
        delta: { type: "text_delta", text: "这条路由已成功连接。" },
      },
      { type: "message_delta", usage: { output_tokens: 24 } },
      { type: "message_stop" },
    ];
    await route.fulfill({
      contentType: "text/event-stream",
      body: events
        .map(
          (value) =>
            "event: " +
            value.type +
            "\ndata: " +
            JSON.stringify(value) +
            "\n\n",
        )
        .join(""),
    });
  });
  return state;
}
