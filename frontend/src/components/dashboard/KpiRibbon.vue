<script setup lang="ts">
/**
 * 4 单元独立 Bento 资产控制舱（Bento Top HUD Ribbon）
 * 彻底消除单所 OKX 数据偏置，以【三所对等聚合多所总资产 (multi_venue_portfolio)】为唯一事实源：
 * 1. 多所组合总权益（OKX + Binance + Gate 聚合权益、三所总可用、三所总本金、综合保证金占用率与能量条）
 * 2. 基准累计收益（多所历史累计净收益、综合净收益率、夏普锚定、策略基线）
 * 3. 今日已结盈亏（全所今日资金费、手续费、胜率、成交笔数、盈亏比）
 * 4. 当前持仓浮盈（多所持仓占用本金、名义敞口、多空分布、OCO防线）
 */
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { Wallet, TrendingUp, Zap, ShieldCheck } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { fmtNum } from '../../utils/format';
import { APP_VERSION } from '../../config/version';

const router = useRouter();
const store = useDashboardStore();
const venueStore = useVenueAccountsStore();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

// 三所对等聚合多所投资组合总资产快照（/api/all multi_venue_portfolio）
const mvp = computed(() => store.multiVenuePortfolio || (store.data as any)?.multi_venue_portfolio || null);
const hasMvp = computed(() => mvp.value && Number(mvp.value.total_equity || 0) > 0);

// ==========================================
// Card 1: 三所对等组合总权益 (OKX + Binance + Gate)
// ==========================================
const totalEqNum = computed<number | null>(() => {
  if (hasMvp.value) return Number(mvp.value.total_equity);
  const sum = venueStore.portfolioSummary;
  if (sum && Number(sum.total_equity || 0) > 0) return Number(sum.total_equity);
  if (account.value.total_eq != null && account.value.total_eq !== '') {
    const val = Number(account.value.total_eq);
    return Number.isFinite(val) ? val : null;
  }
  return null;
});
const totalEqStr = computed(() => (totalEqNum.value !== null ? fmtNum(totalEqNum.value, 2) : '--'));

// 三所总可用资金 (非 OKX 单所)
const availEqStr = computed(() => {
  if (hasMvp.value && mvp.value.total_available != null) {
    return fmtNum(Number(mvp.value.total_available), 2);
  }
  const av = account.value.avail_eq ?? account.value.available;
  if (av != null && av !== '') {
    const val = Number(av);
    return Number.isFinite(val) ? fmtNum(val, 2) : '--';
  }
  return '--';
});

// 三所组合初始总本金 (3 所各 5000 = 15000，或由后端三所汇总)
const initialCapStr = computed(() => {
  if (hasMvp.value) {
    const activeCount = Number(mvp.value.active_venues_count || 3);
    const perCap = Number(account.value.initial_capital || 5000);
    return fmtNum(perCap * activeCount, 2);
  }
  const cap = account.value.initial_capital;
  if (cap != null && cap !== '') {
    const val = Number(cap);
    return Number.isFinite(val) ? fmtNum(val, 2) : '--';
  }
  return '--';
});

// 三所真实保证金占用与占用率
const actualPosMargin = computed(() => {
  if (hasMvp.value && mvp.value.margin_used != null) {
    return Number(mvp.value.margin_used);
  }
  const sum = store.positions.reduce((s, p: any) => s + (Number(p.margin_usdt ?? p.margin ?? 0) || 0), 0);
  if (sum > 0) return sum;
  const accMargin = Number(account.value.total_pos_margin || 0);
  return Number.isFinite(accMargin) ? accMargin : 0;
});

const marginUsagePct = computed(() => {
  if (hasMvp.value && mvp.value.utilization_pct != null) {
    return Math.min(100, Math.max(0, Math.round(Number(mvp.value.utilization_pct) * 10) / 10));
  }
  if (totalEqNum.value && totalEqNum.value > 0 && actualPosMargin.value > 0) {
    return Math.min(100, Math.round((actualPosMargin.value / totalEqNum.value) * 1000) / 10);
  }
  return 0;
});

