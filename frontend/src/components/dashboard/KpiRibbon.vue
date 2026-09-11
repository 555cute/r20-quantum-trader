<script setup lang="ts">
/**
 * US-004 · 三所组合中枢（Tri-Venue Portfolio Hub）：
 *   聚合总权益 + 可交互三色资产分布条（OKX 蓝 / Binance 金 / Gate 翠）
 *   + 保证金占用 / 云端防线 / 多空比 等六格 KPI。
 *
 * 平权铁律：场所顺序与品牌色一律取自 venueMeta / venueAccounts store
 * （assetDistribution 恒定 3 行），组件内不再硬编码任何所的十六进制色。
 */
import { computed, onMounted, ref } from 'vue';
import { ShieldCheck } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, arrow } from '../../utils/format';
import { venueBrandHex } from '../../utils/venueMeta';
import BaseStat from '../base/BaseStat.vue';
import DataStatus from './DataStatus.vue';
import BaseSparkline from '../base/BaseSparkline.vue';

const store = useDashboardStore();
const venueStore = useVenueAccountsStore();
const { t } = useI18n();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

/* ———— 三所聚合权益与资产分布（store 恒定 3 行，未知显「—」） ———— */
const isLiveEnv = computed(() => venueStore.isLive);
const envBadgeText = computed(() => (isLiveEnv.value ? t('dash.venueAccounts.envLive') : t('dash.venueAccounts.envDemo')));

const portfolioSummary = computed(
  () => venueStore.portfolioSummary || (store.data as any)?.multi_venue_portfolio || null,
);

const totalEquityNum = computed(() => {
  const sum = portfolioSummary.value;
  if (sum && Number(sum.total_equity || 0) > 0) return Number(sum.total_equity);
  return Number(account.value.total_eq || 0);
});
const totalAggregatedEquity = computed(() => fmtNum(totalEquityNum.value, 2));

/** 三所分布行（venueAccounts store 单一事实源，恒定 okx/binance/gate 序） */
const distRows = computed(() => venueStore.assetDistribution);
const hasMultiVenue = computed(
  () => distRows.value.some((r) => r.equity !== null || r.sharePct !== null) && totalEquityNum.value > 0,
);
/** 分布条未知段：按「已报所占比之和」补中性灰，避免视觉误读为 0 */
const distUnknownPct = computed(() => {
  const known = distRows.value.reduce((s, r) => s + (r.sharePct ?? 0), 0);
  return Math.max(0, Math.min(100, Math.round((100 - known) * 10) / 10));
});

/** 聚焦所：点击分布条/图例切换，再次点击取消（纯视觉高亮，不改数据） */
const focusedVenue = ref<string | null>(null);
function toggleFocus(key: string) {
  focusedVenue.value = focusedVenue.value === key ? null : key;
}
function segOpacity(key: string): number {
  return !focusedVenue.value || focusedVenue.value === key ? 1 : 0.28;
}

/* ———— 六格 KPI ———— */
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
const floatRoi = computed(() => (posMargin.value > 0 ? (floatPnl.value / posMargin.value) * 100 : 0));

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length);
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length);

const actualMarginUsed = computed(() => {
  if (posMargin.value > 0) return posMargin.value;
  const sum = portfolioSummary.value;
  if (sum && typeof sum.margin_used === 'number') return Number(sum.margin_used);
  return Number(account.value.total_pos_margin || 0);
});
const marginUsage = computed(() =>
  totalEquityNum.value > 0 ? Math.round((actualMarginUsed.value / totalEquityNum.value) * 1000) / 10 : 0,
);

