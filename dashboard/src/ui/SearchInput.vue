<script setup lang="ts">
import Icon from "./Icon.vue";

defineProps<{
  label: string;
  placeholder: string;
  size?: number;
  autofocus?: boolean;
}>();
const model = defineModel<string>({ required: true });
</script>
<template>
  <div class="search-input">
    <Icon name="search" :size="size" />
    <input
      v-model="model"
      :aria-label="label"
      :placeholder="placeholder"
      :autofocus="autofocus"
    />
  </div>
</template>
