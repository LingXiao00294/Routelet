<script setup lang="ts">
import { computed, ref } from "vue";
import { useTelemetry } from "../state/telemetry";
import { compact, count, money } from "../domain/format";
import Modal from "./Modal.vue";
import SearchInput from "./SearchInput.vue";
const emit = defineEmits<{ close: [] }>();
const telemetry = useTelemetry();
const group = ref("virtual"),
  query = ref(""),
  sort = ref("count");
const rows = computed(() => {
  const items =
    group.value === "virtual"
      ? telemetry.models.map((item) => ({
          ...item,
          name: item.virtual_model,
          provider: "",
          query: { model: item.virtual_model },
        }))
      : telemetry.realModels.map((item) => ({
          ...item,
          name: item.model,
          query: { provider: item.provider, provider_model: item.model },
        }));
  return items
    .filter((item) =>
      (item.name + " " + item.provider)
        .toLowerCase()
        .includes(query.value.toLowerCase()),
    )
    .sort((a, b) =>
      sort.value === "cost"
        ? (b.total_cost_usd ?? -1) - (a.total_cost_usd ?? -1)
        : b.count - a.count,
    );
});
</script>
<template>
  <Modal
    title="完整模型用量"
    eyebrow="USAGE BREAKDOWN"
    wide
    @close="emit('close')"
  >
    <div class="usage-controls">
      <div class="segmented">
        <button
          :class="{ active: group === 'virtual' }"
          @click="group = 'virtual'"
        >
          Router</button
        ><button :class="{ active: group === 'real' }" @click="group = 'real'">
          实际模型
        </button>
      </div>
      <label class="sr-only" for="usage-sort">排序方式</label
      ><select id="usage-sort" v-model="sort">
        <option value="count">按调用量排序</option>
        <option value="cost">按费用排序</option>
      </select>
    </div>
    <SearchInput
      v-model="query"
      class="usage-search"
      label="筛选用量模型"
      placeholder="搜索模型或 Provider…"
      :size="17"
    />
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>模型 / Provider</th>
            <th class="numeric">调用量</th>
            <th class="numeric">成功率</th>
            <th class="numeric">输入 / 输出 Token</th>
            <th class="numeric">费用</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in rows"
            :key="JSON.stringify([item.provider, item.name])"
          >
            <td>
              <RouterLink
                :to="{ path: '/calls', query: item.query }"
                class="text-link"
                @click="emit('close')"
                >{{ item.name }}</RouterLink
              ><span v-if="item.provider" class="cell-sub">{{
                item.provider
              }}</span>
            </td>
            <td class="numeric mono">{{ count(item.count) }}</td>
            <td class="numeric mono">
              {{
                item.count
                  ? ((item.success_count / item.count) * 100).toFixed(1) + "%"
                  : "—"
              }}
            </td>
            <td class="numeric mono">
              {{ compact(item.total_input_tokens) }} /
              {{ compact(item.total_output_tokens) }}
            </td>
            <td class="numeric mono">{{ money(item.total_cost_usd) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!rows.length" class="empty-message">没有匹配的用量记录</p>
    <p class="help">
      全部历史统计，费用按调用快照计算。此处分组 Token
      只展示输入与输出，不包含缓存；点击模型可查看对应调用。
    </p>
  </Modal>
</template>
