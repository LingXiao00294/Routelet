import type { Config, ModelRef, Provider } from "./types";
import { priceFields } from "./types";

export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
export const sameRef = (a?: ModelRef | null, b?: ModelRef | null) =>
  !!a && !!b && a.provider === b.provider && a.model === b.model;
export const refKey = (value: ModelRef) =>
  JSON.stringify([value.provider, value.model]);
export const own = (object: object, key: string) => Object.hasOwn(object, key);
export function newProvider(): Provider {
  return {
    type: "anthropic",
    api_key: "",
    base_url: "",
    timeout_seconds: 120,
    max_concurrent: 0,
    max_queue: 0,
    queue_wait_timeout: 30,
    rate_limit_cooldown: 30,
    models: {},
  };
}
export function normalizeConfig(raw: Partial<Config>): Config {
  return {
    server: {
      host: "127.0.0.1",
      port: 9456,
      log_level: "info",
      log_file: "logs/agent-router.log",
      log_max_bytes: 10000000,
      log_backup_count: 5,
      ...raw.server,
    },
    router: {
      mode: "sticky",
      failure_threshold: 5,
      recovery_timeout: 600,
      ...raw.router,
    },
    providers: Object.fromEntries(
      Object.entries(raw.providers ?? {}).map(([name, provider]) => [
        name,
        { ...newProvider(), ...provider, models: provider.models ?? {} },
      ]),
    ),
    models: clone(raw.models ?? {}),
  };
}
export function canonical(value: unknown): string {
  const sorted = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(sorted);
    if (item && typeof item === "object")
      return Object.fromEntries(
        Object.entries(item)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([key, val]) => [key, sorted(val)]),
      );
    return item;
  };
  return JSON.stringify(sorted(value));
}
export function cleanConfig(config: Config): Config {
  const result = clone(config);
  for (const provider of Object.values(result.providers)) {
    delete provider.has_key;
    delete provider.api_key_unresolved;
  }
  return result;
}
export function redactKeys(config: Config): Config {
  const result = clone(config);
  for (const provider of Object.values(result.providers)) {
    provider.has_key = !!provider.api_key && !provider.api_key.includes("${");
    provider.api_key = "";
  }
  return result;
}
export function catalog(config: Config): ModelRef[] {
  return Object.entries(config.providers).flatMap(([provider, item]) =>
    Object.keys(item.models).map((model) => ({ provider, model })),
  );
}
export function removeCatalogEntry(
  config: Config,
  provider: string,
  model?: string,
) {
  if (model === undefined) delete config.providers[provider];
  else delete config.providers[provider]?.models[model];
  const removed = (ref: ModelRef) =>
    ref.provider === provider && (model === undefined || ref.model === model);
  for (const [name, route] of Object.entries(config.models)) {
    route.models = route.models.filter((ref) => !removed(ref));
    if (!route.models.length) {
      delete config.models[name];
      continue;
    }
    if (route.pinned_model && removed(route.pinned_model)) {
      if (config.router.mode === "sticky")
        route.pinned_model = clone(route.models[0]);
      else delete route.pinned_model;
    }
  }
}
export function affectedRoutes(
  config: Config,
  provider: string,
  model?: string,
): string[] {
  return Object.entries(config.models)
    .filter(([, route]) =>
      route.models.some(
        (ref) =>
          ref.provider === provider &&
          (model === undefined || ref.model === model),
      ),
    )
    .map(([name]) => name);
}
function finite(value: unknown, min: number, integer = false): boolean {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= min &&
    (!integer || Number.isInteger(value))
  );
}
export function validateConfig(config: Config, existing?: Config): string[] {
  const errors: string[] = [];
  if (!config.server.host.trim()) errors.push("监听地址不能为空");
  if (!finite(config.server.port, 1, true) || config.server.port > 65535)
    errors.push("端口应为 1–65535 的整数");
  if (!finite(config.server.log_max_bytes, 1, true))
    errors.push("日志文件大小必须为正整数");
  if (!finite(config.server.log_backup_count, 0, true))
    errors.push("日志保留数必须为非负整数");
  if (!finite(config.router.failure_threshold, 1, true))
    errors.push("熔断阈值必须为正整数");
  if (!finite(config.router.recovery_timeout, Number.MIN_VALUE))
    errors.push("熔断恢复时间必须大于 0");
  for (const [name, provider] of Object.entries(config.providers)) {
    if (!name.trim()) errors.push("上游名称不能为空");
    if (!provider.api_key.trim() && !own(existing?.providers ?? {}, name))
      errors.push(name + "：新上游需要 API Key");
    if (
      own(existing?.providers ?? {}, name) &&
      provider.api_key !== existing?.providers[name].api_key &&
      (/^\*+$/.test(provider.api_key) ||
        /^.{4}\*+.{4}$/s.test(provider.api_key) ||
        provider.api_key === "${PLACEHOLDER}")
    )
      errors.push(
        name + "：请输入完整 API Key，不能使用脱敏占位符；留空可保留原密钥",
      );
    try {
      const url = new URL(provider.base_url);
      if (
        !["https:", "http:"].includes(url.protocol) ||
        !url.hostname ||
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        /\s/.test(provider.base_url)
      )
        throw new Error();
    } catch {
      errors.push(
        name + "：请填写不含认证信息、查询参数和片段的完整 HTTP(S) 地址",
      );
    }
    for (const field of [
      "timeout_seconds",
      "queue_wait_timeout",
      "rate_limit_cooldown",
    ] as const)
      if (!finite(provider[field], Number.MIN_VALUE))
        errors.push(name + "：" + field + " 必须大于 0");
    for (const field of ["max_concurrent", "max_queue"] as const)
      if (!finite(provider[field], 0, true))
        errors.push(name + "：" + field + " 必须为非负整数");
    if (
      provider.failure_threshold != null &&
      !finite(provider.failure_threshold, 1, true)
    )
      errors.push(name + "：熔断阈值必须为正整数");
    if (
      provider.recovery_timeout != null &&
      !finite(provider.recovery_timeout, Number.MIN_VALUE)
    )
      errors.push(name + "：恢复时间必须大于 0");
    for (const [model, prices] of Object.entries(provider.models)) {
      if (!model.trim()) errors.push(name + "：模型名称不能为空");
      for (const field of priceFields)
        if (prices[field.key] != null && !finite(prices[field.key], 0))
          errors.push(name + "/" + model + "：" + field.label + "价格无效");
    }
  }
  for (const [name, route] of Object.entries(config.models)) {
    if (!name.trim()) errors.push("路由名称不能为空");
    if (!route.models.length) errors.push(name + "：至少选择一个候选模型");
    const refs = new Set<string>();
    for (const ref of route.models) {
      if (!own(config.providers[ref.provider]?.models ?? {}, ref.model))
        errors.push(
          name + "：引用了不存在的 " + ref.provider + "/" + ref.model,
        );
      if (refs.has(refKey(ref))) errors.push(name + "：候选模型重复");
      refs.add(refKey(ref));
    }
    if (
      config.router.mode === "sticky" &&
      !route.models.some((ref) => sameRef(ref, route.pinned_model))
    )
      errors.push(name + "：请选择一个固定模型");
    if (
      route.pinned_model &&
      !route.models.some((ref) => sameRef(ref, route.pinned_model))
    )
      errors.push(name + "：固定模型必须属于候选列表");
  }
  return errors;
}
export function changes(before: Config, after: Config): string[] {
  const result: string[] = [];
  for (const section of ["providers", "models"] as const) {
    const label = section === "providers" ? "上游" : "路由";
    for (const name of new Set([
      ...Object.keys(before[section]),
      ...Object.keys(after[section]),
    ])) {
      if (!own(before[section], name))
        result.push("新增" + label + " · " + name);
      else if (!own(after[section], name))
        result.push("删除" + label + " · " + name);
      else if (
        canonical(before[section][name]) !== canonical(after[section][name])
      )
        result.push("更新" + label + " · " + name);
    }
  }
  if (canonical(before.router) !== canonical(after.router))
    result.push("更新路由策略与熔断设置");
  if (canonical(before.server) !== canonical(after.server))
    result.push("更新服务与日志设置");
  return result;
}
