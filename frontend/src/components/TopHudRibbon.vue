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
  <!-- P1: Single-row KPI band (Binance stat-callout pattern) — flat, no card fill,
       every metric carries a history anchor (Δ/sub), directional color is text-only -->
  <div
    class="rounded-xl border px-3 sm:px-4 py-2.5 flex flex-wrap items-center gap-x-5 gap-y-2 sm:gap-x-7 shadow-xs transition-colors"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- 1. Total equity — the ONE display-size anchor -->
    <div class="flex items-baseline space-x-1.5" :title="`${t('hud.availMargin')} ${availEq} · ${t('hud.initialCapital')} ${initialCap}`">
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-muted);">{{ t('hud.accountEquity') }}</span>
      <span class="text-[20px] leading-none font-black font-mono tracking-tight num-tabular" style="color: var(--text-primary);">{{ totalEq }}</span>
      <span class="text-[11px] font-mono" style="color: var(--text-faint);">USDT</span>
      <span
        class="text-[11px] font-mono font-bold num-tabular"
        :style="{ color: Number(todayNet) > 0 ? 'var(--color-up)' : Number(todayNet) < 0 ? 'var(--color-down)' : 'var(--text-muted)' }"
      >{{ Number(todayNet) > 0 ? '▲' : Number(todayNet) < 0 ? '▼' : '·' }} {{ Number(todayNet) >= 0 ? '+' : '' }}{{ todayNet }} {{ t('nav.today') }}</span>
    </div>

    <!-- 2. Today realized -->
    <div class="flex items-baseline space-x-1.5" :title="`${t('hud.fundingFee')} ${todayFunding} · ${t('hud.tradingFee')} ${todayFees}`">
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-muted);">{{ t('hud.todaySettled') }}</span>
      <span
        class="text-[15px] leading-none font-bold font-mono num-tabular"
        :style="{ color: Number(todayNet) > 0 ? 'var(--color-up)' : Number(todayNet) < 0 ? 'var(--color-down)' : 'var(--text-main)' }"
      >{{ Number(todayNet) >= 0 ? '+' : '' }}{{ todayNet }}</span>
      <span class="text-[11px] font-mono" style="color: var(--text-faint);">{{ todayTrades }} {{ t('hud.tradesCount') }} · {{ t('hud.winRate') }} {{ winRatePct }}%</span>
    </div>

    <!-- 3. Floating PnL with ROI anchor -->
    <div class="flex items-baseline space-x-1.5" :title="`${t('hud.holdingCapital')} ${totalPosMarginStr} · ${t('hud.notionalValue') || 'Notional'} ${totalPosNotionalStr}`">
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-muted);">{{ t('hud.unrealizedPnl') }}</span>
      <span
        class="text-[15px] leading-none font-bold font-mono num-tabular"
        :style="{ color: posUplNum > 0 ? 'var(--color-up)' : posUplNum < 0 ? 'var(--color-down)' : 'var(--text-main)' }"
      >{{ posUplNum >= 0 ? '+' : '' }}{{ posUplStr }}</span>
      <span
        class="text-[11px] font-mono font-bold num-tabular"
        :style="{ color: Number(posRoiPct) > 0 ? 'var(--color-up)' : Number(posRoiPct) < 0 ? 'var(--color-down)' : 'var(--text-faint)' }"
      >({{ Number(posRoiPct) >= 0 ? '+' : '' }}{{ posRoiPct }}%)</span>
    </div>

    <!-- 4. Long / Short -->
    <div class="flex items-baseline space-x-1.5">
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-muted);">{{ t('hud.longShort') || 'Long/Short' }}</span>
      <span class="text-[15px] leading-none font-bold font-mono num-tabular"><span style="color: var(--color-up);">{{ longCount }}</span><span class="text-[11px]" style="color: var(--text-faint);"> / </span><span style="color: var(--color-down);">{{ shortCount }}</span></span>
    </div>

    <!-- 5. Margin usage -->
    <div class="flex items-baseline space-x-1.5">
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-muted);">{{ t('hud.marginUsagePct') }}</span>
      <span class="text-[15px] leading-none font-bold font-mono num-tabular" style="color: var(--text-main);">{{ marginUsage }}%</span>
    </div>

    <!-- 6. OCO protection — shield + green TEXT only (D7: never a fill) -->
    <div class="flex items-center space-x-1.5 ml-auto" :title="`${t('hud.prodTag')} · ${totalPosCount} ${t('hud.trades')}`">
      <ShieldCheck class="w-3.5 h-3.5 shrink-0" :style="{ color: ocoProtectedRatio === '100%' ? 'var(--color-up)' : 'var(--color-warn)' }" />
      <span class="text-[11px] font-mono font-bold" :style="{ color: ocoProtectedRatio === '100%' ? 'var(--color-up)' : 'var(--color-warn)' }">OCO {{ ocoProtectedRatio }}</span>
    </div>
  </div>
</template>
