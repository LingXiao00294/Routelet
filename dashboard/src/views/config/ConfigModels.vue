<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import type { ModelRef } from "@/api/types";
import { useConfigStore } from "@/stores/config";
import { useConfirm } from "@/composables/useConfirm";
import { formatActualModel } from "@/utils/format";
import {
  adjacentMoveTarget,
  type MoveDirection,
} from "@/utils/modelOrdering";

const store = useConfigStore();
const confirm = useConfirm();
const { draft, models, fieldErrors } = storeToRefs(store);
const newName = ref("");
const failoverEnabled = computed(() => draft.value?.router.mode === "failover");
const actualModelGroups = computed(() =>
  Object.entries(draft.value?.providers ?? {})
    .map(([provider, config], providerIndex) => ({
      provider,
      options: Object.keys(config.models).map((model, modelIndex) => ({
        id: `${providerIndex}-${modelIndex}`,
        provider,
        model,
        label: formatActualModel(provider, model),
      })),
    }))
    .filter((group) => group.options.length > 0),
);
const actualModelOptions = computed(() => actualModelGroups.value.flatMap((group) => group.options));
const availableOptions = computed(() =>
  Object.fromEntries(
    Object.entries(models.value).map(([name, config]) => [
      name,
      actualModelOptions.value.filter(
        (candidate) =>
          !config.models.some(
            (selected) =>
              selected.provider === candidate.provider && selected.model === candidate.model,
          ),
      ),
    ]),
  ),
);
const dragCandidate = ref<{
  model: string;
  index: number;
  pointerId: number;
  startX: number;
  startY: number;
  row: HTMLElement;
} | null>(null);
const draggingRef = ref<{
  model: string;
  index: number;
  pointerId: number;
  startX: number;
  startY: number;
  left: number;
  top: number;
  width: number;
} | null>(null);
const dropTarget = ref<{ model: string; index: number } | null>(null);
const dragPreview = ref<HTMLElement | null>(null);
const moveAnnouncement = ref("");
const recentlyMoved = ref<{ model: string; key: number } | null>(null);
const refKeys = new WeakMap<ModelRef, number>();
let nextRefKey = 0;
let moveFeedbackTimer: ReturnType<typeof setTimeout> | null = null;
const DRAG_START_DISTANCE = 6;
const dragPreviewRow = computed(() => {
  const source = draggingRef.value;
  return source ? models.value[source.model]?.models[source.index] ?? null : null;
});

watch(
  models,
  () => store.ensureStickyPins(),
  { deep: true, immediate: true },
);

onBeforeUnmount(() => {
  if (moveFeedbackTimer) clearTimeout(moveFeedbackTimer);
});

function add() {
  store.addModel(newName.value.trim());
  newName.value = "";
}

async function remove(name: string) {
  const ok = await confirm.confirm({
    title: "删除虚拟模型",
    message: `确定删除「${name}」？`,
    confirmText: "删除",
    danger: true,
  });
  if (ok) store.removeModel(name);
}

function addRef(model: string) {
  const m = models.value[model];
  if (!m) return;
  const option = availableOptions.value[model]?.[0];
  if (option) m.models.push({ provider: option.provider, model: option.model });
}

function canAddRef(model: string): boolean {
  return Boolean(availableOptions.value[model]?.length);
}

function optionId(row: ModelRef): string {
  return (
    actualModelOptions.value.find(
      (option) => option.provider === row.provider && option.model === row.model,
    )?.id ?? ""
  );
}

function optionDisabled(model: string, index: number, option: ModelRef): boolean {
  return models.value[model]?.models.some(
    (selected, selectedIndex) =>
      selectedIndex !== index &&
      selected.provider === option.provider &&
      selected.model === option.model,
  ) ?? false;
}

function selectRef(model: string, index: number, event: Event) {
  const option = actualModelOptions.value.find(
    (candidate) => candidate.id === (event.target as HTMLSelectElement).value,
  );
  if (!option || !models.value[model]) return;
  models.value[model].models[index] = {
    provider: option.provider,
    model: option.model,
  };
}

function removeRef(model: string, idx: number) {
  const m = models.value[model];
  if (!m) return;
  m.models.splice(idx, 1);
}

function refKey(row: ModelRef): number {
  let key = refKeys.get(row);
  if (key == null) {
    key = nextRefKey;
    nextRefKey += 1;
    refKeys.set(row, key);
  }
  return key;
}

function clearDragState() {
  dragCandidate.value = null;
  draggingRef.value = null;
  dropTarget.value = null;
}

