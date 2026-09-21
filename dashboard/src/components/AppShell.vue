<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { storeToRefs } from "pinia";
import { useAppStore } from "@/stores/app";
import { useConfigStore } from "@/stores/config";
import { useRefreshStore } from "@/stores/refresh";
import { useToast } from "@/composables/useToast";
import type { RouterMode } from "@/api/types";
import type { RefreshInterval } from "@/stores/refresh";

const app = useAppStore();
const configStore = useConfigStore();
const refresh = useRefreshStore();
const toast = useToast();
const route = useRoute();
const { healthy, mode, savingMode, config, staleData } = storeToRefs(app);
const { dirty, draft, loading: configLoading } = storeToRefs(configStore);
const { interval } = storeToRefs(refresh);
const pendingMode = ref<RouterMode | null>(null);

const nav = [
  { to: "/", label: "Overview", name: "overview" },
  { to: "/calls", label: "Calls", name: "calls" },
  { to: "/config", label: "Config", prefix: "/config" },
];

function isActive(item: (typeof nav)[number]) {
  if ("prefix" in item && item.prefix) return route.path.startsWith(item.prefix);
  return route.path === item.to;
}

const healthLabel = computed(() => {
  if (healthy.value === null) return "检测中";
  return healthy.value ? "已连接" : "断开";
});

const modeDisabled = computed(
  () => !config.value || savingMode.value || configLoading.value || dirty.value,
);

/** When Config has unsaved edits, show draft mode so the two controls don't disagree. */
const displayMode = computed(() => {
  if (pendingMode.value) return pendingMode.value;
  if (dirty.value && draft.value) return draft.value.router.mode;
  return mode.value;
});

const failoverEnabled = computed(() => displayMode.value === "failover");

async function onFailoverChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const next = input.checked ? "failover" : "sticky";
  pendingMode.value = next;
  try {
    const refreshed = await app.setMode(next);
    if (refreshed) {
      toast.success(input.checked ? "故障转移已开启" : "已切换到指定模型模式");
    } else {
      toast.push("模式已切换，但重新加载失败，数据可能过期", "info");
    }
  } catch (err) {
    pendingMode.value = null;
    input.checked = failoverEnabled.value;
    toast.error(err instanceof Error ? err.message : "切换失败");
  } finally {
    pendingMode.value = null;
  }
}

function onInterval(e: Event) {
  const v = Number((e.target as HTMLSelectElement).value) as RefreshInterval;
  refresh.setIntervalSec(v);
}
</script>

<template>
  <div class="shell">
    <aside class="side">
      <div class="brand-block">
        <div class="brand">Agent Router</div>
        <p class="tagline">本地 LLM 路由监控</p>
      </div>
      <nav class="nav">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-link"
          :class="{ active: isActive(item) }"
        >
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="side-foot muted">
        <span class="mono">v0.2</span>
      </div>
    </aside>

    <div class="main">
      <header class="top">
        <div class="top-left">
          <span
            class="health"
            :class="healthy === true ? 'ok' : healthy === false ? 'bad' : ''"
          >
            <span class="dot" />
            {{ healthLabel }}
          </span>
          <span v-if="staleData" class="stale-badge badge badge-warn">数据可能过期</span>
        </div>
        <div class="top-actions">
          <label
            class="ctrl mode-ctrl"
            :class="{ disabled: modeDisabled }"
            :title="dirty ? '配置页有未保存更改，请先保存或刷新' : configLoading ? '配置加载中' : undefined"
          >
            <span class="ctrl-label">故障转移</span>
            <input
              class="sr-only"
              type="checkbox"
              role="switch"
              aria-label="开启或关闭故障转移"
              :aria-busy="savingMode"
              :checked="failoverEnabled"
              :disabled="modeDisabled"
              @change="onFailoverChange"
            />
            <span class="switch-track" aria-hidden="true">
              <span class="switch-thumb" />
            </span>
          </label>
          <label class="ctrl">
            <span class="ctrl-label">Refresh</span>
            <select
              class="ctrl-select"
              :value="interval"
              @change="onInterval"
            >
              <option :value="0">Off</option>
              <option :value="5">5s</option>
              <option :value="10">10s</option>
              <option :value="30">30s</option>
              <option :value="60">60s</option>
            </select>
          </label>
          <button
            v-if="!route.path.startsWith('/config')"
            class="btn btn-sm"
            type="button"
            title="刷新 (R)"
            @click="refresh.bump()"
          >
            刷新
          </button>
          <button class="btn btn-sm" type="button" @click="app.toggleTheme()">
            {{ app.theme === "light" ? "深色" : "浅色" }}
          </button>
        </div>
      </header>
      <main class="content">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--nav-width) 1fr;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}

