<script setup lang="ts">
/** KPI 带：单行六格，每格必带副值/走势锚点；绿红只作文字色 */
import { computed, onMounted, ref } from 'vue';
import { ShieldCheck } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, arrow } from '../../utils/format';
import BaseStat from '../base/BaseStat.vue';
import BaseSparkline from '../base/BaseSparkline.vue';

const store = useDashboardStore();
const { t } = useI18n();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

const equity = computed(() => fmtNum(Number(account.value.total_eq || 0), 2));
const todayNet = computed(() => Number(today.value.net_realized ?? today.value.total_pnl ?? 0));
const todayTrades = computed(() => Number(today.value.win_trades ?? 0) + Number(today.value.loss_trades ?? 0));
const todayWinRate = computed(() => {
  const w = Number(today.value.win_trades ?? 0);
  const n = todayTrades.value;
  return n > 0 ? Math.round((w / n) * 100) : null;
});

const floatPnl = computed(() => Number(account.value.pos_upl_total ?? account.value.upl ?? 0));
const posMargin = computed(() =>
  store.positions.reduce((s, p: any) => s + (Number(p.margin_usdt ?? p.margin ?? 0) || 0), 0),
);
const floatRoi = computed(() =>
  posMargin.value > 0 ? (floatPnl.value / posMargin.value) * 100 : 0,
);

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length);
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length);

const marginUsage = computed(() => Number(account.value.margin_usage_pct || 0));

const ocoCoverage = computed(() => {
  const total = store.positions.length;
  if (!total) return { pct: 100, missing: 0 };
  const ok = store.positions.filter((p: any) => p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected').length;
  return { pct: Math.round((ok / total) * 100), missing: total - ok };
});

/* 14 日净值走势（一次性拉取，失败静默） */
const eqSeries = ref<number[]>([]);
onMounted(async () => {
  try {
    const r = await fetch('/api/v1/equity_history?days=14');
    const d = await r.json();
    eqSeries.value = (d.days || []).map((x: any) => Number(x.equity)).filter((n: number) => Number.isFinite(n));
  } catch {
    /* sparkline optional */
  }
});
</script>

<template>
  <div class="card grid grid-cols-2 gap-2 p-2 md:grid-cols-3 xl:grid-cols-6 xl:gap-0 xl:p-0">
    <BaseStat
      :label="t('dash.matrix.kpi.equity')"
      :value="equity"
      :hint="t('dash.matrix.kpi.equityTip')"
     
    >
      <template #extra>
        <span class="num text-xs font-semibold" :class="todayNet >= 0 ? 'up' : 'down'">
          {{ arrow(todayNet) }} {{ fmtSigned(todayNet) }}
        </span>
        <BaseSparkline :values="eqSeries" :width="48" :height="20" />
      </template>
    </BaseStat>

    <BaseStat
      :label="t('dash.matrix.kpi.todayPnl')"
      :value="fmtSigned(todayNet)"
      :delta="todayTrades ? `${todayTrades} ${t('common.unitCount')} · ${todayWinRate}%` : undefined"
      :delta-tone="todayNet >= 0 ? 'up' : 'down'"
      :hint="t('dash.matrix.kpi.todayTip')"
     
    />

    <BaseStat
      :label="t('dash.matrix.kpi.floatPnl')"
      :value="fmtSigned(floatPnl)"
      :delta="store.positions.length ? `(${fmtPct(floatRoi)})` : '--'"
      :delta-tone="floatPnl >= 0 ? 'up' : 'down'"
      :hint="t('dash.matrix.kpi.floatTip')"
     
    />

    <BaseStat
      :label="t('dash.matrix.kpi.ls')"
      :value="`${longCount} / ${shortCount}`"
      hint="L / S"
     
    />

    <BaseStat
      :label="t('dash.matrix.kpi.margin')"
      :value="`${fmtNum(marginUsage, 1)}%`"
      :delta="posMargin > 0 ? `${fmtNum(posMargin, 0)} U` : undefined"
      delta-tone="muted"
      :hint="t('dash.matrix.kpi.marginTip')"
     
    />

    <BaseStat
      :label="t('dash.matrix.kpi.oco')"
      :value="`${ocoCoverage.pct}%`"
      :delta="ocoCoverage.missing ? t('dash.matrix.kpi.missN', undefined, { n: ocoCoverage.missing }) : t('dash.matrix.kpi.allCovered')"
      :delta-tone="ocoCoverage.pct === 100 ? 'up' : 'warn'"
      :hint="t('dash.matrix.kpi.ocoTip')"
    >
      <template #extra>
        <ShieldCheck class="h-4 w-4 shrink-0" :style="{ color: ocoCoverage.pct === 100 ? 'var(--up)' : 'var(--warn)' }" />
      </template>
    </BaseStat>
  </div>
</template>
