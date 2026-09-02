<script setup lang="ts">
import { computed } from 'vue'
import MetricCard from '../components/MetricCard.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import EquityChart from '../components/EquityChart.vue'
import { useTerminalStore } from '../stores/terminal'
const store=useTerminalStore()
const acc=computed(()=>store.account||{})
const today=computed(()=>store.data.today_stats||{})
const perf=computed(()=>store.data.performance||{})
const money=(v:any,d=2)=>Number(v||0).toFixed(d)
const signed=(v:any)=>`${Number(v||0)>=0?'+':''}${money(v)}`
const initial=computed(()=>Number(acc.value.initial_capital||10000))
const peak=computed(()=>Math.max(initial.value,...(store.data.snapshots||[]).map((x:any)=>Number(x.total_eq||x.equity||initial.value))))
</script>
<template>
  <div class="space-y-4">
    <section class="panel p-3 sm:p-4">
      <div class="grid grid-cols-1 gap-3 divide-y divider min-[380px]:grid-cols-2 lg:grid-cols-4 lg:gap-4 lg:divide-x lg:divide-y-0">
        <MetricCard label="官方账户总权益" :value="money(acc.total_eq)" suffix="USDT" tone="brand"><template #footer><span class="data-pill data-pill-brand">可用 <b>{{ money(acc.avail_eq,1) }} U</b></span><span class="data-pill">占用率 <b>{{ money(acc.margin_usage_pct,1) }}%</b></span></template></MetricCard>
        <MetricCard label="累计净收益" :value="signed(acc.cum_net_pnl)" suffix="USDT" :tone="Number(acc.cum_net_pnl||0)>=0?'profit':'loss'"><template #footer><span class="data-pill" :class="Number(acc.cum_roi_pct||0)>=0?'data-pill-profit':'data-pill-loss'">ROI <b>{{ signed(acc.cum_roi_pct) }}%</b></span><span class="data-pill">手续费 <b>{{ money(acc.cum_total_fees) }} U</b></span></template></MetricCard>
        <MetricCard label="今日已结盈亏" :value="signed(today.net_realized)" suffix="USDT" :tone="Number(today.net_realized||0)>=0?'profit':'loss'"><template #footer><span class="data-pill data-pill-profit"><b>{{ today.win_trades||0 }}胜</b></span><span class="data-pill data-pill-loss"><b>{{ today.loss_trades||0 }}负</b></span><span class="data-pill data-pill-brand">胜率 <b>{{ money(today.win_rate,1) }}%</b></span></template></MetricCard>
        <MetricCard label="在途持仓浮盈" :value="signed(acc.upl)" suffix="USDT" :tone="Number(acc.upl||0)>=0?'profit':'loss'"><template #badge><span class="badge badge-profit">{{ store.positions.length }}/6 仓</span></template><template #footer><span class="data-pill data-pill-profit"><span class="pulse-dot"></span><b>云端止盈止损守护</b></span></template></MetricCard>
      </div>
    </section>

    <section class="panel p-4 sm:p-5">
      <SectionHeader title="实盘在途持仓" subtitle="交易所实时盯盘与云端保护状态" :count="`${store.positions.length} / 6`" tone="profit"/>
      <div v-if="store.positions.length" class="hidden overflow-x-auto md:block">
        <table class="terminal-table font-mono"><thead><tr><th>标的 / 杠杆</th><th>方向</th><th>开仓均价</th><th>标记现价</th><th>保证金</th><th>云端止损</th><th class="text-right">净浮盈</th><th class="text-right">ROI</th></tr></thead>
          <tbody><tr v-for="p in store.positions" :key="p.instId"><td><b>{{ p.name||p.instId }}</b> <span class="badge">{{ p.lever||3 }}x</span></td><td><span class="badge" :class="String(p.posSide).includes('long')?'badge-profit':'badge-loss'">{{ String(p.posSide).includes('long')?'做多':'做空' }}</span></td><td>{{ p.avgPx||'--' }}</td><td><b>{{ p.markPx||'--' }}</b></td><td>{{ money(p.margin_usdt,1) }} U</td><td>{{ p.exchangeSl||'--' }}</td><td class="text-right" :class="Number(p.upl)>=0?'text-profit':'text-loss'">{{ signed(p.upl) }} U</td><td class="text-right" :class="Number(p.roi_pct)>=0?'text-profit':'text-loss'">{{ signed(p.roi_pct) }}%</td></tr></tbody></table>
      </div>
      <div v-if="store.positions.length" class="grid gap-2.5 md:hidden"><article v-for="p in store.positions" :key="p.instId" class="subpanel p-3"><div class="flex items-center justify-between gap-2"><div><b class="font-mono text-sm">{{ p.name||p.instId }}</b><span class="badge ml-2">{{ p.lever||3 }}x</span></div><span class="badge" :class="String(p.posSide).includes('long')?'badge-profit':'badge-loss'">{{ String(p.posSide).includes('long')?'做多':'做空' }}</span></div><div class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t divider pt-3 text-[11px] font-mono"><div class="muted">开仓均价 <b class="float-right text-main">{{ p.avgPx||'--' }}</b></div><div class="muted">标记现价 <b class="float-right text-main">{{ p.markPx||'--' }}</b></div><div class="muted">保证金 <b class="float-right text-main">{{ money(p.margin_usdt,1) }} U</b></div><div class="muted">云端止损 <b class="float-right text-main">{{ p.exchangeSl||'--' }}</b></div></div><div class="mt-3 flex items-center justify-between border-t divider pt-3 font-mono text-xs"><span class="muted">净浮盈 / ROI</span><b :class="Number(p.upl)>=0?'text-profit':'text-loss'">{{ signed(p.upl) }} U · {{ signed(p.roi_pct) }}%</b></div></article></div><EmptyState v-else text="当前无在途持仓"/>
    </section>

    <section class="panel p-4 sm:p-5">
      <SectionHeader title="在途限价挂单" subtitle="盘口做市挂单与预设保护" :count="store.orders.length" tone="warning"/>
      <div v-if="store.orders.length" class="hidden overflow-x-auto md:block"><table class="terminal-table font-mono"><thead><tr><th>标的</th><th>类型</th><th>挂单价</th><th>数量</th><th>止盈</th><th>止损</th><th>时间</th></tr></thead><tbody><tr v-for="(o,i) in store.orders" :key="o.ordId||i"><td><b>{{ o.inst }}</b></td><td>{{ o.side }}</td><td>{{ o.px }}</td><td>{{ o.sz }} 张</td><td class="text-profit">{{ o.tp_px }}</td><td class="text-loss">{{ o.sl_px }}</td><td class="muted">{{ o.time }}</td></tr></tbody></table></div><div v-if="store.orders.length" class="grid gap-2.5 md:hidden"><article v-for="(o,i) in store.orders" :key="o.ordId||i" class="subpanel p-3"><div class="flex justify-between gap-2"><b class="font-mono text-sm">{{ o.inst }}</b><span class="badge badge-warning">{{ o.side }}</span></div><div class="mt-3 grid grid-cols-2 gap-2 border-t divider pt-3 text-[11px] font-mono"><span class="muted">挂单价 <b class="float-right text-main">{{ o.px }}</b></span><span class="muted">数量 <b class="float-right text-main">{{ o.sz }}张</b></span><span class="muted">止盈 <b class="float-right text-profit">{{ o.tp_px }}</b></span><span class="muted">止损 <b class="float-right text-loss">{{ o.sl_px }}</b></span></div><p class="mt-2 text-right text-[10px] muted">{{ o.time }}</p></article></div><EmptyState v-else text="当前无在途限价挂单"/>
    </section>

    <section class="panel p-4 sm:p-5">
      <SectionHeader title="官方账户总权益走势" subtitle="账户净值、初始本金与动态回撤"><template #actions><div class="hidden gap-4 text-[10px] font-mono muted sm:flex"><span>初始 <b class="text-main">{{ money(initial) }} U</b></span><span>峰值 <b class="text-profit">{{ money(peak) }} U</b></span><span>胜率 <b>{{ money(perf.win_rate,1) }}%</b></span></div></template></SectionHeader>
      <EquityChart :snapshots="store.data.snapshots||[]" :initial-capital="initial"/>
    </section>
  </div>
</template>
