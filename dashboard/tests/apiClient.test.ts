import { afterEach, expect, test } from "bun:test";
import { ApiError, request } from "../src/api/client";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function abortableFetch(_input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    const signal = init?.signal;
    const rejectAbort = () => reject(new DOMException("aborted", "AbortError"));
    if (signal?.aborted) rejectAbort();
    else signal?.addEventListener("abort", rejectAbort, { once: true });
  });
}

test("caller cancellation is forwarded and differs from timeout", async () => {
  globalThis.fetch = abortableFetch as typeof fetch;
  const controller = new AbortController();
  const pending = request("/health", {
    signal: controller.signal,
    timeoutMs: 50,
  });

  controller.abort();

  try {
    await pending;
    throw new Error("request should have been cancelled");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("请求已取消");
  }
});

test("internal deadline still reports timeout", async () => {
  globalThis.fetch = abortableFetch as typeof fetch;

  try {
    await request("/health", { timeoutMs: 1 });
    throw new Error("request should have timed out");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("请求超时");
  }
});
