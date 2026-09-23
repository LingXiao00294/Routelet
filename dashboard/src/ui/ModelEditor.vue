<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import type { Price } from "../domain/types";
import { priceFields } from "../domain/types";
import { canonical, clone, own } from "../domain/config";
import { useWorkspace } from "../state/workspace";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
const props = defineProps<{ provider: string; model?: string }>();
const emit = defineEmits<{ close: [] }>();
const workspace = useWorkspace();
const name = ref(props.model ?? ""),
  error = ref("");
const prices = reactive<Price>(
  clone(
    props.model
      ? workspace.draft!.providers[props.provider].models[props.model]
      : {},
  ),
);
const initial = canonical({ name: name.value, prices });
const changed = computed(
  () => initial !== canonical({ name: name.value, prices }),
);
function apply() {
  const provider = workspace.draft?.providers[props.provider];
  if (!provider) return;
  const key = name.value.trim();
  if (!key) {
    error.value = "请输入实际模型名称";
    return;
  }
  if (!props.model && own(provider.models, key)) {
    error.value = "该实际模型已存在";
    return;
  }
  for (const field of priceFields) {
    const value = prices[field.key];
    if (value != null && (!Number.isFinite(value) || value < 0)) {
      error.value = "价格必须是非负的有限数值";
      return;
    }
  }
  provider.models = { ...provider.models, [key]: clone(prices) };
  emit("close");
}
</script>
<template>
  <Modal
    :title="model ? '编辑模型价格' : '登记实际模型'"
    :eyebrow="provider"
    :guard="changed"
    @close="$emit('close')"
    ><form id="model-form" @submit.prevent="apply">
      <label class="field"
        ><span>实际模型名称 <b>*</b></span
        ><input
          v-model="name"
          required
          :disabled="!!model"
          placeholder="例如 model-pro"
          autofocus
        /><small>必须与 Provider API 接受的模型 ID 完全一致。</small></label
      >
      <div class="form-section-title">
        <h3>模型价格</h3>
        <span>USD / 1M Token</span>
      </div>
      <div class="form-grid">
        <label v-for="field in priceFields" :key="field.key" class="field"
          ><span>{{ field.label }}</span
          ><input
            :value="prices[field.key] ?? ''"
            type="number"
            min="0"
            step="any"
            placeholder="未配置"
            @input="
              prices[field.key] =
                ($event.target as HTMLInputElement).value === ''
                  ? null
                  : Number(($event.target as HTMLInputElement).value)
            "
        /></label>
      </div>
      <p class="help">
        留空代表未配置；填写 0
        表示显式零价格。调整价格不会改变历史调用的费用快照。
      </p>
      <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    </form>
    <template #footer
      ><button class="button" @click="$emit('close')">取消</button
      ><button class="button primary" form="model-form" type="submit">
        <Icon name="check" :size="16" />应用到草稿
      </button></template
    ></Modal
  >
</template>
