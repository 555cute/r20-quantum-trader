<script setup lang="ts">
/** 置信档位：未校准概率不展示裸数字 —— 高/中/低 + 色点，原值进 title */
import { computed } from 'vue';
import { confTier } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ value: number | null | undefined }>();
const { t } = useI18n();

const tier = computed(() => confTier(props.value));
const dot = computed(() => {
  const v = tier.value?.tier;
  return v === 'high' ? 'var(--up)' : v === 'mid' ? 'var(--warn)' : 'var(--ink-3)';
});
</script>

<template>
  <span v-if="tier" class="badge" :title="`${Math.round((Number(value) || 0) * 100) / 100}`">
    <span class="dot" :style="{ backgroundColor: dot }" />
    {{ t(`common.conf.${tier.tier}`) }}
  </span>
  <span v-else class="t-faint">--</span>
</template>
