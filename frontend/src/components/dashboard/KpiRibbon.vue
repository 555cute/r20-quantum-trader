<script setup lang="ts">
/**
 * KpiRibbon.vue · DeepSeek Harness 风格核心指标仪表盘
 * 纯净低饱和黑白/深灰主题，分层卡片结构，呈现多所总权益、走势、浮亏与防线
 */
import { computed, onMounted, ref } from 'vue';
import { ShieldCheck, Layers } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, arrow } from '../../utils/format';
import { venueColor } from '../../utils/venueMeta';
import BaseStat from '../base/BaseStat.vue';
import BaseSparkline from '../base/BaseSparkline.vue';

const store = useDashboardStore();
const venueStore = useVenueAccountsStore();
const { t } = useI18n();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

const isLiveEnv = computed(() => venueStore.environment === 'live');
const envBadgeText = computed(() => (isLiveEnv.value ? t('dash.venueAccounts.envLive') : t('dash.venueAccounts.envDemo')));

/* ── 资金环境轴：所有权益来源都必须先过这道闸 ────────────────────────────────
 * 修的是这个 bug：把面板从「模拟盘」切到「实盘」后，总权益显示的却是
 * OKX **模拟盘**的余额（用户实测：OKX 只配了模拟盘 key，切到实盘后
 * 主页总权益仍显示 5,299.69 —— 正是模拟盘余额）。
 *
 * 成因是**回退链没有环境轴**：
 *   1. `venueStore.portfolioSummary`（`/api/v1/venue_accounts`，带 environment）
 *      在实盘档下正确地聚合为 0（三所均 unavailable，OKX 被跨档闸拒绝）；
 *   2. 于是 `totalEquityNum` 回退到 `account.total_eq` —— 那是 `/api/all` 里的
 *      **OKX 单所快照**，不区分档位，取的正是后端 OKX 当前档（模拟盘）的钱。
 *
 * 两处都要闸：聚合摘要按 `environment` 比对，单所快照按 `account.environment`
 * （后端 `dashboard_cache.py` 确实按 OKX 实际档位写入该字段）比对。
 * 两边都不匹配时**显示「—」**——宁可未知，也不拿另一档的钱顶到这一档头上。
 * ────────────────────────────────────────────────────────────────────────── */
const selectedEnv = computed(() => String(venueStore.environment || 'demo').trim().toLowerCase());

function envOf(o: any): string {
  return String(o?.environment || '').trim().toLowerCase();
}

/** 聚合结果只在「数据档位 === 当前所选档位」时可用（换挡瞬间的旧数据也不算）。 */
const scopedSummary = computed(() => {
  const fromEndpoint = venueStore.portfolioSummary;
  if (fromEndpoint && envOf(fromEndpoint) === selectedEnv.value) return fromEndpoint;
  const fromPayload = (store.data as any)?.multi_venue_portfolio;
  if (fromPayload && envOf(fromPayload) === selectedEnv.value) return fromPayload;
  return null;
});

/** 单所 OKX 快照只在它自己那一档与所选一致时才可回落。 */
const okxSnapshotInScope = computed(() => envOf(account.value) === selectedEnv.value);

const portfolioSummary = computed(() => scopedSummary.value);
const hasMultiVenue = computed(() => {
  const sum = scopedSummary.value;
  return !!sum && Number(sum.total_equity || 0) > 0;
});

/** null = 该档位没有任何可读账户（不是 0，也不是别的档的钱）。 */
const totalEquityNum = computed<number | null>(() => {
  const sum = scopedSummary.value;
  const agg = Number(sum?.total_equity || 0);
  if (agg > 0) return agg;
  if (okxSnapshotInScope.value) {
    const one = Number(account.value.total_eq || 0);
    if (one > 0) return one;
  }
  return null;
});

const totalAggregatedEquity = computed(() =>
  totalEquityNum.value === null ? t('dash.venueAccounts.unknown') : fmtNum(totalEquityNum.value, 2),
);

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

const actualMarginUsed = computed(() => {
  if (posMargin.value > 0) return posMargin.value;
  const sum = portfolioSummary.value;
  if (sum && typeof sum.margin_used === 'number') return Number(sum.margin_used);
  return Number(account.value.total_pos_margin || 0);
});

