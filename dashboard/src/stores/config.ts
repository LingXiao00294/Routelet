import { computed, ref } from "vue";
import { defineStore } from "pinia";
import * as api from "@/api";
import type {
  ActualModelConfig,
  AppConfig,
  ConfigReferenceError,
  RouterMode,
  VirtualModelConfig,
} from "@/api/types";
import {
  buildPutPayload,
  captureMaskedApiKeys,
  emptyRouter,
  emptyServer,
  isBackendMaskedShape,
  isBlankOrPlaceholderKey,
  normalizeAppConfig,
  normalizeModels,
} from "@/utils/configPayload";

function cloneConfig(c: AppConfig): AppConfig {
  return structuredClone(c);
}

function referenceConflict(error: unknown): ConfigReferenceError | null {
  if (!(error instanceof api.ApiError)) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || !("error" in detail)) return null;
  const conflict = (detail as { error?: unknown }).error;
  if (!conflict || typeof conflict !== "object") return null;
  const candidate = conflict as Partial<ConfigReferenceError>;
  if (
    (candidate.code !== "provider_in_use" && candidate.code !== "model_in_use") ||
    typeof candidate.provider !== "string" ||
    !Array.isArray(candidate.referenced_by)
  ) {
    return null;
  }
  return candidate as ConfigReferenceError;
}

