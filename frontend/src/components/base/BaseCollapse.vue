<script setup lang="ts">
/** 折叠区：标题行 + chevron，内容懒渲染（v-show 保留状态） */
import { ref } from 'vue';
import { ChevronDown } from 'lucide-vue-next';

const props = withDefaults(
  defineProps<{ defaultOpen?: boolean; chevronFirst?: boolean }>(),
  { defaultOpen: false },
);

const open = ref(props.defaultOpen);
</script>

<template>
  <div class="card-flat overflow-hidden">
    <button
      class="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-[var(--surface-3)] cursor-pointer"
      :aria-expanded="open"
      @click="open = !open"
    >
      <ChevronDown
        v-if="!chevronFirst"
        class="h-3.5 w-3.5 shrink-0 transition-transform duration-150"
        :class="open ? '' : '-rotate-90'"
        style="color: var(--ink-3)"
      />
      <span class="min-w-0 flex-1"><slot name="head" /></span>
      <ChevronDown
        v-if="chevronFirst"
        class="h-3.5 w-3.5 shrink-0 transition-transform duration-150"
        :class="open ? '' : '-rotate-90'"
        style="color: var(--ink-3)"
      />
    </button>
    <div v-show="open" style="border-top: 1px solid var(--line-1)">
      <slot />
    </div>
  </div>
</template>