.side {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  padding: 1.25rem 1rem;
  border-right: 1px solid var(--border);
  background: color-mix(in srgb, var(--bg-elevated) 92%, transparent);
  min-height: 0;
  overflow-y: auto;
}

.brand {
  font-size: 1.15rem;
  font-weight: 700;
}

.tagline {
  margin: 0.25rem 0 0;
  font-size: 0.8rem;
  color: var(--text-muted);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.nav-link {
  padding: 0.55rem 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-weight: 600;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-link:hover {
  background: var(--bg-hover);
  color: var(--text);
}

.nav-link.active {
  background: var(--accent-soft);
  color: var(--accent);
}

.side-foot {
  margin-top: auto;
  font-size: 0.75rem;
}

.main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.content {
  flex: 1;
  padding: 1.25rem;
  min-height: 0;
  overflow-y: auto;
}

.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  min-height: var(--header-height);
  padding: 0.65rem 1.25rem;
  border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--bg-elevated) 92%, transparent);
  flex-shrink: 0;
  z-index: 10;
}

.top-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.top-left {
  display: flex;
  align-items: center;
  gap: 0.65rem;
}

.stale-badge {
  font-size: 0.72rem;
}

.mode-ctrl {
  padding: 0.1rem 0.35rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-elevated);
  cursor: pointer;
  user-select: none;
}

.mode-ctrl.disabled {
  cursor: not-allowed;
}

.switch-track {
  position: relative;
  width: 28px;
  height: 16px;
  border-radius: 999px;
  background: var(--bg-muted);
  box-shadow: inset 0 0 0 1px var(--border-strong);
  transition: background 0.18s ease, box-shadow 0.18s ease;
}

.switch-thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--bg-elevated);
  box-shadow: 0 1px 3px rgba(21, 32, 43, 0.3);
  transition: transform 0.18s ease;
}

.mode-ctrl input:checked + .switch-track {
  background: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.mode-ctrl input:checked + .switch-track .switch-thumb {
  transform: translateX(12px);
}

.mode-ctrl input:focus-visible + .switch-track {
  box-shadow: var(--focus-ring);
}

.health {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  white-space: nowrap;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-muted);
}

.health .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
}

.health.ok {
  color: var(--success);
}
.health.ok .dot {
  background: var(--success);
  box-shadow: 0 0 0 3px var(--success-soft);
}

.health.bad {
  color: var(--danger);
}
.health.bad .dot {
  background: var(--danger);
}

.ctrl {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
}

.ctrl-label {
  color: var(--text-muted);
  font-weight: 600;
}

.ctrl-select {
  min-height: 28px;
  padding: 0.15rem 0.4rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
}

@media (max-width: 860px) {
  .shell {
    grid-template-columns: 1fr;
    height: auto;
    overflow: visible;
  }
  .side {
    overflow-y: visible;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
  .main {
    overflow: visible;
  }
  .content {
    overflow-y: visible;
  }
  .nav {
    flex-direction: row;
    flex-wrap: wrap;
  }
}

@media (max-width: 520px) {
  .top {
    align-items: stretch;
    flex-direction: column;
    gap: 0.55rem;
  }

  .top-actions {
    justify-content: flex-start;
  }
}
</style>
