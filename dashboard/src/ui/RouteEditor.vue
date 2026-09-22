<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import type { ModelRef, VirtualModel } from "../domain/types";
import {
  canonical,
  catalog,
  clone,
  own,
  refKey,
  sameRef,
} from "../domain/config";
import { useWorkspace } from "../state/workspace";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
const props = defineProps<{ name?: string }>();
const emit = defineEmits<{ close: [] }>();
const workspace = useWorkspace();
const name = ref(props.name ?? ""),
  error = ref(""),
  selected = ref("");
const form = ref<VirtualModel>(
  clone(props.name ? workspace.draft!.models[props.name] : { models: [] }),
);
const initial = canonical({ name: name.value, form: form.value });
const changed = computed(
  () => initial !== canonical({ name: name.value, form: form.value }),
);
const options = computed(() =>
  workspace.draft
    ? catalog(workspace.draft).filter(
        (ref) => !form.value.models.some((item) => sameRef(item, ref)),
      )
    : [],
);
const groups = computed(() => [
  ...new Set(options.value.map((ref) => ref.provider)),
]);
const list = ref<HTMLElement>();
const drag = shallowRef<{
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
const movedKey = ref("");
const announcement = ref("");
let scrollFrame = 0;
let feedbackTimer: ReturnType<typeof setTimeout> | undefined;
const destination = computed(() => {
  if (!drag.value || dropIndex.value === null) return null;
  return dropIndex.value > drag.value.index
    ? dropIndex.value - 1
    : dropIndex.value;
});
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
  const element = list.value;
  const source = drag.value;
  if (!element || !source) return;
  const bounds = element.getBoundingClientRect();
  const viewport = element.closest(".modal-body")!.getBoundingClientRect();
  const { x, y } = pointer.value;
  if (
    x < bounds.left ||
    x > bounds.right ||
    y < viewport.top ||
    y > viewport.bottom
  ) {
    dropIndex.value = null;
    return;
  }
  const rows = Array.from(
    element.querySelectorAll<HTMLElement>(".route-editor-row"),
  );
  const before = rows.findIndex((row) => {
    const rect = row.getBoundingClientRect();
    return y < rect.top + rect.height / 2;
  });
  const slot = before < 0 ? rows.length : before;
  const to = slot > source.index ? slot - 1 : slot;
  dropIndex.value = to === source.index ? null : slot;
}
function autoScroll() {
  if (!dragging.value) return;
  const body = list.value?.closest<HTMLElement>(".modal-body");
  if (body) {
    const bounds = body.getBoundingClientRect();
    const { x, y } = pointer.value;
    if (
      x >= bounds.left &&
      x <= bounds.right &&
      y >= bounds.top &&
      y <= bounds.bottom
    ) {
      const edge = 40;
      const delta =
        y < bounds.top + edge
          ? -Math.min(12, (bounds.top + edge - y) / 3)
          : y > bounds.bottom - edge
            ? Math.min(12, (y - bounds.bottom + edge) / 3)
            : 0;
      body.scrollTop += delta;
    }
  }
  updateTarget();
  scrollFrame = requestAnimationFrame(autoScroll);
}
function startDrag(event: PointerEvent, index: number) {
  if (event.button !== 0 || !event.isPrimary || form.value.models.length < 2)
    return;
  clearDrag();
  const handle = event.currentTarget as HTMLElement;
  const row = handle.closest<HTMLElement>(".route-editor-row")!;
  const { left, top, width, height } = row.getBoundingClientRect();
  handle.focus({ preventScroll: true });
  handle.setPointerCapture(event.pointerId);
  pointer.value = { x: event.clientX, y: event.clientY };
  drag.value = {
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
function finishDrag(event: PointerEvent) {
  const source = drag.value;
  if (!source || source.pointerId !== event.pointerId) return;
  if (dragging.value) {
    pointer.value = { x: event.clientX, y: event.clientY };
    updateTarget();
    if (destination.value !== null) reorder(source.index, destination.value);
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
onBeforeUnmount(() => {
  clearDrag();
  clearTimeout(feedbackTimer);
});
function add() {
  const ref = options.value.find((ref) => refKey(ref) === selected.value);
  if (!ref) return;
  form.value.models.push(clone(ref));
  if (!form.value.pinned_model) form.value.pinned_model = clone(ref);
  selected.value = "";
}
function move(index: number, direction: number) {
  clearDrag();
  reorder(index, index + direction);
}
function reorder(index: number, to: number) {
  const refs = form.value.models;
  if (to < 0 || to >= refs.length || to === index) return;
  const [item] = refs.splice(index, 1);
  refs.splice(to, 0, item);
  movedKey.value = refKey(item);
  announcement.value = `${item.model} 已移到第 ${to + 1} 位`;
  clearTimeout(feedbackTimer);
  feedbackTimer = setTimeout(() => (movedKey.value = ""), 700);
}
function remove(index: number) {
  const [removed] = form.value.models.splice(index, 1);
  if (sameRef(removed, form.value.pinned_model))
    form.value.pinned_model = form.value.models[0]
      ? clone(form.value.models[0])
      : null;
}
function pin(ref: ModelRef) {
  form.value.pinned_model = clone(ref);
}
function apply() {
  if (!workspace.draft) return;
  const key = name.value.trim();
  if (!key) {
    error.value = "请输入路由名称";
    return;
  }
  if (key !== props.name && own(workspace.draft.models, key)) {
    error.value = "该路由名称已存在";
    return;
  }
  if (!form.value.models.length) {
    error.value = "至少添加一个候选模型";
    return;
  }
  if (props.name && key !== props.name)
    delete workspace.draft.models[props.name];
  workspace.draft.models = {
    ...workspace.draft.models,
    [key]: clone(form.value),
  };
  emit("close");
}
</script>
<template>
  <Modal
    :title="props.name ? '编辑模型路由' : '建立新的模型路由'"
    eyebrow="ROUTE BUILDER"
    :guard="changed"
    wide
    @close="$emit('close')"
    ><form
      id="route-form"
      @submit.prevent="apply"
      @keydown.esc.capture="cancelWithEscape"
    >
      <label class="field"
        ><span>虚拟模型名称 <b>*</b></span
        ><input
          v-model="name"
          placeholder="例如 coding-assistant"
          required
          autofocus
        /><small>客户端使用这个名称调用，底层模型可随时替换。</small></label
      >
      <div class="form-section-title">
        <h3>候选模型链</h3>
        <span>拖动调整顺序 · 从上到下，优先级递减</span>
      </div>
      <div ref="list" class="route-editor-list">
        <TransitionGroup name="route-sort" tag="div">
          <div
            v-for="(ref, index) in form.models"
            :key="refKey(ref)"
            class="route-editor-row"
            :class="{
              'is-dragging': dragging && drag?.index === index,
              'just-moved': movedKey === refKey(ref),
            }"
          >
            <span
              v-if="dropIndex === index"
              class="route-drop-indicator"
              aria-hidden="true"
            >
              <span>放到第 {{ destination! + 1 }} 位</span>
            </span>
            <span
              v-if="
                index === form.models.length - 1 &&
                dropIndex === form.models.length
              "
              class="route-drop-indicator after"
              aria-hidden="true"
            >
              <span>放到第 {{ destination! + 1 }} 位</span>
            </span>
            <button
              type="button"
              class="route-drag-handle icon-button small"
              :aria-label="'拖动排序 ' + ref.provider + '/' + ref.model"
              title="拖动排序，也可用上下方向键调整"
              :disabled="form.models.length < 2"
              @pointerdown="startDrag($event, index)"
              @pointermove="moveDrag"
              @pointerup="finishDrag"
              @pointercancel="clearDrag"
              @lostpointercapture="clearDrag"
              @keydown.up.prevent="move(index, -1)"
              @keydown.down.prevent="move(index, 1)"
            >
              <Icon name="grip" :size="16" />
            </button>
            <span class="step-index">{{ index + 1 }}</span>
            <div class="route-editor-name">
              <strong>{{ ref.model }}</strong
              ><span>{{ ref.provider }}</span>
            </div>
            <button
              type="button"
              class="pin-button"
              :class="{ pinned: sameRef(ref, form.pinned_model) }"
              :aria-pressed="sameRef(ref, form.pinned_model)"
              :aria-label="'固定 ' + ref.provider + '/' + ref.model"
              @click="pin(ref)"
            >
              <Icon name="pin" :size="15" /><span>{{
                sameRef(ref, form.pinned_model) ? "已固定" : "固定"
              }}</span>
            </button>
            <div class="order-buttons">
              <button
                type="button"
                class="icon-button small"
                :disabled="index === 0"
                :aria-label="'上移 ' + ref.model"
                @click="move(index, -1)"
              >
                <Icon name="up" :size="16" /></button
              ><button
                type="button"
                class="icon-button small"
                :disabled="index === form.models.length - 1"
                :aria-label="'下移 ' + ref.model"
                @click="move(index, 1)"
              >
                <Icon name="down" :size="16" />
              </button>
            </div>
            <button
              type="button"
              class="icon-button small danger-hover"
              :aria-label="'移除候选 ' + ref.model"
              @click="remove(index)"
            >
              <Icon name="close" :size="16" />
            </button>
          </div>
        </TransitionGroup>
        <div v-if="!form.models.length" class="catalog-empty">
          从下方目录中添加第一个候选模型。
        </div>
      </div>
      <p class="sr-only" role="status" aria-live="polite">{{ announcement }}</p>
      <div
        v-if="dragging && drag"
        class="route-drag-preview"
        :style="previewStyle"
        aria-hidden="true"
      >
        <Icon name="grip" :size="16" />
        <span class="step-index">{{ drag.index + 1 }}</span>
        <div class="route-editor-name">
          <strong>{{ form.models[drag.index].model }}</strong>
          <span>{{ form.models[drag.index].provider }}</span>
        </div>
        <span class="badge neutral">{{
          destination === null ? "拖动调整顺序" : `放到第 ${destination + 1} 位`
        }}</span>
      </div>
      <div class="add-candidate">
        <div class="candidate-select">
          <select v-model="selected" aria-label="选择候选模型">
            <option value="" disabled>
              {{
                options.length
                  ? "从实际模型目录中选择…"
                  : "没有更多可用模型，请先在上游服务中添加"
              }}
            </option>
            <optgroup v-for="group in groups" :key="group" :label="group">
              <option
                v-for="option in options.filter(
                  (ref) => ref.provider === group,
                )"
                :key="refKey(option)"
                :value="refKey(option)"
              >
                {{ option.model }}
              </option>
            </optgroup>
          </select>
          <Icon name="down" :size="16" />
        </div>
        <button class="button" type="button" :disabled="!selected" @click="add">
          <Icon name="plus" :size="16" />添加
        </button>
      </div>
      <p class="help">
        自动路由开启时按顺序尝试候选模型；关闭时只使用标记为「已固定」的模型，报错直接返回。
      </p>
      <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    </form>
    <template #footer
      ><button class="button" @click="$emit('close')">取消</button
      ><button class="button primary" form="route-form" type="submit">
        <Icon name="check" :size="16" />应用到草稿
      </button></template
    ></Modal
  >
</template>
