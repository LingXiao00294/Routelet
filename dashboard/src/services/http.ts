export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
  timeout = 20000,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort(init.signal?.reason);
  if (init.signal?.aborted) abort();
  else init.signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(
    () => controller.abort(new Error("请求超时，请检查服务连接")),
    timeout,
  );
  try {
    const response = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const text = await response.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      throw new ApiError(
        "服务返回了无法解析的响应（HTTP " + response.status + "）",
        response.status,
      );
    }
    if (!response.ok) {
      const obj = body as {
        detail?: unknown;
        error?: { message?: string };
      } | null;
      const detail = obj?.detail ?? obj?.error?.message;
      throw new ApiError(
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : "请求失败（HTTP " + response.status + "）",
        response.status,
      );
    }
    return body as T;
  } catch (error) {
    if (controller.signal.aborted)
      throw controller.signal.reason instanceof Error
        ? controller.signal.reason
        : new Error("请求已取消");
    throw error;
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", abort);
  }
}
export const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : "操作失败，请重试";
