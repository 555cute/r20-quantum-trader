<script setup lang="ts">
/**
 * DataStatus.vue · DeepSeek Harness 风格引擎与数据健康状态指示器
 * 熔断检测、引擎在线状态、调度周期与实时数据更新心跳
 */
import { computed } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import TimeAgo from '../base/TimeAgo.vue';

const store = useDashboardStore();
const { t } = useI18n();

const health = computed(() => (store.data as any)?.data_health || {});
const breaker = computed(() => (store.data as any)?.state_snapshot?.circuit_breaker || {});

const engine = computed(() => {
  if (!store.isConnected) return { dot: 'error', cls: 'down', label: t('dash.shell.connLost') };
  const s = String(health.value.status || '').toUpperCase();
  if (s === 'LIVE') return { dot: 'active', cls: '', label: t('status.live') };
  if (s === 'PARTIAL') return { dot: 'warn', cls: '', label: t('status.attention') };
  if (s === 'NOT_READY') return { dot: 'warn', cls: '', label: t('common.notConfigured') };
  if (s === 'STALE' || store.isStale) return { dot: 'warn', cls: '', label: t('status.stale') };
  return { dot: 'error', cls: '', label: t('status.offline') };
});
const updated = computed(() => store.lastUpdated);
const cycleMinutes = computed<number | null>(() => {
  const v = Number((health.value as any)?.cycle_minutes);
  return Number.isFinite(v) && v > 0 ? v : null;
});

const fundEnv = computed(() => {
  const d = store.data as any;
  const env = String(d?.environment || d?.account?.environment || '').toLowerCase();
  if (!env) return null;
  const isMixed = Boolean(d?.is_mixed_environment);
  if (isMixed) {
    const vEnvs = (d?.venue_environments as Record<string, string>) || {};
    const details = Object.entries(vEnvs)
      .map(([k, v]) => `${k.toUpperCase()}: ${String(v).toUpperCase()}`)
      .join(' · ');
    return {
      dot: 'error',
      cls: 'text-[var(--down)] border-[var(--down-line)] bg-[var(--down-bg)]',
      label: t('dash.shell.mixedEnv'),
      title: `${t('dash.shell.mixedBannerTip')}${t('common.punct.colon')}${details}`,
    };
  }
  if (env === 'live') {
    return {
      dot: 'active',
      cls: 'text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]',
      label: t('dash.shell.live'),
      title: t('dash.shell.liveBannerTip'),
    };
  }
  return {
    dot: 'warn',
    cls: 'text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]',
    label: t('dash.shell.demo'),
    title: t('dash.shell.demoBannerTip'),
  };
});
</script>

<template>
  <div class="flex items-center gap-1.5">
    <!-- 资金环境徽章：明晰区分实盘/模拟盘/混合环境，杜绝用户混淆 -->
    <span
      v-if="fundEnv"
      class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-3xs font-mono border"
      :class="fundEnv.cls"
      :title="fundEnv.title"
      :aria-label="fundEnv.title"
      tabindex="0"
    >
      <span class="dsh-status-dot" :class="fundEnv.dot" aria-hidden="true" />
      <span class="font-semibold">{{ fundEnv.label }}</span>
    </span>

    <!-- 批 73：这是全站**唯一**呈现「三级熔断已生效」的地方，而熔断原因此前只挂在
         :title 上 —— 一个不可聚焦的 <span>，键盘与触摸根本够不到。
         熔断是硬性安全停机，操作员必须能当场知道为什么被停。
         修法三重：① role="alert" 让熔断发生时被读屏器立刻通报；
         ② tabindex="0" + aria-label 让键盘可达、读屏器读得到完整原因；
         ③ 桌面端把原因**直接显示出来**（截断），不必悬停。 -->
    <span
      v-if="breaker.active"
      class="dsh-pill text-[var(--down)] border-[var(--down-line)] bg-[var(--down-bg)]"
      role="alert"
      tabindex="0"
      :title="breaker.reason"
      :aria-label="breaker.reason
        ? `${t('dash.shell.breaker')}${t('common.punct.colon')}${breaker.reason}`
        : t('dash.shell.breaker')"
    >
      <span class="dsh-status-dot error" aria-hidden="true" />
      {{ t('dash.shell.breaker') }}
    </span>
    <span
      v-if="breaker.active && breaker.reason"
      class="hidden max-w-[18rem] truncate align-middle text-3xs md:inline-block"
      style="color: var(--ink-3)"
      aria-hidden="true"
    >
      {{ breaker.reason }}
    </span>

    <div
      class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-3xs font-mono border"
      style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2)"
    >
      <span class="dsh-status-dot" :class="engine.dot" aria-hidden="true" />
      <span class="font-medium" style="color: var(--ink-1)">{{ engine.label }}</span>
      <span v-if="cycleMinutes" class="text-[var(--ink-3)]">· {{ cycleMinutes }}M</span>
      <span v-if="updated" class="hidden lg:inline text-[var(--ink-3)]">
        · <TimeAgo :time="updated" />
      </span>
    </div>
  </div>
</template>