const marginUsage = computed(() => {
  const eq = totalEquityNum.value;
  if (eq !== null && eq > 0) {
    return Math.round((actualMarginUsed.value / eq) * 1000) / 10;
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
  <div class="dsh-card">
    <!-- 头部：多所组合分布与状态 -->
    <header
      v-if="hasMultiVenue"
      class="dsh-card-header text-3xs font-medium"
    >
      <div class="flex items-center gap-2">
        <span class="flex items-center gap-1.5 font-bold" style="color: var(--ink-strong)">
          <Layers class="h-3.5 w-3.5 text-[var(--accent)]" />
          {{ t('dash.matrix.kpi.multiEquity') }}
        </span>
        <span class="hidden md:inline font-mono font-semibold" style="color: var(--ink-1)">{{ totalAggregatedEquity }} U</span>
        <!-- 档位徽标：这是「这几个数属于哪一档」的唯一可见出口。
             缺了它，读者只能靠记忆分辨手上这串钱是实盘还是模拟盘。 -->
        <span
          class="rounded px-1.5 py-0.5 border text-3xs font-mono"
          :style="isLiveEnv
            ? 'background-color: var(--up-bg); border-color: var(--up-line); color: var(--up)'
            : 'background-color: var(--warn-bg); border-color: var(--warn-line); color: var(--warn)'"
          data-test="kpi-env-badge"
        >
          {{ envBadgeText }}
        </span>
        <span
          class="rounded px-1.5 py-0.5 border text-3xs font-mono"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2)"
        >
          {{ t('dash.matrix.kpi.venuesConnected', undefined, { n: portfolioSummary?.active_venues_count }) }}
        </span>
      </div>

      <!-- 资产份额条 -->
      <div class="hidden sm:flex items-center gap-3 font-mono">
        <span class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: venueColor('okx') }" />
          <span style="color: var(--ink-2)">OKX</span>
          <span style="color: var(--ink-1)">{{ distOkx }}%</span>
        </span>
        <span class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: venueColor('binance') }" />
          <span style="color: var(--ink-2)">Binance</span>
          <span style="color: var(--ink-1)">{{ distBinance }}%</span>
        </span>
        <span class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: venueColor('gate') }" />
          <span style="color: var(--ink-2)">Gate</span>
          <span style="color: var(--ink-1)">{{ distGate }}%</span>
        </span>
      </div>
    </header>

    <!-- 6 个核心指标单元格 -->
    <div class="grid grid-cols-2 gap-px bg-[var(--line-1)] sm:grid-cols-3 xl:grid-cols-6">
      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.comboEquity')"
          :value="totalAggregatedEquity"
          :hint="
            totalEquityNum === null
              ? t('dash.matrix.kpi.comboEquityEmpty')
              : hasMultiVenue
                ? `${envBadgeText} ${t('dash.matrix.kpi.comboEquityTip')}`
                : t('dash.matrix.kpi.equityTip')
          "
        >
          <!-- 今日盈亏与净值走势取自**后端交易轴**（OKX 台账），而本格的总权益是
               按所选档位聚合的。两者档位不一致时（例：切到实盘但只有模拟盘 key）
               把它们并排放在同一个「组合总权益」格子里，等于又把另一档的钱画回来。
               总权益未知时这一行整块不渲染；今日盈亏本身在下方的「今日已实现」有专格。 -->
          <template v-if="totalEquityNum !== null" #extra>
            <div class="flex items-center gap-2 mt-1">
              <span class="num text-xs font-semibold" :class="todayNet >= 0 ? 'up' : 'down'">
                {{ arrow(todayNet) }} {{ fmtSigned(todayNet) }}
              </span>
              <BaseSparkline :values="eqSeries" :width="48" :height="18" />
            </div>
          </template>
        </BaseStat>
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.todayPnl')"
          :value="fmtSigned(todayNet)"
          :delta="todayTrades ? `${todayTrades} ${t('common.unitCount')} · ${todayWinRate}%` : undefined"
          :delta-tone="todayWinRate === null ? 'muted' : todayWinRate >= 50 ? 'up' : 'down'"
          :hint="t('dash.matrix.kpi.todayTip')"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.floatPnl')"
          :value="fmtSigned(floatPnl)"
          :delta="store.positions.length ? `(${fmtPct(floatRoi)})` : '--'"
          :delta-tone="floatPnl >= 0 ? 'up' : 'down'"
          :hint="t('dash.matrix.kpi.floatTip')"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.ls')"
          :value="`${longCount} / ${shortCount}`"
          hint="L / S"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.margin')"
          :value="`${fmtNum(marginUsage, 1)}%`"
          :delta="actualMarginUsed > 0 ? `${fmtNum(actualMarginUsed, 2)} U` : '0.00 U'"
          :delta-tone="marginUsage > 70 ? 'down' : marginUsage > 30 ? 'warn' : 'muted'"
          :hint="t('dash.matrix.kpi.marginTip')"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.oco')"
          :value="`${ocoCoverage.pct}%`"
          :delta="ocoCoverage.missing ? t('dash.matrix.kpi.missN', undefined, { n: ocoCoverage.missing }) : t('dash.matrix.kpi.allCovered')"
          :delta-tone="ocoCoverage.pct === 100 ? 'up' : 'warn'"
          :hint="t('dash.matrix.kpi.ocoTip')"
        >
          <template #extra>
            <ShieldCheck class="h-4 w-4 shrink-0 mt-1" :style="{ color: ocoCoverage.pct === 100 ? 'var(--up)' : 'var(--warn)' }" />
          </template>
        </BaseStat>
      </div>
    </div>
  </div>
</template>
