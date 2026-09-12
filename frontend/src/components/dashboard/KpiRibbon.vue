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
import { Wallet, TrendingUp, Zap, ShieldCheck } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useI18n } from '../../composables/useI18n';
import { fmtNum } from '../../utils/format';

const store = useDashboardStore();
const venueStore = useVenueAccountsStore();
const { t } = useI18n();

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
// Card 3: 今日已结盈亏
// ==========================================
const todayNetNum = computed<number | null>(() => {
  const net = today.value.net_realized ?? today.value.total_pnl;
  if (net != null && net !== '') {
    const val = Number(net);
    return Number.isFinite(val) ? val : null;
  }
  return 0;
});

const winTrades = computed(() => Number(today.value.win_trades ?? 0));
const lossTrades = computed(() => Number(today.value.loss_trades ?? 0));
const totalTrades = computed(() => winTrades.value + lossTrades.value);

const todayWinRateStr = computed(() => {
  if (today.value.win_rate != null) return `${Number(today.value.win_rate).toFixed(1)}%`;
  if (totalTrades.value > 0) {
    return `${((winTrades.value / totalTrades.value) * 100).toFixed(1)}%`;
  }
  return '--';
});

const fundingFeeStr = computed(() => {
  if (today.value.funding_fee != null) {
    const f = Number(today.value.funding_fee);
    return Number.isFinite(f) ? `${f >= 0 ? '+' : ''}${f.toFixed(2)} U` : '--';
  }
  return '--';
});

const tradingFeeStr = computed(() => {
  if (today.value.trading_fee != null) {
    const f = Number(today.value.trading_fee);
    return Number.isFinite(f) ? `${f.toFixed(2)} U` : '--';
  }
  return '--';
});