function modelRows(model: string): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-ref-row]")).filter(
    (el) => el.dataset.refModel === model,
  );
}

function setDropTargetFromPointer(event: PointerEvent, model: string) {
  const rows = modelRows(model);
  if (!rows.length) {
    if (dropTarget.value) dropTarget.value = null;
    return;
  }
  const dragIndex =
    draggingRef.value && draggingRef.value.model === model ? draggingRef.value.index : -1;
  // Walk rows in DOM order (= array order) and find the first one whose vertical
  // midpoint falls below the pointer; the dragged item wants to sit just above it.
  let insertAt = rows.length;
  for (let i = 0; i < rows.length; i++) {
    const { top, height } = rows[i].getBoundingClientRect();
    if (event.clientY < top + height / 2) {
      insertAt = i;
      break;
    }
  }
  // Hide the indicator when the drop resolves to the item's own slot (no-op).
  if (dragIndex >= 0) {
    const destination = insertAt > dragIndex ? insertAt - 1 : insertAt;
    if (destination === dragIndex) {
      if (dropTarget.value) dropTarget.value = null;
      return;
    }
  }
  if (dropTarget.value?.model !== model || dropTarget.value.index !== insertAt) {
    dropTarget.value = { model, index: insertAt };
  }
}

/** FLIP: capture each row's vertical position before a reorder. */
function snapshotModelRows(model: string): Map<HTMLElement, number> {
  const tops = new Map<HTMLElement, number>();
  for (const el of modelRows(model)) tops.set(el, el.getBoundingClientRect().top);
  return tops;
}

/** FLIP: after the DOM settles into its new order, animate rows back from their old spots. */
function flipFromSnapshot(model: string, prev: Map<HTMLElement, number>) {
  const rows = modelRows(model);
  let touched = false;
  for (const el of rows) {
    const oldTop = prev.get(el);
    if (oldTop == null) continue;
    const dy = oldTop - el.getBoundingClientRect().top;
    if (dy === 0) continue;
    el.style.transition = "none";
    el.style.transform = `translateY(${dy}px)`;
    touched = true;
  }
  if (!touched) return;
  // Commit the inverted transforms, then release them so the stylesheet transition plays.
  void rows[0]?.offsetWidth;
  for (const el of rows) {
    if (el.style.transform) {
      el.style.transition = "";
      el.style.transform = "";
    }
  }
}

function moveModelRef(model: string, from: number, to: number) {
  const row = models.value[model]?.models[from];
  if (!row || from === to) return;
  if (to < 0 || to >= models.value[model].models.length) return;

  const snapshot = snapshotModelRows(model);
  const key = refKey(row);
  store.moveRef(model, from, to);
  recentlyMoved.value = { model, key };
  moveAnnouncement.value = `${formatActualModel(row.provider, row.model)} 已移至第 ${to + 1} 位`;
  if (moveFeedbackTimer) clearTimeout(moveFeedbackTimer);
  moveFeedbackTimer = setTimeout(() => {
    if (recentlyMoved.value?.model === model && recentlyMoved.value.key === key) {
      recentlyMoved.value = null;
      moveAnnouncement.value = "";
    }
    moveFeedbackTimer = null;
  }, 1_400);
  void nextTick(() => flipFromSnapshot(model, snapshot));
}

function moveBy(model: string, index: number, direction: MoveDirection) {
  const length = models.value[model]?.models.length ?? 0;
  const target = adjacentMoveTarget(length, index, direction);
  if (target != null) moveModelRef(model, index, target);
}

function canMove(length: number, index: number, direction: MoveDirection): boolean {
  return adjacentMoveTarget(length, index, direction) != null;
}

function isRecentlyMoved(model: string, row: ModelRef): boolean {
  return recentlyMoved.value?.model === model && recentlyMoved.value.key === refKey(row);
}

function moveDragPreview(clientX: number, clientY: number) {
  const source = draggingRef.value;
  const preview = dragPreview.value;
  if (!source || !preview) return;
  preview.style.transform = `translate3d(${clientX - source.startX}px, ${clientY - source.startY}px, 0) scale(1.02)`;
}

function startPointerDrag(event: PointerEvent, model: string, index: number) {
  if (event.button !== 0) return;
  const handle = event.currentTarget as HTMLElement;
  const row = handle.closest<HTMLElement>("[data-ref-row]");
  if (!row) return;
  handle.setPointerCapture(event.pointerId);
  dragCandidate.value = {
    model,
    index,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    row,
  };
}

