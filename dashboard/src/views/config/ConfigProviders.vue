<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { useConfigStore } from "@/stores/config";
import { useConfirm } from "@/composables/useConfirm";
import { useOverlayChrome } from "@/composables/useOverlayChrome";
import { useToast } from "@/composables/useToast";
import type { ActualModelConfig, ProviderConfig } from "@/api/types";
import { isBlankOrPlaceholderKey } from "@/utils/configPayload";
import { formatActualModel } from "@/utils/format";
import { summarizeProviderModels } from "@/utils/providerPresentation";

const store = useConfigStore();
const confirm = useConfirm();
const toast = useToast();
const { draft, fieldErrors } = storeToRefs(store);
const newName = ref("");
const editing = ref<string | null>(null);
const modalRef = ref<HTMLElement | null>(null);
const editingModelProvider = ref<string | null>(null);
const editingModelName = ref<string | null>(null);
const modelNameDraft = ref("");
const modelDraft = ref<ActualModelConfig>({});
const modelError = ref("");
const modelModalRef = ref<HTMLElement | null>(null);
const expandedProviders = ref<Set<string>>(new Set());

const providerEntries = computed(() => {
  if (!draft.value) {
    return [] as {
      name: string;
      p: ProviderConfig;
      summary: ReturnType<typeof summarizeProviderModels>;
    }[];
  }
  return Object.entries(draft.value.providers).map(([name, p]) => ({
    name,
    p,
    summary: summarizeProviderModels(p.models),
  }));
});

const editingProvider = computed(() => {
  if (!editing.value || !draft.value) return null;
  return draft.value.providers[editing.value] ?? null;
});

const modalOpen = computed(() => Boolean(editing.value && editingProvider.value));
useOverlayChrome(modalOpen, modalRef);
const modelModalOpen = computed(() => editingModelProvider.value !== null);
useOverlayChrome(modelModalOpen, modelModalRef);
const modelDisplayName = computed(() => {
  const provider = editingModelProvider.value;
  if (!provider) return "";
  const model = editingModelName.value ?? (modelNameDraft.value.trim() || "<model>");
  return formatActualModel(provider, model);
});

watch(newName, (value) => {
  if (fieldErrors.value.providers !== "名称已存在") return;
  const name = value.trim();
  if (name && draft.value?.providers[name]) return;
  const { providers: _nameError, ...remainingErrors } = fieldErrors.value;
  void _nameError;
  fieldErrors.value = remainingErrors;
});

function add() {
  const name = newName.value.trim();
  if (!name) return;
  store.addProvider(name);
  if (draft.value?.providers[name]) {
    newName.value = "";
    editing.value = name;
  }
}

function openEdit(name: string) {
  editing.value = name;
}

function toggleModelSummary(provider: string) {
  const next = new Set(expandedProviders.value);
  if (next.has(provider)) next.delete(provider);
  else next.add(provider);
  expandedProviders.value = next;
}

function closeEdit() {
  editing.value = null;
}

function openNewActualModel(provider: string) {
  editingModelProvider.value = provider;
  editingModelName.value = null;
  modelNameDraft.value = "";
  modelDraft.value = {};
  modelError.value = "";
}

function openActualModel(provider: string, model: string) {
  const actualModel = draft.value?.providers[provider]?.models[model];
  if (!actualModel) return;
  editingModelProvider.value = provider;
  editingModelName.value = model;
  modelNameDraft.value = model;
  modelDraft.value = { ...actualModel };
  modelError.value = "";
}

function closeActualModel() {
  editingModelProvider.value = null;
  editingModelName.value = null;
  modelNameDraft.value = "";
  modelDraft.value = {};
  modelError.value = "";
}

function setOptionalPrice(field: keyof ActualModelConfig, event: Event) {
  const value = (event.target as HTMLInputElement).value;
  modelDraft.value[field] = value === "" ? undefined : Number(value);
}