const profitFactor = computed(() => {
  const pf = today.value.profit_factor;
  if (pf != null && pf !== '') {
    const val = Number(pf);
    return Number.isFinite(val) ? val.toFixed(2) : String(pf);
  }
  return '--';
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
    <div class="flex items-center justify-between text-2xs px-1 text-[var(--ink-3)]">
      <div class="flex items-center gap-1.5 font-medium">
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

    <!-- 4 单元独立 Bento 资产控制舱（移动端 2x2 对称紧凑网格，桌面端 4 列横排） -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-2.5 xl:gap-3">
      <!-- 单元 1：多所组合总权益 (OKX + Binance + Gate) -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between pb-1">
          <div class="flex items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <Wallet class="h-3.5 w-3.5 text-indigo-400 shrink-0" />
            <span class="truncate">多所组合总权益</span>
          </div>
          <span
            class="badge badge-mono text-3xs font-bold px-1.5 py-0.2 rounded"
            style="background-color: var(--surface-3); color: var(--ink-2); border-color: var(--line-2)"
          >
            {{ prodBadge }}
          </span>
        </div>

        <div class="py-1">
          <div class="num font-black tracking-tight text-xl sm:text-2xl text-[var(--ink-strong)] truncate">
            {{ totalEqStr !== '--' ? '$' + totalEqStr : '--' }}
          </div>
        </div>

        <div class="grid grid-cols-2 gap-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="truncate">总可用 <b class="text-[var(--ink-1)]">{{ availEqStr !== '--' ? '$' + availEqStr : '--' }}</b></div>
          <div class="truncate text-right">总本金 <b class="text-[var(--ink-1)]">{{ initialCapStr !== '--' ? '$' + initialCapStr : '--' }}</b></div>
        </div>

        <!-- 保证金占用进度条 -->
        <div class="pt-1 border-t" style="border-color: var(--line-1)">
          <div class="flex items-center justify-between text-[11px] mb-1">
            <span class="t-faint">综合保证金占用率</span>
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
        <div class="flex items-center justify-between pb-1">
          <div class="flex items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <TrendingUp class="h-3.5 w-3.5 text-[var(--up)] shrink-0" />
            <span class="truncate">基准累计收益</span>
          </div>
          <span
            v-if="cumRoiNum !== null"
            class="badge text-3xs font-bold px-1.5 py-0.2 rounded"
            :class="cumRoiNum >= 0 ? 'badge-up' : 'badge-down'"
          >
            {{ cumRoiNum >= 0 ? '+' : '' }}{{ cumRoiNum.toFixed(2) }}%
          </span>
          <span v-else class="badge badge-quiet text-3xs">--</span>
        </div>

        <div class="py-1">
          <div
            v-if="cumPnlNum !== null"
            class="num font-black tracking-tight text-xl sm:text-2xl truncate"
            :class="cumPnlNum >= 0 ? 'up' : 'down'"
          >
            {{ cumPnlNum >= 0 ? '+' : '' }}{{ cumPnlNum.toFixed(2) }}
          </div>
          <div v-else class="num font-black tracking-tight text-xl sm:text-2xl t-faint">
            --
          </div>
        </div>

        <div class="grid grid-cols-2 gap-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="truncate">
            净收益率
            <b v-if="cumRoiNum !== null" :class="cumRoiNum >= 0 ? 'up' : 'down'">
              {{ cumRoiNum >= 0 ? '+' : '' }}{{ cumRoiNum.toFixed(2) }}%
            </b>
            <b v-else class="t-faint">--</b>
          </div>
          <div class="truncate text-right">夏普锚定 <b class="text-[var(--up)]">{{ sharpeRatio }}</b></div>
        </div>

        <!-- 策略基线与全网实盘验证 -->
        <div class="pt-1 border-t flex items-center justify-between text-[11px]" style="border-color: var(--line-1)">
          <span class="t-faint">策略基线 2026-03</span>
          <span class="font-bold text-[var(--up)] flex items-center gap-0.5 cursor-pointer">
            全网实盘验证
          </span>
        </div>
      </div>

      <!-- 单元 3：今日已结盈亏 -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between pb-1">
          <div class="flex items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <Zap class="h-3.5 w-3.5 text-amber-400 shrink-0" />
            <span class="truncate">今日已结盈亏</span>
          </div>
          <span class="badge text-3xs font-bold px-1.5 py-0.2 rounded" :class="todayWinRateStr !== '--' ? 'badge-up' : 'badge-quiet'">
            胜率 {{ todayWinRateStr }}
          </span>
        </div>

        <div class="py-1">
          <div
            v-if="todayNetNum !== null"
            class="num font-black tracking-tight text-xl sm:text-2xl truncate"
            :class="todayNetNum >= 0 ? 'up' : 'down'"
          >
            {{ todayNetNum >= 0 ? '+' : '' }}{{ todayNetNum.toFixed(2) }}
          </div>
          <div v-else class="num font-black tracking-tight text-xl sm:text-2xl t-faint">
            0.00
          </div>
        </div>

        <div class="grid grid-cols-2 gap-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="truncate">资金费 <b :class="fundingFeeStr.startsWith('+') ? 'up' : fundingFeeStr.startsWith('-') ? 'down' : ''">{{ fundingFeeStr }}</b></div>
          <div class="truncate text-right">手续费 <b class="down">{{ tradingFeeStr }}</b></div>
        </div>

        <!-- 双色成交胜负比进度条 -->
        <div class="pt-1 border-t" style="border-color: var(--line-1)">
          <div class="w-full h-1.5 rounded-full overflow-hidden flex mb-1" style="background-color: var(--surface-3)">
            <div class="bg-[var(--up)] h-full transition-all" :style="{ width: `${totalTrades > 0 ? (winTrades / totalTrades) * 100 : 50}%` }" />
            <div class="bg-[var(--down)] h-full flex-1" />
          </div>
          <div class="flex items-center justify-between text-[11px] num">
            <span class="t-faint">成交：{{ totalTrades }} 笔 ({{ winTrades }}胜/{{ lossTrades }}负)</span>
            <span class="t-faint">盈亏比：<b class="text-[var(--up)]">{{ profitFactor }}</b></span>
          </div>
        </div>
      </div>

      <!-- 单元 4：当前持仓浮盈 (多所合并) -->
      <div
        class="card rounded-2xl border p-3 sm:p-3.5 flex flex-col justify-between transition-all"
        style="background-color: var(--surface-1); border-color: var(--line-1)"
      >
        <div class="flex items-center justify-between pb-1">
          <div class="flex items-center gap-1.5 text-2xs sm:text-xs font-bold" style="color: var(--ink-1)">
            <ShieldCheck class="h-3.5 w-3.5 text-blue-400 shrink-0" />
            <span class="truncate">当前持仓浮盈</span>
          </div>
          <span
            v-if="posRoiNum !== null"
            class="badge text-3xs font-bold px-1.5 py-0.2 rounded"
            :class="posRoiNum >= 0 ? 'badge-up' : 'badge-down'"
          >
            ROI {{ posRoiNum >= 0 ? '+' : '' }}{{ posRoiNum.toFixed(2) }}%
          </span>
          <span v-else class="badge badge-quiet text-3xs">--</span>
        </div>

        <div class="py-1">
          <div
            class="num font-black tracking-tight text-xl sm:text-2xl truncate"
            :class="posUplNum >= 0 ? 'up' : 'down'"
          >
            {{ posUplNum >= 0 ? '+' : '' }}{{ posUplNum.toFixed(2) }}
          </div>
        </div>

        <div class="grid grid-cols-2 gap-0.5 text-[10px] sm:text-[11px] num t-faint pb-1">
          <div class="truncate">持仓本金 <b class="text-[var(--ink-1)]">{{ actualPosMargin > 0 ? '$' + fmtNum(actualPosMargin, 2) : '$0.00' }}</b></div>
          <div class="truncate text-right">名义敞口 <b class="text-[var(--ink-1)]">${{ notionalExposureStr }}</b></div>
        </div>

        <!-- 多空比与 OCO 防线 -->
        <div class="pt-1 border-t flex items-center justify-between text-[11px] num" style="border-color: var(--line-1)">
          <span class="t-faint">多: <b class="up">{{ longCount }}</b> 空: <b class="down">{{ shortCount }}</b> (共{{ totalPos }}笔)</span>
          <span class="font-bold text-[var(--up)] flex items-center gap-0.5">
            OCO: {{ ocoOk }}%
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
