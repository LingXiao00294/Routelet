import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { useLocalStorage } from "@vueuse/core";
import * as api from "@/api";
import type { AppConfig, CircuitBreakerMap, RouterMode } from "@/api/types";
import { useConfigStore } from "@/stores/config";
import { normalizeAppConfig } from "@/utils/configPayload";

export type ThemeMode = "light" | "dark";

export const useAppStore = defineStore("app", () => {
  const theme = useLocalStorage<ThemeMode>("ar-theme", "light");
  const healthy = ref<boolean | null>(null);
  const healthError = ref<string | null>(null);
  const config = ref<AppConfig | null>(null);
  const configLoading = ref(false);
  const configError = ref<string | null>(null);
  const circuit = ref<CircuitBreakerMap>({});
  const savingMode = ref(false);
  const staleData = ref(false);
  let healthSeq = 0;
  let configSeq = 0;
  let circuitSeq = 0;

  const mode = computed(() => config.value?.router.mode ?? "sticky");

  function applyTheme(next: ThemeMode) {
    theme.value = next;
    document.documentElement.setAttribute("data-theme", next);
  }

  function initTheme() {
    applyTheme(theme.value);
  }

  function toggleTheme() {
    applyTheme(theme.value === "light" ? "dark" : "light");
  }

  async function checkHealth() {
    const seq = ++healthSeq;
    try {
      const res = await api.getHealth();
      if (seq !== healthSeq) return;
      healthy.value = res.status === "ok";
      healthError.value = null;
    } catch (err) {
      if (seq !== healthSeq) return;
      healthy.value = false;
      healthError.value = err instanceof Error ? err.message : "unreachable";
    }
  }

  async function loadConfig(silent = false) {
    const seq = ++configSeq;
    if (!silent) configLoading.value = true;
    configError.value = null;
    try {
      const result = normalizeAppConfig(await api.getConfig());
      if (seq !== configSeq) return true;
      config.value = result;
      return true;
    } catch (err) {
      if (seq !== configSeq) return true;
      configError.value = err instanceof Error ? err.message : "加载配置失败";
      if (!silent) throw err;
      return false;
    } finally {
      if (seq === configSeq) configLoading.value = false;
    }
  }

  async function loadCircuit(silent = false) {
    const seq = ++circuitSeq;
    try {
      const result = await api.getCircuitBreaker();
      if (seq !== circuitSeq) return true;
      circuit.value = result;
      return true;
    } catch (err) {
      if (seq !== circuitSeq) return true;
      if (!silent) throw err;
      return false;
    }
  }

  /** Load all startup state without leaking a rejected task to the browser. */
  async function loadInitialState(): Promise<boolean> {
    const results = await Promise.allSettled([
      checkHealth(),
      loadConfig(),
      loadCircuit(),
    ]);
    const failed =
      healthy.value === false || results.some((result) => result.status === "rejected");
    staleData.value = failed;
    return failed;
  }

  /** Immediate mode PUT via config store sanitize path; syncs app.config after. */
  async function setMode(next: RouterMode): Promise<boolean> {
    if (savingMode.value) {
      throw new Error("模式正在切换，请稍候");
    }
    const configStore = useConfigStore();
    if (configStore.dirty) {
      throw new Error("配置页有未保存更改，请先保存或刷新后再切换故障转移");
    }
    const prev = config.value?.router.mode;
    if (prev === next) return true;
    savingMode.value = true;
    let persisted = false;
    try {
      const editorConfigOk = await configStore.setRouterMode(next);
      persisted = true;
      if (config.value) {
        config.value = {
          ...config.value,
          router: { ...config.value.router, mode: next },
        };
      }
      const appConfigOk = await loadConfig(true);
      const refreshed = editorConfigOk && appConfigOk;
      if (!refreshed) staleData.value = true;
      return refreshed;
    } catch (err) {
      if (!persisted && config.value && prev) {
        config.value = {
          ...config.value,
          router: { ...config.value.router, mode: prev },
        };
      }
      throw err;
    } finally {
      savingMode.value = false;
    }
  }

  const openCircuits = computed(() =>
    Object.entries(circuit.value).filter(([, s]) => s !== "closed"),
  );

  return {
    theme,
    healthy,
    healthError,
    config,
    configLoading,
    configError,
    circuit,
    savingMode,
    staleData,
    mode,
    openCircuits,
    applyTheme,
    initTheme,
    toggleTheme,
    checkHealth,
    loadConfig,
    loadCircuit,
    loadInitialState,
    setMode,
  };
});
