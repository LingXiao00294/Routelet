<script setup lang="ts">
import EmptyState from "../ui/EmptyState.vue";
</script>
<template>
  <section class="panel">
    <EmptyState
      title="页面不存在"
      description="这个页面不存在，回到工作空间继续。"
      icon="routes"
      ><RouterLink to="/" class="button primary"
        >返回运行总览</RouterLink
      ></EmptyState
    >
  </section>
</template>