function movePointerDrag(event: PointerEvent) {
  const candidate = dragCandidate.value;
  if (!candidate || candidate.pointerId !== event.pointerId) return;

  if (!draggingRef.value) {
    const distance = Math.hypot(event.clientX - candidate.startX, event.clientY - candidate.startY);
    if (distance < DRAG_START_DISTANCE) return;
    const { left, top, width } = candidate.row.getBoundingClientRect();
    draggingRef.value = {
      model: candidate.model,
      index: candidate.index,
      pointerId: candidate.pointerId,
      startX: candidate.startX,
      startY: candidate.startY,
      left,
      top,
      width,
    };
    void nextTick(() => moveDragPreview(event.clientX, event.clientY));
  } else {
    moveDragPreview(event.clientX, event.clientY);
  }

  event.preventDefault();
  setDropTargetFromPointer(event, candidate.model);
}

function finishPointerDrag(event: PointerEvent) {
  const candidate = dragCandidate.value;
  const source = draggingRef.value;
  if (!candidate || candidate.pointerId !== event.pointerId) return;

  const target = source && dropTarget.value?.model === source.model ? dropTarget.value : null;
  if (source && target) {
    const insertAt = target.index;
    const destination = source.index < insertAt ? insertAt - 1 : insertAt;
    if (destination !== source.index) {
      moveModelRef(source.model, source.index, destination);
    }
  }
  clearDragState();
}

function dragPreviewStyle(): Record<string, string> {
  const source = draggingRef.value;
  if (!source) return {};
  return {
    left: `${source.left}px`,
    top: `${source.top}px`,
    width: `${source.width}px`,
  };
}

function cancelPointerDrag(event: PointerEvent) {
  if (dragCandidate.value?.pointerId === event.pointerId) {
    clearDragState();
  }
}

function hasDropTarget(model: string, index: number, placement: "before" | "after"): boolean {
  const target = dropTarget.value;
  if (!target || target.model !== model) return false;
  return target.index === (placement === "before" ? index : index + 1);
}

function isDragging(model: string, index: number): boolean {
  return draggingRef.value?.model === model && draggingRef.value.index === index;
}

function isDropTarget(model: string, index: number, placement: "before" | "after"): boolean {
  return !isDragging(model, index) && hasDropTarget(model, index, placement);
}

function setPin(model: string, idx: number) {
  const m = models.value[model];
  if (!m) return;
  const ref = m.models[idx];
  if (!ref) return;
  m.pinned_model = { ...ref };
}

function isPinned(model: string, idx: number) {
  const m = models.value[model];
  const ref = m?.models[idx];
  if (!m || !ref) return false;
  return (
    m.pinned_model?.provider === ref.provider && m.pinned_model?.model === ref.model
  );
}
</script>

