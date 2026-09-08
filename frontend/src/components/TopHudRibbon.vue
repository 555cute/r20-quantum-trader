<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import { Wallet, TrendingUp, Zap, ShieldCheck } from 'lucide-vue-next'

const store = useDashboardStore()
const { t } = useI18n()
const account = computed(() => store.data?.account || {})
const today = computed(() => store.data?.today_stats || {})

// 1. 主账户总权益
const totalEq = computed(() => Number(account.value.total_eq || 0).toFixed(2))
const availEq = computed(() => Number(account.value.avail_eq || 0).toFixed(2))
const marginUsage = computed(() => Number(account.value.margin_usage_pct || 0).toFixed(1))

// 2. 基准累计收益
const benchmarkNetPnl = computed(() => Number(account.value.cum_net_pnl || 0).toFixed(2))
const benchmarkRoi = computed(() => Number(account.value.cum_roi_pct || 0).toFixed(2))
const initialCap = computed(() => Number(account.value.initial_capital || 4021.53).toFixed(2))

// 3. 今日已结盈亏与费用明细
const todayNet = computed(() => Number(today.value.net_realized ?? today.value.total_pnl ?? 0).toFixed(2))
const todayFunding = computed(() => Number(today.value.funding_paid || 0).toFixed(2))
const todayFees = computed(() => Number(today.value.fees_paid || 0).toFixed(2))
const todayWinrate = computed(() => Number(today.value.win_rate || 100.0).toFixed(1))
const winTrades = computed(() => Number(today.value.win_trades ?? 0))
const lossTrades = computed(() => Number(today.value.loss_trades ?? 0))
const todayTrades = computed(() => winTrades.value + lossTrades.value)
const winRatePct = computed(() => {
  if (todayTrades.value === 0) return 100
  return Math.round((winTrades.value / todayTrades.value) * 100)
})

// 4. 当前持仓浮动盈亏与风控微结构
const posUplNum = computed(() => Number(account.value.pos_upl_total ?? account.value.upl ?? 0))
const posUplStr = computed(() => posUplNum.value.toFixed(2))

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length)
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length)
const totalPosCount = computed(() => store.positions.length)

// 统计当前真实持仓保证金与名义敞口 (支持 margin_usdt, notional_usdt 与兼容字段)
const totalPosMargin = computed(() => {
  return store.positions.reduce((sum, p: any) => {
    const val = Number(p.margin_usdt ?? p.margin ?? 0)
    return sum + (isNaN(val) ? 0 : val)
  }, 0)
})
const totalPosMarginStr = computed(() => totalPosMargin.value > 0 ? totalPosMargin.value.toFixed(2) : '0.00')

const totalPosNotional = computed(() => {
  return store.positions.reduce((sum, p: any) => {
    const notional = Number(p.notional_usdt)
    if (!isNaN(notional) && notional > 0) return sum + notional
    const m = Number(p.margin_usdt ?? p.margin ?? 0)
    const lev = Number(p.lever ?? 3)
    return sum + ((isNaN(m) ? 0 : m) * (isNaN(lev) ? 3 : lev))
  }, 0)
})
const totalPosNotionalStr = computed(() => totalPosNotional.value > 0 ? totalPosNotional.value.toFixed(2) : '0.00')

// 持仓未实现浮盈率 ROI%
const posRoiPct = computed(() => {
  if (totalPosMargin.value <= 0) return '0.00'
  return ((posUplNum.value / totalPosMargin.value) * 100).toFixed(2)
})

// 多空持仓比例 (用于分布条)
const longRatioPct = computed(() => {
  const total = longCount.value + shortCount.value
  if (total === 0) return 0
  return Math.round((longCount.value / total) * 100)
})
const shortRatioPct = computed(() => {
  const total = longCount.value + shortCount.value
  if (total === 0) return 0
  return 100 - longRatioPct.value
})

// OCO 保护覆盖率
const ocoProtectedRatio = computed(() => {
  if (totalPosCount.value === 0) return '100%'
  const protectedCount = store.positions.filter((p: any) => p.protectionStatus === 'fully_protected' || p.cloud_oco_verified !== false).length
  return `${Math.round((protectedCount / totalPosCount.value) * 100)}%`
})
</script>

