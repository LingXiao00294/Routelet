export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? (formatDetail(detail) || `HTTP ${status}`));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function formatDetail(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (typeof detail === "object" && detail !== null && "detail" in detail) {
    return formatDetail((detail as { detail: unknown }).detail);
  }
  if (typeof detail === "object" && detail !== null && "error" in detail) {
    const err = (detail as { error: { message?: string } }).error;
    if (err?.message) return err.message;
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

const DEFAULT_TIMEOUT_MS = 30_000;

export async function request<T>(
  path: string,
  options: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = options;
  const controller = new AbortController();
  const callerSignal = init.signal;
  let abortReason: "caller" | "timeout" | null = null;
  const abortFromCaller = () => {
    if (abortReason) return;
    abortReason = "caller";
    controller.abort();
  };
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timer = setTimeout(() => {
    if (abortReason) return;
    abortReason = "timeout";
    controller.abort();
  }, timeoutMs);

  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  try {
    const res = await fetch(path, {
      ...init,
      headers,
      signal: controller.signal,
    });

    if (res.status === 204) {
      return undefined as T;
    }

    const text = await res.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }

    if (!res.ok) {
      const detail =
        data && typeof data === "object" && data !== null && "detail" in data
          ? (data as { detail: unknown }).detail
          : data;
      throw new ApiError(res.status, detail);
    }

    return data as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (
      controller.signal.aborted ||
      (err instanceof DOMException && err.name === "AbortError")
    ) {
      throw new ApiError(0, abortReason === "caller" ? "请求已取消" : "请求超时");
    }
    throw new ApiError(0, err instanceof Error ? err.message : "网络错误");
  } finally {
    clearTimeout(timer);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}
