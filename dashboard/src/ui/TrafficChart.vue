<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from "vue";
import type { Daily } from "../domain/types";
import { compact, count, money } from "../domain/format";
const props = defineProps<{ rows: Daily[] }>();
const active = ref<number | null>(null);
const svgElement = ref<SVGSVGElement>();
const width = ref(870);
const plotWidth = computed(() => width.value - 54);
const plotEnd = computed(() => width.value - 16);
const observer = new ResizeObserver(() => {
  if (svgElement.value)
    width.value = Math.max(260, svgElement.value.clientWidth);
});
onMounted(() => {
  if (svgElement.value) observer.observe(svgElement.value);
});
onUnmounted(() => observer.disconnect());
const max = computed(
  () => Math.max(4, ...props.rows.map((row) => row.count)) * 1.15,
);
const points = computed(() =>
  props.rows.map((row, index) => ({
    x: 38 + (index * plotWidth.value) / Math.max(1, props.rows.length - 1),
    y: 185 - (row.count / max.value) * 150,
  })),
);
const line = computed(() =>
  points.value
    .map((point, index) => (index ? "L" : "M") + point.x + "," + point.y)
    .join(" "),
);
const area = computed(
  () => line.value + " L" + plotEnd.value + ",185 L38,185 Z",
);
const selected = computed(() =>
  active.value === null ? null : props.rows[active.value],
);
</script>
<template>
  <div class="chart-wrapper" @mouseleave="active = null">
    <div class="chart-summary">
      <span class="legend"><i class="dot text-green" />全部请求</span
      ><span class="chart-tip" aria-live="polite">{{
        selected
          ? selected.day +
            " · " +
            count(selected.count) +
            " 次 · " +
            (selected.count - selected.success_count) +
            " 次失败 · " +
            money(selected.cost_usd)
          : "按 UTC 自然日汇总"
      }}</span>
    </div>
    <svg
      ref="svgElement"
      class="traffic-chart"
      :viewBox="`0 0 ${width} 225`"
      role="img"
      aria-label="每日请求量趋势，详细数据可展开下方表格"
    >
      <defs>
        <linearGradient id="traffic-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity=".23" />
          <stop offset="100%" stop-color="var(--accent)" stop-opacity=".01" />
        </linearGradient>
      </defs>
      <g v-for="n in 4" :key="n">
        <line
          x1="38"
          :x2="plotEnd"
          :y1="185 - (n - 1) * 50"
          :y2="185 - (n - 1) * 50"
          stroke="var(--line)"
          stroke-dasharray="3 5"
        />
        <text
          x="28"
          :y="189 - (n - 1) * 50"
          text-anchor="end"
          class="chart-label"
        >
          {{ compact(Math.round((max * (n - 1)) / 3)) }}
        </text>
      </g>
      <path v-if="rows.length" :d="area" fill="url(#traffic-fill)" />
      <path
        v-if="rows.length"
        :d="line"
        stroke="var(--accent)"
        stroke-width="2.8"
        stroke-linejoin="round"
        fill="none"
      />
      <g v-for="(point, index) in points" :key="index">
        <rect
          :x="
            Math.max(38, point.x - plotWidth / 2 / Math.max(1, rows.length - 1))
          "
          y="20"
          :width="
            Math.min(
              plotWidth / Math.max(1, rows.length - 1),
              plotEnd -
                Math.max(
                  38,
                  point.x - plotWidth / 2 / Math.max(1, rows.length - 1),
                ),
            )
          "
          height="170"
          fill="transparent"
          @mouseenter="active = index"
        />
        <text
          v-if="
            index === 0 ||
            index === rows.length - 1 ||
            (index < rows.length - 2 &&
              index %
                Math.ceil(
                  rows.length / Math.max(2, Math.floor(width / 110)),
                ) ===
                0)
          "
          :x="point.x"
          y="216"
          text-anchor="middle"
          class="chart-label"
        >
          {{ rows[index]?.day.slice(5).replace("-", "/") }}
        </text>
        <circle
          v-if="active === index"
          :cx="point.x"
          :cy="point.y"
          r="5"
          fill="var(--accent)"
          stroke="var(--surface)"
          stroke-width="3"
        />
      </g>
    </svg>
    <details class="chart-data">
      <summary>查看每日明细</summary>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>日期（UTC）</th>
              <th>请求</th>
              <th>成功</th>
              <th>费用</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.day">
              <td>{{ row.day }}</td>
              <td>{{ count(row.count) }}</td>
              <td>{{ count(row.success_count) }}</td>
              <td>{{ money(row.cost_usd) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </div>
</template>
