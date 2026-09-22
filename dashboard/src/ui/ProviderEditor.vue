<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import type { Config, Provider } from "../domain/types";
import {
  canonical,
  clone,
  newProvider,
  own,
  validateConfig,
} from "../domain/config";
import { useWorkspace } from "../state/workspace";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
const props = defineProps<{ name?: string }>();
const emit = defineEmits<{ close: [] }>();
const workspace = useWorkspace();
const name = ref(props.name ?? ""),
  error = ref("");
const form = reactive<Provider>(
  clone(props.name ? workspace.draft!.providers[props.name] : newProvider()),
);
if (props.name && own(workspace.base?.providers ?? {}, props.name))
  form.api_key = "";
const initial = canonical({ name: name.value, form });
const changed = computed(
  () => initial !== canonical({ name: name.value, form }),
);
function apply() {
  if (!workspace.draft) return;
  const key = name.value.trim();
  if (!key) {
    error.value = "请输入上游名称";
    return;
  }
  if (!props.name && own(workspace.draft.providers, key)) {
    error.value = "该名称已存在，请使用其他名称";
    return;
  }
  const provider = clone(form);
  provider.base_url = provider.base_url.trim().replace(/\/+$/, "");
  if (!provider.api_key && props.name)
    provider.api_key = workspace.draft.providers[props.name].api_key;
  if (
    provider.api_key &&
    (!props.name ||
      provider.api_key !== workspace.draft.providers[props.name]?.api_key)
  ) {
    provider.has_key = !provider.api_key.includes("${");
    provider.api_key_unresolved = provider.api_key.includes("${");
  }
  const candidate: Config = {
    ...clone(workspace.draft),
    providers: { [key]: provider },
    models: {},
  };
  const issues = validateConfig(candidate, workspace.base ?? undefined);
  if (issues.length) {
    error.value = issues.join("；");
    return;
  }
  workspace.draft.providers = { ...workspace.draft.providers, [key]: provider };
  emit("close");
}
const limits = [
  {
    key: "timeout_seconds",
    label: "请求超时",
    min: 0.1,
    step: "any",
    unit: "秒",
  },
  {
    key: "max_concurrent",
    label: "最大并发",
    min: 0,
    step: "1",
    unit: "0 为不限",
  },
  {
    key: "max_queue",
    label: "排队上限",
    min: 0,
    step: "1",
    unit: "0 为不排队",
  },
  {
    key: "queue_wait_timeout",
    label: "排队超时",
    min: 0.1,
    step: "any",
    unit: "秒",
  },
  {
    key: "rate_limit_cooldown",
    label: "限流冷却",
    min: 0.1,
    step: "any",
    unit: "秒",
  },
] as const;
</script>
<template>
  <Modal
    :title="name && props.name ? '编辑上游 · ' + name : '连接一个新上游'"
    eyebrow="PROVIDER CONNECTION"
    :guard="changed"
    :busy="workspace.saving"
    @close="$emit('close')"
  >
    <form id="provider-form" @submit.prevent="apply">
      <fieldset :disabled="workspace.saving">
        <div class="form-grid">
          <label class="field full"
            ><span>上游名称 <b>*</b></span
            ><input
              v-model="name"
              required
              :disabled="!!props.name"
              placeholder="例如 anthropic-primary"
              autofocus
            /><small>用于标识连接与路由引用，创建后保持固定。</small></label
          ><label class="field full"
            ><span>Base URL <b>*</b></span
            ><input
              v-model="form.base_url"
              type="url"
              required
              placeholder="https://api.anthropic.com"
            /><small
              >Anthropic Messages 兼容地址，不包含 /v1/messages。</small
            ></label
          ><label class="field full"
            ><span>API Key <b v-if="!props.name">*</b></span
            ><input
              v-model="form.api_key"
              type="password"
              autocomplete="new-password"
              :required="!props.name"
              :placeholder="
                props.name ? '留空保留现有密钥' : '输入密钥或 ${ENV_VAR}'
              "
            /><small>密钥只在发布时提交；也支持环境变量引用。</small></label
          >
        </div>
        <details class="advanced-settings">
          <summary>超时、并发与熔断设置<Icon name="down" :size="16" /></summary>
          <div class="form-grid">
            <label v-for="item in limits" :key="item.key" class="field"
              ><span>{{ item.label }}</span>
              <div class="input-unit">
                <input
                  v-model.number="form[item.key]"
                  type="number"
                  :min="item.min"
                  :step="item.step"
                  required
                /><span>{{ item.unit }}</span>
              </div></label
            ><label class="field"
              ><span>独立熔断阈值</span
              ><input
                :value="form.failure_threshold ?? ''"
                type="number"
                min="1"
                step="1"
                placeholder="继承全局设置"
                @input="
                  form.failure_threshold =
                    ($event.target as HTMLInputElement).value === ''
                      ? null
                      : Number(($event.target as HTMLInputElement).value)
                " /></label
            ><label class="field"
              ><span>独立恢复时间（秒）</span
              ><input
                :value="form.recovery_timeout ?? ''"
                type="number"
                min="0.1"
                step="any"
                placeholder="继承全局设置"
                @input="
                  form.recovery_timeout =
                    ($event.target as HTMLInputElement).value === ''
                      ? null
                      : Number(($event.target as HTMLInputElement).value)
                "
            /></label>
          </div>
        </details>
        <div v-if="error" class="alert error" role="alert">{{ error }}</div>
      </fieldset>
    </form>
    <template #footer
      ><button class="button" @click="$emit('close')">取消</button
      ><button
        class="button primary"
        form="provider-form"
        type="submit"
        :disabled="workspace.saving"
      >
        <Icon name="check" :size="16" />应用到草稿
      </button></template
    >
  </Modal>
</template>
