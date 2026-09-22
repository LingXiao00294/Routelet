<script setup lang="ts">
import { useWorkspace } from "../state/workspace";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
const emit = defineEmits<{ close: [] }>();
const workspace = useWorkspace();
async function publish() {
  if (await workspace.save()) emit("close");
}
</script>
<template>
  <Modal
    title="发布配置"
    eyebrow="REVIEW & PUBLISH"
    :busy="workspace.saving"
    @close="$emit('close')"
  >
    <p class="muted">
      以下调整将一起写入配置并热重载，已开始的请求可继续完成。
    </p>
    <div class="change-list">
      <div v-for="change in workspace.edits" :key="change">
        <Icon name="check" :size="16" /><span>{{ change }}</span>
      </div>
    </div>
    <div v-if="workspace.problems.length" class="alert error">
      <strong>请先完成以下设置</strong>
      <ul>
        <li v-for="problem in workspace.problems" :key="problem">
          {{ problem }}
        </li>
      </ul>
    </div>
    <div v-if="workspace.error" class="alert error" role="alert">
      {{ workspace.error }}
    </div>
    <p v-if="workspace.edits.includes('更新服务与日志设置')" class="help">
      监听地址与端口需重启服务后生效。
    </p>
    <template #footer
      ><button
        class="button"
        :disabled="workspace.saving"
        @click="$emit('close')"
      >
        继续编辑</button
      ><button
        class="button primary"
        :disabled="
          workspace.saving || !!workspace.problems.length || !workspace.dirty
        "
        @click="publish"
      >
        <Icon
          :name="workspace.saving ? 'refresh' : 'check'"
          :class="{ spinning: workspace.saving }"
          :size="16"
        />{{ workspace.saving ? "正在发布…" : "确认发布" }}
      </button></template
    >
  </Modal>
</template>
