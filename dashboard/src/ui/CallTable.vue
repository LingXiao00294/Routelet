<script setup lang="ts">
import type { Call } from "../domain/types";
import {
  compact,
  dateTime,
  latency,
  money,
  totalTokens,
} from "../domain/format";
import Icon from "./Icon.vue";
defineProps<{ calls: Call[]; compactView?: boolean }>();
defineEmits<{ select: [id: string] }>();
</script>
<template>
  <div class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th>请求 / 时间</th>
          <th>模型路由</th>
          <th>状态</th>
          <th class="numeric">耗时</th>
          <th v-if="!compactView" class="numeric">Token</th>
          <th class="numeric">费用</th>
          <th><span class="sr-only">详情</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="call in calls" :key="call.id">
          <td>
            <button
              class="text-link mono"
              :title="call.id"
              @click="$emit('select', call.id)"
            >
              {{ call.id.slice(0, 8) }}</button
            ><span class="cell-sub">{{ dateTime(call.timestamp) }}</span>
          </td>
          <td>
            <span class="cell-title">{{ call.virtual_model }}</span
            ><span
              class="cell-sub truncate"
              :title="
                (call.provider_name ?? '') + '/' + (call.provider_model ?? '')
              "
              >{{
                call.provider_name && call.provider_model
                  ? call.provider_name + " / " + call.provider_model
                  : "未选中上游"
              }}</span
            >
          </td>
          <td>
            <span
              class="badge"
              :class="call.status === 'success' ? 'green' : 'red'"
              ><i class="dot" />{{
                call.status === "success" ? "成功" : "失败"
              }}</span
            ><span v-if="call.attempt > 1" class="cell-sub"
              >{{ call.attempt }} 次尝试</span
            >
          </td>
          <td class="numeric mono">{{ latency(call.latency_ms) }}</td>
          <td v-if="!compactView" class="numeric mono">
            {{ compact(totalTokens(call)) }}
          </td>
          <td class="numeric mono">{{ money(call.cost_usd) }}</td>
          <td>
            <button
              class="icon-button small"
              :aria-label="'查看调用 ' + call.id"
              @click="$emit('select', call.id)"
            >
              <Icon name="arrowup" :size="16" />
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
