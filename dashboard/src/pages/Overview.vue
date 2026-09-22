<script setup lang="ts">
import { computed, ref } from "vue";
import { useWorkspace } from "../state/workspace";
import { useTelemetry } from "../state/telemetry";
import { compact, count, fillDays, latency, money } from "../domain/format";
import Icon from "../ui/Icon.vue";
import TrafficChart from "../ui/TrafficChart.vue";
import CallTable from "../ui/CallTable.vue";
import CallInspector from "../ui/CallInspector.vue";
import EmptyState from "../ui/EmptyState.vue";
import UsageDetails from "../ui/UsageDetails.vue";
const workspace = useWorkspace(),
  telemetry = useTelemetry();
const usageOpen = ref(false);
const selectedCall = ref(""),
  modelTab = ref("virtual");
const trend = computed(() => fillDays(telemetry.daily, telemetry.days));
const volume = computed(() =>
  trend.value.reduce((sum, row) => sum + row.count, 0),
);
const modelRows = computed(() =>
  modelTab.value === "virtual"
    ? [...telemetry.models]
        .sort((a, b) => b.count - a.count)
        .slice(0, 5)
        .map((row) => ({
          ...row,
          name: row.virtual_model,
          detail: "Router",
          query: { model: row.virtual_model },
        }))
    : telemetry.realModels.slice(0, 5).map((row) => ({
        ...row,
        name: row.model,
        detail: row.provider,
        query: { provider: row.provider, provider_model: row.model },
      })),
);
const largest = computed(() =>
  Math.max(1, ...modelRows.value.map((row) => row.count)),
);
const providers = computed(() =>
  Object.entries(workspace.base?.providers ?? {}),
);
const configured = computed(
  () => Object.keys(workspace.base?.models ?? {}).length,
);
const openCircuits = computed(
  () =>
    Object.values(telemetry.circuits).filter((value) => value === "open")
      .length,
);
const cards = computed(() => {
  const value = telemetry.summary;
  return [
    {
      title: "累计请求",
      value: value ? count(value.total_calls) : "—",
      unit: "次",
      icon: "activity",
      note: value ? count(value.success_count) + " 次成功完成" : "等待统计数据",
      tone: "mint",
    },
    {
      title: "请求成功率",
      value: value && value.total_calls ? value.success_rate.toFixed(1) : "—",
      unit: "%",
      icon: "shield",
      note: value
        ? count(value.error_count) + " 次失败 · 全部历史"
        : "等待统计数据",
      tone: "blue",
    },
    {
      title: "平均响应耗时",
      value: value?.success_count ? latency(value.avg_latency_ms) : "—",
      unit: "",
      icon: "clock",
      note: "成功请求的完整响应耗时",
      tone: "amber",
    },
    {
      title: "累计使用费用",
      value: value ? money(value.total_cost_usd, 2) : "—",
      unit: "USD",
      icon: "coin",
      note: value
        ? compact(
            value.total_input_tokens +
              value.total_output_tokens +
              value.total_cache_read +
              value.total_cache_write,
          ) + " Token · 含缓存"
        : "等待统计数据",
      tone: "purple",
    },
  ];
});
</script>
<template>
  <section class="page-heading">
    <div>
      <div class="eyebrow">YOUR ROUTING, AT A GLANCE</div>
      <h1>运行总览<span class="heading-dot">.</span></h1>
      <p>每一次调用，每一条路径，尽在掌握。</p>
    </div>
    <div class="heading-actions">
      <label class="auto-refresh"
        ><input v-model="telemetry.autoRefresh" type="checkbox" /><span
          >每 15 秒刷新</span
        ></label
      ><RouterLink to="/playground" class="button primary"
        ><Icon name="play" :size="15" />发送测试请求</RouterLink
      >
    </div>
  </section>
  <div v-if="workspace.base && !configured" class="onboarding-banner">
    <div>
      <span class="badge green">从这里开始</span>
      <h2>连接模型，建立你的第一个 Router。</h2>
      <p>添加 Provider → 登记实际模型 → 配置 Router → 发布并测试</p>
      <RouterLink to="/providers" class="button primary"
        >连接第一个 Provider<Icon name="arrow" :size="16"
      /></RouterLink>
    </div>
    <div class="onboarding-art" aria-hidden="true">
      <span class="art-client"><Icon name="terminal" :size="26" /></span
      ><span class="art-wire" /><span class="art-router"
        ><Icon name="routes" :size="32" /></span
      ><span class="art-branches"><i /><i /><i /></span
      ><span class="art-models"
        ><Icon name="token" /><Icon name="token" /><Icon name="token"
      /></span>
    </div>
  </div>
  <div class="stat-grid">
    <article v-for="(card, index) in cards" :key="card.title" class="stat-card">
      <div class="stat-top">
        <span>{{ card.title }}</span
        ><span class="stat-icon" :class="card.tone"
          ><Icon :name="card.icon" :size="18"
        /></span>
      </div>
      <div
        class="stat-value"
        :class="{ skeleton: !telemetry.summary && telemetry.loading }"
      >
        {{ card.value }}<small>{{ card.unit }}</small>
      </div>
      <div class="stat-bottom">
        <span>{{ card.note }}</span
        ><span class="stat-number">0{{ index + 1 }}</span>
      </div>
    </article>
  </div>
  <div class="overview-primary">
    <section class="panel traffic-panel">
      <div class="panel-heading">
        <div>
          <h2>请求流量 <span class="subtle-label">TRAFFIC</span></h2>
          <p>
            最近 {{ telemetry.days }} 天，共
            <strong>{{ telemetry.summary ? count(volume) : "—" }}</strong>
            次调用
          </p>
        </div>
        <div class="segmented">
          <button
            v-for="days in [7, 14, 30]"
            :key="days"
            :class="{ active: telemetry.days === days }"
            :disabled="telemetry.loading"
            @click="telemetry.setDays(days)"
          >
            {{ days }} 天
          </button>
        </div>
      </div>
      <TrafficChart v-if="telemetry.summary" :rows="trend" />
      <div
        v-else-if="telemetry.loading"
        class="skeleton-block"
        aria-label="正在加载趋势"
      />
      <EmptyState
        v-else
        title="趋势数据暂不可用"
        description="连接恢复后可查看每日调用情况。"
      />
    </section>
    <section class="panel route-health">
      <div class="panel-heading">
        <h2>Router 配置</h2>
        <Icon name="routes" :size="19" />
      </div>
      <div class="health-orbit">
        <svg viewBox="0 0 180 180" aria-hidden="true">
          <circle
            cx="90"
            cy="90"
            r="70"
            fill="none"
            stroke="var(--line)"
            stroke-width="8"
          />
          <circle
            v-if="configured"
            cx="90"
            cy="90"
            r="70"
            fill="none"
            stroke="var(--accent)"
            stroke-width="8"
            stroke-linecap="round"
            stroke-dasharray="440 440"
            transform="rotate(-90 90 90)"
          />
          <circle
            cx="90"
            cy="90"
            r="55"
            fill="none"
            stroke="var(--line)"
            stroke-dasharray="2 7"
          />
        </svg>
        <div>
          <strong>{{ configured }}</strong
          ><span>已配置 Routers</span>
        </div>
      </div>
      <div class="health-facts">
        <span
          ><i class="dot text-green" />{{ providers.length }} 个 Provider</span
        ><span :class="{ 'text-red': openCircuits }"
          >{{ openCircuits }} 个熔断中</span
        >
      </div>
      <RouterLink to="/routes" class="panel-bottom-link"
        >管理 Routers<Icon name="arrow" :size="16"
      /></RouterLink>
    </section>
  </div>
  <div class="overview-secondary">
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>模型用量</h2>
          <p>累计调用分布 · 前 5 位</p>
        </div>
        <div class="tabs compact-tabs">
          <button
            :class="{ active: modelTab === 'virtual' }"
            @click="modelTab = 'virtual'"
          >
            Router</button
          ><button
            :class="{ active: modelTab === 'real' }"
            @click="modelTab = 'real'"
          >
            实际模型
          </button>
        </div>
      </div>
      <div v-if="modelRows.length" class="model-usage-list">
        <RouterLink
          v-for="(model, index) in modelRows"
          :key="model.detail + model.name"
          :to="{ path: '/calls', query: model.query }"
          class="model-usage-row"
          ><span class="model-rank">0{{ index + 1 }}</span>
          <div>
            <div class="usage-label">
              <strong class="truncate" :title="model.name">{{
                model.name
              }}</strong
              ><span class="mono"
                >{{ count(model.count) }}<small> 次</small></span
              >
            </div>
            <div class="usage-track">
              <span :style="{ width: (model.count / largest) * 100 + '%' }" />
            </div>
            <div class="usage-meta">
              <span>{{ model.detail }}</span
              ><span>{{ money(model.total_cost_usd) }}</span>
            </div>
          </div></RouterLink
        >
      </div>
      <EmptyState
        v-else
        title="用量将在这里汇集"
        description="发起第一次请求后，即可比较各模型的使用情况。"
        icon="token"
      />
      <button
        v-if="telemetry.models.length || telemetry.realModels.length"
        class="panel-bottom-link usage-detail-trigger"
        @click="usageOpen = true"
      >
        查看完整用量<Icon name="arrow" :size="16" />
      </button>
    </section>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>Providers</h2>
          <p>密钥配置与当前熔断状态</p>
        </div>
        <RouterLink to="/providers" class="text-link"
          >查看全部<Icon name="arrow" :size="14"
        /></RouterLink>
      </div>
      <div v-if="providers.length" class="provider-status-list">
        <div
          v-for="([name, provider], index) in providers.slice(0, 4)"
          :key="name"
          class="provider-status-row"
        >
          <span class="provider-avatar" :class="'color-' + (index % 4)">{{
            name.slice(0, 1).toUpperCase()
          }}</span>
          <div class="provider-status-name">
            <strong>{{ name }}</strong
            ><span
              >{{ Object.keys(provider.models).length }} 个模型 · Anthropic
              兼容</span
            >
          </div>
          <span
            class="badge"
            :class="
              !provider.has_key || telemetry.circuits[name] === 'open'
                ? 'amber'
                : 'green'
            "
            >{{
              !provider.has_key
                ? "待配置密钥"
                : telemetry.circuits[name] === "open"
                  ? "已熔断"
                  : telemetry.circuits[name] === "half_open"
                    ? "恢复探测中"
                    : "已配置"
            }}</span
          >
        </div>
      </div>
      <EmptyState
        v-else
        title="连接你的模型服务"
        description="所有 Providers 的连接和模型，都从这里管理。"
        icon="providers"
        ><RouterLink to="/providers" class="button small"
          >添加 Provider<Icon name="plus" :size="14" /></RouterLink
      ></EmptyState>
    </section>
  </div>
  <section class="panel recent-panel">
    <div class="panel-heading">
      <div>
        <h2>
          最近调用
          <span v-if="telemetry.recent" class="count-bubble">{{
            telemetry.recent.total
          }}</span>
        </h2>
        <p>从请求到响应，保留每一步的细节</p>
      </div>
      <RouterLink to="/calls" class="text-link"
        >查看所有调用<Icon name="arrow" :size="15"
      /></RouterLink>
    </div>
    <CallTable
      v-if="telemetry.recent?.data.length"
      :calls="telemetry.recent.data"
      compact-view
      @select="selectedCall = $event"
    /><EmptyState
      v-else
      title="等待第一条调用"
      description="调用记录会自动出现在这里，无需额外配置。"
    />
  </section>
  <CallInspector
    v-if="selectedCall"
    :id="selectedCall"
    @close="selectedCall = ''"
  />
  <UsageDetails v-if="usageOpen" @close="usageOpen = false" />
</template>