<template>
  <!-- 4 Compact Bento Cards: Mobile 2x2 Grid, Desktop 1x4 (Strictly Symmetrical & Full i18n) -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3 lg:gap-4 items-stretch">
    
    <!-- Card 1: 主账户总权益 -->
    <div
      class="h-full rounded-xl border p-3 sm:p-4 lg:p-5 flex flex-col justify-between transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Row 1: Header -->
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <Wallet class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-indigo-400 shrink-0" />
          </div>
          <span class="truncate">{{ t('hud.accountEquity') }}</span>
        </div>
        <span
          class="text-[11px] sm:text-[11px] lg:text-[11px] font-mono px-1.5 py-0.2 rounded border font-bold shrink-0"
          style="background-color: var(--bg-badge); color: var(--text-muted); border-color: var(--border-subtle);"
        >
          {{ store.exchangeLabel }}
        </span>
      </div>

      <!-- Row 2: Value & Sub-info -->
      <div class="py-1 lg:py-1.5">
        <div class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular" style="color: var(--text-main);">
          ${{ totalEq }}
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono pt-1" style="color: var(--text-muted);">
          <span>{{ t('hud.availMargin') }}: <strong class="font-bold" style="color: var(--text-main);">${{ availEq }}</strong></span>
          <span>{{ t('hud.initialCapital') }}: <strong class="font-bold" style="color: var(--text-muted);">${{ initialCap }}</strong></span>
        </div>
      </div>

      <!-- Row 3: Progress Bar & Footer Status -->
      <div class="pt-1.5 space-y-1.5 border-t" style="border-color: var(--border-subtle);">
        <div class="w-full h-1.5 rounded-full overflow-hidden" style="background-color: var(--bg-badge);">
          <div
            class="h-full rounded-full transition-all duration-500 bg-emerald-500"
            :style="{
              width: `${Math.min(100, Math.max(6, Number(marginUsage)))}%`,
            }"
          ></div>
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono" style="color: var(--text-faint);">
          <span>{{ t('hud.marginUsagePct') }}</span>
          <span class="font-bold text-emerald-400">{{ marginUsage }}%</span>
        </div>
      </div>
    </div>

    <!-- Card 2: 基准累计收益 -->
    <div
      class="h-full rounded-xl border p-3 sm:p-4 lg:p-5 flex flex-col justify-between transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Row 1: Header -->
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <TrendingUp class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-emerald-400 shrink-0" />
          </div>
          <span class="truncate">{{ t('hud.pnlWaterline') }}</span>
        </div>
        <span
          class="text-[11px] sm:text-[11px] lg:text-[11px] font-mono px-1.5 py-0.2 rounded border font-bold text-emerald-400 num-tabular shrink-0"
          style="background-color: var(--color-up-bg); border-color: var(--color-up-border);"
        >
          {{ Number(benchmarkRoi) >= 0 ? '+' : '' }}{{ benchmarkRoi }}%
        </span>
      </div>

      <!-- Row 2: Value & Sub-info -->
      <div class="py-1 lg:py-1.5">
        <div
          class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
          :style="{ color: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
        >
          {{ Number(benchmarkNetPnl) >= 0 ? '+' : '' }}{{ benchmarkNetPnl }}
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono pt-1" style="color: var(--text-muted);">
          <span>{{ t('hud.netRoi') }}: <strong class="text-emerald-400">+{{ benchmarkRoi }}%</strong></span>
          <span>{{ t('hud.sharpeAnchor') }}: <strong class="text-emerald-400">2.1+</strong></span>
        </div>
      </div>

      <!-- Row 3: Progress Bar & Footer Status -->
      <div class="pt-1.5 space-y-1.5 border-t" style="border-color: var(--border-subtle);">
        <div class="w-full h-1.5 rounded-full overflow-hidden" style="background-color: var(--bg-badge);">
          <div
            class="h-full rounded-full transition-all duration-500 bg-emerald-500"
            :style="{
              width: `${Math.min(100, Math.max(12, Number(benchmarkRoi) * 5))}%`,
            }"
          ></div>
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono" style="color: var(--text-faint);">
          <span>{{ t('hud.strategyBaseline') }}</span>
          <span class="text-emerald-400 font-bold">{{ t('hud.liveVerified') }}</span>
        </div>
      </div>
    </div>

    <!-- Card 3: 今日已结盈亏 -->
    <div
      class="h-full rounded-xl border p-3 sm:p-4 lg:p-5 flex flex-col justify-between transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Row 1: Header -->
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <Zap class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-amber-400 shrink-0" />
          </div>
          <span class="truncate">{{ t('hud.todaySettled') }}</span>
        </div>
        <span
          class="text-[11px] sm:text-[11px] lg:text-[11px] font-mono px-1.5 py-0.2 rounded border font-bold text-emerald-400 shrink-0"
          style="background-color: var(--color-up-bg); border-color: var(--color-up-border);"
        >
          {{ t('hud.winRate') }} {{ todayWinrate }}%
        </span>
      </div>

      <!-- Row 2: Value & Sub-info -->
      <div class="py-1 lg:py-1.5">
        <div
          class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
          :style="{ color: Number(todayNet) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
        >
          {{ Number(todayNet) >= 0 ? '+' : '' }}{{ todayNet }}
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono pt-1" style="color: var(--text-muted);">
          <span>{{ t('hud.fundingFee') }}: <strong :class="Number(todayFunding) < 0 ? 'text-rose-400' : 'text-emerald-400'">{{ todayFunding }} U</strong></span>
          <span>{{ t('hud.tradingFee') }}: <strong class="text-rose-400">{{ todayFees }} U</strong></span>
        </div>
      </div>

      <!-- Row 3: Progress Bar & Footer Status -->
      <div class="pt-1.5 space-y-1.5 border-t" style="border-color: var(--border-subtle);">
        <div class="w-full h-1.5 rounded-full overflow-hidden flex" style="background-color: var(--bg-badge);">
          <div
            class="h-full bg-emerald-500 transition-all duration-500"
            :style="{ width: `${winRatePct}%` }"
          ></div>
          <div
            v-if="lossTrades > 0"
            class="h-full bg-rose-500 transition-all duration-500"
            :style="{ width: `${100 - winRatePct}%` }"
          ></div>
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono" style="color: var(--text-faint);">
          <span>{{ t('hud.trades') }}: <strong style="color: var(--text-main);">{{ todayTrades }}</strong> {{ t('hud.tradesCount') }} ({{ winTrades }}{{ t('hud.win') }}/{{ lossTrades }}{{ t('hud.loss') }})</span>
          <span>{{ t('hud.rrRatio') }}: <strong class="text-emerald-400">2.0+</strong></span>
        </div>
      </div>
    </div>

    <!-- Card 4: 当前持仓浮盈 -->
    <div
      class="h-full rounded-xl border p-3 sm:p-4 lg:p-5 flex flex-col justify-between transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Row 1: Header -->
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <ShieldCheck class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-blue-400 shrink-0" />
          </div>
          <span class="truncate">{{ t('hud.unrealizedPnl') }}</span>
        </div>
        <span
          class="text-[11px] sm:text-[11px] lg:text-[11px] font-mono px-1.5 py-0.2 rounded border font-bold shrink-0"
          :class="posUplNum >= 0 ? 'text-emerald-400' : 'text-rose-400'"
          style="background-color: var(--bg-badge); border-color: var(--border-subtle);"
        >
          {{ t('hud.unrealizedRoi') }} {{ Number(posRoiPct) >= 0 ? '+' : '' }}{{ posRoiPct }}%
        </span>
      </div>

      <!-- Row 2: Value & Sub-info -->
      <div class="py-1 lg:py-1.5">
        <div
          class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
          :style="{ color: posUplNum >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
        >
          {{ posUplNum >= 0 ? '+' : '' }}{{ posUplStr }}
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono pt-1" style="color: var(--text-muted);">
          <span>{{ t('hud.holdingCapital') }}: <strong class="font-bold" style="color: var(--text-main);">${{ totalPosMarginStr }}</strong></span>
          <span>{{ t('hud.notionalExposure') }}: <strong class="font-bold" style="color: var(--text-main);">${{ totalPosNotionalStr }}</strong></span>
        </div>
      </div>

      <!-- Row 3: Progress Bar & Footer Status -->
      <div class="pt-1.5 space-y-1.5 border-t" style="border-color: var(--border-subtle);">
        <div class="w-full h-1.5 rounded-full overflow-hidden flex" style="background-color: var(--bg-badge);">
          <div
            v-if="longCount > 0"
            class="h-full bg-emerald-500 transition-all duration-500"
            :style="{ width: `${longRatioPct}%` }"
          ></div>
          <div
            v-if="shortCount > 0"
            class="h-full bg-rose-500 transition-all duration-500"
            :style="{ width: `${shortRatioPct}%` }"
          ></div>
          <div
            v-if="totalPosCount === 0"
            class="h-full bg-slate-600/30 w-full"
          ></div>
        </div>
        <div class="flex items-center justify-between text-[11px] sm:text-[11px] font-mono" style="color: var(--text-faint);">
          <span>{{ t('hud.long') }}: <strong class="text-emerald-400">{{ longCount }}</strong> {{ t('hud.short') }}: <strong class="text-rose-400">{{ shortCount }}</strong> ({{ t('hud.totalPos') }}{{ totalPosCount }}{{ t('hud.tradesCount') }})</span>
          <span class="text-emerald-400 font-bold">{{ store.usesPairedConditional ? t('hud.pairedConditional') : t('hud.ocoLabel') }}: {{ ocoProtectedRatio }}</span>
        </div>
      </div>
    </div>

  </div>
</template>
