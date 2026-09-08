<script setup lang="ts">
/** 视图页头：标题 + 一句话说明 + 数据状态 chips + 右侧动作区 */
import DataStatus from './DataStatus.vue';

defineProps<{ title: string; desc?: string }>();
</script>

<template>
  <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 px-0.5 pt-1">
    <div class="min-w-0">
      <h1 class="t-page-title">{{ title }}</h1>
      <p v-if="desc" class="t-page-desc">{{ desc }}</p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
      <DataStatus />
    </div>
  </div>
</template>
