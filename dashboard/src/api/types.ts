/** Metrics / calls / config / circuit types — mirrors backend contracts. */

export interface Summary {
  total_calls: number;
  success_count: number;
  error_count: number;
  success_rate: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read: number;
  total_cache_write: number;
  total_cost_usd: number | null;
  avg_latency_ms: number;
}

export interface ModelStatVirtual {
  virtual_model: string;
  count: number;
  success_count: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_cost_usd: number | null;
}

export interface ModelStatReal {
  provider: string;
  model: string;
  count: number;
  success_count: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_cost_usd: number | null;
}

export interface ProviderStat {
  provider: string;
  count: number;
  success_count: number;
}

export interface DailyStat {
  day: string;
  count: number;
  success_count: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  cost_usd: number | null;
}

export interface FailoverEntry {
  provider: string;
  model: string;
  error: string;
  latency_ms?: number;
}

export interface CallSummary {
  id: string;
  timestamp: string;
  virtual_model: string;
  provider_name: string | null;
  provider_model: string | null;
  attempt: number;
  latency_ms: number | null;
  status: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  cost_usd: number | null;
}

export interface CallDetail extends CallSummary {
  provider_type: string | null;
  provider_url: string | null;
  request_body: string | null;
  request_tokens: number | null;
  error_type: string | null;
  error_message: string | null;
  response_body: string | null;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  cache_read_price_per_million: number | null;
  cache_write_price_per_million: number | null;
  failover_details: string | null;
}

/** Compatibility name for components that consume a complete call detail. */
export type CallRecord = CallDetail;

export interface CallsPage {
  data: CallSummary[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export type RouterMode = "failover" | "sticky";
export type ProviderType = "anthropic";
export type LogLevel = "debug" | "info" | "warning" | "error";
export type CircuitState = "closed" | "open" | "half_open";

export interface ServerConfig {
  host: string;
  port: number;
  log_level: LogLevel;
  log_file: string;
  log_max_bytes: number;
  log_backup_count: number;
}

export interface RouterConfig {
  failure_threshold: number;
  recovery_timeout: number;
  mode: RouterMode;
}

export interface ProviderConfig {
  type: ProviderType;
  api_key: string;
  base_url: string;
  timeout_seconds: number;
  failure_threshold?: number | null;
  recovery_timeout?: number | null;
  max_concurrent?: number;
  max_queue?: number;
  queue_wait_timeout?: number;
  rate_limit_cooldown?: number;
  has_key?: boolean;
  api_key_unresolved?: boolean;
  models: Record<string, ActualModelConfig>;
}

export interface ActualModelConfig {
  input_price_per_million?: number;
  output_price_per_million?: number;
  cache_read_price_per_million?: number;
  cache_write_price_per_million?: number;
}

export interface ModelRef {
  provider: string;
  model: string;
}

export interface VirtualModelConfig {
  pinned_model: ModelRef | null;
  models: ModelRef[];
}

export interface AppConfig {
  server: ServerConfig;
  router: RouterConfig;
  providers: Record<string, ProviderConfig>;
  models: Record<string, VirtualModelConfig>;
}

export interface ConfigReferenceError {
  code: "provider_in_use" | "model_in_use";
  provider: string;
  model?: string;
  referenced_by: string[];
}

export type CircuitBreakerMap = Record<string, CircuitState>;

export interface HealthResponse {
  status: string;
}

export interface V1Model {
  id: string;
  type: string;
  display_name: string;
  created_at: string;
}

export interface V1ModelsResponse {
  data: V1Model[];
}

export interface ApiOk {
  status: string;
  message?: string;
  provider?: string;
}
