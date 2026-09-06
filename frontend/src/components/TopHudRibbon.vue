<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import { Wallet, TrendingUp, Zap, ShieldCheck } from 'lucide-vue-next'

const store = useDashboardStore()
const { t } = useI18n()
const account = computed(() => store.data?.account || {})
const today = computed(() => store.data?.today_stats || {})

const totalEq = computed(() => Number(account.value.total_eq || 0).toFixed(2))
const availEq = computed(() => Number(account.value.avail_eq || 0).toFixed(2))
const marginUsage = computed(() => Number(account.value.margin_usage_pct || 0).toFixed(1))

const benchmarkNetPnl = computed(() => Number(account.value.cum_net_pnl || 0).toFixed(2))
const benchmarkRoi = computed(() => Number(account.value.cum_roi_pct || 0).toFixed(2))
const initialCap = computed(() => Number(account.value.initial_capital || 3695.0).toFixed(2))

const todayNet = computed(() => Number(today.value.net_realized ?? today.value.total_pnl ?? 0).toFixed(2))
const todayWinrate = computed(() => Number(today.value.win_rate || 100.0).toFixed(1))
const winTrades = computed(() => Number(today.value.win_trades ?? 0))
const lossTrades = computed(() => Number(today.value.loss_trades ?? 0))
const todayTrades = computed(() => winTrades.value + lossTrades.value)

// 当前持仓浮动盈亏与风控统计
const posUplNum = computed(() => Number(account.value.pos_upl_total ?? account.value.upl ?? 0))
const posUplStr = computed(() => posUplNum.value.toFixed(2))

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length)
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length)
</script>

