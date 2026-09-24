<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useWorkspace } from "../state/workspace";
import { cleanConfig, clone } from "../domain/config";
import { downloadText } from "../domain/format";
import { errorText, request } from "../services/http";
import { notify } from "../state/notifications";
import Icon from "../ui/Icon.vue";
import Modal from "../ui/Modal.vue";
const workspace = useWorkspace();
interface BodyRecordingStatus {
  enabled: boolean;
  expires_at: string | null;
}
const recording = ref<BodyRecordingStatus | null>(null);
const recordingDuration = ref(15);
const recordingBusy = ref(false);
const recordingError = ref("");
const confirmRecording = ref(false);
const recordingExpiry = computed(() =>
  recording.value?.expires_at
    ? new Date(recording.value.expires_at).toLocaleString()
    : "",
);
let recordingPoll: ReturnType<typeof setInterval> | undefined;
let recordingRequestId = 0;
async function loadRecording() {
  const requestId = ++recordingRequestId;
  try {
    const status = await request<BodyRecordingStatus>("/api/recording/bodies");
    if (requestId !== recordingRequestId || recordingBusy.value) return;
    recording.value = status;
    recordingError.value = "";
  } catch (reason) {
    if (requestId !== recordingRequestId || recordingBusy.value) return;
    recordingError.value = errorText(reason);
  }
}
async function enableRecording() {
  recordingBusy.value = true;
  recordingRequestId++;
  try {
    recording.value = await request<BodyRecordingStatus>(
      "/api/recording/bodies",
      {
        method: "PUT",
        body: JSON.stringify({ duration_minutes: recordingDuration.value }),
      },
    );
    recordingError.value = "";
    confirmRecording.value = false;
    notify("正文记录已开启，到期将自动关闭", "info");
  } catch (reason) {
    recordingError.value = errorText(reason);
  } finally {
    recordingRequestId++;
    recordingBusy.value = false;
  }
}
async function disableRecording() {
  recordingBusy.value = true;
  recordingRequestId++;
  try {
    recording.value = await request<BodyRecordingStatus>(
      "/api/recording/bodies",
      {
        method: "DELETE",
      },
    );
    recordingError.value = "";
    notify("正文记录已关闭");
  } catch (reason) {
    recordingError.value = errorText(reason);
  } finally {
    recordingRequestId++;
    recordingBusy.value = false;
  }
}
onMounted(() => {
  loadRecording();
  recordingPoll = setInterval(loadRecording, 15000);
});
onUnmounted(() => clearInterval(recordingPoll));
function exportConfig() {
  if (!workspace.base) return;
  const config = cleanConfig(clone(workspace.base));
  for (const provider of Object.values(config.providers))
    provider.api_key = "${YOUR_API_KEY}";
  downloadText(
    "routelet-config.redacted.json",
    JSON.stringify(config, null, 2),
    "application/json",
  );
}
</script>
<template>
  <section class="page-heading">
    <div>
      <div class="eyebrow">MAKE IT YOURS</div>
      <h1>系统设置<span class="heading-dot">.</span></h1>
      <p>为你的工作方式，调好每一个细节。</p>
    </div>
    <button class="button" :disabled="!workspace.base" @click="exportConfig">
      <Icon name="download" :size="16" />导出已发布配置（脱敏）
    </button>
  </section>
  <div v-if="workspace.loading && !workspace.draft" class="skeleton-block" />
  <fieldset
    v-if="workspace.draft"
    :disabled="workspace.saving"
    class="settings-layout"
  >
    <section class="settings-section">
      <div class="settings-intro">
        <span class="stat-icon mint"><Icon name="shield" /></span>
        <h2>全局熔断保护</h2>
        <p>
          自动故障转移开启时，连续失败后暂时跳过
          Provider，给服务恢复的时间。Provider 的独立设置优先于全局配置。
        </p>
      </div>
      <div class="panel settings-form">
        <label class="field"
          ><span>连续失败阈值</span>
          <div class="input-unit">
            <input
              aria-label="连续失败阈值"
              v-model.number="workspace.draft.router.failure_threshold"
              type="number"
              min="1"
              step="1"
              required
            /><span>次</span>
          </div>
          <small>可重试错误达到此次数后触发熔断。</small></label
        ><label class="field"
          ><span>恢复等待时间</span>
          <div class="input-unit">
            <input
              v-model.number="workspace.draft.router.recovery_timeout"
              type="number"
              min="0.1"
              step="any"
              required
            /><span>秒</span>
          </div>
          <small>等待后进入半开状态，允许一次恢复探测。</small></label
        >
        <div class="info-strip">
          <Icon name="shield" :size="17" /><span
            >401 / 403 会立即熔断；429 / 529 使用 Provider 限流冷却。</span
          >
        </div>
      </div>
    </section>
    <section class="settings-section">
      <div class="settings-intro">
        <span class="stat-icon blue"><Icon name="providers" /></span>
        <h2>服务监听</h2>
        <p>API 和 Dashboard 共享监听地址与端口。</p>
        <span class="badge amber">需重启服务</span>
      </div>
      <div class="panel settings-form">
        <div class="form-grid">
          <label class="field"
            ><span>监听地址</span
            ><input
              v-model="workspace.draft.server.host"
              placeholder="127.0.0.1"
              required /></label
          ><label class="field"
            ><span>端口</span
            ><input
              aria-label="端口"
              v-model.number="workspace.draft.server.port"
              type="number"
              min="1"
              max="65535"
              step="1"
              required
          /></label>
        </div>
        <p class="help">
          推荐使用回环地址 127.0.0.1。此服务无内置鉴权，远程监听还需要启动参数
          --allow-remote。
        </p>
        <div class="setting-note">
          <Icon name="clock" :size="17" /><span
            >发布会保存此设置；新的监听地址在下次启动时生效。</span
          >
        </div>
      </div>
    </section>
    <section class="settings-section">
      <div class="settings-intro">
        <span class="stat-icon purple"><Icon name="book" /></span>
        <h2>日志与保留</h2>
        <p>
          调整日志详细程度、文件位置和轮转策略，排查问题时保持足够的上下文。
        </p>
      </div>
      <div class="panel settings-form">
        <div class="form-grid">
          <label class="field"
            ><span>日志级别</span
            ><select v-model="workspace.draft.server.log_level">
              <option value="debug">Debug · 调试</option>
              <option value="info">Info · 常规</option>
              <option value="warning">Warning · 警告</option>
              <option value="error">Error · 错误</option>
            </select></label
          ><label class="field"
            ><span>轮转保留数量</span
            ><input
              v-model.number="workspace.draft.server.log_backup_count"
              type="number"
              min="0"
              step="1"
              required /></label
          ><label class="field full"
            ><span>日志文件路径</span
            ><input
              v-model="workspace.draft.server.log_file"
              placeholder="留空则只输出到控制台"
            /><small
              >相对路径基于 ~/.routelet/，留空则只输出到控制台。</small
            ></label
          ><label class="field full"
            ><span>单个日志文件大小上限</span>
            <div class="input-unit">
              <input
                v-model.number="workspace.draft.server.log_max_bytes"
                type="number"
                min="1"
                step="1"
                required
              /><span>bytes</span>
            </div></label
          >
        </div>
      </div>
    </section>
  </fieldset>
  <section class="settings-section">
    <div class="settings-intro">
      <span class="stat-icon amber"><Icon name="book" /></span>
      <h2>调用正文记录</h2>
      <p>临时保存请求与响应正文，用于排查具体调用。</p>
    </div>
    <div class="panel settings-form">
      <p class="help">
        当前状态：<strong>{{
          recording?.enabled ? "已开启" : "已关闭（默认）"
        }}</strong>
        <span v-if="recording?.enabled">
          · 将于 {{ recordingExpiry }} 自动关闭</span
        >
      </p>
      <p class="help">
        正文可能包含提示词、模型输出及其他敏感信息。记录响应会显著增加数据库大小；关闭后不会删除已有正文。
      </p>
      <div v-if="recordingError" class="alert error" role="alert">
        {{ recordingError }}
      </div>
      <div class="form-grid">
        <label class="field">
          <span>开启时长</span>
          <select v-model.number="recordingDuration" :disabled="recordingBusy">
            <option :value="15">15 分钟</option>
            <option :value="60">1 小时</option>
            <option :value="240">4 小时</option>
            <option :value="1440">24 小时</option>
          </select>
        </label>
      </div>
      <div class="heading-actions">
        <button
          class="button"
          :disabled="recordingBusy"
          @click="confirmRecording = true"
        >
          {{ recording?.enabled ? "重设记录期限" : "开启正文记录" }}
        </button>
        <button
          v-if="recording?.enabled"
          class="button danger"
          :disabled="recordingBusy"
          @click="disableRecording"
        >
          立即关闭
        </button>
      </div>
    </div>
  </section>
  <Modal
    v-if="confirmRecording"
    :title="
      recording?.enabled ? '确认重设记录期限？' : '确认开启调用正文记录？'
    "
    :busy="recordingBusy"
    @close="confirmRecording = false"
  >
    <p>
      接下来的
      {{ recordingDuration }} 分钟会保存请求与响应正文，可能包含敏感内容。
      <strong>记录响应会显著增加数据库大小。</strong>
      到期或服务重启后自动关闭；已保存的正文不会自动清除。
    </p>
    <p v-if="recording?.enabled">
      新期限从现在开始计算，可能早于当前的自动关闭时间。
    </p>
    <div v-if="recordingError" class="alert error" role="alert">
      {{ recordingError }}
    </div>
    <template #footer>
      <button
        class="button"
        :disabled="recordingBusy"
        @click="confirmRecording = false"
      >
        取消
      </button>
      <button
        class="button primary"
        :disabled="recordingBusy"
        @click="enableRecording"
      >
        {{
          recordingBusy
            ? "正在保存…"
            : recording?.enabled
              ? "确认重设"
              : "确认开启"
        }}
      </button>
    </template>
  </Modal>
</template>
