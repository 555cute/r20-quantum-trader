<script setup lang="ts">
import { ref } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { TrendingUp, TrendingDown, ArrowUpRight, Compass, Cpu, Activity } from 'lucide-vue-next'
import FactorDetailModal from './FactorDetailModal.vue'

const store = useDashboardStore()
const selectedInstrument = ref<any | null>(null)
const drawerVisible = ref(false)

function openDetail(item: any) {
  selectedInstrument.value = item
  drawerVisible.value = true
}

function getActionStyle(action?: string) {
  if (action === 'BUY_LONG') {
    return {
      backgroundColor: 'var(--color-up-bg)',
      borderColor: 'var(--color-up-border)',
      color: 'var(--color-up)',
    }
  }
  if (action === 'SELL_SHORT') {
    return {
      backgroundColor: 'var(--color-down-bg)',
      borderColor: 'var(--color-down-border)',
      color: 'var(--color-down)',
    }
  }
  return {
    backgroundColor: 'var(--bg-badge)',
    borderColor: 'var(--border-subtle)',
    color: 'var(--text-muted)',
  }
}

function getActionLabel(action?: string) {
  if (action === 'BUY_LONG') return '顺势做多 BUY'
  if (action === 'SELL_SHORT') return '顺势做空 SELL'
  return '空仓等待 WAIT'
}
</script>

<template>
  <div class="space-y-2.5">
    <!-- Macro Summary Telemetry Strip -->
    <div
      class="rounded-xl border p-3 flex items-start space-x-2.5 transition-colors shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div
        class="w-6 h-6 rounded-md flex items-center justify-center border shrink-0 mt-0.5"
        style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
      >
        <Compass class="w-3.5 h-3.5" />
      </div>
      <div class="flex-1 min-w-0">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-bold font-mono uppercase tracking-wider" style="color: var(--color-brand);">
            宏观多周期推演基调
          </span>
          <span class="text-[10px] font-mono" style="color: var(--text-faint);">
            {{ store.data?.timestamp ? String(store.data.timestamp).slice(11, 19) : '' }}
          </span>
        </div>
        <p class="text-[11px] font-sans mt-0.5 leading-relaxed line-clamp-2" style="color: var(--text-muted);" :title="store.macroAssessment">
          {{ store.macroAssessment }}
        </p>
      </div>
    </div>

    <!-- Section Title -->
    <div class="flex items-center justify-between px-1">
      <div class="flex items-center space-x-2">
        <Activity class="w-3.5 h-3.5" style="color: var(--color-brand);" />
        <h2 class="text-xs font-mono font-black uppercase tracking-wider" style="color: var(--text-main);">
          {{ store.factors.length ? `${store.factors.length} 标的因果动力学与微结构雷达` : '动态资产池因果动力学与微结构雷达' }}
        </h2>
      </div>
      <span class="text-[10px] font-mono" style="color: var(--text-faint);">
        点击卡片下钻微积分推演
      </span>
    </div>

    <!-- 6-Asset Quantitative Ticker Cards Grid: 2 cols on narrow, 3 cols on tablet/desktop -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
      <div
        v-for="item in store.factors"
        :key="item.instId"
        @click="openDetail(item)"
        class="rounded-xl border p-2.5 transition-all duration-150 flex flex-col justify-between cursor-pointer group shadow-xs hover:border-[var(--border-medium)] hover:bg-[var(--bg-card-hover)]"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <!-- Top: Header Info -->
        <div>
          <div class="flex items-center justify-between pb-1.5 border-b" style="border-color: var(--border-subtle);">
            <div class="flex items-center space-x-1.5">
              <span class="font-mono font-black text-sm tracking-wide" style="color: var(--text-main);">
                {{ item.name }}
              </span>
              <span class="text-[9px] font-mono px-1 py-0.2 rounded border" style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-faint);">
                SWAP
              </span>
            </div>
            <div class="text-right font-mono">
              <div class="text-xs font-black num-tabular" style="color: var(--text-main);">
                ${{ item.price }}
              </div>
              <div
                class="text-[10px] font-bold font-mono flex items-center justify-end space-x-0.5 num-tabular"
                :style="{ color: item.chg24h >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
              >
                <TrendingUp v-if="item.chg24h >= 0" class="w-2.5 h-2.5" />
                <TrendingDown v-else class="w-2.5 h-2.5" />
                <span>{{ item.chg24h >= 0 ? '+' : '' }}{{ item.chg24h }}%</span>
              </div>
            </div>
          </div>

          <!-- Calculus Telemetry Grid -->
          <div
            class="grid grid-cols-4 gap-1 my-1.5 py-1 px-1.5 rounded-lg border text-[10px] font-mono"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint);">速度 v</div>
              <div
                class="font-bold num-tabular truncate"
                :style="{ color: (item.calculus?.velocity_1h ?? 0) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
              >
                {{ item.calculus?.velocity_1h ?? '--' }}
              </div>
            </div>
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint);">加速 a</div>
              <div class="font-bold num-tabular truncate" style="color: var(--text-main);">
                {{ item.calculus?.accel_1h ?? '--' }}
              </div>
            </div>
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint);">冲击 j</div>
              <div class="font-bold num-tabular truncate" style="color: var(--text-muted);">
                {{ item.calculus?.jerk_1h ?? '--' }}
              </div>
            </div>
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint);">ADX</div>
              <div class="font-bold num-tabular truncate" style="color: var(--color-brand);">
                {{ item.adx_1h ?? '--' }}
              </div>
            </div>
          </div>

          <!-- Microstructure Flow -->
          <div class="flex items-center justify-between text-[10px] font-mono mb-1.5 px-0.5" style="color: var(--text-muted);">
            <span>聪明钱: <strong class="num-tabular" style="color: var(--text-main);">{{ item.smart_money?.weighted_long_pct ?? 50 }}%多</strong></span>
            <span>净流: <strong class="num-tabular" style="color: var(--text-main);">{{ item.smart_money?.net_flow_usdt ?? '0 U' }}</strong></span>
          </div>
        </div>

        <!-- Bottom: Decision Status & Drawer Trigger -->
        <div class="pt-1.5 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center justify-between">
            <span
              class="px-1.5 py-0.2 rounded text-[10px] font-bold font-mono border"
              :style="getActionStyle(item.decision?.action || item.action)"
            >
              {{ getActionLabel(item.decision?.action || item.action) }}
            </span>
            <div class="flex items-center space-x-1 text-xs font-mono font-bold" style="color: var(--text-muted);">
              <span class="text-[10px]" style="color: var(--text-faint);">置信:</span>
              <span class="num-tabular" style="color: var(--text-main);">{{ item.decision?.confidence || item.confidence || 0 }}%</span>
              <ArrowUpRight class="w-3.5 h-3.5 opacity-60 group-hover:opacity-100 transition-opacity" />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Factor Detail Modal (Drawer) -->
    <FactorDetailModal
      v-if="drawerVisible && selectedInstrument"
      :visible="drawerVisible"
      :instrument="selectedInstrument"
      :full-prompt-text="store.data?.ai_last_prompt || ''"
      @close="drawerVisible = false"
    />
  </div>
</template>
