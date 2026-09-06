<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import { Wallet, TrendingUp, Calendar, Activity, ShieldCheck, ArrowDownRight } from 'lucide-vue-next'

const store = useDashboardStore()
const { t } = useI18n()
const account = computed(() => store.data?.account || {})
const today = computed(() => store.data?.today_stats || {})

const totalEq = computed(() => Number(account.value.total_eq || 0).toFixed(2))
const availEq = computed(() => Number(account.value.avail_eq || 0).toFixed(2))
const marginUsage = computed(() => Number(account.value.margin_usage_pct || 0).toFixed(1))

const benchmarkNetPnl = computed(() => Number(account.value.cum_net_pnl || 0).toFixed(2))
const benchmarkRoi = computed(() => Number(account.value.cum_roi_pct || 0).toFixed(2))
const initialCap = computed(() => Number(account.value.initial_capital || 0).toFixed(2))
const cumRealizedPnl = computed(() => Number(account.value.cum_realized_pnl || 0).toFixed(2))

const todayNet = computed(() => Number(today.value.net_realized ?? today.value.total_pnl ?? 0).toFixed(2))
const todayWinrate = computed(() => Number(today.value.win_rate || 0).toFixed(1))
const todayTrades = computed(() => (today.value.win_trades || 0) + (today.value.loss_trades || 0))

// 当前持仓浮动盈亏与风控统计
const posUplNum = computed(() => Number(account.value.pos_upl_total ?? account.value.upl ?? 0))
const posUplStr = computed(() => posUplNum.value.toFixed(2))

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length)
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length)

const totalPosMargin = computed(() => {
  const sum = store.positions.reduce((acc, p) => acc + Number((p as any).margin_usdt ?? p.margin ?? 0), 0)
  return sum.toFixed(2)
})

const posUplRatio = computed(() => {
  const margin = Number(totalPosMargin.value)
  if (margin > 0) {
    return (posUplNum.value / margin * 100).toFixed(2)
  }
  return '0.00'
})

const allProtected = computed(() =>
  store.positions.length > 0 &&
  store.positions.every((p) => p.protectionStatus === 'fully_protected' || Number(p.protectionCoveragePct || 0) >= 100)
)
</script>