function saveActualModel() {
  const provider = editingModelProvider.value;
  if (!provider || !draft.value?.providers[provider]) return;
  const draftName = modelNameDraft.value.trim();
  if (!draftName) {
    modelError.value = "实际模型名不能为空";
    return;
  }
  const prices = Object.values(modelDraft.value);
  if (prices.some((price) => price != null && (!Number.isFinite(price) || price < 0))) {
    modelError.value = "费用需为 ≥ 0 的数字或留空";
    return;
  }
  const editingExisting = editingModelName.value !== null;
  const name = editingModelName.value ?? draftName;
  const updated = editingExisting
    ? store.updateActualModel(provider, name, modelDraft.value)
    : store.addActualModel(provider, name, modelDraft.value);
  if (!updated) {
    modelError.value = editingExisting
      ? "实际模型已不存在，请关闭弹窗后刷新"
      : "同一 Provider 下的实际模型名不能重复";
    return;
  }
  const errorKey = `providers.${provider}.models.${name}`;
  const nextErrors = { ...fieldErrors.value };
  delete nextErrors[errorKey];
  fieldErrors.value = nextErrors;
  toast.success(`${formatActualModel(provider, name)} 已${editingExisting ? "更新" : "添加"}，保存后生效`);
  closeActualModel();
}

async function removeActualModel(provider: string, model: string) {
  const ok = await confirm.confirm({
    title: "删除实际模型",
    message: `确定删除「${formatActualModel(provider, model)}」？`,
    confirmText: "删除",
    danger: true,
  });
  if (!ok) return;
  const referencedBy = store.removeActualModel(provider, model);
  if (referencedBy.length) {
    modelError.value = `仍被虚拟模型引用：${referencedBy.join("、")}。请先保存引用移除。`;
    toast.error(modelError.value);
    return;
  }
  toast.success(`${formatActualModel(provider, model)} 已删除，保存后生效`);
  closeActualModel();
}

async function remove(name: string) {
  const ok = await confirm.confirm({
    title: "删除 Provider",
    message: `确定删除「${name}」？`,
    confirmText: "删除",
    danger: true,
  });
  if (ok) {
    const referencedBy = store.removeProvider(name);
    if (referencedBy.length) {
      fieldErrors.value = {
        ...fieldErrors.value,
        providers: `Provider「${name}」仍被虚拟模型引用：${referencedBy.join("、")}。请先保存引用移除。`,
      };
      toast.error(fieldErrors.value.providers);
      return;
    }
    toast.success(`Provider「${name}」已删除，保存后生效`);
    if (editing.value === name) editing.value = null;
  }
}

function shortUrl(url: string) {
  if (!url) return "—";
  try {
    const u = new URL(url);
    return u.host + (u.pathname === "/" ? "" : u.pathname.replace(/\/$/, ""));
  } catch {
    return url.length > 40 ? `${url.slice(0, 40)}…` : url;
  }
}

function hasFieldError(name: string) {
  const prefix = `providers.${name}.`;
  return Object.keys(fieldErrors.value).some((k) => k.startsWith(prefix));
}

function keyLabel(p: ProviderConfig) {
  if (p.has_key) return "已配置";
  if (!isBlankOrPlaceholderKey(p.api_key)) return "已填写";
  return "无 key";
}

function keyOk(p: ProviderConfig) {
  return p.has_key || !isBlankOrPlaceholderKey(p.api_key);
}

function onModalKey(e: KeyboardEvent) {
  if (e.key !== "Escape") return;
  if (modelModalOpen.value) {
    e.preventDefault();
    closeActualModel();
  } else if (editing.value) {
    e.preventDefault();
    closeEdit();
  }
}

onMounted(() => window.addEventListener("keydown", onModalKey));
onUnmounted(() => window.removeEventListener("keydown", onModalKey));
</script>

