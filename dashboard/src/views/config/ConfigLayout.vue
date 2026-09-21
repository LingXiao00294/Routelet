<script setup lang="ts">
import { onMounted, onBeforeUnmount } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { storeToRefs } from "pinia";
import { useAppStore } from "@/stores/app";
import { useConfigStore } from "@/stores/config";
import { useConfirm } from "@/composables/useConfirm";
import { useToast } from "@/composables/useToast";
import { useAutoRefresh } from "@/composables/useAutoRefresh";

const store = useConfigStore();
const app = useAppStore();
const toast = useToast();
const confirm = useConfirm();
const route = useRoute();
const { dirty, saving, loading, error } = storeToRefs(store);

const links = [
  { to: "/config/models", label: "Models" },
  { to: "/config/providers", label: "Providers" },
  { to: "/config/server", label: "Server" },
  { to: "/config/router", label: "Router" },
  { to: "/config/circuit", label: "Circuit" },
];

function handleBeforeUnload(e: BeforeUnloadEvent) {
  if (!dirty.value) return;
  e.preventDefault();
  e.returnValue = "";
}

onMounted(() => {
  window.addEventListener("beforeunload", handleBeforeUnload);
  void store
    .load()
    .then(() => app.loadConfig(true))
    .catch((err: Error) => toast.error(err.message));
});

onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", handleBeforeUnload);
});

async function save() {
  try {
    const editorConfigOk = await store.save();
    const appConfigOk = await app.loadConfig(true);
    if (editorConfigOk && appConfigOk) {
      toast.success("已保存");
    } else {
      app.staleData = true;
      toast.push("配置已保存，但重新加载失败，数据可能过期", "info");
    }
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "保存失败");
  }
}

async function reload() {
  if (dirty.value) {
    const ok = await confirm.confirm({
      title: "放弃未保存更改？",
      message: "刷新将丢弃当前编辑，恢复为磁盘上的配置。",
      confirmText: "刷新",
      danger: true,
    });
    if (!ok) return;
  }
  try {
    await store.load();
    if (await app.loadConfig(true)) {
      toast.success("已刷新");
    } else {
      app.staleData = true;
      toast.push("编辑器已刷新，但全局配置同步失败，数据可能过期", "info");
    }
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "刷新失败");
  }
}

useAutoRefresh(async () => {
  if (dirty.value) return;
  await store.load();
  await app.loadConfig(true);
});
</script>

<template>
  <div class="page fade-up">
    <header class="page-head">
      <div>
        <h1>Config</h1>
        <p class="muted">编辑 config.toml · Ctrl/Cmd+S 保存</p>
      </div>
      <Teleport to="body">
        <div class="actions floating-actions">
          <button class="btn" type="button" :disabled="loading" title="重新载入配置" @click="reload">
            刷新
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="!dirty || saving"
            @click="save"
          >
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </Teleport>
    </header>

    <div v-if="error" class="error-state panel">{{ error }}</div>
    <div v-if="loading && !store.draft" class="empty-state panel">加载配置…</div>
    <template v-else-if="store.draft">
      <nav class="subnav">
        <RouterLink
          v-for="l in links"
          :key="l.to"
          :to="l.to"
          class="sub-link"
          :class="{ active: route.path === l.to }"
        >
          {{ l.label }}
        </RouterLink>
      </nav>
      <RouterView />
    </template>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  min-height: 3rem;
  padding-right: 15rem;
  margin-bottom: 1rem;
}
.page-head h1 {
  margin: 0;
  font-size: 1.5rem;
}
.page-head p {
  margin: 0.25rem 0 0;
}
.actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.floating-actions {
  position: fixed;
  top: calc(var(--header-height) + 0.8rem);
  right: 1.25rem;
  z-index: 9;
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: color-mix(in srgb, var(--bg-elevated) 94%, transparent);
  box-shadow: var(--shadow);
  backdrop-filter: blur(10px);
}
.subnav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 1rem;
}
.sub-link {
  padding: 0.4rem 0.75rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--text-secondary);
}
.sub-link.active {
  background: var(--accent-soft);
  border-color: transparent;
  color: var(--accent);
}
@media (max-width: 860px) {
  .page {
    padding-bottom: 4.5rem;
  }

  .page-head {
    padding-right: 0;
  }

  .floating-actions {
    top: auto;
    right: 0.75rem;
    bottom: 0.75rem;
  }
}
</style>