<template>
  <section v-if="draft" class="wrap">
    <p class="sr-only" aria-live="polite" aria-atomic="true">
      {{ moveAnnouncement }}
    </p>
    <div class="toolbar panel">
      <div class="field grow">
        <label>新建虚拟模型</label>
        <input v-model="newName" placeholder="例如 opus-router" @keydown.enter="add" />
      </div>
      <button class="btn btn-primary" type="button" @click="add">添加</button>
    </div>
    <p v-if="fieldErrors.models" class="err">{{ fieldErrors.models }}</p>

    <article v-for="(m, name) in models" :key="name" class="panel card">
      <header class="card-head">
        <div>
          <h3 class="mono">{{ name }}</h3>
          <p class="muted tiny">
            拖动左侧手柄或使用上下移按钮调整模型链顺序
            <template v-if="failoverEnabled"> · 失败时按顺序尝试</template>
            <template v-else> · 仅调用 Pin 指定的模型</template>
          </p>
          <p v-if="fieldErrors[`models.${name}`]" class="err">{{ fieldErrors[`models.${name}`] }}</p>
          <p v-if="fieldErrors[`models.${name}.pin`]" class="err">{{ fieldErrors[`models.${name}.pin`] }}</p>
        </div>
        <button class="btn btn-sm btn-danger" type="button" @click="remove(name)">删除</button>
      </header>

      <div v-if="!m.models.length" class="empty-state model-empty">
        <strong>尚未选择实际模型</strong>
        <span>请先在 Providers 中建立目录，再把模型加入这条路由链。</span>
      </div>
      <div
        v-for="(row, idx) in m.models"
        :key="refKey(row)"
        class="ref-row"
        data-ref-row
        :data-ref-model="name"
        :data-ref-index="idx"
        title="拖动调整优先级"
        :class="{
          'is-dragging': isDragging(name, idx),
          'just-moved': isRecentlyMoved(name, row),
          'drop-before': isDropTarget(name, idx, 'before'),
          'drop-after': isDropTarget(name, idx, 'after'),
        }"
      >
        <span
          class="drag-handle"
          aria-hidden="true"
          title="拖动调整优先级"
          @pointerdown="startPointerDrag($event, name, idx)"
          @pointermove="movePointerDrag"
          @pointerup="finishPointerDrag"
          @pointercancel="cancelPointerDrag"
          @lostpointercapture="clearDragState"
        >
          ⠿
        </span>
        <span class="prio mono">#{{ idx + 1 }}</span>
        <label
          class="model-picker"
          :title="`更换 ${formatActualModel(row.provider, row.model)}`"
        >
          <span class="picker-value mono">{{ formatActualModel(row.provider, row.model) }}</span>
          <span class="picker-arrow" aria-hidden="true">⌄</span>
          <select
            :value="optionId(row)"
            :aria-label="`为 ${name} 选择实际模型`"
            @change="selectRef(name, idx, $event)"
          >
            <option disabled value="">选择实际模型</option>
            <optgroup
              v-for="group in actualModelGroups"
              :key="group.provider"
              :label="group.provider"
            >
              <option
                v-for="option in group.options"
                :key="option.id"
                :value="option.id"
                :disabled="optionDisabled(name, idx, option)"
              >
                {{ option.label }}
              </option>
            </optgroup>
          </select>
        </label>
        <div class="ref-actions">
          <button
            class="btn btn-sm move-btn"
            type="button"
            :disabled="!canMove(m.models.length, idx, -1)"
            :aria-label="`将 ${formatActualModel(row.provider, row.model)} 上移一位`"
            title="上移一位"
            @click="moveBy(name, idx, -1)"
          >
            ↑
          </button>
          <button
            class="btn btn-sm move-btn"
            type="button"
            :disabled="!canMove(m.models.length, idx, 1)"
            :aria-label="`将 ${formatActualModel(row.provider, row.model)} 下移一位`"
            title="下移一位"
            @click="moveBy(name, idx, 1)"
          >
            ↓
          </button>
          <button
            class="btn btn-sm"
            type="button"
            :class="{ 'btn-primary': isPinned(name, idx) }"
            title="设为指定模型"
            @click="setPin(name, idx)"
          >
            Pin
          </button>
          <button
            class="btn btn-sm btn-danger move-btn"
            type="button"
            :aria-label="`从 ${name} 删除 ${formatActualModel(row.provider, row.model)}`"
            title="删除实际模型"
            @click="removeRef(name, idx)"
          >
            ×
          </button>
        </div>
        <p v-if="fieldErrors[`models.${name}.ref.${idx}`]" class="err ref-err">
          {{ fieldErrors[`models.${name}.ref.${idx}`] }}
        </p>
        <p v-if="fieldErrors[`models.${name}.ref.${idx}.model`]" class="err ref-err">
          {{ fieldErrors[`models.${name}.ref.${idx}.model`] }}
        </p>
      </div>

      <div class="card-foot">
        <button
          class="btn btn-sm"
          type="button"
          :disabled="!canAddRef(name)"
          :title="canAddRef(name) ? '添加目录中的实际模型' : '没有更多可添加的实际模型'"
          @click="addRef(name)"
        >
          添加实际模型
        </button>
        <span v-if="m.pinned_model" class="pin-summary muted" :title="formatActualModel(m.pinned_model.provider, m.pinned_model.model)">
          当前指定：{{ formatActualModel(m.pinned_model.provider, m.pinned_model.model) }}
        </span>
      </div>
    </article>
  </section>

  <Teleport to="body">
    <div
      v-if="draggingRef && dragPreviewRow"
      ref="dragPreview"
      class="ref-row drag-preview"
      :style="dragPreviewStyle()"
      aria-hidden="true"
    >
      <span class="drag-handle">⠿</span>
      <span class="prio mono">#{{ draggingRef.index + 1 }}</span>
      <span class="model-picker model-picker-disabled mono">
        {{ formatActualModel(dragPreviewRow.provider, dragPreviewRow.model) }}
      </span>
      <div class="ref-actions">
        <button class="btn btn-sm move-btn" type="button" disabled>↑</button>
        <button class="btn btn-sm move-btn" type="button" disabled>↓</button>
        <button
          class="btn btn-sm"
          type="button"
          :class="{ 'btn-primary': isPinned(draggingRef.model, draggingRef.index) }"
          disabled
        >
          Pin
        </button>
        <button class="btn btn-sm btn-danger move-btn" type="button" disabled>×</button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.wrap { display: grid; gap: 0.85rem; }
