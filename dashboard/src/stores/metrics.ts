import { ref } from "vue";
import { defineStore } from "pinia";
import { useLocalStorage } from "@vueuse/core";
import * as api from "@/api";
import type {
  DailyStat,
  ModelStatReal,
  ModelStatVirtual,
  ProviderStat,
  Summary,
} from "@/api/types";
import { fillDailyGaps } from "@/utils/format";

export const useMetricsStore = defineStore("metrics", () => {
  const summary = ref<Summary | null>(null);
  const byRealModel = ref<ModelStatReal[]>([]);
  const byModel = ref<ModelStatVirtual[]>([]);
  const byProvider = ref<ProviderStat[]>([]);
  const daily = ref<DailyStat[]>([]);
  const days = useLocalStorage<number>("ar-trend-days", 30);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const loadedOnce = ref(false);
  let refreshSeq = 0;
  let refreshRequest: { days: number; promise: Promise<void> } | null = null;

  async function refresh(silent = false) {
    if (!silent) loading.value = true;
    const d = days.value;
    if (!refreshRequest || refreshRequest.days !== d) {
      const seq = ++refreshSeq;
      error.value = null;
      const promise = (async () => {
        try {
          const [s, real, virt, prov, dayRows] = await Promise.all([
            api.getSummary(),
            api.getByRealModel(),
            api.getByModel(),
            api.getByProvider(),
            api.getDaily(d),
          ]);
          if (seq !== refreshSeq) return;
          summary.value = s;
          byRealModel.value = real;
          byModel.value = virt;
          byProvider.value = prov;
          daily.value = fillDailyGaps(dayRows, d);
          loadedOnce.value = true;
        } catch (err) {
          if (seq !== refreshSeq) return;
          error.value = err instanceof Error ? err.message : "加载指标失败";
          throw err;
        } finally {
          if (seq === refreshSeq) {
            loading.value = false;
            refreshRequest = null;
          }
        }
      })();
      refreshRequest = { days: d, promise };
    }
    try {
      await refreshRequest.promise;
    } catch (err) {
      if (!silent) throw err;
    }
  }

  async function setDays(next: number) {
    days.value = next;
    await refresh(true);
  }

  return {
    summary,
    byRealModel,
    byModel,
    byProvider,
    daily,
    days,
    loading,
    error,
    loadedOnce,
    refresh,
    setDays,
  };
});