const isLive = computed(() => venueStore.environment === 'live' || mvp.value?.environment === 'live');
const prodBadge = computed(() => (isLive.value ? '3所 · PROD' : '3所 · DEMO'));

// ==========================================
// Card 2: 真实基准累计收益
// ==========================================
const cumPnlNum = computed<number | null>(() => {
  const p = account.value.cum_net_pnl ?? today.value.total_pnl;
  if (p != null && p !== '') {
    const val = Number(p);
    return Number.isFinite(val) ? val : null;
  }
  return null;
});

const cumRoiNum = computed<number | null>(() => {
  const r = account.value.cum_roi_pct;
  if (r != null && r !== '') {
    const val = Number(r);
    return Number.isFinite(val) ? val : null;
  }
  return null;
});

const sharpeRatio = computed(() => {
  const s = account.value.sharpe_ratio;
  return s != null && s !== '' ? String(s) : '--';
});

// ==========================================
// Card 3: 今日已结盈亏与实战胜率
// ==========================================
const perf = computed(() => (store.data as any)?.performance || {});

const todayNetNum = computed<number>(() => {
  if (today.value.net_realized != null && Number(today.value.net_realized) !== 0) {
    return Number(today.value.net_realized);
  }
  if (today.value.total_pnl != null && Number(today.value.total_pnl) !== 0) {
    return Number(today.value.total_pnl);
  }
  return Number(today.value.net_realized ?? 0);
});

const winTrades = computed(() => Number(today.value.win_trades ?? 0));
const lossTrades = computed(() => Number(today.value.loss_trades ?? 0));
const todayTrades = computed(() => winTrades.value + lossTrades.value);

// 累计交易表现 (performance 兜底)
const allTrades = computed(() => Number(perf.value.all_trades ?? (store.data as any)?.review?.total_trades ?? 0));
const allWins = computed(() => Number(perf.value.win_trades ?? 0));
const allLosses = computed(() => Number(perf.value.loss_trades ?? 0));
const allWinRate = computed(() => Number(perf.value.win_rate ?? (store.data as any)?.review?.win_rate ?? 0));
const allPf = computed(() => perf.value.profit_factor ?? (store.data as any)?.review?.profit_factor ?? null);

const todayWinRateStr = computed(() => {
  if (todayTrades.value > 0) {
    const r = (winTrades.value / todayTrades.value) * 100;
    return `胜率 ${r.toFixed(1)}%`;
  }
  if (allTrades.value > 0) {
    return `累计胜率 ${allWinRate.value.toFixed(1)}%`;
  }
  return '今日无平仓';
});

const tradesSummaryText = computed(() => {
  if (todayTrades.value > 0) {
    return `成交：${todayTrades.value} 笔 (${winTrades.value}胜/${lossTrades.value}负)`;
  }
  if (allTrades.value > 0) {
    return `累计：${allTrades.value} 笔 (${allWins.value}胜/${allLosses.value}负)`;
  }
  return '成交：0 笔 (待触发)';
});

const winBarPct = computed(() => {
  if (todayTrades.value > 0) {
    return (winTrades.value / todayTrades.value) * 100;
  }
  if (allTrades.value > 0) {
    return allWinRate.value;
  }
  return 50;
});

const profitFactor = computed(() => {
  if (today.value.profit_factor != null && String(today.value.profit_factor) !== '') {
    return String(today.value.profit_factor);
  }
  if (allPf.value != null) {
    return Number(allPf.value).toFixed(2);
  }
  return '--';
});

const fundingFeeStr = computed(() => {
  const f = today.value.funding_paid ?? today.value.funding_fee;
  if (f != null && Number.isFinite(Number(f))) {
    const val = Number(f);
    return `${val >= 0 ? '+' : ''}${val.toFixed(2)} U`;
  }
  return '0.00 U';
});

