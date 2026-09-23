<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { useWorkspace } from "../state/workspace";
import { navigation } from "../domain/navigation";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
const emit = defineEmits<{ close: [] }>();
const router = useRouter(),
  workspace = useWorkspace();
const query = ref(""),
  selected = ref(0);
const items = computed(() =>
  [
    ...navigation,
    ...Object.keys(workspace.draft?.models ?? {}).map((name) => ({
      label: name,
      detail: "Router",
      path: "/routes?q=" + encodeURIComponent(name),
      icon: "routes",
    })),
    ...Object.keys(workspace.draft?.providers ?? {}).map((name) => ({
      label: name,
      detail: "Provider",
      path: "/providers?q=" + encodeURIComponent(name),
      icon: "providers",
    })),
  ].filter((item) =>
    (item.label + item.detail)
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
);
function go(index: number) {
  const item = items.value[index];
  if (item) {
    router.push(item.path);
    emit("close");
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    selected.value = (selected.value + 1) % Math.max(1, items.value.length);
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    selected.value =
      (selected.value - 1 + items.value.length) %
      Math.max(1, items.value.length);
  }
  if (event.key === "Enter") {
    event.preventDefault();
    go(selected.value);
  }
}
</script>
<template>
  <Modal title="快速前往" eyebrow="COMMAND CENTER" @close="$emit('close')"
    ><div @keydown="keydown">
      <div class="search-input command-input">
        <Icon name="search" /><input
          v-model="query"
          aria-label="搜索页面、Router 或 Provider"
          placeholder="搜索页面、Router 或 Provider…"
          autofocus
          @input="selected = 0"
        />
      </div>
      <div class="command-results">
        <button
          v-for="(item, index) in items"
          :key="item.path"
          class="command-item"
          :class="{ selected: index === selected }"
          @click="go(index)"
          @focus="selected = index"
        >
          <Icon :name="item.icon" /><span
            ><strong>{{ item.label }}</strong
            ><small>{{ item.detail }}</small></span
          ><Icon name="arrow" :size="16" />
        </button>
        <p v-if="!items.length" class="empty-message">没有找到匹配项</p>
      </div>
    </div>
    <template #footer
      ><span class="help">↑ ↓ 选择 · Enter 打开 · Esc 关闭</span></template
    ></Modal
  >
</template>
