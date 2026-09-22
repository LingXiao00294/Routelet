import { ref } from "vue";
import { defineStore } from "pinia";
import type {
  CallPage,
  CircuitStates,
  Daily,
  ModelMetric,
  RealMetric,
  Summary,
} from "../domain/types";
import { errorText, request } from "../services/http";

export const useTelemetry = defineStore("telemetry", () => {
  const summary = ref<Summary | null>(null),
    daily = ref<Daily[]>([]),
    models = ref<ModelMetric[]>([]);
  const realModels = ref<RealMetric[]>([]),
    recent = ref<CallPage | null>(null),
    circuits = ref<CircuitStates>({});
  const connected = ref<boolean | null>(null),
    loading = ref(false),
    error = ref(""),
    updated = ref<Date | null>(null);
  const days = ref(14),
    autoRefresh = ref(true);
  let pending: Promise<void> | null = null;
  async function refresh() {
    if (pending) return pending;
    const period = days.value;
    pending = (async () => {
      loading.value = true;
      try {
        const [health, stats, trend, groups, actual, calls, states] =
          await Promise.all([
            request<{ status: string }>("/health"),
            request<Summary>("/api/metrics/summary"),
            request<Daily[]>("/api/metrics/daily?days=" + period),
            request<ModelMetric[]>("/api/metrics/by-model"),
            request<RealMetric[]>("/api/metrics/by-real-model"),
            request<CallPage>("/api/calls?page=1&size=6"),
            request<CircuitStates>("/api/circuit-breaker"),
          ]);
        connected.value = health.status === "ok";
        summary.value = stats;
        if (days.value === period) daily.value = trend;
        models.value = groups;
        realModels.value = actual;
        recent.value = calls;
        circuits.value = states;
        updated.value = new Date();
        error.value = "";
      } catch (reason) {
        error.value = errorText(reason);
        connected.value = false;
      } finally {
        loading.value = false;
        pending = null;
      }
    })();
    return pending;
  }
  async function setDays(value: number) {
    days.value = value;
    if (pending) await pending;
    await refresh();
  }
  return {
    summary,
    daily,
    models,
    realModels,
    recent,
    circuits,
    connected,
    loading,
    error,
    updated,
    days,
    autoRefresh,
    refresh,
    setDays,
  };
});