<template>
  <section v-if="draft" class="wrap">
    <div class="toolbar panel">
      <div class="field grow">
        <label>新建 Provider 名称</label>
        <input v-model="newName" placeholder="例如 zai" @keydown.enter="add" />
      </div>
      <button class="btn btn-primary" type="button" @click="add">添加</button>
    </div>
    <p v-if="fieldErrors.providers" class="err">{{ fieldErrors.providers }}</p>

    <div v-if="!providerEntries.length" class="empty-state panel">暂无 Provider，请先添加</div>
    <div v-else class="provider-grid">
      <article
        v-for="{ name, p, summary } in providerEntries"
        :key="name"
        class="panel provider-card"
        :class="{ warn: hasFieldError(name) }"
      >
        <header class="card-head">
          <div class="provider-identity">
            <h3 class="mono name" :title="name">{{ name }}</h3>
            <div class="badges">
            <span class="badge badge-muted">{{ p.type }}</span>
            <span
              class="badge"
              :class="keyOk(p) ? 'badge-success' : 'badge-warn'"
            >
              {{ keyLabel(p) }}
            </span>
            <span class="badge badge-muted">{{ summary.total }} 个模型</span>
            <span v-if="hasFieldError(name)" class="badge badge-danger">校验</span>
            </div>
          </div>
          <button class="btn btn-sm" type="button" @click="openEdit(name)">设置</button>
        </header>

        <p class="provider-url mono muted" :title="p.base_url || undefined">
          {{ shortUrl(p.base_url) }}
        </p>

        <div class="catalog">
          <p class="catalog-label">实际模型目录</p>
          <div v-if="summary.total" class="model-summary">
            <button
              v-for="model in (expandedProviders.has(name) ? Object.keys(p.models) : summary.visible)"
              :key="model"
              class="model-chip mono"
              type="button"
              :title="`编辑 ${formatActualModel(name, model)}`"
              @click.stop="openActualModel(name, model)"
            >
              {{ formatActualModel(name, model) }}
            </button>
            <button
              v-if="summary.remaining"
              class="more-models"
              type="button"
              :aria-expanded="expandedProviders.has(name)"
              @click="toggleModelSummary(name)"
            >
              {{ expandedProviders.has(name) ? "收起" : `还有 ${summary.remaining} 个` }}
            </button>
          </div>
          <div v-else class="catalog-empty">尚未配置实际模型</div>
        </div>

        <footer class="card-actions">
          <button class="btn btn-sm" type="button" @click="openNewActualModel(name)">
            添加模型
          </button>
          <button class="btn btn-sm btn-danger" type="button" @click="remove(name)">删除</button>
        </footer>
      </article>
    </div>

    <Teleport to="body">
      <div
        v-if="editing && editingProvider"
        class="overlay"
        @click.self="closeEdit"
      >
        <div
          ref="modalRef"
          class="modal panel"
          role="dialog"
          aria-modal="true"
          tabindex="-1"
          :aria-label="`编辑 ${editing}`"
        >
          <header class="modal-head">
            <div>
              <h3>编辑 Provider</h3>
              <p class="mono muted">{{ editing }}</p>
            </div>
            <button class="btn btn-sm" type="button" @click="closeEdit">关闭</button>
          </header>

          <div class="grid">
            <div class="field">
              <label for="provider-type">type</label>
              <input id="provider-type" :value="editingProvider.type" readonly />
              <span class="field-hint">当前仅支持 Anthropic Messages API 兼容 Provider</span>
            </div>
            <div class="field">
              <label>api_key（留空保留原值）</label>
              <input
                v-model="editingProvider.api_key"
                :placeholder="editingProvider.has_key ? '•••• 已配置，留空保留' : '必填'"
                autocomplete="off"
              />
              <span v-if="fieldErrors[`providers.${editing}.api_key`]" class="err">
                {{ fieldErrors[`providers.${editing}.api_key`] }}
              </span>
            </div>
            <div class="field span2">
              <label>base_url</label>
              <input v-model="editingProvider.base_url" />
              <span v-if="fieldErrors[`providers.${editing}.base_url`]" class="err">
                {{ fieldErrors[`providers.${editing}.base_url`] }}
              </span>
            </div>
            <div class="field">
              <label>timeout_seconds</label>
              <input v-model.number="editingProvider.timeout_seconds" type="number" min="1" />
              <span v-if="fieldErrors[`providers.${editing}.timeout`]" class="err">
                {{ fieldErrors[`providers.${editing}.timeout`] }}
              </span>
            </div>
            <div class="field">
              <label>failure_threshold（空=全局）</label>
              <input
                :value="editingProvider.failure_threshold ?? ''"
                type="number"
                @input="editingProvider.failure_threshold = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)"
              />
            </div>
            <div class="field">
              <label>recovery_timeout（空=全局）</label>
              <input
                :value="editingProvider.recovery_timeout ?? ''"
                type="number"
                @input="editingProvider.recovery_timeout = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)"
              />
            </div>
            <div class="field">
              <label>max_concurrent（0=不限）</label>
              <input v-model.number="editingProvider.max_concurrent" type="number" min="0" />
              <span v-if="fieldErrors[`providers.${editing}.max_concurrent`]" class="err">
                {{ fieldErrors[`providers.${editing}.max_concurrent`] }}
              </span>
            </div>
            <div class="field">
              <label>max_queue（0=不排队）</label>
              <input v-model.number="editingProvider.max_queue" type="number" min="0" />
              <span v-if="fieldErrors[`providers.${editing}.max_queue`]" class="err">
                {{ fieldErrors[`providers.${editing}.max_queue`] }}
              </span>
            </div>
            <div class="field">
              <label>queue_wait_timeout</label>
              <input v-model.number="editingProvider.queue_wait_timeout" type="number" min="1" />
              <span v-if="fieldErrors[`providers.${editing}.queue_wait_timeout`]" class="err">
                {{ fieldErrors[`providers.${editing}.queue_wait_timeout`] }}
              </span>
            </div>
            <div class="field">
              <label>rate_limit_cooldown</label>
              <input v-model.number="editingProvider.rate_limit_cooldown" type="number" min="1" />
              <span v-if="fieldErrors[`providers.${editing}.rate_limit_cooldown`]" class="err">
                {{ fieldErrors[`providers.${editing}.rate_limit_cooldown`] }}
              </span>
            </div>
          </div>

          <footer class="modal-foot">
            <button class="btn btn-danger" type="button" @click="remove(editing!)">删除</button>
            <button class="btn btn-primary" type="button" @click="closeEdit">完成</button>
          </footer>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="editingModelProvider" class="overlay" @click.self="closeActualModel">
        <div
          ref="modelModalRef"
          class="modal model-modal panel"
          role="dialog"
          aria-modal="true"
          tabindex="-1"
          :aria-label="editingModelName ? `编辑 ${formatActualModel(editingModelProvider, editingModelName)}` : `添加 ${editingModelProvider} 实际模型`"
        >
          <header class="modal-head">
            <div>
              <h3>{{ editingModelName ? "编辑实际模型" : "添加实际模型" }}</h3>
              <p class="mono muted model-display-name" :title="modelDisplayName">
                {{ modelDisplayName }}
              </p>
            </div>
            <button class="btn btn-sm" type="button" @click="closeActualModel">关闭</button>
          </header>

          <div class="grid">
            <div class="field span2">
              <label>模型名</label>
              <input
                v-model="modelNameDraft"
                class="mono"
                :readonly="editingModelName !== null"
                placeholder="例如 claude-sonnet-4-5"
              />
            </div>
            <div class="field">
              <label>input_price_per_million</label>
              <input
                :value="modelDraft.input_price_per_million ?? ''"
                type="number"
                min="0"
                step="any"
                @input="setOptionalPrice('input_price_per_million', $event)"
              />
            </div>
            <div class="field">
              <label>output_price_per_million</label>
              <input
                :value="modelDraft.output_price_per_million ?? ''"
                type="number"
                min="0"
                step="any"
                @input="setOptionalPrice('output_price_per_million', $event)"
              />
            </div>
            <div class="field">
              <label>cache_read_price_per_million</label>
              <input
                :value="modelDraft.cache_read_price_per_million ?? ''"
                type="number"
                min="0"
                step="any"
                @input="setOptionalPrice('cache_read_price_per_million', $event)"
              />
            </div>
            <div class="field">
              <label>cache_write_price_per_million</label>
              <input
                :value="modelDraft.cache_write_price_per_million ?? ''"
                type="number"
                min="0"
                step="any"
                @input="setOptionalPrice('cache_write_price_per_million', $event)"
              />
            </div>
            <p v-if="modelError" class="err span2">{{ modelError }}</p>
          </div>

          <footer class="modal-foot" :class="{ 'align-end': !editingModelName }">
            <button
              v-if="editingModelName"
              class="btn btn-danger"
              type="button"
              @click="removeActualModel(editingModelProvider, editingModelName)"
            >
              删除
            </button>
            <button class="btn btn-primary" type="button" @click="saveActualModel">保存</button>
          </footer>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.wrap {
  display: grid;
  gap: 0.75rem;
}

