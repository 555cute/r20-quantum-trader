<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { ShieldCheck, ShieldAlert, Layers } from 'lucide-vue-next'

const store = useDashboardStore()

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
  store.positions.length > 0 && store.positions.every((p: any) => p.protectionStatus === 'fully_protected' || Number(p.protectionCoveragePct || 0) >= 100)
)
// 并发持仓上限等于标的池规模（执行层 MAX_CONCURRENT_POSITIONS = len(池)），
// 此前模板把分母写死成 6，扩池后会出现 "11 / 6" 这类荒谬显示。
const poolCapacity = computed(() => Math.max(store.factors?.length || 0, 1))
</script>

<template>
  <div
    class="rounded-xl border transition-all shadow-xs p-4 sm:p-5"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Card Header -->
    <div class="flex flex-wrap items-center justify-between gap-3 pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
      <div class="flex items-center space-x-2.5">
        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
        <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wider" style="color: var(--text-main);">
          实盘多空持仓与云端风控
        </h2>
        <span
          class="px-2 py-0.5 rounded text-xs font-mono font-bold border"
          style="background-color: var(--bg-badge); color: var(--color-brand); border-color: var(--border-subtle);"
        >
          {{ store.positions.length }} / {{ poolCapacity }} 在途
        </span>
      </div>

      <!-- Cloud OCO Status Badge -->
      <div
        class="flex items-center space-x-1.5 text-xs font-mono px-2.5 py-1 rounded-lg border font-medium"
        :style="{
          backgroundColor: allProtected ? 'var(--color-up-bg)' : 'var(--color-warn-bg)',
          borderColor: allProtected ? 'var(--color-up-border)' : 'var(--color-warn-border)',
          color: allProtected ? 'var(--color-up)' : 'var(--color-warn)'
        }"
      >
        <ShieldCheck v-if="allProtected" class="w-3.5 h-3.5" />
        <ShieldAlert v-else class="w-3.5 h-3.5" />
        <span>{{ allProtected ? '100% 交易所云端 OCO 止损覆盖' : '部分仓位未设止损' }}</span>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-if="store.positions.length === 0"
      class="py-10 text-center rounded-xl border border-dashed"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <div
        class="w-10 h-10 mx-auto mb-2 rounded-xl flex items-center justify-center border"
        style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-muted);"
      >
        <Layers class="w-4 h-4" />
      </div>
      <p class="text-xs font-mono font-medium">当前无在途持仓 · AI 决策中枢空仓扫描中 (100% 现金流动性安全)</p>
    </div>

    <!-- Positions Table -->
    <div v-else class="overflow-x-auto">
      <table class="w-full text-left text-xs font-mono whitespace-nowrap">
        <thead>
          <tr
            class="text-[11px] uppercase tracking-wider border-b"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <th class="py-2.5 px-3.5 font-bold">标的 / 杠杆</th>
            <th class="py-2.5 px-3.5 font-bold">方向</th>
            <th class="py-2.5 px-3.5 font-bold">持仓量</th>
            <th class="py-2.5 px-3.5 font-bold">开仓均价</th>
            <th class="py-2.5 px-3.5 font-bold">最新市价</th>
            <th class="py-2.5 px-3.5 font-bold">保证金占用</th>
            <th class="py-2.5 px-3.5 font-bold">云端止损防线</th>
            <th class="py-2.5 px-3.5 text-right font-bold">未结盈亏 / ROI</th>
          </tr>
        </thead>
        <tbody class="divide-y" style="border-color: var(--border-subtle);">
          <tr
            v-for="pos in store.positions"
            :key="pos.instId"
            class="transition-colors"
            style="border-color: var(--border-subtle);"
            :class="'hover:bg-[var(--bg-card-hover)]'"
          >
            <!-- 标的 / 杠杆 -->
            <td class="py-3 px-3.5">
              <div class="flex items-center space-x-2">
                <span class="font-black text-sm tracking-wide font-mono" style="color: var(--text-main);">
                  {{ pos.name }}
                </span>
                <span class="badge-lever">
                  {{ pos.lever }}x
                </span>
              </div>
            </td>

            <!-- 方向 -->
            <td class="py-3 px-3.5">
              <span :class="pos.side === 'long' ? 'capsule-direction-long' : 'capsule-direction-short'">
                <span>{{ pos.side === 'long' ? '多头 BUY' : '空头 SELL' }}</span>
              </span>
            </td>

            <!-- 持仓量 -->
            <td class="py-3 px-3.5 font-bold num-tabular" style="color: var(--text-main);">
              {{ pos.pos }} <span class="text-[11px] font-normal" style="color: var(--text-faint);">张</span>
            </td>

            <!-- 开仓均价 -->
            <td class="py-3 px-3.5 font-mono num-tabular" style="color: var(--text-muted);">
              ${{ fmt2(pos.avgPx) }}
            </td>

            <!-- 标记市价 -->
            <td class="py-3 px-3.5 font-black font-mono text-sm num-tabular" style="color: var(--text-main);">
              ${{ fmt4(pos.markPx ?? pos.last) }}
            </td>

            <!-- 实际保证金 -->
            <td class="py-3 px-3.5 font-mono num-tabular" style="color: var(--text-main);">
              ${{ fmt2(pos.margin_usdt ?? pos.margin) }} <span class="text-[11px]" style="color: var(--text-faint);">U</span>
            </td>

            <!-- 云端止损防线 -->
            <td class="py-3 px-3.5">
              <div
                class="inline-flex items-center space-x-1 px-2 py-0.5 rounded border text-[11px]"
                :style="{
                  backgroundColor: 'var(--bg-badge)',
                  borderColor: 'var(--border-subtle)',
                  color: pos.side === 'long' ? 'var(--color-down)' : 'var(--color-up)'
                }"
              >
                <ShieldCheck class="w-3 h-3 shrink-0" />
                <span class="font-bold num-tabular">${{ pos.displayStop || '--' }}</span>
              </div>
            </td>

            <!-- 未结浮盈 / ROI -->
            <td class="py-3 px-3.5 text-right">
              <div
                class="text-sm font-black font-mono num-tabular"
                :style="{ color: Number(pos.upl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }"
              >
                {{ Number(pos.upl) >= 0 ? '+' : '' }}{{ fmt2(pos.upl) }} U
              </div>
              <div
                class="text-[11px] font-bold font-mono num-tabular"
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
</template>
