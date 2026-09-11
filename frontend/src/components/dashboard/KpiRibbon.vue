<script setup lang="ts">
/**
 * KPI 监控中枢（Tri-Venue Portfolio Hub）
 * 聚合呈现 OKX / Binance / Gate 三所组合总权益、交互式三色资产分布条与六大风控指标。
 */
import { computed, onMounted, ref } from 'vue';
import { ShieldCheck, Layers, TrendingUp, AlertCircle } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, arrow } from '../../utils/format';
import { VENUE_KEYS, venueLabel, venueBrandColor } from '../../utils/venueMeta';
import BaseStat from '../base/BaseStat.vue';
import DataStatus from './DataStatus.vue';
import BaseSparkline from '../base/BaseSparkline.vue';

const store = useDashboardStore();
const venueStore = useVenueAccountsStore();
const { t } = useI18n();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

/** 多所组合总权益与保证金占用 */
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

const totalAggregatedEquity = computed(() => fmtNum(totalEquityNum.value, 2));

/** 资产分布（三所平权对等） */
const distOkx = computed(() => Number(portfolioSummary.value?.asset_distribution?.okx?.share_pct || 0));
const distBinance = computed(() => Number(portfolioSummary.value?.asset_distribution?.binance?.share_pct || 0));
const distGate = computed(() => Number(portfolioSummary.value?.asset_distribution?.gate?.share_pct || 0));

const okxEquity = computed(() => fmtNum(Number(portfolioSummary.value?.asset_distribution?.okx?.equity ?? 0), 2));
const binanceEquity = computed(() => fmtNum(Number(portfolioSummary.value?.asset_distribution?.binance?.equity ?? 0), 2));
const gateEquity = computed(() => fmtNum(Number(portfolioSummary.value?.asset_distribution?.gate?.equity ?? 0), 2));

const distHint = computed(() => {
  if (!hasMultiVenue.value) return t('dash.matrix.kpi.equityTip');
  return `多所对等聚合 · OKX: ${distOkx.value}% · Binance: ${distBinance.value}% · Gate: ${distGate.value}%`;
});

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

/** 实际持仓已占用保证金 */
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

/* 14 日净值走势 */
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
  <div class="card space-y-2 p-2 sm:p-2.5 xl:p-3">
    <!-- 顶层状态栏 -->
    <div class="flex items-center justify-between border-b px-1 sm:px-2 pb-2" style="border-color: var(--line-1)">
      <DataStatus />
    </div>

    <!-- 六格核心指标卡片矩阵 -->
    <div class="grid grid-cols-2 gap-1.5 sm:gap-2 md:grid-cols-3 xl:grid-cols-6 xl:gap-0">
      <BaseStat
        :label="`[${envBadgeText}] ${hasMultiVenue ? '组合总权益 (U)' : t('dash.matrix.kpi.equity')}`"
        :value="totalAggregatedEquity"
        :hint="distHint"
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
        hint="多空持仓比 (Long / Short)"
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
