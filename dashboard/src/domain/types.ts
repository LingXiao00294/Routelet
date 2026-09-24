export interface Price {
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  cache_read_price_per_million?: number | null;
  cache_write_price_per_million?: number | null;
}
export interface ModelRef {
  provider: string;
  model: string;
}
export interface Provider {
  type: "anthropic";
  api_key: string;
  base_url: string;
  has_key?: boolean;
  api_key_unresolved?: boolean;
  timeout_seconds: number;
  failure_threshold?: number | null;
  recovery_timeout?: number | null;
  max_concurrent: number;
  max_queue: number;
  queue_wait_timeout: number;
  rate_limit_cooldown: number;
  models: Record<string, Price>;
  model_order?: string[] | null;
}
export interface VirtualModel {
  models: ModelRef[];
  pinned_model?: ModelRef | null;
}
export interface Config {
  server: {
    host: string;
    port: number;
    log_level: string;
    log_file: string;
    log_max_bytes: number;
    log_backup_count: number;
  };
  router: {
    mode: "sticky" | "failover";
    failure_threshold: number;
    recovery_timeout: number;
  };
  providers: Record<string, Provider>;
  models: Record<string, VirtualModel>;
}
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
export interface Daily {
  day: string;
  count: number;
  success_count: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number | null;
}
export interface ModelMetric {
  virtual_model: string;
  count: number;
  success_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number | null;
}
export interface RealMetric extends Omit<ModelMetric, "virtual_model"> {
  provider: string;
  model: string;
}
export interface Call extends Price {
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
  error_type?: string | null;
  error_message?: string | null;
  request_body?: string | null;
  response_body?: string | null;
  failover_details?: string | null;
}
export interface CallPage {
  data: Call[];
  total: number;
  page: number;
  size: number;
  pages: number;
}
export interface Attempt {
  provider: string;
  model?: string;
  error?: string;
  latency_ms?: number;
  [key: string]: unknown;
}
export type CircuitStates = Record<string, "closed" | "open" | "half_open">;
export const priceFields = [
  { key: "input_price_per_million", label: "输入" },
  { key: "output_price_per_million", label: "输出" },
  { key: "cache_read_price_per_million", label: "缓存读取" },
  { key: "cache_write_price_per_million", label: "缓存写入" },
] as const;
