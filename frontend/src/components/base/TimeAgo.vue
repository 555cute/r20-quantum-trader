<script setup lang="ts">
/** 相对时间显示（走 i18n），30s 心跳刷新；绝对时间进 title */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { fmtDateTime } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ time: string | number | Date }>();
const { t } = useI18n();
const now = ref(Date.now());
let timer = 0;

onMounted(() => {
  timer = window.setInterval(() => (now.value = Date.now()), 30_000);
});
onBeforeUnmount(() => window.clearInterval(timer));

const text = computed(() => {
  const ts = props.time instanceof Date ? props.time : new Date(props.time);
  const diff = Math.max(0, now.value - ts.getTime());
  if (Number.isNaN(ts.getTime())) return String(props.time);
  const s = Math.floor(diff / 1000);
  if (s < 5) return t('time.justNow');
  if (s < 60) return t('time.secondsAgo', undefined, { n: s });
  const m = Math.floor(s / 60);
  if (m < 60) return t('time.minutesAgo', undefined, { n: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t('time.hoursAgo', undefined, { n: h });
  return t('time.daysAgo', undefined, { n: Math.floor(h / 24) });
});
const abs = computed(() => fmtDateTime(props.time));
</script>

<template>
  <time class="t-faint whitespace-nowrap" :title="abs">{{ text }}</time>
</template>
