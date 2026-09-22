<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { CallPage } from "../domain/types";
import { useTelemetry } from "../state/telemetry";
import { useWorkspace } from "../state/workspace";
import { count, csvCell, downloadText } from "../domain/format";
import { errorText, request } from "../services/http";
import { usePolling } from "../composables/usePolling";
import CallTable from "../ui/CallTable.vue";
import CallInspector from "../ui/CallInspector.vue";
import EmptyState from "../ui/EmptyState.vue";
import Icon from "../ui/Icon.vue";
const route = useRoute(),
  router = useRouter(),
  telemetry = useTelemetry(),
  workspace = useWorkspace();
const data = ref<CallPage | null>(null),
  loading = ref(false),
  error = ref(""),
  selectedCall = ref("");
const filter = computed(() => ({
  model: String(route.query.model ?? ""),
  status: String(route.query.status ?? ""),
  provider: String(route.query.provider ?? ""),
  provider_model: String(route.query.provider_model ?? ""),
}));
const page = computed(() => {
  const value = Number(route.query.page);
  return Number.isSafeInteger(value) && value > 0 ? value : 1;
});
const size = computed(() =>
  [20, 50, 100].includes(Number(route.query.size))
    ? Number(route.query.size)
    : 20,
);
const virtualOptions = computed(() =>
  [
    ...new Set([
      ...Object.keys(workspace.base?.models ?? {}),
      ...telemetry.models.map((row) => row.virtual_model),
      filter.value.model,
    ]),
  ]
    .filter(Boolean)
    .sort(),
);
const providerOptions = computed(() =>
  [
    ...new Set([
      ...Object.keys(workspace.base?.providers ?? {}),
      ...telemetry.realModels.map((row) => row.provider),
      filter.value.provider,
    ]),
  ]
    .filter(Boolean)
    .sort(),
);
const realOptions = computed(() =>
  [
    ...new Set([
      ...Object.entries(workspace.base?.providers ?? {})
        .filter(
          ([name]) => !filter.value.provider || name === filter.value.provider,
        )
        .flatMap(([, provider]) => Object.keys(provider.models)),
      ...telemetry.realModels
        .filter(
          (row) =>
            !filter.value.provider || row.provider === filter.value.provider,
        )
        .map((row) => row.model),
      filter.value.provider_model,
    ]),
  ]
    .filter(Boolean)
    .sort(),
);
const filtered = computed(() => Object.values(filter.value).some(Boolean));
let controller: AbortController | undefined;
let generation = 0;
let pendingKey = "";
let pending: Promise<void> | null = null;
function update(key: string, value: string) {
  const query = { ...route.query, [key]: value || undefined, page: undefined };
  if (key === "provider") Object.assign(query, { provider_model: undefined });
  router.replace({ query });
}
async function load() {
  const params = new URLSearchParams({
    page: String(page.value),
    size: String(size.value),
  });
  for (const [key, value] of Object.entries(filter.value))
    if (value) params.set(key, value);
  const key = params.toString();
  if (pending && pendingKey === key) return pending;
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal,
    current = ++generation;
  pendingKey = key;
  loading.value = true;
  error.value = "";
  pending = (async () => {
    try {
      const result = await request<CallPage>("/api/calls?" + key, { signal });
      if (current !== generation) return;
      if (result.page > result.pages) {
        await router.replace({
          query: { ...route.query, page: String(result.pages) },
        });
        return;
      }
      data.value = result;
    } catch (reason) {
      if (!signal.aborted && current === generation)
        error.value = errorText(reason);
    } finally {
      if (current === generation) {
        loading.value = false;
        pending = null;
      }
    }
  })();
  return pending;
}
function exportPage() {
  if (!data.value) return;
  const fields = [
    "id",
    "timestamp",
    "virtual_model",
    "provider_name",
    "provider_model",
    "status",
    "attempt",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
  ] as const;
  const csv = [
    fields.join(","),
    ...data.value.data.map((call) =>
      fields.map((key) => csvCell(call[key])).join(","),
    ),
  ].join("\r\n");
  downloadText(
    "agent-router-calls-page-" + page.value + ".csv",
    "\uFEFF" + csv,
    "text/csv",
  );
}
watch(
  () => route.fullPath,
  () => {
    data.value = null;
    load();
  },
  { immediate: true },
);
usePolling(load, () => telemetry.autoRefresh);
onUnmounted(() => {
  generation++;
  controller?.abort();
});
</script>
<template>
  <section class="page-heading">
    <div>
      <div class="eyebrow">FOLLOW EVERY REQUEST</div>
      <h1>调用记录<span class="heading-dot">.</span></h1>
      <p>完整的执行上下文，让排查问题少一点猜测。</p>
    </div>
    <div class="heading-actions">
      <button
        class="button"
        :disabled="!data?.data.length || loading"
        @click="exportPage"
      >
        <Icon name="download" :size="16" />导出当前页</button
      ><button class="button" :disabled="loading" @click="load">
        <Icon name="refresh" :size="16" :class="{ spinning: loading }" />刷新
      </button>
    </div>
  </section>
  <section class="panel">
    <div class="filter-bar">
      <label class="filter-field"
        ><span>虚拟模型</span
        ><select
          aria-label="虚拟模型"
          :value="filter.model"
          @change="update('model', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">所有路由</option>
          <option v-for="name in virtualOptions" :key="name">{{ name }}</option>
        </select></label
      ><label class="filter-field"
        ><span>上游服务</span
        ><select
          aria-label="上游服务"
          :value="filter.provider"
          @change="
            update('provider', ($event.target as HTMLSelectElement).value)
          "
        >
          <option value="">所有上游</option>
          <option v-for="name in providerOptions" :key="name">
            {{ name }}
          </option>
        </select></label
      ><label class="filter-field"
        ><span>实际模型</span
        ><select
          aria-label="实际模型"
          :value="filter.provider_model"
          @change="
            update('provider_model', ($event.target as HTMLSelectElement).value)
          "
        >
          <option value="">所有实际模型</option>
          <option v-for="name in realOptions" :key="name">{{ name }}</option>
        </select></label
      ><label class="filter-field"
        ><span>请求状态</span
        ><select
          aria-label="请求状态"
          :value="filter.status"
          @change="update('status', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">所有状态</option>
          <option value="success">成功</option>
          <option value="error">失败</option>
        </select></label
      ><button
        v-if="filtered"
        class="button ghost small filter-clear"
        @click="router.replace('/calls')"
      >
        清除筛选<Icon name="close" :size="14" />
      </button>
    </div>
    <div class="table-caption">
      <span
        ><strong>{{ data ? count(data.total) : "—" }}</strong> 条{{
          filtered ? "匹配" : ""
        }}记录</span
      ><span class="muted">时间按本地时区显示 · 最新优先</span>
    </div>
    <div v-if="error" class="alert error inset" role="alert">
      {{ error }}<button class="button small" @click="load">重试</button>
    </div>
    <div
      v-if="loading && !data"
      class="table-skeleton"
      aria-label="正在加载调用记录"
    >
      <div v-for="n in 6" :key="n" class="skeleton" />
    </div>
    <CallTable
      v-else-if="data?.data.length"
      :calls="data.data"
      @select="selectedCall = $event"
    />
    <EmptyState
      v-else-if="!error"
      :title="filtered ? '没有符合条件的调用' : '还没有调用记录'"
      :description="
        filtered
          ? '试试调整模型或状态筛选条件。'
          : '从请求实验室发送一条请求，即可在这里查看执行详情。'
      "
      ><button
        v-if="filtered"
        class="button small"
        @click="router.replace('/calls')"
      >
        清除筛选</button
      ><RouterLink v-else to="/playground" class="button small"
        >打开请求实验室<Icon name="arrow" :size="14" /></RouterLink
    ></EmptyState>
    <div class="pagination">
      <label
        >每页<select
          aria-label="每页记录数"
          :value="size"
          @change="update('size', ($event.target as HTMLSelectElement).value)"
        >
          <option :value="20">20</option>
          <option :value="50">50</option>
          <option :value="100">100</option></select
        >条</label
      >
      <div>
        <span>第 {{ page }} / {{ data?.pages ?? 1 }} 页</span
        ><button
          class="icon-button"
          aria-label="上一页"
          :disabled="page <= 1 || loading"
          @click="
            router.replace({
              query: { ...route.query, page: String(page - 1) },
            })
          "
        >
          <Icon name="chevron" class="rotate-180" :size="17" /></button
        ><button
          class="icon-button"
          aria-label="下一页"
          :disabled="page >= (data?.pages ?? 1) || loading"
          @click="
            router.replace({
              query: { ...route.query, page: String(page + 1) },
            })
          "
        >
          <Icon name="chevron" :size="17" />
        </button>
      </div>
    </div>
  </section>
  <p class="help page-note">
    费用为调用时的价格快照。导出仅包含当前页摘要，不包含请求或响应正文。
  </p>
  <CallInspector
    v-if="selectedCall"
    :id="selectedCall"
    @close="selectedCall = ''"
  />
</template>
