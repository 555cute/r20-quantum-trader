<script setup lang="ts">
import { ref, computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
import {
  ShieldCheck,
  ShieldAlert,
  Layers,
  Clock,
  Activity,
} from 'lucide-vue-next'

const store = useDashboardStore()
const i18n = useI18nStore()

const activeTab = ref<'positions' | 'orders'>('positions')
const selectedSymbol = ref<string>('ALL')

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
    return selectedSymbol.value === 'ALL' || sym === selectedSymbol.value
  })
})

const filteredOrders = computed(() => {
  return store.pendingOrders.filter((o) => {
    const sym = o.name || o.instId.split('-')[0]
    return selectedSymbol.value === 'ALL' || sym === selectedSymbol.value
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
    class="rounded-xl border transition-all shadow-xs overflow-hidden font-mono"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Tactical Desk Header Ribbon -->
    <div
      class="px-3 sm:px-4 py-2 border-b flex flex-wrap items-center justify-between gap-2.5"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- Left: Desk Tabs Switcher -->
      <div class="flex items-center space-x-1 p-0.5 rounded border text-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <button
          @click="activeTab = 'positions'"
          class="h-7 flex items-center space-x-1.5 px-2.5 rounded font-bold transition-all cursor-pointer"
          :style="activeTab === 'positions'
            ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
            : { color: 'var(--text-muted)' }"
        >
          <Activity class="w-3.5 h-3.5" />
          <span>{{ i18n.t.activePositions }}</span>
          <span
            class="px-1.5 py-0.2 rounded-full text-[10px] font-bold"
            :style="activeTab === 'positions'
              ? { backgroundColor: 'var(--bg-app)', color: 'var(--text-main)' }
              : { backgroundColor: 'var(--bg-badge)', color: 'var(--text-muted)' }"
          >
            {{ store.positions.length }}
          </span>
        </button>

        <button
          @click="activeTab = 'orders'"
          class="h-7 flex items-center space-x-1.5 px-2.5 rounded font-bold transition-all cursor-pointer"
          :style="activeTab === 'orders'
            ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
            : { color: 'var(--text-muted)' }"
        >
          <Clock class="w-3.5 h-3.5" />
          <span>{{ i18n.t.pendingOrders }}</span>
          <span
            class="px-1.5 py-0.2 rounded-full text-[10px] font-bold"
            :style="activeTab === 'orders'
              ? { backgroundColor: 'var(--bg-app)', color: 'var(--text-main)' }
              : { backgroundColor: 'var(--bg-badge)', color: 'var(--text-muted)' }"
          >
            {{ store.pendingOrders.length }}
          </span>
        </button>
      </div>

      <!-- Right: Search & Protection Indicator -->
      <div class="flex items-center space-x-2">
        <div class="hidden sm:flex items-center space-x-1">
          <button
            v-for="sym in availableSymbols"
            :key="sym"
            @click="selectedSymbol = sym"
            class="h-6.5 px-2 rounded text-[10px] font-bold transition-all cursor-pointer border"
            :style="selectedSymbol === sym
              ? { backgroundColor: 'var(--bg-badge)', borderColor: 'var(--border-medium)', color: 'var(--text-main)' }
              : { borderColor: 'transparent', color: 'var(--text-muted)' }"
          >
            {{ sym }}
          </button>
        </div>

        <!-- Cloud OCO Status Badge -->
        <div
          class="h-7 flex items-center space-x-1 text-[11px] px-2 rounded border font-bold"
          :style="{
            backgroundColor: allProtected ? 'var(--color-up-bg)' : 'var(--color-warn-bg)',
            borderColor: allProtected ? 'var(--color-up-border)' : 'var(--color-warn-border)',
            color: allProtected ? 'var(--color-up)' : 'var(--color-warn)'
          }"
        >
          <ShieldCheck v-if="allProtected" class="w-3 h-3" />
          <ShieldAlert v-else class="w-3 h-3" />
          <span>{{ allProtected ? (i18n.locale === 'zh' ? '100% 云端止损保护' : '100% Cloud OCO Protected') : (i18n.locale === 'zh' ? '部分仓位未设止损' : 'Partially Unprotected') }}</span>
        </div>
      </div>
    </div>

    <!-- TAB CONTENT 1: POSITIONS -->
    <div v-if="activeTab === 'positions'">
      <div v-if="filteredPositions.length === 0" class="py-12 text-center rounded-b-xl border-dashed">
        <div
          class="w-10 h-10 mx-auto mb-2 rounded-xl flex items-center justify-center border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <Layers class="w-4 h-4" />
        </div>
        <p class="text-xs" style="color: var(--text-muted);">
          {{ i18n.t.noPositions }}
        </p>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-left text-xs whitespace-nowrap">
          <thead>
            <tr
              class="text-[10px] uppercase tracking-wider border-b"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              <th class="py-2.5 px-3 font-bold">{{ i18n.locale === 'zh' ? '标的 / 杠杆' : 'Asset / Lev' }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.direction }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.positionSize }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.entryPrice }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.markPrice }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.locale === 'zh' ? '保证金占用' : 'Margin' }}</th>
              <th class="py-2.5 px-3 text-right font-bold">{{ i18n.locale === 'zh' ? '未结盈亏' : 'UPL / ROE' }}</th>
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
              <td class="py-2.5 px-3">
                <div class="flex items-center space-x-1.5">
                  <span class="font-black text-xs tracking-wide" style="color: var(--text-main);">
                    {{ pos.name }}
                  </span>
                  <span
                    class="px-1 py-0.2 rounded text-[9px] font-bold border"
                    style="background-color: var(--bg-badge); color: var(--text-main); border-color: var(--border-subtle);"
                  >
                    {{ pos.lever }}x
                  </span>
                </div>
              </td>

              <!-- 方向 -->
              <td class="py-2.5 px-3">
                <span
                  class="px-1.5 py-0.2 rounded text-[10px] font-bold inline-flex items-center space-x-1 border"
                  :style="{
                    backgroundColor: pos.side === 'long' ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                    borderColor: pos.side === 'long' ? 'var(--color-up-border)' : 'var(--color-down-border)',
                    color: pos.side === 'long' ? 'var(--color-up)' : 'var(--color-down)'
                  }"
                >
                  <span>{{ pos.side === 'long' ? i18n.t.actionBuy : i18n.t.actionSell }}</span>
                </span>
              </td>

              <!-- 持仓量 -->
              <td class="py-2.5 px-3 font-bold num-tabular" style="color: var(--text-main);">
                {{ pos.pos }} <span class="text-[10px] font-normal" style="color: var(--text-faint);">{{ i18n.locale === 'zh' ? '张' : 'cont' }}</span>
              </td>

              <!-- 开仓均价 -->
              <td class="py-2.5 px-3 num-tabular" style="color: var(--text-muted);">
                ${{ fmt2(pos.avgPx) }}
              </td>

              <!-- 标记市价 -->
              <td class="py-2.5 px-3 font-black text-xs num-tabular" style="color: var(--text-main);">
                ${{ fmt4(pos.markPx ?? pos.last) }}
              </td>

              <!-- 实际保证金 -->
              <td class="py-2.5 px-3 num-tabular" style="color: var(--text-main);">
                ${{ fmt2(pos.margin_usdt ?? pos.margin) }}
              </td>

              <!-- 未结盈亏 -->
              <td class="py-2.5 px-3 text-right">
                <div
                  class="font-black text-xs num-tabular"
                  :style="{ color: Number(pos.upl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
                >
                  {{ Number(pos.upl) >= 0 ? '+' : '' }}${{ fmt2(pos.upl) }}
                </div>
                <div
                  class="text-[10px] font-bold num-tabular"
                  :style="{ color: Number(pos.uplRatio ?? pos.roe_pct ?? 0) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
                >
                  {{ Number(pos.uplRatio ?? pos.roe_pct ?? 0) >= 0 ? '+' : '' }}{{ Number(pos.uplRatio ?? pos.roe_pct ?? 0).toFixed(2) }}%
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- TAB CONTENT 2: PENDING ORDERS -->
    <div v-else>
      <div v-if="filteredOrders.length === 0" class="py-12 text-center rounded-b-xl border-dashed">
        <div
          class="w-10 h-10 mx-auto mb-2 rounded-xl flex items-center justify-center border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <Clock class="w-4 h-4" />
        </div>
        <p class="text-xs" style="color: var(--text-muted);">
          {{ i18n.t.noOrders }}
        </p>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-left text-xs whitespace-nowrap">
          <thead>
            <tr
              class="text-[10px] uppercase tracking-wider border-b"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.symbol }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.direction }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.locale === 'zh' ? '委托类型' : 'Type' }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.locale === 'zh' ? '委托价' : 'Order Px' }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.locale === 'zh' ? '委托量' : 'Size' }}</th>
              <th class="py-2.5 px-3 font-bold">{{ i18n.t.tradeTime }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ord in filteredOrders"
              :key="ord.ordId"
              class="border-b last:border-b-0 transition-colors hover:bg-[var(--bg-card-hover)]"
              style="border-color: var(--border-subtle);"
            >
              <td class="py-2.5 px-3 font-bold text-slate-200">{{ ord.instId }}</td>
              <td class="py-2.5 px-3 font-bold" :class="ord.side === 'buy' ? 'text-emerald-400' : 'text-rose-400'">
                {{ ord.side === 'buy' ? i18n.t.actionBuy : i18n.t.actionSell }}
              </td>
              <td class="py-2.5 px-3 text-slate-400 uppercase text-[10px]">{{ ord.ordType }}</td>
              <td class="py-2.5 px-3 num-tabular text-slate-200">${{ ord.px || '--' }}</td>
              <td class="py-2.5 px-3 num-tabular text-slate-200">{{ ord.sz }}</td>
              <td class="py-2.5 px-3 text-slate-500 text-[10px]">{{ ord.cTime || '--' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
