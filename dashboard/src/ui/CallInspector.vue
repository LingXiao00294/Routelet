<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { Call } from "../domain/types";
import { priceFields } from "../domain/types";
import {
  count,
  dateTime,
  latency,
  money,
  parseAttempts,
  prettyJson,
} from "../domain/format";
import { errorText, request } from "../services/http";
import { copyText } from "../state/notifications";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
const props = defineProps<{ id: string }>();
defineEmits<{ close: [] }>();
const call = ref<Call | null>(null),
  error = ref(""),
  tab = ref("trace"),
  loading = ref(true);
const controller = new AbortController();
const attempts = computed(() => parseAttempts(call.value?.failover_details));
async function load() {
  error.value = "";
  loading.value = true;
  try {
    call.value = await request<Call>(
      "/api/calls/" + encodeURIComponent(props.id),
      { signal: controller.signal },
    );
  } catch (reason) {
    if (!controller.signal.aborted) error.value = errorText(reason);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
onUnmounted(() => controller.abort());
</script>
<template>
  <Modal
    title="调用详情"
    eyebrow="REQUEST INSPECTOR"
    wide
    @close="$emit('close')"
  >
    <div v-if="loading" class="skeleton-block" aria-label="正在加载调用详情" />
    <div v-else-if="error" class="alert error" role="alert">
      {{ error }}<button class="button small" @click="load">重试</button>
    </div>
    <template v-else-if="call">
      <div class="detail-id">
        <code>{{ call.id }}</code
        ><button
          class="icon-button"
          aria-label="复制请求 ID"
          @click="copyText(call.id)"
        >
          <Icon name="copy" :size="16" />
        </button>
      </div>
      <div class="detail-metrics">
        <div>
          <span>状态</span
          ><strong
            :class="call.status === 'success' ? 'text-green' : 'text-red'"
            >{{ call.status === "success" ? "成功" : "失败" }}</strong
          >
        </div>
        <div>
          <span>总耗时</span><strong>{{ latency(call.latency_ms) }}</strong>
        </div>
        <div>
          <span>费用</span><strong>{{ money(call.cost_usd, 6) }}</strong>
        </div>
        <div>
          <span>Provider 尝试</span><strong>{{ call.attempt }} 次</strong>
        </div>
      </div>
      <div class="detail-route">
        <span class="badge neutral">{{ call.virtual_model }}</span
        ><Icon name="arrow" :size="16" /><code
          >{{ call.provider_name ?? "—" }} /
          {{ call.provider_model ?? "—" }}</code
        ><span class="muted">{{ dateTime(call.timestamp) }}</span>
      </div>
      <div v-if="call.error_type || call.error_message" class="alert error">
        <strong>{{ call.error_type }}</strong>
        <p>{{ call.error_message }}</p>
      </div>
      <div class="tabs">
        <button
          v-for="item in [
            { id: 'trace', label: '执行与用量' },
            { id: 'request', label: '请求正文' },
            { id: 'response', label: '响应正文' },
          ]"
          :key="item.id"
          :class="{ active: tab === item.id }"
          @click="tab = item.id"
        >
          {{ item.label }}
        </button>
      </div>
      <template v-if="tab === 'trace'">
        <h3 class="section-label">TOKEN 用量与价格快照</h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>类型</th>
                <th class="numeric">Token</th>
                <th class="numeric">USD / 1M Token</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(field, index) in priceFields" :key="field.key">
                <td>{{ field.label }}</td>
                <td class="numeric mono">
                  {{
                    count(
                      [
                        call.input_tokens,
                        call.output_tokens,
                        call.cache_read_tokens,
                        call.cache_write_tokens,
                      ][index],
                    )
                  }}
                </td>
                <td class="numeric mono">{{ money(call[field.key]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="help">
          价格取自调用时的快照；— 表示未配置，$0.0000 表示显式零价格。
        </p>
        <h3 class="section-label">
          故障转移记录 <span class="muted">{{ attempts.length }}</span>
        </h3>
        <div v-for="(attempt, index) in attempts" :key="index" class="attempt">
          <span class="step-index">{{ index + 1 }}</span>
          <div>
            <strong
              >{{ attempt.provider
              }}<span v-if="attempt.model"> / {{ attempt.model }}</span></strong
            >
            <p v-if="attempt.error !== undefined" class="text-red">
              {{ attempt.error || "Provider 返回空错误消息" }}
            </p>
          </div>
          <span class="mono muted">{{ latency(attempt.latency_ms) }}</span>
        </div>
        <p v-if="!attempts.length" class="muted">此次调用没有记录故障转移。</p>
      </template>
      <template v-else
        ><div class="code-toolbar">
          <span>{{ tab === "request" ? "REQUEST BODY" : "RESPONSE BODY" }}</span
          ><button
            class="button small"
            :disabled="
              !(tab === 'request' ? call.request_body : call.response_body)
            "
            @click="
              copyText(
                prettyJson(
                  tab === 'request' ? call.request_body : call.response_body,
                ),
              )
            "
          >
            <Icon name="copy" :size="14" />复制
          </button>
        </div>
        <pre
          v-if="tab === 'request' ? call.request_body : call.response_body"
          class="code-block"
          >{{
            prettyJson(
              tab === "request" ? call.request_body : call.response_body,
            )
          }}</pre>
        <p v-else class="help">
          此次调用未记录{{
            tab === "request" ? "请求" : "响应"
          }}正文。用量与状态仍可在「执行与用量」中查看。
        </p></template
      >
    </template>
  </Modal>
</template>
