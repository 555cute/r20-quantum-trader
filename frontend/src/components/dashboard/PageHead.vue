<script setup lang="ts">
/** 视图动作行：页面标题/描述已按用户反馈移除（信息冗余），仅在有需要时渲染右对齐动作区 */
defineProps<{ title?: string; desc?: string }>();
</script>

<template>
  <div v-if="$slots.actions" class="flex flex-wrap items-center justify-end gap-2">
    <slot name="actions" />
  </div>
</template>
