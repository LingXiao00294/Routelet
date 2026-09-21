<script setup lang="ts">
import { onMounted } from "vue";
import { RouterLink } from "vue-router";
import { storeToRefs } from "pinia";
import { useMetricsStore } from "@/stores/metrics";
import { useAppStore } from "@/stores/app";
import StatsCards from "@/components/StatsCards.vue";
import TrendChart from "@/components/TrendChart.vue";
import ModelChart from "@/components/ModelChart.vue";
import {
  formatActualModel,
  formatNumber,
  formatTokens,
  formatUsd,
} from "@/utils/format";
import { useAutoRefresh } from "@/composables/useAutoRefresh";

const metrics = useMetricsStore();
const app = useAppStore();
const {
  summary,
  byRealModel,
  byModel,
  byProvider,
  daily,
  days,
  loading,
  error,
  loadedOnce,
} = storeToRefs(metrics);
const { openCircuits } = storeToRefs(app);

onMounted(() => {
  void metrics.refresh();
  void app.loadCircuit(true);
});

useAutoRefresh(async () => {
  await metrics.refresh(true);
  if (metrics.error) throw new Error(metrics.error);
});
</script>

<template>
  <div class="page fade-up">
    <header class="page-head">
      <div>
        <h1>Overview</h1>
        <p class="muted">调用概览、趋势与熔断状态</p>
      </div>
      <div class="day-switch">
        <button
          v-for="d in [7, 30, 90]"
          :key="d"
          type="button"
          class="btn btn-sm"
          :class="{ 'btn-primary': days === d }"
          @click="metrics.setDays(d)"
        >
          {{ d }} 天
        </button>
      </div>
    </header>

    <div v-if="openCircuits.length" class="alert panel">
      <div>
        <strong>熔断告警</strong>
        <span class="muted">
          —
          {{ openCircuits.map(([n, s]) => `${n} (${s})`).join(" · ") }}
        </span>
      </div>
      <RouterLink class="btn btn-sm" to="/config/circuit">管理</RouterLink>
    </div>

    <div v-if="error && !loadedOnce" class="error-state panel">{{ error }}</div>
    <div v-else-if="loading && !loadedOnce" class="empty-state panel">加载中…</div>
    <template v-else>
      <StatsCards :summary="summary" />

      <div class="grid-2">
        <section class="panel chart-panel trend-panel">
          <div class="panel-head">
            <h2 class="panel-title">日趋势</h2>
            <span class="muted mono">UTC</span>
          </div>
          <TrendChart :data="daily" />
        </section>
        <section class="panel chart-panel model-panel">
          <div class="panel-head">
            <h2 class="panel-title">真实模型分布</h2>
          </div>
          <ModelChart :data="byRealModel" />
          <div v-if="byRealModel.length" class="table-wrap real-model-stats">
            <table class="data">
              <thead>
                <tr>
                  <th>真实模型</th>
                  <th>调用</th>
                  <th>成功</th>
                  <th>Token</th>
                  <th>费用</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in byRealModel"
                  :key="`${row.provider}\u0000${row.model}`"
                >
                  <td class="mono">
                    {{ formatActualModel(row.provider, row.model) }}
                  </td>
                  <td class="mono">{{ formatNumber(row.count) }}</td>
                  <td class="mono">{{ formatNumber(row.success_count) }}</td>
                  <td class="mono">
                    {{ formatTokens(row.total_input_tokens) }} /
                    {{ formatTokens(row.total_output_tokens) }}
                  </td>
                  <td class="mono">{{ formatUsd(row.total_cost_usd) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div class="grid-2">
        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">虚拟模型</h2>
          </div>
          <div v-if="!byModel.length" class="empty-state">暂无数据</div>
          <div v-else class="table-wrap">
            <table class="data">
              <thead>
                <tr>
                  <th>模型</th>
                  <th>调用</th>
                  <th>成功</th>
                  <th>Token</th>
                  <th>费用</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in byModel" :key="row.virtual_model">
                  <td class="mono">{{ row.virtual_model }}</td>
                  <td class="mono">{{ formatNumber(row.count) }}</td>
                  <td class="mono">{{ formatNumber(row.success_count) }}</td>
                  <td class="mono">
                    {{ formatTokens(row.total_input_tokens) }} /
                    {{ formatTokens(row.total_output_tokens) }}
                  </td>
                  <td class="mono">{{ formatUsd(row.total_cost_usd) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <h2 class="panel-title">Provider 类型</h2>
          </div>
          <div v-if="!byProvider.length" class="empty-state">暂无数据</div>
          <div v-else class="table-wrap">
            <table class="data">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>调用</th>
                  <th>成功</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in byProvider" :key="row.provider">
                  <td class="mono">{{ row.provider }}</td>
                  <td class="mono">{{ formatNumber(row.count) }}</td>
                  <td class="mono">{{ formatNumber(row.success_count) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.page-head h1 {
  margin: 0;
  font-size: 1.5rem;
}

.page-head p {
  margin: 0.25rem 0 0;
}

.day-switch {
  display: flex;
  flex-shrink: 0;
  gap: 0.35rem;
}

.day-switch .btn {
  white-space: nowrap;
}

.alert {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.85rem 1rem;
  margin-bottom: 1rem;
  border-color: var(--warn);
  background: var(--warn-soft);
}

.grid-2 {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 1rem;
  margin-top: 1rem;
}

.grid-2 > .panel {
  min-width: 0;
}

.panel {
  padding: 1rem;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.chart-panel {
  min-height: 320px;
}

.real-model-stats {
  max-height: 280px;
  margin-top: 0.75rem;
  overflow: auto;
}

.trend-panel,
.model-panel {
  grid-column: 1 / -1;
}

@media (max-width: 960px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 520px) {
  .page-head {
    flex-direction: column;
  }

  .day-switch {
    align-self: stretch;
  }

  .day-switch .btn {
    flex: 1;
  }
}
</style>
