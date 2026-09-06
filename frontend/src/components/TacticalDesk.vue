<script setup lang="ts">
import { ref, computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import {
  ShieldCheck,
  ShieldAlert,
  Layers,
  Clock,
  Activity,
} from 'lucide-vue-next'

const store = useDashboardStore()

const activeTab = ref<'positions' | 'orders'>('positions')
const selectedSymbol = ref<string>('ALL')
const searchQuery = ref<string>('')

const availableSymbols = computed(() => {
  const set = new Set<string>()
  store.positions.forEach((p) => {
    const s = p.name || p.instId.split('-')[0]
    if (s) set.add(s)
  })
  store.pendingOrders.forEach((o) => {
    const s = o.name || o.instId.split('-')[0]
    if (s) set.add(s)
  })
  return ['ALL', ...Array.from(set)]
})

const filteredPositions = computed(() => {
  return store.positions.filter((p) => {
    const sym = p.name || p.instId.split('-')[0]
    const matchSymbol = selectedSymbol.value === 'ALL' || sym === selectedSymbol.value
    const matchQuery =
      !searchQuery.value ||
      p.instId.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      (p.name && p.name.toLowerCase().includes(searchQuery.value.toLowerCase()))
    return matchSymbol && matchQuery
  })
})

const filteredOrders = computed(() => {
  return store.pendingOrders.filter((o) => {
    const sym = o.name || o.instId.split('-')[0]
    const matchSymbol = selectedSymbol.value === 'ALL' || sym === selectedSymbol.value
    const matchQuery =
      !searchQuery.value ||
      o.instId.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      (o.name && o.name.toLowerCase().includes(searchQuery.value.toLowerCase()))
    return matchSymbol && matchQuery
  })
})

function fmt2(v: any): string {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? ''))
  return Number.isFinite(n) ? n.toFixed(2) : '--'
}

function fmt4(v: any): string {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? ''))
  if (!Number.isFinite(n)) return '--'
  return n >= 100 ? n.toFixed(2) : String(parseFloat(n.toFixed(4)))
}

const allProtected = computed(() =>
  store.positions.length > 0 &&
  store.positions.every(
    (p: any) =>
      p.protectionStatus === 'fully_protected' ||
      Number(p.protectionCoveragePct || 0) >= 100
  )
)
</script>

