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
          await Promise.allSettled([
            request<{ status: string }>("/health"),
            request<Summary>("/api/metrics/summary"),
            request<Daily[]>("/api/metrics/daily?days=" + period),
            request<ModelMetric[]>("/api/metrics/by-model"),
            request<RealMetric[]>("/api/metrics/by-real-model"),
            request<CallPage>("/api/calls?page=1&size=6"),
            request<CircuitStates>("/api/circuit-breaker"),
          ]);
        connected.value =
          health.status === "fulfilled" && health.value.status === "ok";
        if (stats.status === "fulfilled") summary.value = stats.value;
        if (trend.status === "fulfilled" && days.value === period)
          daily.value = trend.value;
        if (groups.status === "fulfilled") models.value = groups.value;
        if (actual.status === "fulfilled") realModels.value = actual.value;
        if (calls.status === "fulfilled") recent.value = calls.value;
        if (states.status === "fulfilled") circuits.value = states.value;
        const results = [health, stats, trend, groups, actual, calls, states];
        const labels = [
          "健康检查",
          "汇总统计",
          "请求趋势",
          "Router 统计",
          "实际模型统计",
          "最近调用",
          "熔断状态",
        ];
        const failures = results.flatMap((result, index) =>
          result.status === "rejected"
            ? [labels[index] + "：" + errorText(result.reason)]
            : [],
        );
        if (health.status === "fulfilled" && !connected.value)
          failures.unshift("健康检查：服务状态异常");
        if (results.some((result) => result.status === "fulfilled"))
          updated.value = new Date();
        error.value = failures.join("；");
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
