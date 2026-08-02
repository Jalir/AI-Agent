<template>
  <div class="header-title">{{ title }}</div>
</template>

<script setup lang="ts">
/** 独立组件：仅 title 变化时更新，避免 md-body 流式刷新连带闪动 */
defineProps<{
  title: string
}>()
</script>
