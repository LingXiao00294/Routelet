import type {
  ActualModelConfig,
  AppConfig,
  LogLevel,
  ProviderConfig,
  RouterConfig,
  ServerConfig,
  VirtualModelConfig,
} from "@/api/types";

export function emptyServer(): ServerConfig {
  return {
    host: "127.0.0.1",
    port: 9456,
    log_level: "info",
    log_file: "logs/agent-router.log",
    log_max_bytes: 10_000_000,
    log_backup_count: 5,
  };
}

export function emptyRouter(): RouterConfig {
  return {
    failure_threshold: 5,
    recovery_timeout: 600,
    mode: "sticky",
  };
}

function asFinite(value: unknown, fallback: number): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function asNullableFinite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function optionalPrice(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : Number(value);
  return value != null && value !== "" && Number.isFinite(number) && number >= 0
    ? number
    : undefined;
}

function normalizeActualModel(raw: unknown): ActualModelConfig {
  const model = (raw ?? {}) as Record<string, unknown>;
  return {
    input_price_per_million: optionalPrice(model.input_price_per_million),
    output_price_per_million: optionalPrice(model.output_price_per_million),
    cache_read_price_per_million: optionalPrice(model.cache_read_price_per_million),
    cache_write_price_per_million: optionalPrice(model.cache_write_price_per_million),
  };
}

const LOG_LEVELS: LogLevel[] = ["debug", "info", "warning", "error"];

/** Fill omitted provider and actual-model fields with their backend defaults. */
export function normalizeProviderConfig(
  raw: Partial<ProviderConfig> | Record<string, unknown> | null | undefined,
): ProviderConfig {
  const provider = (raw ?? {}) as Record<string, unknown>;
  const rawModels =
    provider.models && typeof provider.models === "object"
      ? (provider.models as Record<string, unknown>)
      : {};
  const models: Record<string, ActualModelConfig> = {};
  for (const [name, model] of Object.entries(rawModels)) {
    models[name] = normalizeActualModel(model);
  }
  return {
    type: "anthropic",
    api_key: typeof provider.api_key === "string" ? provider.api_key : "",
    base_url: typeof provider.base_url === "string" ? provider.base_url : "",
    timeout_seconds: asFinite(provider.timeout_seconds, 120),
    failure_threshold: asNullableFinite(provider.failure_threshold),
    recovery_timeout: asNullableFinite(provider.recovery_timeout),
    max_concurrent: asFinite(provider.max_concurrent, 0),
    max_queue: asFinite(provider.max_queue, 0),
    queue_wait_timeout: asFinite(provider.queue_wait_timeout, 30),
    rate_limit_cooldown: asFinite(provider.rate_limit_cooldown, 30),
    has_key: Boolean(provider.has_key),
    api_key_unresolved: Boolean(provider.api_key_unresolved),
    models,
  };
}

/** Normalize GET /api/config into the complete dashboard configuration shape. */
export function normalizeAppConfig(
  raw: Partial<AppConfig> | Record<string, unknown> | null | undefined,
): AppConfig {
  const source = (raw ?? {}) as Partial<AppConfig> & Record<string, unknown>;
  const serverIn = (source.server ?? {}) as Partial<ServerConfig>;
  const routerIn = (source.router ?? {}) as Partial<RouterConfig>;
  const providersIn =
    source.providers && typeof source.providers === "object"
      ? (source.providers as Record<string, Partial<ProviderConfig>>)
      : {};

  const logLevel = serverIn.log_level;
  const server: ServerConfig = {
    host:
      typeof serverIn.host === "string" && serverIn.host
        ? serverIn.host
        : "127.0.0.1",
    port: asFinite(serverIn.port, 9456),
    log_level: LOG_LEVELS.includes(logLevel as LogLevel)
      ? (logLevel as LogLevel)
      : "info",
    log_file:
      typeof serverIn.log_file === "string"
        ? serverIn.log_file
        : "logs/agent-router.log",
    log_max_bytes: asFinite(serverIn.log_max_bytes, 10_000_000),
    log_backup_count: asFinite(serverIn.log_backup_count, 5),
  };

  const router: RouterConfig = {
    failure_threshold: asFinite(routerIn.failure_threshold, 5),
    recovery_timeout: asFinite(routerIn.recovery_timeout, 600),
    mode: routerIn.mode === "failover" ? "failover" : "sticky",
  };

  const providers: Record<string, ProviderConfig> = {};
  for (const [name, provider] of Object.entries(providersIn)) {
    providers[name] = normalizeProviderConfig(provider);
  }

  return {
    server,
    router,
    providers,
    models: normalizeModels(source.models as AppConfig["models"]),
  };
}