.toolbar {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
}

.grow {
  flex: 1;
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
  gap: 0.85rem;
}

.provider-card {
  display: flex;
  min-width: 0;
  min-height: 238px;
  flex-direction: column;
  padding: 1rem;
}

.provider-card.warn {
  border-color: var(--danger);
  background: color-mix(in srgb, var(--danger-soft) 45%, var(--bg-elevated));
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.provider-identity {
  min-width: 0;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.45rem;
}

.name {
  max-width: 100%;
  margin: 0;
  overflow-wrap: anywhere;
  font-weight: 600;
  font-size: 1rem;
}

.provider-url {
  min-height: 1.15rem;
  margin: 0.65rem 0 0;
  overflow: hidden;
  font-size: 0.8rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog {
  flex: 1;
  min-width: 0;
  margin-top: 0.85rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
}

.catalog-label {
  margin: 0 0 0.45rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
}

.model-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}

.model-chip {
  max-width: 100%;
  overflow: hidden;
  padding: 0.18rem 0.45rem;
  color: var(--text-muted);
  background: var(--bg-muted);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.72rem;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.model-chip:hover {
  color: var(--text);
  border-color: var(--accent);
}

.more-models,
.catalog-empty {
  color: var(--text-muted);
  font-size: 0.75rem;
}

.more-models {
  align-self: center;
  padding: 0.18rem 0.3rem;
  border: 0;
  background: transparent;
  white-space: nowrap;
}

.more-models:hover {
  color: var(--accent);
}

.catalog-empty {
  padding: 0.65rem;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  text-align: center;
}

.card-actions {
  display: flex;
  justify-content: space-between;
  gap: 0.35rem;
  margin-top: 0.85rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
}

.err {
  color: var(--danger);
  font-size: 0.78rem;
}

.field-hint {
  color: var(--text-muted);
  font-size: 0.75rem;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  background: rgba(10, 14, 20, 0.45);
  padding: 1rem;
}

.modal {
  width: min(640px, 100%);
  max-height: min(90vh, 820px);
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  animation: modal-in 0.18s ease;
}

.model-modal {
  width: min(600px, 100%);
}

@keyframes modal-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  padding: 1rem 1.1rem;
  border-bottom: 1px solid var(--border);
}

.modal-head h3 {
  margin: 0;
  font-size: 1.05rem;
}

.modal-head p {
  margin: 0.2rem 0 0;
  font-size: 0.8rem;
}

.model-display-name {
  max-width: min(70vw, 470px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  padding: 1rem 1.1rem;
  overflow: auto;
}

.span2 {
  grid-column: span 2;
}

.modal-foot {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.85rem 1.1rem;
  border-top: 1px solid var(--border);
}

.modal-foot.align-end {
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .toolbar .btn {
    width: 100%;
  }
  .provider-card {
    min-height: 0;
  }
  .card-head {
    align-items: flex-start;
  }
  .grid,
  .span2 {
    grid-template-columns: 1fr;
    grid-column: auto;
  }
  .modal {
    max-height: calc(100dvh - 1rem);
  }
}
</style>