<template>
  <div
    class="rounded-xl border transition-all shadow-xs overflow-hidden"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Tactical Desk Header Ribbon -->
    <div
      class="px-4 py-2.5 2xl:px-6 2xl:py-3 border-b flex flex-wrap items-center justify-between gap-2.5 2xl:gap-3.5"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- Left: Desk Tabs Switcher -->
      <div class="flex items-center space-x-1 p-0.5 rounded-lg border text-xs 2xl:text-sm font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <button
          @click="activeTab = 'positions'"
          class="h-7.5 2xl:h-8.5 flex items-center space-x-2 px-3 2xl:px-4 rounded-md font-bold transition-all cursor-pointer"
          :style="activeTab === 'positions'
            ? { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-main)', borderColor: 'var(--border-medium)', boxShadow: 'var(--shadow-card)' }
            : { color: 'var(--text-muted)' }"
          :class="activeTab === 'positions' ? 'border shadow-xs' : 'hover:text-[var(--text-main)]'"
        >
          <Activity class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          <span>在途实盘持仓</span>
          <span
            class="px-1.5 py-0.2 2xl:px-2 rounded-full text-[10px] 2xl:text-xs font-mono font-bold"
            :style="activeTab === 'positions'
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { backgroundColor: 'var(--bg-badge)', color: 'var(--text-muted)' }"
          >
            {{ store.positions.length }}
          </span>
        </button>

        <button
          @click="activeTab = 'orders'"
          class="h-7.5 2xl:h-8.5 flex items-center space-x-2 px-3 2xl:px-4 rounded-md font-bold transition-all cursor-pointer"
          :style="activeTab === 'orders'
            ? { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-main)', borderColor: 'var(--border-medium)', boxShadow: 'var(--shadow-card)' }
            : { color: 'var(--text-muted)' }"
          :class="activeTab === 'orders' ? 'border shadow-xs' : 'hover:text-[var(--text-main)]'"
        >
          <Clock class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          <span>在途限价挂单</span>
          <span
            class="px-1.5 py-0.2 2xl:px-2 rounded-full text-[10px] 2xl:text-xs font-mono font-bold"
            :style="activeTab === 'orders'
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { backgroundColor: 'var(--bg-badge)', color: 'var(--text-muted)' }"
          >
            {{ store.pendingOrders.length }}
          </span>
        </button>
      </div>

      <!-- Right: Search & Protection Indicator -->
      <div class="flex items-center space-x-2 2xl:space-x-3">
        <!-- Coin Quick Filters -->
        <div class="hidden sm:flex items-center space-x-1">
          <button
            v-for="sym in availableSymbols"
            :key="sym"
            @click="selectedSymbol = sym"
            class="h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-md text-[11px] 2xl:text-xs font-mono transition-all cursor-pointer border"
            :style="selectedSymbol === sym
              ? { backgroundColor: 'var(--bg-badge)', borderColor: 'var(--border-medium)', color: 'var(--text-main)', fontWeight: 'bold' }
              : { borderColor: 'transparent', color: 'var(--text-muted)' }"
          >
            {{ sym }}
          </button>
        </div>

        <!-- Cloud OCO Status Badge -->
        <div
          class="h-7.5 2xl:h-8.5 flex items-center space-x-1.5 text-xs 2xl:text-sm font-mono px-2.5 2xl:px-3.5 rounded-lg border font-medium"
          :style="{
            backgroundColor: allProtected ? 'var(--color-up-bg)' : 'var(--color-warn-bg)',
            borderColor: allProtected ? 'var(--color-up-border)' : 'var(--color-warn-border)',
            color: allProtected ? 'var(--color-up)' : 'var(--color-warn)'
          }"
        >
          <ShieldCheck v-if="allProtected" class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          <ShieldAlert v-else class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          <span class="hidden md:inline">{{ allProtected ? '100% 交易所云端 OCO 止损' : '部分仓位未设止损' }}</span>
          <span class="md:hidden">{{ allProtected ? '100% OCO' : '未全覆盖' }}</span>
        </div>
      </div>
    </div>

    <!-- TAB CONTENT 1: POSITIONS -->
    <div v-if="activeTab === 'positions'">
      <div v-if="filteredPositions.length === 0" class="py-12 2xl:py-16 text-center rounded-b-xl border-dashed">
        <div
          class="w-10 h-10 2xl:w-12 2xl:h-12 mx-auto mb-2.5 2xl:mb-3 rounded-xl flex items-center justify-center border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <Layers class="w-4 h-4 2xl:w-5 2xl:h-5" />
        </div>
        <p class="text-xs 2xl:text-sm font-mono font-medium" style="color: var(--text-muted);">
          {{ store.positions.length === 0 ? '当前无在途实盘持仓 · AI 引擎空仓防御与等待中' : '无符合当前筛选条件的持仓' }}
        </p>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-left text-xs 2xl:text-sm font-mono whitespace-nowrap">
          <thead>
            <tr
              class="text-[11px] 2xl:text-xs uppercase tracking-wider border-b"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[16%] 2xl:w-[15%]">标的 / 杠杆</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[10%] 2xl:w-[10%]">方向</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[11%] 2xl:w-[11%]">持仓量</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[13%] 2xl:w-[13%]">开仓均价</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[14%] 2xl:w-[14%]">最新标记价</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[13%] 2xl:w-[13%]">保证金占用</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[13%] 2xl:w-[13%]">云端止损防线</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 text-right font-bold w-[10%] 2xl:w-[11%]">未结盈亏 / ROI</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="pos in filteredPositions"
              :key="pos.instId"
              class="border-b last:border-b-0 transition-colors hover:bg-[var(--bg-card-hover)]"
              style="border-color: var(--border-subtle);"
            >
              <!-- 标的 / 杠杆 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5">
                <div class="flex items-center space-x-2">
                  <span class="font-black text-sm 2xl:text-base tracking-wide font-mono" style="color: var(--text-main);">
                    {{ pos.name }}
                  </span>
                  <span class="badge-lever">
                    {{ pos.lever }}x
                  </span>
                </div>
              </td>

              <!-- 方向 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5">
                <span :class="pos.side === 'long' ? 'capsule-direction-long' : 'capsule-direction-short'">
                  <span>{{ pos.side === 'long' ? '多头 BUY' : '空头 SELL' }}</span>
                </span>
              </td>

              <!-- 持仓量 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-bold num-tabular" style="color: var(--text-main);">
                {{ pos.pos }} <span class="text-[10px] 2xl:text-xs font-normal" style="color: var(--text-faint);">张</span>
              </td>

              <!-- 开仓均价 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-mono num-tabular" style="color: var(--text-muted);">
                ${{ fmt2(pos.avgPx) }}
              </td>

              <!-- 标记市价 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-black font-mono text-sm 2xl:text-base num-tabular" style="color: var(--text-main);">
                ${{ fmt4(pos.markPx ?? pos.last) }}
              </td>

              <!-- 实际保证金 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-mono num-tabular" style="color: var(--text-main);">
                ${{ fmt2(pos.margin_usdt ?? pos.margin) }} <span class="text-[10px] 2xl:text-xs" style="color: var(--text-faint);">U</span>
              </td>

              <!-- 云端止损防线 -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5">
                <div
                  class="inline-flex items-center space-x-1 px-2 py-0.5 2xl:px-2.5 2xl:py-1 rounded-[3px] border text-[11px] 2xl:text-xs"
                  :style="{
                    backgroundColor: 'var(--bg-badge)',
                    borderColor: 'var(--border-subtle)',
                    color: pos.side === 'long' ? 'var(--color-down)' : 'var(--color-up)'
                  }"
                >
                  <ShieldCheck class="w-3 h-3 2xl:w-3.5 2xl:h-3.5 shrink-0" />
                  <span class="font-bold num-tabular">${{ pos.displayStop || '--' }}</span>
                </div>
              </td>

              <!-- 未结浮盈 / ROI -->
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 text-right">
                <div
                  class="text-sm 2xl:text-base font-black font-mono num-tabular"
                  :style="{ color: Number(pos.upl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
                >
                  {{ Number(pos.upl) >= 0 ? '+' : '' }}{{ fmt2(pos.upl) }} U
                </div>
                <div
                  class="text-[10px] 2xl:text-xs font-bold font-mono num-tabular"
                  :style="{ color: Number(pos.uplRatio ?? pos.roi) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
                >
                  {{ Number(pos.uplRatio ?? pos.roi) >= 0 ? '+' : '' }}{{ fmt2(pos.uplRatio ?? pos.roi) }}%
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- TAB CONTENT 2: PENDING ORDERS -->
    <div v-else>
      <div v-if="filteredOrders.length === 0" class="py-12 2xl:py-16 text-center rounded-b-xl border-dashed">
        <div
          class="w-10 h-10 2xl:w-12 2xl:h-12 mx-auto mb-2.5 2xl:mb-3 rounded-xl flex items-center justify-center border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <Clock class="w-4 h-4 2xl:w-5 2xl:h-5" />
        </div>
        <p class="text-xs 2xl:text-sm font-mono font-medium" style="color: var(--text-muted);">
          {{ store.pendingOrders.length === 0 ? '当前无在途限价挂单 · 挂单池就绪 (AI 周期动态调整)' : '无符合当前筛选条件的在途挂单' }}
        </p>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-left text-xs 2xl:text-sm font-mono whitespace-nowrap">
          <thead>
            <tr
              class="text-[11px] 2xl:text-xs uppercase tracking-wider border-b"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[18%] 2xl:w-[18%]">订单号</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[14%] 2xl:w-[14%]">标的</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[13%] 2xl:w-[13%]">操作类型</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[14%] 2xl:w-[14%]">挂单限价</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[13%] 2xl:w-[13%]">委托数量</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 font-bold w-[14%] 2xl:w-[14%]">挂单时间</th>
              <th class="py-2.5 px-4 2xl:px-6 2xl:py-3.5 text-right font-bold w-[14%] 2xl:w-[14%]">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ord in filteredOrders"
              :key="ord.ordId"
              class="border-b last:border-b-0 transition-colors hover:bg-[var(--bg-card-hover)]"
              style="border-color: var(--border-subtle);"
            >
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-mono text-xs 2xl:text-sm" style="color: var(--text-faint);">
                {{ ord.ordId }}
              </td>
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-black font-mono text-sm 2xl:text-base" style="color: var(--text-main);">
                {{ ord.name || ord.inst || (ord.instId ? ord.instId.split('-')[0] : '--') }}
              </td>
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5">
                <span :class="(ord.side_raw === 'buy' || ord.side === 'buy' || String(ord.side).includes('多')) ? 'capsule-direction-long' : 'capsule-direction-short'">
                  <span>{{ (ord.side_raw === 'buy' || ord.side === 'buy' || String(ord.side).includes('多')) ? '买入开多' : '卖出开空' }}</span>
                </span>
              </td>
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-mono font-black num-tabular text-sm 2xl:text-base" style="color: var(--text-main);">
                ${{ ord.px }}
              </td>
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 font-bold num-tabular" style="color: var(--text-main);">
                {{ ord.sz }} 张
              </td>
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 num-tabular" style="color: var(--text-muted);">
                {{ ord.time || (ord.cTime ? new Date(parseInt(ord.cTime)).toLocaleTimeString() : '--') }}
              </td>
              <td class="py-3 px-4 2xl:px-6 2xl:py-3.5 text-right font-bold" style="color: var(--text-main);">
                <span class="inline-flex items-center space-x-1.5 text-[11px] 2xl:text-xs font-mono px-2 py-0.5 2xl:px-2.5 2xl:py-1 rounded-[3px] border" style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--color-brand);">
                  <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                  <span>挂单中</span>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
