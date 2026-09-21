import { request } from "./client";
import type {
  ApiOk,
  AppConfig,
  CallDetail,
  CallsPage,
  CircuitBreakerMap,
  DailyStat,
  HealthResponse,
  ModelStatReal,
  ModelStatVirtual,
  ProviderConfig,
  ProviderStat,
  Summary,
  V1ModelsResponse,
  VirtualModelConfig,
} from "./types";

export * from "./types";
export { ApiError, formatDetail } from "./client";

export function getHealth() {
  return request<HealthResponse>("/health");
}

export function getSummary() {
  return request<Summary>("/api/metrics/summary");
}

export function getByModel() {
  return request<ModelStatVirtual[]>("/api/metrics/by-model");
}

export function getByRealModel() {
  return request<ModelStatReal[]>("/api/metrics/by-real-model");
}

export function getByProvider() {
  return request<ProviderStat[]>("/api/metrics/by-provider");
}

export function getDaily(days = 30) {
  return request<DailyStat[]>(`/api/metrics/daily?days=${days}`);
}

export function getCalls(params: {
  page?: number;
  size?: number;
  model?: string;
  status?: string;
  provider?: string;
  provider_model?: string;
} = {}) {
  const q = new URLSearchParams();
  q.set("page", String(params.page ?? 1));
  q.set("size", String(params.size ?? 50));
  if (params.model) q.set("model", params.model);
  if (params.status) q.set("status", params.status);
  if (params.provider) q.set("provider", params.provider);
  if (params.provider_model) q.set("provider_model", params.provider_model);
  return request<CallsPage>(`/api/calls?${q}`);
}

export function getCall(id: string) {
  return request<CallDetail>(`/api/calls/${encodeURIComponent(id)}`);
}

export function getConfig() {
  return request<AppConfig>("/api/config");
}

export function getConfigProviders() {
  return request<Record<string, ProviderConfig>>("/api/config/providers");
}

export function getConfigModels() {
  return request<Record<string, VirtualModelConfig>>("/api/config/models");
}

export function putConfig(body: AppConfig) {
  return request<ApiOk>("/api/config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function getCircuitBreaker() {
  return request<CircuitBreakerMap>("/api/circuit-breaker");
}

export function resetCircuitBreaker(provider: string) {
  return request<ApiOk>(
    `/api/circuit-breaker/${encodeURIComponent(provider)}/reset`,
    { method: "POST" },
  );
}

export function getV1Models() {
  return request<V1ModelsResponse>("/v1/models");
}
