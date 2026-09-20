<script setup lang="ts">
import { onMounted, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { storeToRefs } from "pinia";
import AppShell from "@/components/AppShell.vue";
import ToastStack from "@/components/ToastStack.vue";
import ConfirmDialog from "@/components/ConfirmDialog.vue";
import { provideToast } from "@/composables/useToast";
import { provideConfirm } from "@/composables/useConfirm";
import { useAppStore } from "@/stores/app";
import { useConfigStore } from "@/stores/config";
import { useRefreshStore } from "@/stores/refresh";

const toast = provideToast();
const confirmApi = provideConfirm();

const app = useAppStore();
const config = useConfigStore();
const refresh = useRefreshStore();
const route = useRoute();
const router = useRouter();
const { tick } = storeToRefs(refresh);
let silentRefreshRunning = false;

async function bootstrap() {
  app.initTheme();
  if (await app.loadInitialState()) {
    toast.error("初始数据加载失败，数据可能过期");
  }
}

async function silentRefresh() {
  if (silentRefreshRunning) return;
  silentRefreshRunning = true;
  try {
    let failed = false;
    try {
      const [, configOk, circuitOk] = await Promise.all([
        app.checkHealth(),
        app.loadConfig(true),
        app.loadCircuit(true),
      ]);
      if (!configOk || !circuitOk) failed = true;
    } catch {
      failed = true;
    }
    if (app.healthy === false) failed = true;
    if (await refresh.runHandlers()) failed = true;

    if (failed) {
      if (!app.staleData) {
        toast.error("自动刷新失败，数据可能过期");
      }
      app.staleData = true;
    } else {
      app.staleData = false;
    }
  } finally {
    silentRefreshRunning = false;
  }
}

watch(tick, () => {
  void silentRefresh();
});

function onKey(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement)?.tagName;
  const typing =
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    (e.target as HTMLElement)?.isContentEditable;

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    if (route.path.startsWith("/config") && config.dirty) {
      e.preventDefault();
      if (config.saving) return;
      void config
        .save()
        .then(async (editorConfigOk) => {
          const appConfigOk = await app.loadConfig(true);
          if (editorConfigOk && appConfigOk) {
            toast.success("已保存");
          } else {
            app.staleData = true;
            toast.push("配置已保存，但重新加载失败，数据可能过期", "info");
          }
        })
        .catch((err: Error) => toast.error(err.message));
    }
    return;
  }

  if (!typing && e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    refresh.bump();
  }
}

onMounted(() => {
  void bootstrap();
  window.addEventListener("keydown", onKey);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
});

router.beforeEach(async (to, from) => {
  if (
    from.path.startsWith("/config") &&
    !to.path.startsWith("/config") &&
    config.dirty
  ) {
    const ok = await confirmApi.confirm({
      title: "放弃未保存更改？",
      message: "配置有未保存更改，确定离开？",
      confirmText: "离开",
      danger: true,
    });
    if (!ok) return false;
  }
  return true;
});
</script>

<template>
  <AppShell>
    <RouterView />
  </AppShell>
  <ToastStack />
  <ConfirmDialog />
</template>
