<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 图标边长（px） */
    size?: number
  }>(),
  { size: 22 },
)
</script>

<template>
  <!-- 角线 + 轨道：结构引导 × 智能闭环 -->
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M5.5 16.8V7.6A2.1 2.1 0 0 1 7.6 5.5h9.2"
      stroke="currentColor"
      stroke-width="2.15"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <circle
      cx="14.6"
      cy="14.6"
      r="4.35"
      stroke="currentColor"
      stroke-width="2.15"
    />
    <circle cx="14.6" cy="14.6" r="1.35" fill="currentColor" />
  </svg>
</template>