const tradingFeeStr = computed(() => {
  const f = today.value.fees_paid ?? today.value.trading_fee;
  if (f != null && Number.isFinite(Number(f))) {
    const val = Number(f);
    return `${val.toFixed(2)} U`;
  }
  if (account.value.cum_total_fees != null && Number.isFinite(Number(account.value.cum_total_fees))) {
    return `${Number(account.value.cum_total_fees).toFixed(2)} U`;
  }
  return '0.00 U';
});

// ==========================================
// Card 4: 真实持仓浮动盈亏与风控 (多所合并)
// ==========================================
const posUplNum = computed<number>(() => {
  const u = account.value.pos_upl_total ?? account.value.upl;
  if (u != null && u !== '') {
    const val = Number(u);
    return Number.isFinite(val) ? val : 0;
  }
  return 0;
});

const posRoiNum = computed<number | null>(() => {
  if (account.value.pos_roi_pct != null) {
    const val = Number(account.value.pos_roi_pct);
    return Number.isFinite(val) ? val : null;
  }
  if (actualPosMargin.value > 0) {
    return (posUplNum.value / actualPosMargin.value) * 100;
  }
  return null;
});

// 名义敞口：聚合计算
const notionalExposureStr = computed(() => {
  if (account.value.notional_exposure != null && account.value.notional_exposure !== '') {
    const val = Number(account.value.notional_exposure);
    if (val > 0) return fmtNum(val, 2);
  }
  // 如果 positions 有数据，累加 margin * lever 估算名义敞口
  const sumNotional = store.positions.reduce((acc, p: any) => {
    const m = Number(p.margin_usdt ?? p.margin ?? 0);
    const lev = Number(p.lever ?? 2);
    return acc + (m * lev);
  }, 0);
  return sumNotional > 0 ? fmtNum(sumNotional, 2) : '0.00';
});

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length);
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length);
const totalPos = computed(() => (hasMvp.value ? Number(mvp.value.positions_count) : store.positions.length));

