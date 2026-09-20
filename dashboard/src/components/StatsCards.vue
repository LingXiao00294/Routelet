<script setup lang="ts">
import { computed } from "vue";
import type { Summary } from "@/api/types";
import {
  formatLatency,
  formatNumber,
  formatTokens,
  formatUsd,
} from "@/utils/format";

const props = defineProps<{ summary: Summary | null }>();

const cards = computed(() => {
  const s = props.summary;
  return [
    {
      key: "calls",
      label: "总调用",
      value: formatNumber(s?.total_calls ?? 0),
      sub: s ? `${formatNumber(s.success_count)} 成功 / ${formatNumber(s.error_count)} 失败` : "—",
    },
    {
      key: "rate",
      label: "成功率",
      value: s ? `${formatNumber(s.success_rate, 2)}%` : "—",
      sub: "已是百分数",
    },
    {
      key: "tokens",
      label: "Token",
      value: s
        ? `${formatTokens(s.total_input_tokens)} / ${formatTokens(s.total_output_tokens)}`
        : "—",
      sub: s
        ? `Cache R ${formatTokens(s.total_cache_read)} · W ${formatTokens(s.total_cache_write)}`
        : "—",
    },
    {
      key: "cost",
      label: "费用",
      value: formatUsd(s ? s.total_cost_usd : 0),
      sub: `均延迟 ${formatLatency(s?.avg_latency_ms ?? null)}`,
    },
  ];
});
</script>

<template>
  <div class="cards">
    <article v-for="(c, i) in cards" :key="c.key" class="card panel" :style="{ animationDelay: `${i * 50}ms` }">
      <div class="label">{{ c.label }}</div>
      <div class="value mono">{{ c.value }}</div>
      <div class="sub muted">{{ c.sub }}</div>
    </article>
  </div>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.85rem;
}

.card {
  padding: 1rem 1.1rem;
  animation: fade-up 0.4s ease both;
}

.label {
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
}

.value {
  margin-top: 0.35rem;
  font-size: 1.45rem;
  font-weight: 600;
}

.sub {
  margin-top: 0.35rem;
  font-size: 0.82rem;
}

@media (max-width: 1100px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .cards {
    grid-template-columns: 1fr;
  }
}
</style>
