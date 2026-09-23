<script setup lang="ts">
import { useWorkspace } from "../state/workspace";
import { cleanConfig, clone } from "../domain/config";
import { downloadText } from "../domain/format";
import Icon from "../ui/Icon.vue";
const workspace = useWorkspace();
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
          连续失败后暂时跳过 Provider，给服务恢复的时间。Provider 的独立设置优先于全局配置。
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
            /><small>相对路径基于启动服务的工作目录。</small></label
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
</template>
