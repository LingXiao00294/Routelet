<script setup lang="ts">
import { computed, ref } from "vue";
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
function add() {
  const ref = options.value.find((ref) => refKey(ref) === selected.value);
  if (!ref) return;
  form.value.models.push(clone(ref));
  if (!form.value.pinned_model) form.value.pinned_model = clone(ref);
  selected.value = "";
}
function move(index: number, direction: number) {
  const refs = form.value.models;
  if (index + direction < 0 || index + direction >= refs.length) return;
  [refs[index], refs[index + direction]] = [
    refs[index + direction],
    refs[index],
  ];
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
    ><form id="route-form" @submit.prevent="apply">
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
        <span>从上到下，优先级递减</span>
      </div>
      <div class="route-editor-list">
        <div
          v-for="(ref, index) in form.models"
          :key="refKey(ref)"
          class="route-editor-row"
        >
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
        <div v-if="!form.models.length" class="catalog-empty">
          从下方目录中添加第一个候选模型。
        </div>
      </div>
      <div class="add-candidate">
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
              v-for="option in options.filter((ref) => ref.provider === group)"
              :key="refKey(option)"
              :value="refKey(option)"
            >
              {{ option.model }}
            </option>
          </optgroup></select
        ><button
          class="button"
          type="button"
          :disabled="!selected"
          @click="add"
        >
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
