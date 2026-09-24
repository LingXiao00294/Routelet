<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import { useSearchQuery } from "../composables/useSearchQuery";
import { useWorkspace } from "../state/workspace";
import { useTelemetry } from "../state/telemetry";
import {
  affectedRoutes,
  modelOrder,
  moveModel,
  removeCatalogEntry,
} from "../domain/config";
import { money } from "../domain/format";
import { request, errorText } from "../services/http";
import { notify } from "../state/notifications";
import Icon from "../ui/Icon.vue";
import EmptyState from "../ui/EmptyState.vue";
import Modal from "../ui/Modal.vue";
import ProviderEditor from "../ui/ProviderEditor.vue";
import ModelEditor from "../ui/ModelEditor.vue";
import SearchInput from "../ui/SearchInput.vue";
const workspace = useWorkspace(),
  telemetry = useTelemetry();
const query = useSearchQuery();
const providerEdit = ref<string | null>(null);
const modelEdit = ref<{ provider: string; model?: string } | null>(null);
const removal = ref<{ provider: string; model?: string } | null>(null);
const reset = ref(""),
  resetting = ref(false);
const drag = shallowRef<{
  provider: string;
  model: string;
  index: number;
  pointerId: number;
  handle: HTMLElement;
  startX: number;
  startY: number;
  left: number;
  top: number;
  width: number;
  height: number;
} | null>(null);
const dragging = ref(false);
const pointer = ref({ x: 0, y: 0 });
const dropIndex = ref<number | null>(null);
const announcement = ref("");
const destination = computed(() =>
  drag.value && dropIndex.value !== null
    ? dropIndex.value > drag.value.index
      ? dropIndex.value - 1
      : dropIndex.value
    : null,
);
const previewStyle = computed(() => {
  const source = drag.value;
  return source
    ? {
        left: `${source.left}px`,
        top: `${source.top}px`,
        width: `${source.width}px`,
        minHeight: `${source.height}px`,
        transform: `translate3d(${pointer.value.x - source.startX}px, ${pointer.value.y - source.startY}px, 0)`,
      }
    : {};
});
let scrollFrame = 0;
function clearDrag() {
  const source = drag.value;
  drag.value = null;
  dragging.value = false;
  dropIndex.value = null;
  cancelAnimationFrame(scrollFrame);
  if (source?.handle.hasPointerCapture(source.pointerId))
    source.handle.releasePointerCapture(source.pointerId);
}
function updateTarget() {
  const source = drag.value;
  const list = source?.handle.closest<HTMLElement>(".catalog-list");
  if (!source || !list) return;
  const bounds = list.getBoundingClientRect();
  const { x, y } = pointer.value;
  if (
    x < bounds.left ||
    x > bounds.right ||
    y < bounds.top ||
    y > bounds.bottom
  ) {
    dropIndex.value = null;
    return;
  }
  const rows = Array.from(list.querySelectorAll<HTMLElement>(".catalog-row"));
  const before = rows.findIndex((row) => {
    const rect = row.getBoundingClientRect();
    return y < rect.top + rect.height / 2;
  });
  const slot = before < 0 ? rows.length : before;
  dropIndex.value =
    (slot > source.index ? slot - 1 : slot) === source.index ? null : slot;
}
function autoScroll() {
  if (!dragging.value) return;
  const y = pointer.value.y;
  const edge = 40;
  const delta =
    y < edge
      ? -Math.min(12, (edge - y) / 3)
      : y > window.innerHeight - edge
        ? Math.min(12, (y - window.innerHeight + edge) / 3)
        : 0;
  if (delta) window.scrollBy(0, delta);
  updateTarget();
  scrollFrame = requestAnimationFrame(autoScroll);
}
function startDrag(
  event: PointerEvent,
  provider: string,
  model: string,
  index: number,
) {
  if (
    event.button !== 0 ||
    !event.isPrimary ||
    !workspace.draft ||
    modelOrder(workspace.draft.providers[provider]).length < 2
  )
    return;
  clearDrag();
  const handle = event.currentTarget as HTMLElement;
  const row = handle.closest<HTMLElement>(".catalog-row")!;
  const { left, top, width, height } = row.getBoundingClientRect();
  handle.focus({ preventScroll: true });
  handle.setPointerCapture(event.pointerId);
  pointer.value = { x: event.clientX, y: event.clientY };
  drag.value = {
    provider,
    model,
    index,
    pointerId: event.pointerId,
    handle,
    startX: event.clientX,
    startY: event.clientY,
    left,
    top,
    width,
    height,
  };
}
function moveDrag(event: PointerEvent) {
  const source = drag.value;
  if (!source || source.pointerId !== event.pointerId) return;
  pointer.value = { x: event.clientX, y: event.clientY };
  if (!dragging.value) {
    if (
      Math.hypot(event.clientX - source.startX, event.clientY - source.startY) <
      6
    )
      return;
    dragging.value = true;
    scrollFrame = requestAnimationFrame(autoScroll);
  }
  event.preventDefault();
  updateTarget();
}
function reorder(provider: string, from: number, to: number) {
  const entry = workspace.draft?.providers[provider];
  if (!entry || from === to || to < 0 || to >= modelOrder(entry).length) return;
  const model = modelOrder(entry)[from];
  moveModel(entry, from, to);
  announcement.value = `${model} 已移到第 ${to + 1} 位`;
}
function finishDrag(event: PointerEvent) {
  const source = drag.value;
  if (!source || source.pointerId !== event.pointerId) return;
  if (dragging.value) {
    pointer.value = { x: event.clientX, y: event.clientY };
    updateTarget();
    if (destination.value !== null)
      reorder(source.provider, source.index, destination.value);
  }
  clearDrag();
}
function cancelWithEscape(event: KeyboardEvent) {
  if (!drag.value) return;
  event.preventDefault();
  event.stopPropagation();
  clearDrag();
  announcement.value = "已取消拖动，顺序未改变";
}
onBeforeUnmount(clearDrag);
const providers = computed(() =>
  Object.entries(workspace.draft?.providers ?? {}).filter(([name, provider]) =>
    (
      name +
      " " +
      provider.base_url +
      " " +
      Object.keys(provider.models).join(" ")
    )
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
);
const impacted = computed(() =>
  removal.value && workspace.draft
    ? affectedRoutes(
        workspace.draft,
        removal.value.provider,
        removal.value.model,
      )
    : [],
);
function remove() {
  if (!workspace.draft || !removal.value) return;
  removeCatalogEntry(
    workspace.draft,
    removal.value.provider,
    removal.value.model,
  );
  removal.value = null;
}
async function resetCircuit() {
  resetting.value = true;
  try {
    await request(
      "/api/circuit-breaker/" + encodeURIComponent(reset.value) + "/reset",
      { method: "POST" },
    );
    notify("熔断状态与限流冷却已重置，下次请求将重新尝试该 Provider");
    reset.value = "";
    await telemetry.refresh();
  } catch (error) {
    notify(errorText(error), "error");
  } finally {
    resetting.value = false;
  }
}
</script>
<template>
  <section class="page-heading">
    <div>
      <div class="eyebrow">BRING YOUR OWN MODELS</div>
      <h1>Providers<span class="heading-dot">.</span></h1>
      <p>连接、模型、价格，一处管理。</p>
    </div>
    <button
      class="button primary"
      :disabled="!workspace.draft || workspace.saving"
      @click="providerEdit = ''"
    >
      <Icon name="plus" :size="17" />添加 Provider
    </button>
  </section>
  <div class="page-toolbar">
    <SearchInput
      v-model="query"
      label="搜索 Provider 或模型"
      placeholder="搜索 Provider、地址或模型…"
      :size="18"
    />
    <span class="muted"
      >{{ providers.length }} 个 Provider ·
      {{
        Object.values(workspace.draft?.providers ?? {}).reduce(
          (sum, provider) => sum + Object.keys(provider.models).length,
          0,
        )
      }}
      个实际模型</span
    >
  </div>
  <div v-if="workspace.loading && !workspace.draft" class="skeleton-block" />
  <fieldset v-else :disabled="workspace.saving" class="provider-grid">
    <article
      v-for="([name, provider], index) in providers"
      :key="name"
      class="panel provider-card"
    >
      <div class="provider-card-heading">
        <span class="provider-avatar large" :class="'color-' + (index % 4)">{{
          name.slice(0, 1).toUpperCase()
        }}</span>
        <div>
          <h2>{{ name }}</h2>
          <span>Messages API</span>
        </div>
        <button
          class="icon-button"
          :aria-label="'重置 Provider 保护状态 ' + name"
          :title="'重置 ' + name + ' 的熔断状态与限流冷却'"
          @click="reset = name"
        >
          <Icon name="refresh" :size="17" />
        </button>
        <button
          class="icon-button"
          :aria-label="'编辑 Provider ' + name"
          @click="providerEdit = name"
        >
          <Icon name="edit" :size="17" />
        </button>
      </div>
      <div class="provider-connection">
        <Icon name="link" :size="15" /><code :title="provider.base_url">{{
          provider.base_url
        }}</code>
      </div>
      <div class="provider-badges">
        <span
          class="badge"
          :class="
            provider.api_key_unresolved || !provider.has_key ? 'amber' : 'green'
          "
          ><Icon name="key" :size="12" />{{
            provider.api_key_unresolved
              ? "环境变量未设置"
              : provider.has_key
                ? "密钥已配置"
                : "待配置密钥"
          }}</span
        ><span class="badge neutral"
          ><Icon name="clock" :size="12" />{{ provider.timeout_seconds }}s
          超时</span
        ><span v-if="provider.max_concurrent" class="badge neutral"
          >并发 {{ provider.max_concurrent }}</span
        >
      </div>
      <div
        v-if="telemetry.circuits[name] && telemetry.circuits[name] !== 'closed'"
        class="circuit-alert"
      >
        <span
          ><i class="dot" />{{
            telemetry.circuits[name] === "open"
              ? "熔断中，暂时跳过此 Provider"
              : "半开状态，等待恢复探测"
          }}</span
        >
      </div>
      <div class="catalog-heading">
        <h3>
          实际模型
          <span class="count-bubble">{{
            Object.keys(provider.models).length
          }}</span>
        </h3>
        <div class="catalog-heading-actions">
          <button class="text-link" @click="modelEdit = { provider: name }">
            <Icon name="plus" :size="15" />添加模型
          </button>
          <span>输入 / 输出 · $ / 1M</span>
        </div>
      </div>
      <TransitionGroup
        v-if="Object.keys(provider.models).length"
        name="route-sort"
        tag="div"
        class="catalog-list"
        @keydown.esc.capture="cancelWithEscape"
      >
        <div
          v-for="(model, modelIndex) in modelOrder(provider)"
          :key="model"
          class="catalog-row"
          :class="{
            'is-dragging':
              dragging && drag?.provider === name && drag.index === modelIndex,
          }"
        >
          <span
            v-if="drag?.provider === name && dropIndex === modelIndex"
            class="route-drop-indicator"
            aria-hidden="true"
            ><span>放到第 {{ destination! + 1 }} 位</span></span
          >
          <span
            v-if="
              drag?.provider === name &&
              modelIndex === modelOrder(provider).length - 1 &&
              dropIndex === modelOrder(provider).length
            "
            class="route-drop-indicator after"
            aria-hidden="true"
            ><span>放到第 {{ destination! + 1 }} 位</span></span
          >
          <button
            class="route-drag-handle icon-button small"
            :aria-label="'拖动排序 ' + name + '/' + model"
            title="拖动排序，也可用上下方向键调整"
            :disabled="modelOrder(provider).length < 2"
            @pointerdown="startDrag($event, name, model, modelIndex)"
            @pointermove="moveDrag"
            @pointerup="finishDrag"
            @pointercancel="clearDrag"
            @lostpointercapture="clearDrag"
            @keydown.up.prevent="reorder(name, modelIndex, modelIndex - 1)"
            @keydown.down.prevent="reorder(name, modelIndex, modelIndex + 1)"
          >
            <Icon name="grip" :size="16" />
          </button>
          <button
            class="catalog-model"
            @click="modelEdit = { provider: name, model: String(model) }"
          >
            <Icon name="token" :size="16" /><span :title="String(model)">{{
              model
            }}</span></button
          ><span class="catalog-price mono"
            >{{ money(provider.models[model].input_price_per_million, 2) }} /
            {{
              money(provider.models[model].output_price_per_million, 2)
            }}</span
          ><button
            class="icon-button small danger-hover"
            :aria-label="'删除实际模型 ' + model"
            @click="removal = { provider: name, model: String(model) }"
          >
            <Icon name="trash" :size="14" />
          </button>
        </div>
      </TransitionGroup>
      <div v-else class="catalog-empty">
        还没有模型，登记后即可用于 Router。
      </div>
      <div class="provider-card-foot">
        <span
          >{{ affectedRoutes(workspace.draft!, name).length }} 个 Router
          引用</span
        ><button
          class="icon-button small danger-hover"
          :aria-label="'删除 Provider ' + name"
          @click="removal = { provider: name }"
        >
          <Icon name="trash" :size="15" />
        </button>
      </div>
    </article>
    <button
      v-if="providers.length"
      class="add-provider-card"
      @click="providerEdit = ''"
    >
      <span><Icon name="plus" :size="26" /></span><strong>连接更多可能</strong>
      <p>添加一个 Messages API 兼容 Provider</p>
    </button>
  </fieldset>
  <p class="sr-only" role="status" aria-live="polite">{{ announcement }}</p>
  <div
    v-if="dragging && drag"
    class="route-drag-preview catalog-drag-preview"
    :style="previewStyle"
    aria-hidden="true"
  >
    <Icon name="grip" :size="16" />
    <Icon name="token" :size="16" />
    <strong>{{ drag.model }}</strong>
    <span class="badge neutral">{{
      destination === null ? "拖动调整顺序" : `放到第 ${destination + 1} 位`
    }}</span>
  </div>
  <section v-if="workspace.draft && !providers.length" class="panel">
    <EmptyState
      icon="providers"
      :title="query ? '没有找到匹配的 Provider' : '你的模型，从这里连接'"
      :description="
        query
          ? '尝试搜索 Provider 名称、地址或实际模型。'
          : '添加 API 地址和密钥，登记可用模型，即可配置 Router。'
      "
      ><button
        class="button primary"
        @click="query ? (query = '') : (providerEdit = '')"
      >
        {{ query ? "清除搜索" : "添加第一个 Provider"
        }}<Icon name="plus" :size="16" /></button
    ></EmptyState>
  </section>
  <div class="info-strip">
    <Icon name="shield" :size="18" /><span
      >连接状态仅表示密钥与熔断配置。要确认 Provider
      实际可用，请在请求实验室发起测试。</span
    >
  </div>
  <ProviderEditor
    v-if="providerEdit !== null"
    :name="providerEdit || undefined"
    @close="providerEdit = null"
  />
  <ModelEditor
    v-if="modelEdit"
    :provider="modelEdit.provider"
    :model="modelEdit.model"
    @close="modelEdit = null"
  />
  <Modal
    v-if="removal"
    :title="removal.model ? '删除实际模型？' : '删除 Provider？'"
    @close="removal = null"
    ><p>
      将从草稿中删除
      <strong
        >{{ removal.provider
        }}{{ removal.model ? " / " + removal.model : "" }}</strong
      >。
    </p>
    <div v-if="impacted.length" class="alert warning">
      <strong>将清理 {{ impacted.length }} 个 Router 中的对应引用</strong>
      <p>{{ impacted.join("、") }}</p>
      <p>
        没有剩余候选的 Router 会一起删除；固定模型失效时，将选择第一个剩余候选。
      </p>
    </div>
    <p class="help">检查并发布后才会影响正在运行的配置。</p>
    <template #footer
      ><button class="button" @click="removal = null">取消</button
      ><button class="button danger" @click="remove">
        从草稿中删除
      </button></template
    ></Modal
  >
  <Modal
    v-if="reset"
    title="重置 Provider 保护状态？"
    :busy="resetting"
    @close="reset = ''"
    ><p>
      将立即重置
      <strong>{{ reset }}</strong>
      的熔断状态和限流冷却。下次调用会重新尝试该
      Provider，此操作直接作用于运行时。
    </p>
    <template #footer
      ><button class="button" :disabled="resetting" @click="reset = ''">
        取消</button
      ><button
        class="button primary"
        :disabled="resetting"
        @click="resetCircuit"
      >
        {{ resetting ? "正在重置…" : "确认重置" }}
      </button></template
    ></Modal
  >
</template>