/** Empty / placeholder / all-stars values always preserve the server-side key. */
export function isBlankOrPlaceholderKey(key: string): boolean {
  return !key || key === "${PLACEHOLDER}" || /^\*+$/.test(key);
}

/** Return whether a value has the backend's masked API-key shape. */
export function isBackendMaskedShape(key: string): boolean {
  if (isBlankOrPlaceholderKey(key)) return true;
  if (key.length > 8) {
    const middle = key.slice(4, -4);
    if (middle.length > 0 && [...middle].every((character) => character === "*")) {
      return true;
    }
  }
  return false;
}

export function shouldPreserveApiKey(
  name: string,
  apiKey: string,
  originalMasked: Record<string, string>,
): boolean {
  if (isBlankOrPlaceholderKey(apiKey)) return true;
  if (
    Object.prototype.hasOwnProperty.call(originalMasked, name) &&
    apiKey === originalMasked[name]
  ) {
    return true;
  }
  return isBackendMaskedShape(apiKey);
}

export function captureMaskedApiKeys(
  providers: Record<string, ProviderConfig>,
): Record<string, string> {
  const output: Record<string, string> = {};
  for (const [name, provider] of Object.entries(providers)) {
    output[name] = provider.api_key ?? "";
  }
  return output;
}

export function normalizeModels(
  models: Record<string, VirtualModelConfig> | null | undefined,
  normalized?: Record<string, VirtualModelConfig>,
): Record<string, VirtualModelConfig> {
  const source = normalized ?? models ?? {};
  const output: Record<string, VirtualModelConfig> = {};
  for (const [name, virtualModel] of Object.entries(source)) {
    output[name] = {
      pinned_model: virtualModel.pinned_model
        ? structuredClone(virtualModel.pinned_model)
        : null,
      models: structuredClone(virtualModel.models ?? []),
    };
  }
  return output;
}

export function buildPutPayload(
  draft: AppConfig,
  models: Record<string, VirtualModelConfig>,
  originalMasked: Record<string, string>,
): AppConfig {
  const providers: Record<string, ProviderConfig> = {};
  for (const [name, provider] of Object.entries(draft.providers)) {
    const { has_key: _hasKey, api_key_unresolved: _unresolved, ...rest } = provider;
    void _hasKey;
    void _unresolved;
    const actualModels: Record<string, ActualModelConfig> = {};
    for (const [modelName, actualModel] of Object.entries(provider.models)) {
      actualModels[modelName] = normalizeActualModel(actualModel);
    }
    providers[name] = {
      ...rest,
      api_key: shouldPreserveApiKey(name, provider.api_key, originalMasked)
        ? ""
        : provider.api_key,
      models: actualModels,
    };
  }

  const modelOutput: Record<string, VirtualModelConfig> = {};
  for (const [name, virtualModel] of Object.entries(models)) {
    modelOutput[name] = {
      pinned_model: virtualModel.pinned_model
        ? { ...virtualModel.pinned_model }
        : null,
      models: virtualModel.models.map((model) => ({ ...model })),
    };
  }

  return {
    server: { ...draft.server },
    router: { ...draft.router },
    providers,
    models: modelOutput,
  };
}
