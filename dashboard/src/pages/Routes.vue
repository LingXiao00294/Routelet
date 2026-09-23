<script setup lang="ts">
import { computed, ref } from "vue";
import { useSearchQuery } from "../composables/useSearchQuery";
import { useWorkspace } from "../state/workspace";
import { catalog, clone, sameRef, own, refKey } from "../domain/config";
import { copyText, notify } from "../state/notifications";
import Icon from "../ui/Icon.vue";
import EmptyState from "../ui/EmptyState.vue";
import Modal from "../ui/Modal.vue";
import RouteEditor from "../ui/RouteEditor.vue";
import SearchInput from "../ui/SearchInput.vue";
const workspace = useWorkspace();
const query = useSearchQuery(),
  editing = ref<string | null>(null),
  removal = ref("");
const routes = computed(() =>
  Object.entries(workspace.draft?.models ?? {}).filter(([name, model]) =>
    (
      name +
      " " +
      model.models.map((ref) => ref.provider + " " + ref.model).join(" ")
    )
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
);
const actualCount = computed(() =>
  workspace.draft ? catalog(workspace.draft).length : 0,
);
function setMode(mode: "sticky" | "failover") {
  if (!workspace.draft) return;
  workspace.draft.router.mode = mode;
  if (mode === "sticky")
    for (const model of Object.values(workspace.draft.models))
      if (!model.models.some((ref) => sameRef(ref, model.pinned_model)))
        model.pinned_model = clone(model.models[0] ?? null);
}
function duplicate(name: string) {
  if (!workspace.draft) return;
  let next = name + "-copy",
    index = 2;
  while (own(workspace.draft.models, next)) next = name + "-copy-" + index++;
  workspace.draft.models = {
    ...workspace.draft.models,
    [next]: clone(workspace.draft.models[name]),
  };
  editing.value = next;
  notify("已复制为 " + next + "，保存在草稿中", "info");
}
function remove() {
  if (workspace.draft) delete workspace.draft.models[removal.value];
  removal.value = "";
}
</script>
<template>
  <section class="page-heading">
    <div>
      <div class="eyebrow">ONE NAME. MANY POSSIBILITIES.</div>
      <h1>Routers<span class="heading-dot">.</span></h1>
      <p>给客户端一个稳定的名称，让模型在背后自由协作。</p>
    </div>
    <button
      class="button primary"
      :disabled="!actualCount || workspace.saving"
      @click="editing = ''"
    >
      <Icon name="plus" :size="17" />新建 Router
    </button>
  </section>
  <div v-if="workspace.loading && !workspace.draft" class="skeleton-block" />
  <fieldset v-if="workspace.draft" :disabled="workspace.saving">
    <div class="routing-strategy">
      <span class="strategy-icon"><Icon name="routes" :size="25" /></span>
      <div>
        <h2>自动故障转移</h2>
        <p id="routing-description">
          {{
            workspace.draft.router.mode === "failover"
              ? "Provider 不可用时，按候选顺序自动尝试下一个模型。"
              : "已关闭：仅请求固定模型，报错直接返回，不尝试备用 Provider。"
          }}
        </p>
      </div>
      <button
        class="routing-toggle"
        type="button"
        role="switch"
        aria-label="自动故障转移"
        aria-describedby="routing-description"
        :aria-checked="workspace.draft.router.mode === 'failover'"
        @click="
          setMode(
            workspace.draft.router.mode === 'failover' ? 'sticky' : 'failover',
          )
        "
      >
        <span>{{
          workspace.draft.router.mode === "failover" ? "已开启" : "已关闭"
        }}</span>
        <span class="switch-track" aria-hidden="true"><span /></span>
      </button>
    </div>
    <div class="page-toolbar">
      <SearchInput
        v-model="query"
        label="搜索 Router"
        placeholder="搜索 Router、模型或 Provider…"
        :size="18"
      />
      <span class="muted"
        >{{ routes.length }} 个 Router · {{ actualCount }} 个可选模型</span
      >
    </div>
    <div class="route-card-list">
      <article
        v-for="([name, model], index) in routes"
        :key="name"
        class="panel route-card"
      >
        <div class="route-card-heading">
          <span class="route-number"
            >ROUTER {{ String(index + 1).padStart(2, "0") }}</span
          >
          <div class="route-title">
            <h2>{{ name }}</h2>
            <button
              class="icon-button small"
              :aria-label="'复制 Router 名 ' + name"
              @click="copyText(name)"
            >
              <Icon name="copy" :size="14" />
            </button>
          </div>
          <span class="badge neutral">{{ model.models.length }} 个候选</span>
          <div class="route-actions">
            <button class="button small" @click="editing = name">
              <Icon name="edit" :size="14" />编辑 Router</button
            ><button
              class="icon-button small"
              :aria-label="'复制 Router ' + name"
              @click="duplicate(name)"
            >
              <Icon name="copy" :size="16" /></button
            ><button
              class="icon-button small danger-hover"
              :aria-label="'删除 Router ' + name"
              @click="removal = name"
            >
              <Icon name="trash" :size="16" />
            </button>
          </div>
        </div>
        <div class="route-chain">
          <div class="chain-entry">
            <Icon name="terminal" :size="20" /><span>客户端请求</span>
          </div>
          <template v-for="(ref, refIndex) in model.models" :key="refKey(ref)"
            ><div class="chain-connector">
              <span v-if="refIndex">{{
                workspace.draft.router.mode === "failover" ? "故障转移" : "候选"
              }}</span
              ><Icon name="arrow" :size="20" />
            </div>
            <div
              class="chain-node"
              :class="{
                pinned: sameRef(ref, model.pinned_model),
                inactive:
                  workspace.draft.router.mode === 'sticky' &&
                  !sameRef(ref, model.pinned_model),
              }"
            >
              <div>
                <span class="node-priority">{{
                  String(refIndex + 1).padStart(2, "0")
                }}</span
                ><span v-if="sameRef(ref, model.pinned_model)" class="node-pin"
                  ><Icon name="pin" :size="12" />固定模型</span
                ><span v-else class="node-provider">{{ ref.provider }}</span>
              </div>
              <strong :title="ref.model">{{ ref.model }}</strong
              ><small>{{ ref.provider }}</small>
            </div></template
          >
          <div class="chain-end"><Icon name="check" :size="16" /></div>
        </div>
        <div class="route-card-foot">
          <span
            ><i
              class="dot"
              :class="
                workspace.draft.router.mode === 'failover'
                  ? 'text-green'
                  : 'text-blue'
              "
            />{{
              workspace.draft.router.mode === "failover"
                ? "按优先级自动故障转移"
                : "固定模型 · 报错直接返回"
            }}</span
          ><RouterLink
            :to="{ path: '/playground', query: { model: name } }"
            class="text-link"
            >测试此 Router<Icon name="arrowup" :size="14"
          /></RouterLink>
        </div>
      </article>
    </div>
    <section v-if="!routes.length" class="panel">
      <EmptyState
        icon="routes"
        :title="query ? '没有找到匹配的 Router' : '建立你的第一个 Router'"
        :description="
          query
            ? '尝试其他名称，或者清除搜索条件。'
            : actualCount
              ? '选择候选模型、安排顺序，给它们一个统一的调用名称。'
              : '先添加 Provider 和实际模型，再回到这里配置 Router。'
        "
        ><button v-if="query" class="button" @click="query = ''">
          清除搜索</button
        ><button
          v-else-if="actualCount"
          class="button primary"
          @click="editing = ''"
        >
          新建 Router<Icon name="plus" :size="16" /></button
        ><RouterLink v-else to="/providers" class="button primary"
          >前往 Providers<Icon name="arrow" :size="16" /></RouterLink
      ></EmptyState>
    </section>
  </fieldset>
  <RouteEditor
    v-if="editing !== null"
    :name="editing || undefined"
    @close="editing = null"
  />
  <Modal v-if="removal" title="删除这个 Router？" @close="removal = ''"
    ><p>
      将从草稿中删除 <strong>{{ removal }}</strong
      >。发布后，客户端将无法再使用这个 Router 名称调用。
    </p>
    <template #footer
      ><button class="button" @click="removal = ''">取消</button
      ><button class="button danger" @click="remove">
        从草稿中删除
      </button></template
    ></Modal
  >
</template>
