<script setup lang="ts">
import Icon from "./Icon.vue";
withDefaults(
  defineProps<{ icon?: string; title: string; description?: string }>(),
  { icon: "activity", description: "" },
);
</script>
<template>
  <div class="empty-state">
    <div class="empty-icon"><Icon :name="icon" :size="28" /></div>
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <slot />
  </div>
</template>