<template>
  <!-- 4 Distinct Bento Cards with Spatial Gap Separation (Zero divide lines) -->
  <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 2xl:gap-4">
    
    <!-- Card 1: 官方账户总权益 -->
    <div
      class="rounded-xl border p-4 sm:p-5 2xl:p-6 flex flex-col justify-between space-y-3.5 2xl:space-y-4 transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between">
        <div class="flex items-center space-x-2 2xl:space-x-2.5">
          <div
            class="w-6 h-6 2xl:w-7 2xl:h-7 rounded-md flex items-center justify-center border"
            style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-main);"
          >
            <Wallet class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          </div>
          <span class="text-xs 2xl:text-sm font-bold font-mono" style="color: var(--text-main);">{{ t('hud.accountEquity') }}</span>
        </div>
        <span
          class="text-[9px] 2xl:text-[10px] font-mono px-1.5 py-0.5 rounded border font-bold"
          style="background-color: var(--bg-badge); color: var(--text-muted); border-color: var(--border-subtle);"
        >
          OKX V5 PROD
        </span>
      </div>

      <div>
        <div class="flex items-baseline space-x-1.5 2xl:space-x-2">
          <span class="text-2xl sm:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular" style="color: var(--text-main);">
            ${{ totalEq }}
          </span>
          <span class="text-xs 2xl:text-sm font-mono font-medium" style="color: var(--text-faint);">USDT</span>
        </div>
        
        <!-- Soft Inset Capsule (No dividing line) -->
        <div
          class="mt-3 2xl:mt-4 p-2 2xl:p-3 rounded-lg space-y-1.5 2xl:space-y-2 border text-[11px] 2xl:text-xs font-mono"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
        >
          <div class="flex items-center justify-between" style="color: var(--text-muted);">
            <span>{{ t('hud.available') }}: <strong class="font-semibold" style="color: var(--text-main);">${{ availEq }}</strong></span>
            <span>{{ t('hud.marginRatio') }}: <strong class="num-tabular" :style="{ color: Number(marginUsage) > 50 ? 'var(--color-warn)' : 'var(--text-main)' }">{{ marginUsage }}%</strong></span>
          </div>
          <div class="w-full h-1 2xl:h-1.5 rounded-full overflow-hidden" style="background-color: var(--bg-badge);">
            <div
              class="h-full rounded-full transition-all duration-500"
              :style="{
                width: `${Math.min(100, Math.max(0, Number(marginUsage)))}%`,
                backgroundColor: Number(marginUsage) > 50 ? 'var(--color-warn)' : 'rgba(16, 185, 129, 0.75)'
              }"
            ></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Card 2: 基准净盈亏水线 -->
    <div
      class="rounded-xl border p-4 sm:p-5 2xl:p-6 flex flex-col justify-between space-y-3.5 2xl:space-y-4 transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between">
        <div class="flex items-center space-x-2 2xl:space-x-2.5">
          <div
            class="w-6 h-6 2xl:w-7 2xl:h-7 rounded-md flex items-center justify-center border"
            :style="{
              backgroundColor: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
              borderColor: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up-border)' : 'var(--color-down-border)',
              color: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up)' : 'var(--color-down)'
            }"
          >
            <TrendingUp v-if="Number(benchmarkNetPnl) >= 0" class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
            <ArrowDownRight v-else class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          </div>
          <span class="text-xs 2xl:text-sm font-bold font-mono" style="color: var(--text-main);">{{ t('hud.pnlWaterline') }}</span>
        </div>
        <span class="text-[10px] 2xl:text-[11px] font-mono" style="color: var(--text-faint);">
          {{ t('hud.base') }} ${{ initialCap }}
        </span>
      </div>

      <div>
        <div class="flex items-baseline space-x-2 2xl:space-x-2.5">
          <span
            class="text-2xl sm:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
            :style="{ color: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
          >
            {{ Number(benchmarkNetPnl) >= 0 ? '+' : '' }}{{ benchmarkNetPnl }}
          </span>
          <span
            class="text-xs 2xl:text-sm font-bold font-mono px-1.5 py-0.2 2xl:px-2 2xl:py-0.5 rounded border num-tabular"
            :style="{
              backgroundColor: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
              borderColor: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up-border)' : 'var(--color-down-border)',
              color: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up)' : 'var(--color-down)'
            }"
          >
            {{ Number(benchmarkRoi) >= 0 ? '+' : '' }}{{ benchmarkRoi }}%
          </span>
        </div>

        <div
          class="mt-3 2xl:mt-4 p-2 2xl:p-3 rounded-lg flex items-center justify-between border text-[11px] 2xl:text-xs font-mono"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>{{ t('hud.settledNet') }}: <strong class="num-tabular" :style="{ color: Number(cumRealizedPnl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }">{{ Number(cumRealizedPnl) >= 0 ? '+' : '' }}{{ cumRealizedPnl }} U</strong></span>
          <span>{{ t('hud.deductFee') }}: <strong class="font-semibold" style="color: var(--text-main);">100% {{ t('hud.realProd') }}</strong></span>
        </div>
      </div>
    </div>

    <!-- Card 3: 今日已结 (UTC+8) -->
    <div
      class="rounded-xl border p-4 sm:p-5 2xl:p-6 flex flex-col justify-between space-y-3.5 2xl:space-y-4 transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between">
        <div class="flex items-center space-x-2 2xl:space-x-2.5">
          <div
            class="w-6 h-6 2xl:w-7 2xl:h-7 rounded-md flex items-center justify-center border"
            style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--text-main);"
          >
            <Calendar class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          </div>
          <span class="text-xs 2xl:text-sm font-bold font-mono" style="color: var(--text-main);">{{ t('hud.todaySettled') }}</span>
        </div>
        <span
          class="text-[10px] 2xl:text-[11px] font-mono font-bold px-1.5 py-0.5 2xl:px-2 rounded border num-tabular"
          :style="{
            backgroundColor: Number(todayNet) >= 0 ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
            borderColor: Number(todayNet) >= 0 ? 'var(--color-up-border)' : 'var(--color-down-border)',
            color: Number(todayNet) >= 0 ? 'var(--color-up)' : 'var(--color-down)'
          }"
        >
          {{ t('hud.winRate') }} {{ todayWinrate }}%
        </span>
      </div>

      <div>
        <div class="flex items-baseline space-x-1.5 2xl:space-x-2">
          <span
            class="text-2xl sm:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
            :style="{ color: Number(todayNet) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
          >
            {{ Number(todayNet) >= 0 ? '+' : '' }}{{ todayNet }}
          </span>
          <span class="text-xs 2xl:text-sm font-mono font-medium" style="color: var(--text-faint);">USDT</span>
        </div>

        <div
          class="mt-3 2xl:mt-4 p-2 2xl:p-3 rounded-lg flex items-center justify-between border text-[11px] 2xl:text-xs font-mono"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>{{ t('hud.closedTrades') }}: <strong class="font-semibold num-tabular" style="color: var(--text-main);">{{ todayTrades }} {{ t('hud.tradesCount') }} ({{ today.win_trades || 0 }}{{ t('hud.win') }}/{{ today.loss_trades || 0 }}{{ t('hud.loss') }})</strong></span>
          <span>{{ t('hud.totalFees') }}: <strong class="num-tabular" style="color: var(--text-main);">{{ today.fees_paid || 0 }} U</strong></span>
        </div>
      </div>
    </div>

    <!-- Card 4: 当前持仓净盈亏 -->
    <div
      class="rounded-xl border p-4 sm:p-5 2xl:p-6 flex flex-col justify-between space-y-3.5 2xl:space-y-4 transition-all shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between">
        <div class="flex items-center space-x-2 2xl:space-x-2.5">
          <div
            class="w-6 h-6 2xl:w-7 2xl:h-7 rounded-md flex items-center justify-center border"
            style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--text-main);"
          >
            <Activity class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          </div>
          <span class="text-xs 2xl:text-sm font-bold font-mono" style="color: var(--text-main);">{{ t('hud.unrealizedPnl') }}</span>
        </div>
        <span
          class="text-[10px] 2xl:text-[11px] font-mono px-1.5 py-0.5 rounded border font-bold"
          style="background-color: var(--bg-badge); color: var(--text-muted); border-color: var(--border-subtle);"
        >
          {{ t('hud.holding') }} {{ store.positions.length }}/6 ({{ t('hud.long') }}{{ longCount }}/{{ t('hud.short') }}{{ shortCount }})
        </span>
      </div>

      <div>
        <div class="flex items-baseline space-x-2 2xl:space-x-2.5">
          <span
            class="text-2xl sm:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
            :style="{ color: posUplNum >= 0 ? (posUplNum > 0 ? 'var(--color-up)' : 'var(--text-main)') : 'var(--color-down)' }"
          >
            {{ posUplNum > 0 ? '+' : '' }}{{ posUplStr }}
          </span>
          <span class="text-xs 2xl:text-sm font-mono font-medium" style="color: var(--text-faint);">USDT</span>
          <span
            v-if="store.positions.length > 0"
            class="text-xs 2xl:text-sm font-bold font-mono px-1.5 py-0.2 2xl:px-2 2xl:py-0.5 rounded border num-tabular"
            :style="{
              backgroundColor: posUplNum >= 0 ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
              borderColor: posUplNum >= 0 ? 'var(--color-up-border)' : 'var(--color-down-border)',
              color: posUplNum >= 0 ? 'var(--color-up)' : 'var(--color-down)'
            }"
          >
            {{ Number(posUplRatio) > 0 ? '+' : '' }}{{ posUplRatio }}%
          </span>
        </div>

        <div
          class="mt-3 2xl:mt-4 p-2 2xl:p-3 rounded-lg flex items-center justify-between border text-[11px] 2xl:text-xs font-mono"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>{{ t('hud.marginOccupied') }}: <strong class="font-semibold num-tabular" style="color: var(--text-main);">${{ totalPosMargin }} U</strong></span>
          <span v-if="store.positions.length > 0" class="flex items-center space-x-1">
            <ShieldCheck class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" :style="{ color: allProtected ? 'var(--color-up)' : 'var(--color-warn)' }" />
            <strong :style="{ color: allProtected ? 'var(--color-up)' : 'var(--color-warn)' }">
              {{ allProtected ? '100% OCO' : 'Protected' }}
            </strong>
          </span>
          <span v-else style="color: var(--text-faint);">
            <strong>STANDBY</strong>
          </span>
        </div>
      </div>
    </div>

  </div>
</template>
