<script setup lang="ts">
/**
 * 北京时间契约钟：全站财务基准 = Asia/Shanghai (UTC+8) 自然日。
 * 两行紧凑栈：秒级走针（等宽不抖）+ 日期与基准时区微标签。
 */
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { fmtClock, fmtDate } from '../../utils/format';

const now = ref(new Date());
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => { timer = setInterval(() => { now.value = new Date(); }, 1000); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <time class="clock" :datetime="now.toISOString()" title="全站时间基准：Asia/Shanghai (UTC+8) 自然日">
    <span class="clock-line">
      <span class="dot dot-live" aria-hidden="true" />
      <span class="num clock-time">{{ fmtClock(now) }}</span>
    </span>
    <span class="num clock-meta">{{ fmtDate(now) }} · 北京 UTC+8</span>
  </time>
</template>

<style scoped>
.clock {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: center;
  line-height: 1.15;
  flex-shrink: 0;
}
.clock-line { display: inline-flex; align-items: center; gap: 4px; }
.clock-time {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ink-1);
  font-variant-numeric: tabular-nums slashed-zero;
  letter-spacing: -0.01em;
}
.clock-meta {
  font-size: var(--text-3xs);
  color: var(--ink-3);
  letter-spacing: 0.02em;
  white-space: nowrap;
}
.clock .dot { width: 5px; height: 5px; }
</style>
