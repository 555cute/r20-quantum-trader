<script setup lang="ts" generic="T extends string | number">
/** 分段选择器：v-model + options（value/label/icon/title） */
import type { Component } from 'vue';

defineProps<{
  modelValue: T;
  options: { value: T; label?: string; icon?: Component; title?: string }[];
  large?: boolean;
}>();

defineEmits<{ (e: 'update:modelValue', v: T): void }>();
</script>

<template>
  <div class="seg" :class="large && 'seg-lg'" role="tablist">
    <button
      v-for="opt in options"
      :key="String(opt.value)"
      role="tab"
      :aria-selected="modelValue === opt.value"
      :class="{ 'seg-on': modelValue === opt.value }"
      :title="opt.title"
      @click="$emit('update:modelValue', opt.value)"
    >
      <component v-if="opt.icon" :is="opt.icon" class="h-3.5 w-3.5" />
      <span v-if="opt.label">{{ opt.label }}</span>
    </button>
  </div>
</template>