function validProviderBaseUrl(value: string): boolean {
  if (!value || /\s/.test(value)) return false;
  try {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      Boolean(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

export const useConfigStore = defineStore("config", () => {
  const draft = ref<AppConfig | null>(null);
  const baseline = ref<string>("");
  const knownProviders = ref<Set<string>>(new Set());
  const maskedApiKeys = ref<Record<string, string>>({});
  const models = ref<Record<string, VirtualModelConfig>>({});
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);
  const fieldErrors = ref<Record<string, string>>({});
  let loadPromise: Promise<void> | null = null;
  let loadPromiseContext: string | null = null;
  let loadGeneration = 0;

  const dirty = computed(() => {
    if (!draft.value) return false;
    return JSON.stringify(buildPayload()) !== baseline.value;
  });

  function buildPayload(): AppConfig {
    if (!draft.value) {
      return {
        server: emptyServer(),
        router: emptyRouter(),
        providers: {},
        models: {},
      };
    }
    return buildPutPayload(draft.value, models.value, maskedApiKeys.value);
  }

  function ensureStickyPins() {
    if (draft.value?.router.mode !== "sticky") return;
    for (const virtualModel of Object.values(models.value)) {
      const pinValid = virtualModel.models.some(
        (model) =>
          model.provider === virtualModel.pinned_model?.provider &&
          model.model === virtualModel.pinned_model?.model,
      );
      if (pinValid) continue;
      const first = virtualModel.models[0];
      virtualModel.pinned_model = first ? { ...first } : null;
    }
  }

  /** Capture all user-editable state so an in-flight load cannot replace newer edits. */
  function loadContext(): string | null {
    return draft.value ? JSON.stringify(buildPayload()) : null;
  }

  function invalidateLoad() {
    loadGeneration += 1;
    loadPromise = null;
    loadPromiseContext = null;
    loading.value = false;
  }

  function loadIsCurrent(generation: number, context: string | null): boolean {
    return generation === loadGeneration && loadContext() === context;
  }

  /** Reuse an ordinary active request; forced loads always start a newer generation. */
  function startLoad(force = false): Promise<void> {
    const context = loadContext();
    if (!force && loadPromise && loadPromiseContext === context) return loadPromise;
    if (!force && saving.value) return Promise.resolve();

    const generation = ++loadGeneration;
    const request = (async () => {
      loading.value = true;
      error.value = null;
      try {
        const cfg = await api.getConfig();
        if (!loadIsCurrent(generation, context)) return;
        const normalized = normalizeAppConfig(cfg);
        draft.value = cloneConfig(normalized);
        models.value = normalizeModels(normalized.models);
        knownProviders.value = new Set(Object.keys(normalized.providers));
        maskedApiKeys.value = captureMaskedApiKeys(normalized.providers);
        baseline.value = JSON.stringify(buildPayload());
        fieldErrors.value = {};
      } catch (err) {
        if (!loadIsCurrent(generation, context)) return;
        error.value = err instanceof Error ? err.message : "加载配置失败";
        throw err;
      } finally {
        if (generation === loadGeneration) loading.value = false;
      }
    })();
    loadPromise = request;
    loadPromiseContext = context;
    void request.then(
      () => {
        if (loadPromise === request) {
          loadPromise = null;
          loadPromiseContext = null;
        }
      },
      () => {
        if (loadPromise === request) {
          loadPromise = null;
          loadPromiseContext = null;
        }
      },
    );
    return request;
  }

  function load(): Promise<void> {
    return startLoad();
  }

  function validate(): boolean {
    const errs: Record<string, string> = {};
    if (!draft.value) {
      fieldErrors.value = { _: "配置未加载" };
      return false;
    }
    ensureStickyPins();
    const s = draft.value.server;
    if (!s.host.trim()) errs["server.host"] = "host 不能为空";
    if (!Number.isFinite(s.port) || s.port < 1 || s.port > 65535) {
      errs["server.port"] = "端口需在 1–65535";
    }
    if (!Number.isFinite(s.log_max_bytes) || s.log_max_bytes <= 0) {
      errs["server.log_max_bytes"] = "需为 > 0 的数字";
    }
    if (!Number.isFinite(s.log_backup_count) || s.log_backup_count < 0) {
      errs["server.log_backup_count"] = "需为 ≥ 0 的数字";
    }
    if (
      !Number.isFinite(draft.value.router.failure_threshold) ||
      draft.value.router.failure_threshold < 1
    ) {
      errs["router.failure_threshold"] = "需 ≥ 1";
    }
    if (
      !Number.isFinite(draft.value.router.recovery_timeout) ||
      draft.value.router.recovery_timeout <= 0
    ) {
      errs["router.recovery_timeout"] = "需 > 0";
    }

    if (!Object.keys(draft.value.providers).length) {
      errs.providers = "至少保留一个 provider";
    }
    if (!Object.keys(models.value).length) {
      errs.models = "至少保留一个虚拟模型";
    }

    for (const [name, p] of Object.entries(draft.value.providers)) {
      if (!validProviderBaseUrl(p.base_url)) {
        errs[`providers.${name}.base_url`] =
          "需为不含用户信息、查询参数或片段的绝对 HTTP(S) URL";
      }
      if (!Number.isFinite(p.timeout_seconds) || p.timeout_seconds <= 0) {
        errs[`providers.${name}.timeout`] = "需 > 0";
      }
      if (
        p.max_concurrent != null &&
        (!Number.isFinite(p.max_concurrent) || p.max_concurrent < 0)
      ) {
        errs[`providers.${name}.max_concurrent`] = "需 ≥ 0";
      }
      if (
        p.max_queue != null &&
        (!Number.isFinite(p.max_queue) || p.max_queue < 0)
      ) {
        errs[`providers.${name}.max_queue`] = "需 ≥ 0";
      }
      if (
        p.queue_wait_timeout != null &&
        (!Number.isFinite(p.queue_wait_timeout) || p.queue_wait_timeout <= 0)
      ) {
        errs[`providers.${name}.queue_wait_timeout`] = "需 > 0";
      }
      if (
        p.rate_limit_cooldown != null &&
        (!Number.isFinite(p.rate_limit_cooldown) || p.rate_limit_cooldown <= 0)
      ) {
        errs[`providers.${name}.rate_limit_cooldown`] = "需 > 0";
      }
      if (
        p.failure_threshold != null &&
        (!Number.isFinite(p.failure_threshold) || p.failure_threshold < 1)
      ) {
        errs[`providers.${name}.failure_threshold`] = "需 ≥ 1 或留空";
      }
      if (
        p.recovery_timeout != null &&
        (!Number.isFinite(p.recovery_timeout) || p.recovery_timeout <= 0)
      ) {
        errs[`providers.${name}.recovery_timeout`] = "需 > 0 或留空";
      }
      if (!knownProviders.value.has(name)) {
        if (isBackendMaskedShape(p.api_key)) {
          errs[`providers.${name}.api_key`] = "新建 provider 需要有效 api_key";
        }
      } else if (
        isBackendMaskedShape(p.api_key) &&
        !isBlankOrPlaceholderKey(p.api_key) &&
        p.api_key !== maskedApiKeys.value[name]
      ) {
        errs[`providers.${name}.api_key`] =
          "不能使用脱敏形态的密钥；请输入完整 api_key 或留空保留";
      }
      for (const [modelName, actualModel] of Object.entries(p.models)) {
        if (!modelName.trim()) {
          errs[`providers.${name}.models`] = "实际模型名不能为空";
        }
        const prices = [
          actualModel.input_price_per_million,
          actualModel.output_price_per_million,
          actualModel.cache_read_price_per_million,
          actualModel.cache_write_price_per_million,
        ];
        if (prices.some((price) => price != null && (!Number.isFinite(price) || price < 0))) {
          errs[`providers.${name}.models.${modelName}`] = "费用需为 ≥ 0 的数字或留空";
        }
      }
    }

    for (const [name, m] of Object.entries(models.value)) {
      if (!m.models.length) {
        errs[`models.${name}`] = "至少选择一个实际模型";
      }
      const seen = new Set<string>();
      m.models.forEach((r, i) => {
        if (!r.provider?.trim() || !(r.provider in draft.value!.providers)) {
          errs[`models.${name}.ref.${i}`] = "provider 无效或不存在";
        }
        const provider = draft.value!.providers[r.provider];
        if (!r.model?.trim() || !provider?.models[r.model]) {
          errs[`models.${name}.ref.${i}.model`] = "实际模型不存在";
        }
        const identity = `${r.provider}\u0000${r.model}`;
        if (seen.has(identity)) {
          errs[`models.${name}.ref.${i}`] = "不能重复选择同一实际模型";
        }
        seen.add(identity);
      });
      if (draft.value.router.mode === "sticky" && m.models.length > 0) {
        const ok =
          m.pinned_model &&
          m.models.some(
            (r) => r.provider === m.pinned_model?.provider && r.model === m.pinned_model?.model,
          );
        if (!ok) {
          errs[`models.${name}.pin`] = "sticky 模式需要有效 pin";
        }
      }
    }

    fieldErrors.value = errs;
    return Object.keys(errs).length === 0;
  }

  function validationFailureMessage(action: "save" | "switch"): string {
    const pinError = Object.entries(fieldErrors.value).find(([key]) =>
      key.endsWith(".pin"),
    );
    if (pinError) {
      const modelName = pinError[0].slice("models.".length, -".pin".length);
      const prefix =
        action === "switch"
          ? "无法切换到 sticky 模式"
          : "无法保存 sticky 模式";
      return `${prefix}：模型「${modelName}」未指定有效 pin`;
    }
    return action === "switch"
      ? "配置校验失败，无法切换 Mode"
      : "请先修正表单错误";
  }

  /** Acknowledge persisted keys without replacing edits made while PUT was pending. */
  function acceptSavedConfig(payload: AppConfig): string {
    const savedKeys: Record<string, string> = {};
    const providers: AppConfig["providers"] = {};
    for (const [name, provider] of Object.entries(payload.providers)) {
      savedKeys[name] = provider.api_key || maskedApiKeys.value[name] || "";
      providers[name] = { ...provider, api_key: "" };
    }
    knownProviders.value = new Set(Object.keys(payload.providers));
    maskedApiKeys.value = savedKeys;
    baseline.value = JSON.stringify({ ...payload, providers });
    return baseline.value;
  }

  /** Save the current snapshot, reporting whether the post-write reload succeeded. */
  async function save(): Promise<boolean> {
    if (saving.value) {
      throw new Error("配置正在保存，请稍候");
    }
    if (!draft.value) return true;
    if (!validate()) {
      throw new Error(validationFailureMessage("save"));
    }
    const payload = buildPayload();
    invalidateLoad();
    saving.value = true;
    error.value = null;
    try {
      await api.putConfig(payload);
      const payloadSnapshot = acceptSavedConfig(payload);
      if (loadContext() === payloadSnapshot) {
        try {
          await startLoad(true);
        } catch {
          return false;
        }
      }
      return true;
    } catch (err) {
      const conflict = referenceConflict(err);
      if (conflict) {
        const target =
          conflict.code === "provider_in_use"
            ? `Provider「${conflict.provider}」`
            : `实际模型「${conflict.provider}/${conflict.model}」`;
        const message = `${target}仍被虚拟模型引用：${conflict.referenced_by.join("、")}。请先单独保存引用移除。`;
        fieldErrors.value = { ...fieldErrors.value, providers: message };
        error.value = message;
        throw new Error(message);
      }
      error.value = err instanceof Error ? err.message : "保存失败";
      throw err;
    } finally {
      saving.value = false;
    }
  }

  /** Persist a top-bar mode change and report whether its reload succeeded. */
  async function setRouterMode(next: RouterMode): Promise<boolean> {
    if (saving.value) {
      throw new Error("配置正在保存，请稍候");
    }
    if (dirty.value) {
      throw new Error("配置页有未保存更改，请先保存或刷新后再切换故障转移");
    }
    saving.value = true;
    try {
      // The editor may have been unmounted while another client changed the file.
      // Reserve the save slot, then read a fresh snapshot before choosing a PUT.
      await startLoad(true);
      if (dirty.value) {
        throw new Error("配置页有未保存更改，请先保存或刷新后再切换故障转移");
      }
      if (!draft.value) {
        throw new Error("配置未加载");
      }
      if (draft.value.router.mode === next) return true;

      const previousError = error.value;
      const previousFieldErrors = fieldErrors.value;
      error.value = null;
      const prev = draft.value.router.mode;
      draft.value = {
        ...draft.value,
        router: { ...draft.value.router, mode: next },
      };
      try {
        if (!validate()) {
          throw new Error(validationFailureMessage("switch"));
        }
        const payload = buildPayload();
        invalidateLoad();
        await api.putConfig(payload);
        const payloadSnapshot = acceptSavedConfig(payload);
        if (loadContext() === payloadSnapshot) {
          try {
            await startLoad(true);
          } catch {
            return false;
          }
        }
        return true;
      } catch (err) {
        if (draft.value && draft.value.router.mode === next) {
          draft.value = {
            ...draft.value,
            router: { ...draft.value.router, mode: prev },
          };
        }
        error.value = previousError;
        fieldErrors.value = previousFieldErrors;
        throw err;
      }
    } finally {
      saving.value = false;
    }
  }

  function addProvider(name: string) {
    if (!draft.value || !name.trim()) return;
    if (draft.value.providers[name]) {
      fieldErrors.value = { ...fieldErrors.value, providers: "名称已存在" };
      return;
    }
    const { providers: _nameError, ...remainingErrors } = fieldErrors.value;
    void _nameError;
    fieldErrors.value = remainingErrors;
    draft.value.providers[name] = {
      type: "anthropic",
      api_key: "",
      base_url: "",
      timeout_seconds: 120,
      failure_threshold: null,
      recovery_timeout: null,
      max_concurrent: 0,
      max_queue: 0,
      queue_wait_timeout: 30,
      rate_limit_cooldown: 30,
      has_key: false,
      models: {},
    };
  }

  function referencedBy(provider: string, model?: string): string[] {
    return Object.entries(models.value)
      .filter(([, virtualModel]) => {
        const referencedInModels = virtualModel.models.some(
          (ref) => ref.provider === provider && (model == null || ref.model === model),
        );
        const pinnedModel = virtualModel.pinned_model;
        const referencedByPin =
          pinnedModel?.provider === provider && (model == null || pinnedModel.model === model);
        return referencedInModels || referencedByPin;
      })
      .map(([name]) => name);
  }

  function removeProvider(name: string): string[] {
    if (!draft.value) return [];
    const references = referencedBy(name);
    if (references.length) return references;
    delete draft.value.providers[name];
    knownProviders.value.delete(name);
    const { [name]: _removed, ...rest } = maskedApiKeys.value;
    void _removed;
    maskedApiKeys.value = rest;
    return [];
  }

  function removeActualModel(provider: string, model: string): string[] {
    if (!draft.value?.providers[provider]) return [];
    const references = referencedBy(provider, model);
    if (references.length) return references;
    delete draft.value.providers[provider].models[model];
    return [];
  }

  function addActualModel(
    provider: string,
    model: string,
    config: ActualModelConfig = {},
  ): boolean {
    const providerConfig = draft.value?.providers[provider];
    const name = model.trim();
    if (!providerConfig || !name || providerConfig.models[name]) return false;
    providerConfig.models[name] = { ...config };
    return true;
  }

  function updateActualModel(
    provider: string,
    model: string,
    config: ActualModelConfig,
  ): boolean {
    const providerConfig = draft.value?.providers[provider];
    if (!providerConfig?.models[model]) return false;
    providerConfig.models[model] = { ...config };
    return true;
  }

  function addModel(name: string) {
    if (!name.trim() || models.value[name]) return;
    models.value[name] = {
      pinned_model: null,
      models: [],
    };
  }

  function removeModel(name: string) {
    delete models.value[name];
  }

  function renameModel(oldName: string, newName: string) {
    if (!newName.trim() || oldName === newName || models.value[newName]) return;
    models.value[newName] = models.value[oldName];
    delete models.value[oldName];
  }

  function moveRef(model: string, from: number, to: number) {
    const m = models.value[model];
    if (!m) return;
    const list = [...m.models];
    if (from < 0 || from >= list.length || to < 0 || to >= list.length) return;
    const [item] = list.splice(from, 1);
    list.splice(to, 0, item);
    m.models = list;
  }

  return {
    draft,
    models,
    loading,
    saving,
    error,
    fieldErrors,
    dirty,
    load,
    save,
    setRouterMode,
    validate,
    buildPayload,
    ensureStickyPins,
    addProvider,
    removeProvider,
    addActualModel,
    updateActualModel,
    removeActualModel,
    addModel,
    removeModel,
    renameModel,
    moveRef,
  };
});
