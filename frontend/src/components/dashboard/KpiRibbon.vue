<script setup lang="ts">
/** KPI 带：单行六格，每格必带副值/走势锚点；绿红只作文字色 */
import { computed, onMounted, ref } from 'vue';
import { ShieldCheck } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, arrow } from '../../utils/format';
import BaseStat from '../base/BaseStat.vue';
import DataStatus from './DataStatus.vue';
import BaseSparkline from '../base/BaseSparkline.vue';

const store = useDashboardStore();
const venueStore = useVenueAccountsStore();
const { t } = useI18n();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

const equity = computed(() => fmtNum(Number(account.value.total_eq || 0), 2));

/** US-007 / 问题1修复 · 多所组合总权益与保证金占用严密对账 */
const isLiveEnv = computed(() => venueStore.environment === 'live');
const envBadgeText = computed(() => (isLiveEnv.value ? '实盘' : '模拟'));

const portfolioSummary = computed(() => venueStore.portfolioSummary || (store.data as any)?.multi_venue_portfolio || null);
const hasMultiVenue = computed(() => {
  const sum = portfolioSummary.value;
  return !!sum && Number(sum.total_equity || 0) > 0;
});

const totalEquityNum = computed(() => {
  const sum = portfolioSummary.value;
  if (sum && Number(sum.total_equity || 0) > 0) return Number(sum.total_equity);
  return Number(account.value.total_eq || 0);
});

const totalAggregatedEquity = computed(() => {
  return fmtNum(totalEquityNum.value, 2);
});

const distOkx = computed(() => Number(portfolioSummary.value?.asset_distribution?.okx?.share_pct || 0));
const distBinance = computed(() => Number(portfolioSummary.value?.asset_distribution?.binance?.share_pct || 0));
const distGate = computed(() => Number(portfolioSummary.value?.asset_distribution?.gate?.share_pct || 0));

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

/** 真实保证金占用：严格按实际持仓已占用保证金计算，绝不误用未划转资金差额 */
const actualMarginUsed = computed(() => {
  if (posMargin.value > 0) return posMargin.value;
  const sum = portfolioSummary.value;
  if (sum && typeof sum.margin_used === 'number') return Number(sum.margin_used);
  return Number(account.value.total_pos_margin || 0);
});

const marginUsage = computed(() => {
  if (totalEquityNum.value > 0) {
    return Math.round((actualMarginUsed.value / totalEquityNum.value) * 1000) / 10;
  }
  return 0;
});

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
  <div class="card space-y-2.5 p-2.5 xl:p-3">
    <div class="flex items-center justify-between border-b px-2.5 pb-2" style="border-color: var(--line-1)"><DataStatus /></div>

    <!-- US-007 · 多所组合总权益与资产分配条 (Asset Allocation Bar) -->
    <div v-if="hasMultiVenue" class="flex flex-col gap-1.5 px-2.5 pb-1 border-b" style="border-color: var(--line-1)">
      <div class="flex items-center justify-between text-2xs" style="color: var(--ink-3)">
        <div class="flex items-center gap-2">
          <span class="font-bold text-xs" style="color: var(--ink-1)">多所组合总权益</span>
          <span class="num font-bold text-xs" style="color: var(--ink-strong)">$ {{ totalAggregatedEquity }} U</span>
          <span class="text-3xs" style="color: var(--ink-faint)">({{ portfolioSummary?.active_venues_count }} 所已接入)</span>
        </div>
        <div class="flex items-center gap-3 num text-3xs">
          <span class="flex items-center gap-1"><span class="inline-block w-1.5 h-1.5 rounded-full" style="background-color: #3880ff"></span> OKX {{ distOkx }}%</span>
          <span class="flex items-center gap-1"><span class="inline-block w-1.5 h-1.5 rounded-full" style="background-color: #f3ba2f"></span> Binance {{ distBinance }}%</span>
          <span class="flex items-center gap-1"><span class="inline-block w-1.5 h-1.5 rounded-full" style="background-color: #00be98"></span> Gate {{ distGate }}%</span>
        </div>
      </div>
      <!-- 彩色资产分配横条 -->
      <div class="flex h-1.5 w-full overflow-hidden rounded-full" style="background-color: var(--surface-3)">
        <div
          v-if="distOkx > 0"
          :style="{ width: `${distOkx}%`, backgroundColor: '#3880ff' }"
          class="transition-all duration-300"
          :title="`OKX: ${portfolioSummary?.asset_distribution?.okx?.equity ?? 0} U (${distOkx}%)`"
        />
        <div
          v-if="distBinance > 0"
          :style="{ width: `${distBinance}%`, backgroundColor: '#f3ba2f' }"
          class="transition-all duration-300"
          :title="`Binance: ${portfolioSummary?.asset_distribution?.binance?.equity ?? 0} U (${distBinance}%)`"
        />
        <div
          v-if="distGate > 0"
          :style="{ width: `${distGate}%`, backgroundColor: '#00be98' }"
          class="transition-all duration-300"
          :title="`Gate: ${portfolioSummary?.asset_distribution?.gate?.equity ?? 0} U (${distGate}%)`"
        />
      </div>
    </div>

    <div class="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6 xl:gap-0">
    <BaseStat
      :label="hasMultiVenue ? `[${envBadgeText}] 组合总权益 (U)` : `[${envBadgeText}] ${t('dash.matrix.kpi.equity')}`"
      :value="totalAggregatedEquity"
      :hint="hasMultiVenue ? `${envBadgeText}多所聚合权益` : t('dash.matrix.kpi.equityTip')"
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
      :delta="actualMarginUsed > 0 ? `${fmtNum(actualMarginUsed, 2)} U` : '0.00 U'"
      :delta-tone="marginUsage > 70 ? 'down' : marginUsage > 30 ? 'warn' : 'muted'"
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
  </div>
</template>
