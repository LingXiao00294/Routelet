import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { Config } from "../domain/types";
import {
  canonical,
  changes,
  cleanConfig,
  clone,
  normalizeConfig,
  redactKeys,
  validateConfig,
} from "../domain/config";
import { errorText, request } from "../services/http";
import { notify } from "./notifications";

export const useWorkspace = defineStore("workspace", () => {
  const base = ref<Config | null>(null);
  const draft = ref<Config | null>(null);
  const loading = ref(false),
    saving = ref(false),
    error = ref(""),
    conflict = ref(false);
  const edits = computed(() =>
    base.value && draft.value ? changes(base.value, draft.value) : [],
  );
  const dirty = computed(() => edits.value.length > 0);
  const problems = computed(() =>
    draft.value ? validateConfig(draft.value, base.value ?? undefined) : [],
  );
  let loadingTask: Promise<void> | null = null;
  async function load() {
    if (loadingTask) return loadingTask;
    loadingTask = (async () => {
      loading.value = true;
      error.value = "";
      try {
        const data = normalizeConfig(
          await request<Partial<Config>>("/api/config"),
        );
        if (!dirty.value && !saving.value) {
          base.value = clone(data);
          draft.value = clone(data);
          conflict.value = false;
        }
      } catch (reason) {
        error.value = errorText(reason);
      } finally {
        loading.value = false;
        loadingTask = null;
      }
    })();
    return loadingTask;
  }
  async function discard() {
    if (saving.value) return;
    draft.value = base.value ? clone(base.value) : null;
    await load();
  }
  async function save(): Promise<boolean> {
    if (!draft.value || !base.value || saving.value || problems.value.length)
      return false;
    saving.value = true;
    error.value = "";
    conflict.value = false;
    const candidate = clone(draft.value);
    try {
      const latest = normalizeConfig(
        await request<Partial<Config>>("/api/config"),
      );
      if (canonical(latest) !== canonical(base.value)) {
        conflict.value = true;
        throw new Error(
          "服务器配置已被其他操作修改。请先放弃本地草稿并重新载入，再应用本次调整。",
        );
      }
      await request("/api/config", {
        method: "PUT",
        body: JSON.stringify(cleanConfig(candidate)),
      });
      // Clear plaintext secrets immediately, even if the read after a committed write fails.
      base.value = redactKeys(candidate);
      draft.value = clone(base.value);
      notify("配置已发布，新的 Router 规则已生效");
      try {
        const saved = normalizeConfig(
          await request<Partial<Config>>("/api/config"),
        );
        base.value = clone(saved);
        draft.value = clone(saved);
      } catch {
        error.value = "配置已成功发布，但重新读取失败。请恢复连接后刷新配置。";
        notify(error.value, "info");
      }
      return true;
    } catch (reason) {
      error.value = errorText(reason);
      notify(error.value, "error");
      return false;
    } finally {
      saving.value = false;
    }
  }
  return {
    base,
    draft,
    loading,
    saving,
    error,
    conflict,
    dirty,
    edits,
    problems,
    load,
    discard,
    save,
  };
});