const ocoCoverage = computed(() => {
  const total = store.positions.length;
  if (!total) return { pct: 100, missing: 0 };
  const ok = store.positions.filter(
    (p: any) => p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected',
  ).length;
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

function money(v: number | null): string {
  return v === null ? t('dash.venueAccounts.unknown') : fmtNum(v, 2);
}
</script>

<template>
  <div class="card space-y-2 p-2 sm:p-2.5 xl:p-3" data-test="kpi-ribbon">
    <div class="flex items-center justify-between border-b px-1.5 sm:px-2.5 pb-2" style="border-color: var(--line-1)">
      <DataStatus />
    </div>

    <!-- ═══ 三所组合中枢：聚合总权益 + 可交互资产分布条 ═══ -->
    <div class="flex flex-col gap-2 px-1.5 sm:px-2.5 pb-2 border-b" style="border-color: var(--line-1)">
      <div class="flex flex-wrap items-end justify-between gap-x-4 gap-y-1.5">
        <div class="min-w-0">
          <div class="flex items-center gap-1.5">
            <span class="t-label">{{ t('dash.matrix.kpi.hubTitle') }}</span>
            <span class="badge badge-mono text-3xs" :class="isLiveEnv ? 'badge-down' : 'badge-up'">{{ envBadgeText }}</span>
            <span class="badge text-3xs" style="background: var(--surface-3); color: var(--ink-2)">
              {{ venueStore.reportingVenueCount }}/3 {{ t('dash.matrix.kpi.hubConnected') }}
            </span>
          </div>
          <div class="mt-0.5 flex items-baseline gap-1.5">
            <span class="num text-xl font-bold sm:text-2xl" style="color: var(--ink-strong)">{{ totalAggregatedEquity }}</span>
            <span class="t-faint text-xs">USDT</span>
            <span class="num ml-1 text-xs font-semibold" :class="todayNet >= 0 ? 'up' : 'down'">
              {{ arrow(todayNet) }} {{ fmtSigned(todayNet) }}
            </span>
          </div>
        </div>
        <BaseSparkline :values="eqSeries" :width="72" :height="28" />
      </div>

      <!-- 三色资产分布条（点击图例/色段聚焦单所） -->
      <div>
        <div class="venue-bar" role="img" :aria-label="t('dash.matrix.kpi.hubTitle')" data-test="venue-dist-bar">
          <span
            v-for="r in distRows"
            :key="r.key"
            :class="`venue-bar-${r.key}`"
            :style="{ width: `${r.sharePct ?? 0}%`, opacity: segOpacity(r.key) }"
            :title="`${r.label}: ${money(r.equity)} USDT (${r.sharePct ?? 0}%)`"
          />
          <span
            v-if="hasMultiVenue && distUnknownPct > 0"
            :style="{ width: `${distUnknownPct}%`, backgroundColor: 'var(--venue-neutral)' }"
            :title="t('dash.matrix.kpi.hubUnknown')"
          />
        </div>
        <div class="mt-1.5 grid grid-cols-3 gap-1.5">
          <button
            v-for="r in distRows"
            :key="r.key"
            type="button"
            class="flex min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-1 text-left transition-all duration-200"
            :style="{
              background: focusedVenue === r.key ? 'var(--surface-3)' : 'transparent',
              boxShadow: focusedVenue === r.key ? `inset 0 0 0 1px ${venueBrandHex(r.key)}` : 'none',
            }"
            :aria-pressed="focusedVenue === r.key"
            :title="`${r.label}: ${money(r.equity)} USDT (${r.sharePct ?? 0}%)`"
            @click="toggleFocus(r.key)"
          >
            <span class="venue-dot" :class="`venue-dot-${r.key}`" />
            <span class="min-w-0 flex-1">
              <span class="block truncate text-2xs font-bold" :style="{ color: focusedVenue === r.key ? 'var(--ink-strong)' : 'var(--ink-2)' }">
                {{ r.label }}
              </span>
              <span class="num block truncate text-2xs" style="color: var(--ink-3)">{{ money(r.equity) }}</span>
            </span>
            <span class="num shrink-0 text-2xs font-semibold" style="color: var(--ink-1)">
              {{ r.sharePct === null ? '—' : `${r.sharePct}%` }}
            </span>
          </button>
        </div>
      </div>
    </div>

    <!-- ═══ 六格 KPI ═══ -->
    <div class="grid grid-cols-2 gap-1.5 sm:gap-2 md:grid-cols-3 xl:grid-cols-6 xl:gap-0">
      <BaseStat
        :label="`[${envBadgeText}] ${t('dash.matrix.kpi.equity')}`"
        :value="totalAggregatedEquity"
        :hint="t('dash.matrix.kpi.equityTip')"
      >
        <template #extra>
          <span class="num text-xs font-semibold" :class="todayNet >= 0 ? 'up' : 'down'">
            {{ arrow(todayNet) }} {{ fmtSigned(todayNet) }}
          </span>
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
