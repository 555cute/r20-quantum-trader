<script setup lang="ts">
/** 开关：v-model 布尔，带禁用与无障碍角色 */
const props = defineProps<{ modelValue: boolean; disabled?: boolean }>();
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();

function toggle() {
  if (props.disabled) return;
  emit('update:modelValue', !props.modelValue);
}
</script>

<template>
  <button
    type="button"
    role="switch"
    class="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="toggle"
  />
</template>