.toolbar {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  padding: 0.9rem 1rem;
}
.grow { flex: 1; }
.card { padding: 1rem; }
.card-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.85rem;
}
.card-head h3 {
  max-width: min(70vw, 760px);
  margin: 0;
  overflow-wrap: anywhere;
}
.tiny { margin: 0.25rem 0 0; font-size: 0.8rem; }
.model-empty {
  display: grid;
  gap: 0.25rem;
}
.model-empty strong { color: var(--text-secondary); }
.model-empty span { font-size: 0.78rem; }
.ref-row {
  display: grid;
  grid-template-columns: 28px 40px minmax(150px, 1fr) auto;
  gap: 0.5rem;
  align-items: center;
  position: relative;
  min-height: 54px;
  margin-bottom: 0.65rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-elevated);
  transition:
    border-color 120ms ease,
    box-shadow 120ms ease,
    opacity 120ms ease,
    transform 200ms cubic-bezier(0.2, 0.8, 0.2, 1);
}
.drag-handle {
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  border: 0;
  padding: 0.3rem 0.15rem;
  color: var(--text-muted);
  background: transparent;
  cursor: grab;
  font-size: 1.2rem;
  line-height: 1;
  touch-action: none;
  user-select: none;
}
.drag-handle:active { cursor: grabbing; }
.ref-row.is-dragging {
  opacity: 0.35;
  border-style: dashed;
}
.ref-row.just-moved {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.drag-preview {
  position: fixed;
  z-index: 1000;
  margin: 0;
  pointer-events: none;
  box-shadow: 0 16px 32px rgba(15, 23, 42, 0.26);
  transform: scale(1.02);
  opacity: 0.7;
  transition: none;
}
.ref-row.drop-before::before,
.ref-row.drop-after::after {
  position: absolute;
  right: 0;
  left: 0;
  height: 3px;
  border-radius: 999px;
  background: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-soft);
  content: "";
}
.ref-row.drop-before::before { top: -0.4rem; }
.ref-row.drop-after::after { bottom: -0.4rem; }
.ref-err {
  grid-column: 1 / -1;
  margin: 0;
}
.ref-row input {
  min-height: 34px;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
}
.model-picker {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  min-width: 0;
  min-height: 34px;
  padding: 0.3rem 0.55rem 0.3rem 0.65rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text);
  cursor: pointer;
  font-size: 0.85rem;
}
.picker-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picker-arrow {
  flex: 0 0 auto;
  color: var(--text-muted);
}
.model-picker:hover {
  background: var(--bg-hover);
  border-color: var(--border-strong);
}
.model-picker:focus-within {
  border-color: var(--accent);
  box-shadow: var(--focus-ring);
}
.model-picker select {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}
.model-picker-disabled {
  opacity: 0.5;
  cursor: default;
}
.prio { color: var(--text-muted); font-size: 0.8rem; }
.ref-actions { display: flex; gap: 0.25rem; }
.move-btn {
  width: 28px;
  min-width: 28px;
  padding-inline: 0;
}
.card-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.pin-summary {
  min-width: 0;
  align-self: center;
  overflow: hidden;
  font-size: 0.78rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.err { color: var(--danger); font-size: 0.78rem; margin: 0.25rem 0 0; }
@media (max-width: 680px) {
  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .toolbar .btn { width: 100%; }
  .card { padding: 0.8rem; }
  .card-head { align-items: flex-start; }
  .card-head h3 { max-width: 68vw; }
  .ref-row {
    grid-template-columns: 28px 40px minmax(0, 1fr);
    gap: 0.4rem;
  }
  .drag-handle { grid-column: 1; grid-row: 1; }
  .prio { grid-column: 2; grid-row: 1; }
  .ref-actions {
    grid-column: 3;
    grid-row: 1;
    justify-content: flex-end;
  }
  .model-picker {
    grid-column: 1 / -1;
    grid-row: 2;
  }
  .card-foot {
    align-items: stretch;
    flex-direction: column;
  }
  .pin-summary {
    max-width: 100%;
  }
}
</style>
