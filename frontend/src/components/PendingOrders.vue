<script setup lang="ts">
import { useDashboardStore } from '../stores/dashboard'
import { Clock, CheckCircle2 } from 'lucide-vue-next'

const store = useDashboardStore()
</script>

<template>
  <div
    class="rounded-xl border transition-all shadow-xs p-4 sm:p-5"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <div
      class="flex flex-wrap items-center justify-between gap-2"
      :class="store.pendingOrders.length > 0 ? 'pb-3 mb-3 border-b' : ''"
      style="border-color: var(--border-subtle);"
    >
      <div class="flex items-center space-x-2.5">
        <div
          class="w-6 h-6 rounded-md flex items-center justify-center border"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <Clock class="w-3.5 h-3.5" />
        </div>
        <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wider" style="color: var(--text-main);">
          在途限价挂单监控 (Maker Orders)
        </h2>
        <span
          class="px-2 py-0.5 rounded text-xs font-mono font-bold border"
          style="background-color: var(--bg-badge); color: var(--color-brand); border-color: var(--border-subtle);"
        >
          {{ store.pendingOrders.length }} 笔在途
        </span>
      </div>
      <span class="text-xs font-mono hidden sm:inline" style="color: var(--text-faint);">
        被动撮合成交 · 享受交易所负手续费 Rebate
      </span>
    </div>

    <!-- Empty State: Compact & Tidy -->
    <div
      v-if="store.pendingOrders.length === 0"
      class="py-3 px-4 text-center rounded-lg border border-dashed text-xs font-mono"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      当前无在途限价挂单 · 挂单池就绪 (AI 决策周期动态报单与智能重挂)
    </div>

    <!-- Table -->
    <div v-else class="overflow-x-auto">
      <table class="w-full text-left text-xs font-mono whitespace-nowrap">
        <thead>
          <tr
            class="text-[11px] uppercase tracking-wider border-b"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <th class="py-2.5 px-3.5 font-bold">订单号</th>
            <th class="py-2.5 px-3.5 font-bold">标的</th>
            <th class="py-2.5 px-3.5 font-bold">操作类型</th>
            <th class="py-2.5 px-3.5 font-bold">挂单限价</th>
            <th class="py-2.5 px-3.5 font-bold">委托数量</th>
            <th class="py-2.5 px-3.5 font-bold">挂单时间</th>
            <th class="py-2.5 px-3.5 text-right font-bold">状态</th>
          </tr>
        </thead>
        <tbody class="divide-y" style="border-color: var(--border-subtle);">
          <tr
            v-for="ord in store.pendingOrders"
            :key="ord.ordId"
            class="transition-colors"
            :class="'hover:bg-[var(--bg-card-hover)]'"
          >
            <td class="py-2.5 px-3.5 font-mono text-xs" style="color: var(--text-faint);">
              {{ ord.ordId }}
            </td>
            <td class="py-2.5 px-3.5 font-black font-mono text-sm" style="color: var(--text-main);">
              {{ ord.name || ord.inst || (ord.instId ? ord.instId.split('-')[0] : '--') }}
            </td>
            <td class="py-2.5 px-3.5">
              <span
                class="px-2 py-0.5 rounded text-[11px] font-bold inline-flex items-center space-x-1 border"
                :style="{
                  backgroundColor: (ord.side_raw === 'buy' || ord.side === 'buy' || String(ord.side).includes('多')) ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                  borderColor: (ord.side_raw === 'buy' || ord.side === 'buy' || String(ord.side).includes('多')) ? 'var(--color-up-border)' : 'var(--color-down-border)',
                  color: (ord.side_raw === 'buy' || ord.side === 'buy' || String(ord.side).includes('多')) ? 'var(--color-up)' : 'var(--color-down)'
                }"
              >
                <span>{{ (ord.side_raw === 'buy' || ord.side === 'buy' || String(ord.side).includes('多')) ? '买入开多' : '卖出开空' }}</span>
              </span>
            </td>
            <td class="py-2.5 px-3.5 font-mono font-black num-tabular text-sm" style="color: var(--text-main);">
              ${{ ord.px }}
            </td>
            <td class="py-2.5 px-3.5 font-bold num-tabular" style="color: var(--text-main);">
              {{ ord.sz }} base
            </td>
            <td class="py-2.5 px-3.5 num-tabular" style="color: var(--text-muted);">
              {{ ord.time || (ord.cTime ? new Date(parseInt(ord.cTime)).toLocaleTimeString() : '--') }}
            </td>
            <td class="py-2.5 px-3.5 text-right font-bold" style="color: var(--color-brand);">
              <span class="inline-flex items-center space-x-1">
                <span class="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span>
                <span>挂单中</span>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
