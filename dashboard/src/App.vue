<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useWorkspace } from "./state/workspace";
import { useTelemetry } from "./state/telemetry";
import { dismiss, notices, copyText } from "./state/notifications";
import { usePolling } from "./composables/usePolling";
import Icon from "./ui/Icon.vue";
import CommandPalette from "./ui/CommandPalette.vue";
import PublishDialog from "./ui/PublishDialog.vue";
import Modal from "./ui/Modal.vue";
const route = useRoute(),
  workspace = useWorkspace(),
  telemetry = useTelemetry();
const commandOpen = ref(false),
  publishOpen = ref(false),
  discardOpen = ref(false),
  mobileOpen = ref(false);
const dark = ref(false);
try {
  dark.value = localStorage.getItem("ar-theme") === "dark";
} catch {
  /* Storage is optional. */
}
watch(
  dark,
  (value) => {
    document.documentElement.dataset.theme = value ? "dark" : "light";
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", value ? "#0b1120" : "#e4eafb");
    try {
      localStorage.setItem("ar-theme", value ? "dark" : "light");
    } catch {
      /* Storage is optional. */
    }
  },
  { immediate: true },
);
const nav = [
  { label: "运行总览", icon: "overview", path: "/", group: "工作空间" },
  { label: "调用记录", icon: "activity", path: "/calls", group: "工作空间" },
  { label: "请求实验室", icon: "lab", path: "/playground", group: "工作空间" },
  { label: "路由编排", icon: "routes", path: "/routes", group: "管理" },
  { label: "上游服务", icon: "providers", path: "/providers", group: "管理" },
  { label: "系统设置", icon: "settings", path: "/settings", group: "管理" },
];
const endpoint = computed(() => window.location.origin + "/v1");
function keydown(event: KeyboardEvent) {
  if (document.querySelector("dialog[open]") && !commandOpen.value) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    commandOpen.value = !commandOpen.value;
  }
  if (event.key === "Escape") mobileOpen.value = false;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (workspace.dirty) publishOpen.value = true;
  }
}
function unload(event: BeforeUnloadEvent) {
  if (workspace.dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
}
onMounted(() => {
  workspace.load();
  window.addEventListener("keydown", keydown);
  window.addEventListener("beforeunload", unload);
});
onUnmounted(() => {
  window.removeEventListener("keydown", keydown);
  window.removeEventListener("beforeunload", unload);
});
watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false;
  },
);
usePolling(
  () => telemetry.refresh(),
  () => telemetry.autoRefresh,
);
async function discard() {
  discardOpen.value = false;
  await workspace.discard();
}
</script>
<template>
  <a href="#main-content" class="skip-link">跳转到主要内容</a>
  <div class="app-frame" :class="{ 'nav-open': mobileOpen }">
    <button
      v-if="mobileOpen"
      class="nav-scrim"
      aria-label="关闭导航"
      @click="mobileOpen = false"
    />
    <aside class="sidebar">
      <RouterLink to="/" class="brand" aria-label="Agent Router 首页"
        ><span class="brand-mark"
          ><svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
            <path
              d="M7 9h8c5 0 6 15 13 15h12 M7 39h8c5 0 6-15 13-15 M33 17l8 7-8 7"
              stroke="currentColor"
              stroke-width="5.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            /></svg></span
        ><span
          >agent<span class="brand-light">router</span
          ><small>LOCAL AI GATEWAY</small></span
        ></RouterLink
      >
      <div class="workspace-chip">
        <span class="workspace-symbol">L</span>
        <div><strong>本地工作空间</strong><span>Personal workspace</span></div>
        <span class="local-label">LOCAL</span>
      </div>
      <nav aria-label="主导航">
        <template v-for="group in ['工作空间', '管理']" :key="group"
          ><p class="nav-heading">{{ group }}</p>
          <RouterLink
            v-for="item in nav.filter((item) => item.group === group)"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            :class="{ active: route.path === item.path }"
            :aria-current="route.path === item.path ? 'page' : undefined"
            ><Icon :name="item.icon" :size="19" /><span>{{ item.label }}</span
            ><span
              v-if="item.path === '/routes' && workspace.draft"
              class="nav-count"
              >{{ Object.keys(workspace.draft.models).length }}</span
            ><i
              v-if="route.path === item.path"
              class="nav-active-dot" /></RouterLink
        ></template>
      </nav>
      <div class="sidebar-bottom">
        <div class="endpoint-card">
          <div>
            <Icon name="terminal" :size="15" /><span
              >一个端点，连接所有模型</span
            >
          </div>
          <button :title="endpoint" @click="copyText(endpoint)">
            <code>{{ endpoint.replace(/^https?:\/\//, "") }}</code
            ><Icon name="copy" :size="14" />
          </button>
        </div>
        <div class="sidebar-footer">
          <span class="avatar">AR</span>
          <div><strong>Agent Router</strong><span>让模型协作更简单</span></div>
          <button
            class="theme-button"
            :aria-label="dark ? '切换浅色模式' : '切换深色模式'"
            @click="dark = !dark"
          >
            <Icon :name="dark ? 'sun' : 'moon'" :size="18" />
          </button>
        </div>
      </div>
    </aside>
    <div class="main-frame">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-button mobile-menu"
            aria-label="打开导航"
            :aria-expanded="mobileOpen"
            @click="mobileOpen = !mobileOpen"
          >
            <Icon name="menu" /></button
          ><span>{{ route.meta.section }}</span
          ><span class="breadcrumb-slash">/</span
          ><strong>{{ route.meta.title }}</strong>
        </div>
        <div class="topbar-actions">
          <button class="search-trigger" @click="commandOpen = true">
            <Icon name="search" :size="16" /><span>搜索或快速前往</span
            ><kbd>Ctrl K</kbd></button
          ><span class="topbar-divider" /><span
            class="connection-status"
            :class="{ offline: telemetry.connected === false }"
            ><i class="dot" />{{
              telemetry.connected === null
                ? "正在连接"
                : telemetry.connected
                  ? "服务已连接"
                  : "连接中断"
            }}</span
          ><button
            class="icon-button"
            aria-label="刷新监控数据"
            :disabled="telemetry.loading"
            @click="telemetry.refresh"
          >
            <Icon
              name="refresh"
              :size="18"
              :class="{ spinning: telemetry.loading }"
            />
          </button>
        </div>
      </header>
      <main
        id="main-content"
        tabindex="-1"
        class="page-content"
        :class="{ 'has-draft': workspace.dirty }"
      >
        <div
          v-if="telemetry.error"
          class="alert error global-alert"
          role="alert"
        >
          <Icon name="warning" :size="18" /><span
            >监控数据暂时无法更新，已展示的数据可能过期。{{
              telemetry.error
            }}</span
          ><button
            class="button small"
            :disabled="telemetry.loading"
            @click="telemetry.refresh"
          >
            重试连接
          </button>
        </div>
        <div
          v-if="workspace.error && !workspace.dirty"
          class="alert error global-alert"
          role="alert"
        >
          <span>{{ workspace.error }}</span
          ><button class="button small" @click="workspace.load">
            重新读取配置
          </button>
        </div>
        <RouterView />
        <footer class="page-footer">
          <span
            >AGENT ROUTER
            <span class="footer-dot">·</span> 本地优先，自由连接</span
          ><span v-if="telemetry.updated"
            >数据更新于
            {{
              telemetry.updated.toLocaleTimeString("zh-CN", { hour12: false })
            }}</span
          >
        </footer>
      </main>
      <div v-if="workspace.dirty" class="draft-bar" role="status">
        <span class="draft-indicator" />
        <div>
          <strong>有 {{ workspace.edits.length }} 项未发布调整</strong
          ><span>草稿跨页面保留，发布后统一生效</span>
        </div>
        <div class="draft-actions">
          <button
            class="button ghost"
            :disabled="workspace.saving"
            @click="discardOpen = true"
          >
            放弃草稿</button
          ><button
            class="button primary"
            :disabled="workspace.saving"
            @click="publishOpen = true"
          >
            <Icon name="arrowup" :size="16" />检查并发布
          </button>
        </div>
      </div>
    </div>
  </div>
  <div class="toast-stack" aria-live="polite">
    <div
      v-for="notice in notices"
      :key="notice.id"
      class="toast"
      :class="notice.tone"
    >
      <Icon
        :name="notice.tone === 'error' ? 'warning' : 'check'"
        :size="18"
      /><span>{{ notice.message }}</span
      ><button
        class="icon-button small"
        aria-label="关闭通知"
        @click="dismiss(notice.id)"
      >
        <Icon name="close" :size="15" />
      </button>
    </div>
  </div>
  <CommandPalette v-if="commandOpen" @close="commandOpen = false" />
  <PublishDialog v-if="publishOpen" @close="publishOpen = false" />
  <Modal
    v-if="discardOpen"
    title="放弃未发布的调整？"
    @close="discardOpen = false"
    ><p class="muted">
      本地草稿将被清除，并重新读取服务器配置。已经发布的配置不会改变。
    </p>
    <template #footer
      ><button class="button" @click="discardOpen = false">继续编辑</button
      ><button class="button danger" @click="discard">
        放弃并重新读取
      </button></template
    ></Modal
  >
</template>
