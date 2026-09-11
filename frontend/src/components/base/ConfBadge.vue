<script setup lang="ts">
/**
 * 置信档位：未校准概率不展示裸数字 —— 高/中/低 + 三段强度条，原值进 title。
 * 色仅作辅助识别，文字永远在场（色盲安全铁律）。
 */
import { computed } from 'vue';
import { confTier } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ value: number | null | undefined }>();
const { t } = useI18n();

const tier = computed(() => confTier(props.value));
/** 点亮段数：high=3 / mid=2 / low=1 */
const lit = computed(() => ({ high: 3, mid: 2, low: 1 })[tier.value?.tier || 'low'] || 1);
const badgeCls = computed(() => {
  const v = tier.value?.tier;
  return v === 'high' ? 'badge badge-up' : v === 'mid' ? 'badge badge-warn' : 'badge';
});
</script>

<template>
  <span v-if="tier" :class="badgeCls" :title="`${Math.round((Number(value) || 0) * 100) / 100}`">
    <span class="conf-bars" aria-hidden="true">
      <i v-for="n in 3" :key="n" :class="n <= lit ? 'is-lit' : ''" />
    </span>
    {{ t(`common.conf.${tier.tier}`) }}
  </span>
  <span v-else class="t-faint">--</span>
</template>

<style scoped>
.conf-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 1.5px;
  height: 9px;
  margin-inline-end: 1px;
}
.conf-bars i {
  width: 2.5px;
  border-radius: 1px;
  background-color: currentColor;
  opacity: 0.22;
  transition: opacity var(--dur-fast) var(--ease-out);
}
.conf-bars i:nth-child(1) { height: 4px; }
.conf-bars i:nth-child(2) { height: 6.5px; }
.conf-bars i:nth-child(3) { height: 9px; }
.conf-bars i.is-lit { opacity: 1; }
</style>
