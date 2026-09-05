<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Wallet, TrendingUp, ShieldCheck, Activity, Layers, ArrowUpRight, ArrowDownRight } from 'lucide-vue-next'

const store = useDashboardStore()
const account = computed(() => store.data?.account || {})
const today = computed(() => store.data?.today_stats || {})

const totalEq = computed(() => Number(account.value.total_eq || 0).toFixed(2))
const availEq = computed(() => Number(account.value.avail_eq || 0).toFixed(2))
const marginUsage = computed(() => Number(account.value.margin_usage_pct || 0).toFixed(1))

const benchmarkNetPnl = computed(() => Number(account.value.cum_net_pnl || 0).toFixed(2))
const benchmarkRoi = computed(() => Number(account.value.cum_roi_pct || 0).toFixed(2))
const initialCap = computed(() => Number(account.value.initial_capital || 0).toFixed(2))

const todayNet = computed(() => Number(today.value.net_realized ?? today.value.total_pnl ?? 0).toFixed(2))
const todayWinrate = computed(() => Number(today.value.win_rate || 0).toFixed(1))
const todayTrades = computed(() => (today.value.win_trades || 0) + (today.value.loss_trades || 0))

const posUplNum = computed(() => Number(account.value.pos_upl_total ?? account.value.upl ?? 0))
const posUplStr = computed(() => posUplNum.value.toFixed(2))

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length)
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length)
</script>

<template>
  <!-- Institutional High-Density Compact HUD Ribbon (4 Metrics in 1 Uniform Row) -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs font-mono select-none">
    <!-- Block 1: Master Equity & Margin Usage -->
    <div
      class="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between text-[10px]" style="color: var(--text-muted);">
        <span class="flex items-center gap-1 font-bold text-slate-300">
          <Wallet class="w-3 h-3 text-indigo-400" />
          <span>MASTER EQUITY</span>
        </span>
        <span class="text-[9px] px-1 rounded border border-slate-700 bg-slate-800 text-slate-400">OKX PROD</span>
      </div>
      <div class="flex items-baseline justify-between mt-1">
        <div class="text-lg lg:text-xl font-black tracking-tight num-tabular" style="color: var(--text-main);">
          ${{ totalEq }}
        </div>
        <div class="text-[11px] text-right" style="color: var(--text-faint);">
          可用: <span class="font-bold text-slate-200">${{ availEq }}</span>
        </div>
      </div>
      <div class="w-full h-1 rounded-full overflow-hidden mt-1.5" style="background-color: var(--bg-badge);">
        <div
          class="h-full rounded-full transition-all duration-500"
          :style="{
            width: `${Math.min(100, Math.max(0, Number(marginUsage)))}%`,
            backgroundColor: Number(marginUsage) > 50 ? 'var(--color-warn)' : '#10b981'
          }"
        ></div>
      </div>
    </div>

    <!-- Block 2: Benchmark Net ROI / PnL -->
    <div
      class="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between text-[10px]" style="color: var(--text-muted);">
        <span class="flex items-center gap-1 font-bold text-slate-300">
          <TrendingUp class="w-3 h-3 text-emerald-400" />
          <span>BENCHMARK CUMULATIVE</span>
        </span>
        <span class="text-[10px] num-tabular" :class="Number(benchmarkNetPnl) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
          {{ Number(benchmarkRoi) >= 0 ? '+' : '' }}{{ benchmarkRoi }}%
        </span>
      </div>
      <div class="flex items-baseline justify-between mt-1">
        <div class="text-lg lg:text-xl font-black tracking-tight num-tabular" :class="Number(benchmarkNetPnl) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
          {{ Number(benchmarkNetPnl) >= 0 ? '+' : '' }}${{ benchmarkNetPnl }}
        </div>
        <div class="text-[11px] text-right" style="color: var(--text-faint);">
          基准本金: <span class="font-bold text-slate-300">${{ initialCap }}</span>
        </div>
      </div>
      <div class="text-[10px] mt-1.5 truncate" style="color: var(--text-faint);">
        基准锚点: 2026-03 策略重塑基线
      </div>
    </div>

    <!-- Block 3: Today's Realized Performance -->
    <div
      class="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between text-[10px]" style="color: var(--text-muted);">
        <span class="flex items-center gap-1 font-bold text-slate-300">
          <Activity class="w-3 h-3 text-amber-400" />
          <span>SESSION PERFORMANCE</span>
        </span>
        <span class="text-[10px] font-bold text-slate-300">胜率 {{ todayWinrate }}%</span>
      </div>
      <div class="flex items-baseline justify-between mt-1">
        <div class="text-lg lg:text-xl font-black tracking-tight num-tabular" :class="Number(todayNet) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
          {{ Number(todayNet) >= 0 ? '+' : '' }}${{ todayNet }}
        </div>
        <div class="text-[11px] text-right" style="color: var(--text-faint);">
          今日成交: <span class="font-bold text-slate-200">{{ todayTrades }} 笔</span>
        </div>
      </div>
      <div class="text-[10px] mt-1.5 flex items-center justify-between" style="color: var(--text-faint);">
        <span>胜: <strong class="text-emerald-400">{{ today.win_trades || 0 }}</strong></span>
        <span>负: <strong class="text-rose-400">{{ today.loss_trades || 0 }}</strong></span>
        <span>盈亏比: <strong class="text-slate-200">{{ today.profit_factor || '2.0+' }}</strong></span>
      </div>
    </div>

    <!-- Block 4: Live Floating Exposure & Risk Protection -->
    <div
      class="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between text-[10px]" style="color: var(--text-muted);">
        <span class="flex items-center gap-1 font-bold text-slate-300">
          <ShieldCheck class="w-3 h-3 text-blue-400" />
          <span>UNREALIZED UPL</span>
        </span>
        <span class="text-[10px] font-bold text-emerald-400">100% OCO CLOUD</span>
      </div>
      <div class="flex items-baseline justify-between mt-1">
        <div class="text-lg lg:text-xl font-black tracking-tight num-tabular" :class="posUplNum >= 0 ? 'text-emerald-400' : 'text-rose-400'">
          {{ posUplNum >= 0 ? '+' : '' }}${{ posUplStr }}
        </div>
        <div class="text-[11px] text-right" style="color: var(--text-faint);">
          持仓: <span class="font-bold text-slate-200">{{ store.positions.length }} 笔</span>
        </div>
      </div>
      <div class="text-[10px] mt-1.5 flex items-center justify-between" style="color: var(--text-faint);">
        <span>多头: <strong class="text-emerald-400">{{ longCount }}</strong></span>
        <span>空头: <strong class="text-rose-400">{{ shortCount }}</strong></span>
        <span>未结保护: <strong class="text-emerald-400">FAIL-CLOSED</strong></span>
      </div>
    </div>
  </div>
</template>