const ocoOk = computed(() => {
  const total = store.positions.length;
  if (!total) return 100;
  const ok = store.positions.filter((p: any) => p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected').length;
  return Math.round((ok / total) * 100);
});
</script>

<template>
  <div class="space-y-2">
    <!-- 顶部状态小标 -->
    <div class="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-2xs px-1 text-[var(--ink-3)]">
      <div class="flex flex-wrap items-center gap-1.5 font-medium">
        <span class="h-2 w-2 rounded-full bg-[var(--up)] shadow-[0_0_6px_var(--up)] animate-pulse" />
        <span class="text-[var(--ink-2)] font-semibold">多所平权量化实盘监控</span>
        <span>·</span>
        <span>自动决策周期：<b class="num text-[var(--ink-1)]">15m</b></span>
      </div>
      <div class="hidden sm:flex items-center gap-2">
        <span class="badge text-3xs font-semibold px-2 py-0.5" :class="isLive ? 'badge-up' : 'badge-warn'">
          {{ isLive ? '三所实盘对等执行中' : '三所模拟盘对等运行中' }}
        </span>
      </div>
    </div>

    <!-- 4 单元独立 Bento 资产控制舱（移动端单列堆叠全宽、平板 2 列、桌面 4 列横排） -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-2.5 xl:gap-3">
      <!-- 单元 1：多所组合总权益 (OKX + Binance + Gate) -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between gap-1 pb-1">
          <div class="flex min-w-0 items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <Wallet class="h-3.5 w-3.5 text-indigo-400 shrink-0" />
            <span class="truncate">多所组合总权益</span>
          </div>
          <span
            class="badge badge-mono text-3xs font-bold px-1.5 py-0.2 rounded shrink-0"
            style="background-color: var(--surface-3); color: var(--ink-2); border-color: var(--line-2)"
          >
            {{ prodBadge }}
          </span>
        </div>

        <div class="py-1">
          <div class="num font-black tracking-tight text-lg sm:text-xl lg:text-2xl text-[var(--ink-strong)]">
            {{ totalEqStr !== '--' ? '$' + totalEqStr : '--' }}
          </div>
        </div>

        <div class="grid grid-cols-2 gap-x-1.5 gap-y-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="min-w-0 whitespace-nowrap">总可用 <b class="text-[var(--ink-1)]">{{ availEqStr !== '--' ? '$' + availEqStr : '--' }}</b></div>
          <div class="min-w-0 whitespace-nowrap text-right">总本金 <b class="text-[var(--ink-1)]">{{ initialCapStr !== '--' ? '$' + initialCapStr : '--' }}</b></div>
        </div>

        <!-- 保证金占用进度条 -->
        <div class="pt-1 border-t" style="border-color: var(--line-1)">
          <div class="flex items-center justify-between gap-1.5 text-[11px] mb-1">
            <span class="t-faint whitespace-nowrap">综合保证金占用率</span>
            <span class="num font-bold text-[var(--up)]">{{ marginUsagePct }}%</span>
          </div>
          <div class="w-full h-1.5 rounded-full overflow-hidden" style="background-color: var(--surface-3)">
            <div
              class="h-full rounded-full transition-all duration-500 bg-[var(--up)] shadow-[0_0_6px_var(--up)]"
              :style="{ width: `${Math.min(100, Math.max(6, marginUsagePct))}%` }"
            />
          </div>
        </div>
      </div>

      <!-- 单元 2：基准累计收益 -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between gap-1 pb-1">
          <div class="flex min-w-0 items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <TrendingUp class="h-3.5 w-3.5 text-[var(--up)] shrink-0" />
            <span class="truncate">基准累计收益</span>
          </div>
          <span
            v-if="cumRoiNum !== null"
            class="badge text-3xs font-bold px-1.5 py-0.2 rounded shrink-0"
            :class="cumRoiNum >= 0 ? 'badge-up' : 'badge-down'"
          >
            {{ cumRoiNum >= 0 ? '+' : '' }}{{ cumRoiNum.toFixed(2) }}%
          </span>
          <span v-else class="badge badge-quiet text-3xs shrink-0">--</span>
        </div>

        <div class="py-1">
          <div
            v-if="cumPnlNum !== null"
            class="num font-black tracking-tight text-lg sm:text-xl lg:text-2xl"
            :class="cumPnlNum >= 0 ? 'up' : 'down'"
          >
            {{ cumPnlNum >= 0 ? '+' : '' }}{{ cumPnlNum.toFixed(2) }}
          </div>
          <div v-else class="num font-black tracking-tight text-lg sm:text-xl lg:text-2xl t-faint">
            --
          </div>
        </div>

        <div class="grid grid-cols-2 gap-x-1.5 gap-y-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="min-w-0 whitespace-nowrap">
            净收益率
            <b v-if="cumRoiNum !== null" :class="cumRoiNum >= 0 ? 'up' : 'down'">
              {{ cumRoiNum >= 0 ? '+' : '' }}{{ cumRoiNum.toFixed(2) }}%
            </b>
            <b v-else class="t-faint">--</b>
          </div>
          <div class="min-w-0 whitespace-nowrap text-right">夏普锚定 <b class="text-[var(--up)]">{{ sharpeRatio }}</b></div>
        </div>

        <!-- 策略版本与全量台账穿透核验入口 -->
        <div class="pt-1 border-t flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[11px]" style="border-color: var(--line-1)">
          <span class="t-faint whitespace-nowrap">策略版本 {{ APP_VERSION }}</span>
          <button
            type="button"
            class="font-bold text-[var(--up)] flex items-center gap-0.5 cursor-pointer hover:underline"
            title="查看全量历史成交与生命周期台账"
            @click="router.push('/history')"
          >
            <span>穿透全量台账</span>
            <span class="text-[10px]">→</span>
          </button>
        </div>
      </div>

      <!-- 单元 3：今日已结盈亏 -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between gap-1 pb-1">
          <div class="flex min-w-0 items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <Zap class="h-3.5 w-3.5 text-amber-400 shrink-0" />
            <span class="truncate">今日已结盈亏</span>
          </div>
          <span class="badge text-3xs font-bold px-1.5 py-0.2 rounded shrink-0 whitespace-nowrap" :class="todayWinRateStr.includes('胜率') ? 'badge-up' : 'badge-quiet'">
            {{ todayWinRateStr }}
          </span>
        </div>

        <div class="py-1">
          <div
            class="num font-black tracking-tight text-lg sm:text-xl lg:text-2xl"
            :class="todayNetNum >= 0 ? 'up' : 'down'"
          >
            {{ todayNetNum >= 0 ? '+' : '' }}{{ todayNetNum.toFixed(2) }}
          </div>
        </div>

        <div class="grid grid-cols-2 gap-x-1.5 gap-y-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="min-w-0 whitespace-nowrap">资金费 <b :class="fundingFeeStr.startsWith('+') ? 'up' : fundingFeeStr.startsWith('-') ? 'down' : ''">{{ fundingFeeStr }}</b></div>
          <div class="min-w-0 whitespace-nowrap text-right">手续费 <b :class="tradingFeeStr.startsWith('-') ? 'down' : ''">{{ tradingFeeStr }}</b></div>
        </div>

        <!-- 双色成交胜负比进度条 -->
        <div class="pt-1 border-t" style="border-color: var(--line-1)">
          <div class="w-full h-1.5 rounded-full overflow-hidden flex mb-1" style="background-color: var(--surface-3)">
            <div class="bg-[var(--up)] h-full transition-all" :style="{ width: `${winBarPct}%` }" />
            <div class="bg-[var(--down)] h-full flex-1" />
          </div>
          <div class="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[11px] num">
            <span class="t-faint whitespace-nowrap">{{ tradesSummaryText }}</span>
            <span class="t-faint whitespace-nowrap">盈亏比：<b class="text-[var(--up)]">{{ profitFactor }}</b></span>
          </div>
        </div>
      </div>

      <!-- 单元 4：当前持仓浮盈 (多所合并) -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between gap-1 pb-1">
          <div class="flex min-w-0 items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <ShieldCheck class="h-3.5 w-3.5 text-blue-400 shrink-0" />
            <span class="truncate">当前持仓浮盈</span>
          </div>
          <span
            v-if="posRoiNum !== null"
            class="badge text-3xs font-bold px-1.5 py-0.2 rounded shrink-0 whitespace-nowrap"
            :class="posRoiNum >= 0 ? 'badge-up' : 'badge-down'"
          >
            ROI {{ posRoiNum >= 0 ? '+' : '' }}{{ posRoiNum.toFixed(2) }}%
          </span>
          <span v-else class="badge badge-quiet text-3xs shrink-0">--</span>
        </div>

        <div class="py-1">
          <div
            class="num font-black tracking-tight text-lg sm:text-xl lg:text-2xl"
            :class="posUplNum >= 0 ? 'up' : 'down'"
          >
            {{ posUplNum >= 0 ? '+' : '' }}{{ posUplNum.toFixed(2) }}
          </div>
        </div>

        <div class="grid grid-cols-2 gap-x-1.5 gap-y-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="min-w-0 whitespace-nowrap">持仓本金 <b class="text-[var(--ink-1)]">{{ actualPosMargin > 0 ? '$' + fmtNum(actualPosMargin, 2) : '$0.00' }}</b></div>
          <div class="min-w-0 whitespace-nowrap text-right">名义敞口 <b class="text-[var(--ink-1)]">${{ notionalExposureStr }}</b></div>
        </div>

        <!-- 多空比与 OCO 防线 -->
        <div class="pt-1 border-t flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[11px] num" style="border-color: var(--line-1)">
          <span class="t-faint whitespace-nowrap">多: <b class="up">{{ longCount }}</b> 空: <b class="down">{{ shortCount }}</b> (共{{ totalPos }}笔)</span>
          <span class="font-bold text-[var(--up)] flex items-center gap-0.5">
            OCO: {{ ocoOk }}%
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