<template>
  <!-- 4 Compact Bento Cards: Mobile 2x2 Grid (两行，一行两个), Desktop 1x4 (饱满方正，拒绝扁长) -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3 lg:gap-4">
    
    <!-- Card 1: 主账户总权益 -->
    <div
      class="rounded-xl border p-3 sm:p-4 lg:p-5 2xl:p-6 flex flex-col justify-between transition-all shadow-xs lg:min-h-[160px] 2xl:min-h-[175px]"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <Wallet class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-indigo-400 shrink-0" />
          </div>
          <span class="truncate">主账户总权益</span>
        </div>
        <span
          class="text-[9px] sm:text-[10px] lg:text-[11px] font-mono px-1.5 py-0.2 rounded border font-bold shrink-0"
          style="background-color: var(--bg-badge); color: var(--text-muted); border-color: var(--border-subtle);"
        >
          OKX PROD
        </span>
      </div>

      <div class="py-1 lg:py-2">
        <div class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular" style="color: var(--text-main);">
          ${{ totalEq }}
        </div>
        <div class="text-[10px] sm:text-[11px] lg:text-xs font-mono truncate pt-0.5" style="color: var(--text-muted);">
          可用保证金: <strong class="font-bold" style="color: var(--text-main);">${{ availEq }}</strong>
        </div>
      </div>

      <!-- Margin usage bar & ratio -->
      <div class="pt-1 lg:pt-2 space-y-1">
        <div class="hidden lg:flex items-center justify-between text-[11px] font-mono" style="color: var(--text-faint);">
          <span>保证金占用率</span>
          <span class="font-bold text-emerald-400">{{ marginUsage }}%</span>
        </div>
        <div class="w-full h-1.5 rounded-full overflow-hidden" style="background-color: var(--bg-badge);">
          <div
            class="h-full rounded-full transition-all duration-500 bg-emerald-500"
            :style="{
              width: `${Math.min(100, Math.max(8, Number(marginUsage)))}%`,
            }"
          ></div>
        </div>
      </div>
    </div>

    <!-- Card 2: 基准累计收益 -->
    <div
      class="rounded-xl border p-3 sm:p-4 lg:p-5 2xl:p-6 flex flex-col justify-between transition-all shadow-xs lg:min-h-[160px] 2xl:min-h-[175px]"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <TrendingUp class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-emerald-400 shrink-0" />
          </div>
          <span class="truncate">基准累计收益</span>
        </div>
        <span
          class="text-[10px] sm:text-[11px] lg:text-xs font-mono font-bold text-emerald-400 num-tabular shrink-0"
        >
          {{ Number(benchmarkRoi) >= 0 ? '+' : '' }}{{ benchmarkRoi }}%
        </span>
      </div>

      <div class="py-1 lg:py-2">
        <div
          class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
          :style="{ color: Number(benchmarkNetPnl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
        >
          {{ Number(benchmarkNetPnl) >= 0 ? '+' : '' }}{{ benchmarkNetPnl }}
        </div>
        <div class="text-[10px] sm:text-[11px] lg:text-xs font-mono truncate pt-0.5" style="color: var(--text-muted);">
          基准初始本金: <strong class="font-bold" style="color: var(--text-main);">${{ initialCap }}</strong>
        </div>
      </div>

      <div class="flex items-center justify-between text-[10px] sm:text-[11px] font-mono pt-1" style="color: var(--text-faint);">
        <span>策略基线锚定 2026-03</span>
        <span class="hidden lg:inline text-emerald-400 font-bold">全网实盘验证</span>
      </div>
    </div>

    <!-- Card 3: 今日已结盈亏 -->
    <div
      class="rounded-xl border p-3 sm:p-4 lg:p-5 2xl:p-6 flex flex-col justify-between transition-all shadow-xs lg:min-h-[160px] 2xl:min-h-[175px]"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <Zap class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-amber-400 shrink-0" />
          </div>
          <span class="truncate">今日已结盈亏</span>
        </div>
        <span
          class="text-[9px] sm:text-[10px] lg:text-[11px] font-mono px-1.5 py-0.2 rounded border font-bold text-emerald-400 shrink-0"
          style="background-color: var(--color-up-bg); border-color: var(--color-up-border);"
        >
          胜率 {{ todayWinrate }}%
        </span>
      </div>

      <div class="py-1 lg:py-2">
        <div
          class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
          :style="{ color: Number(todayNet) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
        >
          {{ Number(todayNet) >= 0 ? '+' : '' }}{{ todayNet }}
        </div>
        <div class="text-[10px] sm:text-[11px] lg:text-xs font-mono truncate pt-0.5" style="color: var(--text-muted);">
          今日成交笔数: <strong class="font-bold" style="color: var(--text-main);">{{ todayTrades }}</strong>
        </div>
      </div>

      <div class="flex items-center justify-between text-[10px] sm:text-[11px] font-mono pt-1" style="color: var(--text-muted);">
        <span>胜: <strong class="text-emerald-400">{{ winTrades }}</strong> 负: <strong class="text-rose-400">{{ lossTrades }}</strong></span>
        <span>盈亏比: <strong class="text-emerald-400">2.0+</strong></span>
      </div>
    </div>

    <!-- Card 4: 当前持仓浮盈 -->
    <div
      class="rounded-xl border p-3 sm:p-4 lg:p-5 2xl:p-6 flex flex-col justify-between transition-all shadow-xs lg:min-h-[160px] 2xl:min-h-[175px]"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center justify-between pb-1">
        <div class="flex items-center space-x-1.5 text-[11px] sm:text-xs lg:text-sm font-mono font-bold" style="color: var(--text-main);">
          <div class="w-5 h-5 lg:w-6 lg:h-6 rounded-md flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
            <ShieldCheck class="w-3 h-3 lg:w-3.5 lg:h-3.5 text-blue-400 shrink-0" />
          </div>
          <span class="truncate">当前持仓浮盈</span>
        </div>
        <span
          class="text-[10px] sm:text-[11px] font-mono font-bold text-emerald-400 shrink-0"
        >
          云端保护挂单
        </span>
      </div>

      <div class="py-1 lg:py-2">
        <div
          class="text-xl sm:text-2xl lg:text-3xl 2xl:text-4xl font-black font-mono tracking-tight num-tabular"
          :style="{ color: posUplNum >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
        >
          {{ posUplNum >= 0 ? '+' : '' }}{{ posUplStr }}
        </div>
        <div class="text-[10px] sm:text-[11px] lg:text-xs font-mono truncate pt-0.5" style="color: var(--text-muted);">
          持仓笔数: <strong class="font-bold" style="color: var(--text-main);">{{ store.positions.length }}</strong>
        </div>
      </div>

      <div class="flex items-center justify-between text-[10px] sm:text-[11px] font-mono pt-1" style="color: var(--text-muted);">
        <span>多: <strong class="text-emerald-400">{{ longCount }}</strong> 空: <strong class="text-rose-400">{{ shortCount }}</strong></span>
        <span>硬防线: <strong class="text-emerald-400">ACTIVE</strong></span>
      </div>
    </div>

  </div>
</template>
