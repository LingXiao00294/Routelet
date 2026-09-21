<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { CallRecord } from "@/api/types";
import {
  formatActualModel,
  formatLatency,
  formatTime,
  formatTokens,
  formatUsd,
  parseFailover,
  prettyJson,
} from "@/utils/format";
import { useOverlayChrome } from "@/composables/useOverlayChrome";

const props = defineProps<{
  record: CallRecord | null;
  loading: boolean;
  error: string | null;
}>();

const emit = defineEmits<{ close: [] }>();

const drawerRef = ref<HTMLElement | null>(null);
const overlayActive = ref(true);
useOverlayChrome(overlayActive, drawerRef);

const failover = computed(() => parseFailover(props.record?.failover_details ?? null));

const providerLabel = computed(() => {
  const record = props.record;
  if (!record) return "—";
  if (record.provider_name) {
    return `${record.provider_name} (${record.provider_type || "—"})`;
  }
  if (record.attempt === 0) return "未发起上游请求";
  return failover.value.length ? "未命中（见 Failover 链）" : "未记录最终 Provider";
});

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

onMounted(() => window.addEventListener("keydown", onKey));
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <div class="overlay" @click.self="$emit('close')">
      <aside
        ref="drawerRef"
        class="drawer"
        role="dialog"
        aria-modal="true"
        tabindex="-1"
      >
        <header class="drawer-head">
          <div>
            <h2>调用详情</h2>
            <p v-if="record" class="muted mono">{{ record.id }}</p>
          </div>
          <button class="btn btn-sm" type="button" @click="$emit('close')">关闭</button>
        </header>

        <div v-if="loading" class="empty-state">加载中…</div>
        <div v-else-if="error" class="error-state">{{ error }}</div>
        <div v-else-if="record" class="body">
          <section class="meta">
            <div><span class="k">时间</span><span class="mono">{{ formatTime(record.timestamp) }}</span></div>
            <div><span class="k">虚拟模型</span><span class="mono">{{ record.virtual_model }}</span></div>
            <div><span class="k">Provider</span><span class="mono">{{ providerLabel }}</span></div>
            <div><span class="k">真实模型</span><span class="mono">{{ formatActualModel(record.provider_name, record.provider_model) }}</span></div>
            <div><span class="k">URL</span><span class="mono wrap">{{ record.provider_url || "—" }}</span></div>
            <div><span class="k">Attempt</span><span class="mono">{{ record.attempt > 0 ? record.attempt : "未发起" }}</span></div>
            <div><span class="k">状态</span>
              <span class="badge" :class="record.status === 'success' ? 'badge-success' : 'badge-danger'">
                {{ record.status }}
              </span>
            </div>
            <div><span class="k">延迟</span><span class="mono">{{ formatLatency(record.latency_ms) }}</span></div>
            <div><span class="k">Token</span><span class="mono">{{ formatTokens(record.input_tokens) }} / {{ formatTokens(record.output_tokens) }}</span></div>
            <div><span class="k">Cache</span><span class="mono">{{ formatTokens(record.cache_read_tokens) }} / {{ formatTokens(record.cache_write_tokens) }}</span></div>
            <div><span class="k">费用</span><span class="mono">{{ formatUsd(record.cost_usd) }}</span></div>
          </section>

          <section v-if="record.provider_model" class="block">
            <h3>价格快照 <span class="muted">(USD / 1M Token)</span></h3>
            <div class="price-grid">
              <div><span class="k">输入</span><span class="mono">{{ formatUsd(record.input_price_per_million) }}</span></div>
              <div><span class="k">输出</span><span class="mono">{{ formatUsd(record.output_price_per_million) }}</span></div>
              <div><span class="k">缓存读取</span><span class="mono">{{ formatUsd(record.cache_read_price_per_million) }}</span></div>
              <div><span class="k">缓存写入</span><span class="mono">{{ formatUsd(record.cache_write_price_per_million) }}</span></div>
            </div>
          </section>

          <section v-if="record.error_type || record.error_message" class="block">
            <h3>错误</h3>
            <p class="mono">{{ record.error_type }} — {{ record.error_message }}</p>
          </section>

          <section v-if="failover.length || record.attempt > 1" class="block">
            <h3>Failover 链</h3>
            <ol v-if="failover.length" class="chain">
              <li v-for="(f, i) in failover" :key="i">
                <span class="badge badge-danger">fail</span>
                <span class="mono">{{ formatActualModel(f.provider, f.model) }}</span>
                <span class="muted">{{ f.error }}</span>
                <span v-if="f.latency_ms != null" class="mono muted">{{ f.latency_ms }}ms</span>
              </li>
            </ol>
            <div v-if="record.status === 'success' && record.provider_name" class="chain-ok">
              <span class="badge badge-success">hit</span>
              <span class="mono">{{ formatActualModel(record.provider_name, record.provider_model) }}</span>
            </div>
          </section>

          <section class="block">
            <h3>Request</h3>
            <pre class="json mono">{{ prettyJson(record.request_body) || "—" }}</pre>
          </section>
          <section class="block">
            <h3>Response</h3>
            <pre class="json mono">{{ prettyJson(record.response_body) || "—" }}</pre>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(10, 14, 20, 0.4);
  display: flex;
  justify-content: flex-end;
  align-items: stretch;
}
.drawer {
  width: min(560px, 100%);
  height: 100%;
  min-height: 100%;
  border: none;
  border-left: 1px solid var(--border);
  border-radius: 0;
  background: var(--bg-elevated);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  animation: slide-in 0.22s ease;
}
@keyframes slide-in {
  from { transform: translateX(24px); opacity: 0.6; }
  to { transform: none; opacity: 1; }
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  padding: 1rem 1.1rem;
  border-bottom: 1px solid var(--border);
}
.drawer-head h2 {
  margin: 0;
  font-size: 1.1rem;
}
.drawer-head p {
  margin: 0.25rem 0 0;
  font-size: 0.78rem;
}
.body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 1rem 1.1rem 2rem;
}
.meta {
  display: grid;
  gap: 0.55rem;
  margin-bottom: 1.25rem;
}
.meta > div {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 0.5rem;
  align-items: start;
}
.k {
  color: var(--text-muted);
  font-size: 0.8rem;
  font-weight: 600;
}
.wrap {
  word-break: break-all;
  white-space: normal;
}
.block {
  margin-top: 1.1rem;
}
.block h3 {
  margin: 0 0 0.5rem;
  font-size: 0.9rem;
}
.block h3 .muted {
  font-size: 0.75rem;
  font-weight: 400;
}
.price-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.55rem 1rem;
}
.price-grid > div {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
}
.chain {
  margin: 0;
  padding-left: 1.1rem;
  display: grid;
  gap: 0.45rem;
}
.chain li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: center;
}
.chain-ok {
  margin-top: 0.5rem;
  display: flex;
  gap: 0.4rem;
  align-items: center;
}
.json {
  margin: 0;
  padding: 0.75rem;
  border-radius: var(--radius-sm);
  background: var(--bg-muted);
  border: 1px solid var(--border);
  overflow: auto;
  max-height: 320px;
  font-size: 0.78rem;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
