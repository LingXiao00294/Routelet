<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute } from "vue-router";
import { useWorkspace } from "../state/workspace";
import { useTelemetry } from "../state/telemetry";
import { copyText } from "../state/notifications";
import { errorText } from "../services/http";
import { EventDecoder, type StreamEvent } from "../services/stream";
import { count, latency } from "../domain/format";
import Icon from "../ui/Icon.vue";
import EmptyState from "../ui/EmptyState.vue";
const route = useRoute(),
  workspace = useWorkspace(),
  telemetry = useTelemetry();
const model = ref(String(route.query.model ?? "")),
  prompt = ref("你好，请用一句话介绍你自己。"),
  system = ref("");
const maxTokens = ref(512),
  streaming = ref(true),
  busy = ref(false),
  output = ref(""),
  error = ref(""),
  raw = ref(""),
  tab = ref("text");
const elapsed = ref(0),
  inputTokens = ref<number | null>(null),
  outputTokens = ref<number | null>(null),
  finished = ref(false);
let controller: AbortController | undefined;
let elapsedTimer: ReturnType<typeof setInterval> | undefined,
  timeout: ReturnType<typeof setTimeout> | undefined;
const models = computed(() => Object.keys(workspace.base?.models ?? {}));
watch(
  models,
  (value) => {
    if (!model.value && value.length) model.value = value[0];
  },
  { immediate: true },
);
watch(
  () => route.query.model,
  (value) => {
    if (typeof value === "string") model.value = value;
  },
);
const validModel = computed(() => models.value.includes(model.value));
const body = computed(() => ({
  model: model.value,
  max_tokens: maxTokens.value,
  stream: streaming.value,
  ...(system.value.trim() ? { system: system.value } : {}),
  messages: [{ role: "user", content: prompt.value }],
}));
const payload = computed(() => JSON.stringify(body.value, null, 2));
function stop() {
  controller?.abort();
}
function processEvent(event: StreamEvent) {
  if (raw.value.length < 120000)
    raw.value += "event: " + event.event + "\ndata: " + event.data + "\n\n";
  const value = JSON.parse(event.data) as {
    type?: string;
    error?: { message?: string };
    message?: { usage?: { input_tokens?: number; output_tokens?: number } };
    delta?: { text?: string };
    content_block?: { text?: string };
    usage?: { output_tokens?: number };
  };
  if (event.event === "error" || value.type === "error")
    throw new Error(value.error?.message ?? "上游流式响应发生错误");
  if (value.type === "message_start") {
    inputTokens.value = value.message?.usage?.input_tokens ?? null;
    outputTokens.value = value.message?.usage?.output_tokens ?? null;
  }
  if (value.type === "content_block_start" && value.content_block?.text)
    output.value += value.content_block.text;
  if (value.delta?.text) output.value += value.delta.text;
  if (value.usage?.output_tokens != null)
    outputTokens.value = value.usage.output_tokens;
  if (value.type === "message_stop") finished.value = true;
}
async function send() {
  if (
    busy.value ||
    !validModel.value ||
    !prompt.value.trim() ||
    !Number.isInteger(maxTokens.value) ||
    maxTokens.value < 1
  )
    return;
  controller = new AbortController();
  const signal = controller.signal;
  busy.value = true;
  output.value = "";
  error.value = "";
  raw.value = "";
  elapsed.value = 0;
  inputTokens.value = null;
  outputTokens.value = null;
  finished.value = false;
  const start = performance.now();
  elapsedTimer = setInterval(
    () => (elapsed.value = performance.now() - start),
    100,
  );
  timeout = setTimeout(
    () => controller?.abort(new Error("请求超过 180 秒，已停止等待")),
    180000,
  );
  try {
    const response = await fetch("/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body.value),
      signal,
    });
    if (!response.ok) {
      const text = await response.text();
      raw.value = text;
      let message = "请求失败（HTTP " + response.status + "）";
      try {
        const data = JSON.parse(text);
        message = data.error?.message ?? data.detail ?? message;
      } catch {
        /* Keep HTTP status when not JSON. */
      }
      throw new Error(message);
    }
    if (streaming.value) {
      if (!response.body) throw new Error("浏览器未收到响应流");
      const reader = response.body.getReader(),
        decoder = new TextDecoder(),
        events = new EventDecoder();
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) {
            for (const event of events.push(decoder.decode(), true))
              processEvent(event);
            break;
          }
          for (const event of events.push(
            decoder.decode(chunk.value, { stream: true }),
          ))
            processEvent(event);
        }
        if (!finished.value)
          throw new Error("响应流已结束，但没有收到完成事件");
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } else {
      const data = await response.json();
      raw.value = JSON.stringify(data, null, 2);
      output.value = Array.isArray(data.content)
        ? data.content
            .filter((item: { type: string }) => item.type === "text")
            .map((item: { text: string }) => item.text)
            .join("\n")
        : "";
      inputTokens.value = data.usage?.input_tokens ?? null;
      outputTokens.value = data.usage?.output_tokens ?? null;
      finished.value = true;
    }
  } catch (reason) {
    error.value = signal.aborted
      ? signal.reason instanceof Error && signal.reason.name !== "AbortError"
        ? signal.reason.message
        : "请求已停止，已收到的内容保留在下方。"
      : errorText(reason);
  } finally {
    clearInterval(elapsedTimer);
    clearTimeout(timeout);
    elapsed.value = performance.now() - start;
    busy.value = false;
    telemetry.refresh();
  }
}
onBeforeRouteLeave(
  () =>
    !busy.value ||
    window.confirm("请求仍在进行，离开页面将停止等待。是否继续？"),
);
onUnmounted(() => {
  controller?.abort();
  clearInterval(elapsedTimer);
  clearTimeout(timeout);
});
</script>
<template>
  <section class="page-heading">
    <div>
      <div class="eyebrow">A LITTLE SPACE TO EXPERIMENT</div>
      <h1>请求实验室<span class="heading-dot">.</span></h1>
      <p>把路由变成一次真实对话，直接验证你的接入。</p>
    </div>
    <span class="badge neutral"
      ><Icon name="terminal" :size="14" />POST /v1/messages</span
    >
  </section>
  <div v-if="workspace.dirty" class="alert warning">
    <Icon name="warning" :size="18" /><span
      >实验室使用已发布的配置。要测试草稿中的调整，请先检查并发布。</span
    >
  </div>
  <div class="playground-grid">
    <section class="panel playground-request">
      <div class="panel-heading">
        <h2>构建请求</h2>
        <span class="subtle-label">REQUEST</span>
      </div>
      <form @submit.prevent="send">
        <fieldset :disabled="busy">
          <label class="field"
            ><span>模型路由</span
            ><span class="select-control">
              <select aria-label="模型路由" v-model="model" required>
                <option value="" disabled>选择已发布的模型路由</option>
                <option v-for="name in models" :key="name">{{ name }}</option>
              </select>
              <Icon name="down" :size="16" />
            </span></label
          >
          <p v-if="model && !validModel" class="text-red help">
            此路由尚未发布或已被删除，请选择一个已发布路由。
          </p>
          <label class="field"
            ><span>系统提示词 <small>可选</small></span
            ><textarea
              v-model="system"
              rows="2"
              placeholder="例如：你是一位简洁、准确的编程助手。"
            /></label
          ><label class="field"
            ><span>用户消息 <b>*</b></span
            ><textarea
              v-model="prompt"
              class="prompt-textarea"
              rows="7"
              required
              placeholder="写下你想让模型完成的任务…"
            />
          </label>
          <div class="form-grid">
            <label class="field"
              ><span>最大输出 Token</span
              ><input
                v-model.number="maxTokens"
                type="number"
                min="1"
                max="65536"
                step="1"
                required /></label
            ><label class="stream-control"
              ><input v-model="streaming" type="checkbox" /><span
                ><strong>流式输出</strong><small>逐步接收模型响应</small></span
              ></label
            >
          </div>
        </fieldset>
        <div class="request-actions">
          <span>会产生真实上游调用与费用</span
          ><button
            v-if="busy"
            type="button"
            class="button danger"
            @click="stop"
          >
            <Icon name="stop" :size="14" />停止</button
          ><button
            v-else
            type="submit"
            class="button primary"
            :disabled="!validModel || !prompt.trim()"
          >
            <Icon name="play" :size="14" />发送请求
          </button>
        </div>
      </form>
      <details class="request-preview">
        <summary>查看请求 JSON</summary>
        <button class="button small" @click="copyText(payload)">
          <Icon name="copy" :size="14" />复制 JSON
        </button>
        <pre class="code-block">{{ payload }}</pre>
      </details>
    </section>
    <section class="panel playground-response">
      <div class="panel-heading">
        <h2>
          模型响应
          <span v-if="busy" class="badge green"
            ><i class="dot pulse" />接收中</span
          ><span v-else-if="finished && !error" class="badge green"
            ><Icon name="check" :size="12" />已完成</span
          >
        </h2>
        <button
          class="icon-button"
          aria-label="复制响应"
          :disabled="!(tab === 'text' ? output : raw)"
          @click="copyText(tab === 'text' ? output : raw)"
        >
          <Icon name="copy" :size="17" />
        </button>
      </div>
      <div class="response-tabs tabs">
        <button :class="{ active: tab === 'text' }" @click="tab = 'text'">
          文本</button
        ><button :class="{ active: tab === 'raw' }" @click="tab = 'raw'">
          原始响应
        </button>
      </div>
      <div v-if="error" class="alert error inset" role="alert">{{ error }}</div>
      <div class="response-content">
        <div v-if="tab === 'text' && output" class="response-text">
          {{ output }}<span v-if="busy" class="typing-cursor" />
        </div>
        <pre v-else-if="tab === 'raw' && raw" class="code-block">{{ raw }}</pre>
        <EmptyState
          v-else
          :title="busy ? '模型正在思考…' : '留一点空间，给新的想法'"
          :description="
            busy
              ? '收到响应后，会实时显示在这里。'
              : '在左侧写下消息，看看这条路由会带你去哪里。'
          "
          icon="lab"
        />
      </div>
      <div class="response-stats">
        <div>
          <Icon name="clock" :size="14" /><span>{{
            elapsed ? latency(elapsed) : "—"
          }}</span>
        </div>
        <div>
          输入 <strong>{{ count(inputTokens) }}</strong>
        </div>
        <div>
          输出 <strong>{{ count(outputTokens) }}</strong>
        </div>
        <RouterLink to="/calls" class="text-link"
          >调用详情<Icon name="arrow" :size="13"
        /></RouterLink>
      </div>
    </section>
  </div>
</template>
