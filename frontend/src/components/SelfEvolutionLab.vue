<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Sparkles, Layers, DollarSign, Eye, Activity, RefreshCw, ArrowUpRight, TrendingUp } from 'lucide-vue-next'
import { useApi } from '../composables/useApi'

const { api } = useApi()

// Active Lab Sub-tab: 'unified_ledger' | 'stat_arb' | 'chart_vision'
const activePillar = ref<'unified_ledger' | 'stat_arb' | 'chart_vision'>('unified_ledger')

const loading = ref(false)
const ledgerData = ref<any>(null)
const arbData = ref<any>(null)
const visionData = ref<any>(null)
const selectedVisionSymbol = ref('BTC')
const selectedVisionInterval = ref('15m')

async function loadLedger() {
  try {
    ledgerData.value = await api('/api/v1/lab/unified-ledger')
  } catch (e) {
    console.error(e)
  }
}

async function loadArb() {
  try {
    arbData.value = await api('/api/v1/lab/stat-arb-matrix')
  } catch (e) {
    console.error(e)
  }
}

async function loadVision() {
  try {
    visionData.value = await api(`/api/v1/lab/chart-vision/${selectedVisionSymbol.value}?interval=${selectedVisionInterval.value}`)
  } catch (e) {
    console.error(e)
  }
}

async function refreshActive() {
  loading.value = true
  if (activePillar.value === 'unified_ledger') await loadLedger()
  else if (activePillar.value === 'stat_arb') await loadArb()
  else if (activePillar.value === 'chart_vision') await loadVision()
  loading.value = false
}

onMounted(() => {
  loadLedger()
  loadArb()
  loadVision()
})
</script>

<template>
  <div class="space-y-3.5">
    <!-- Lab Top Banner -->
    <div
      class="rounded-xl border p-4 sm:p-5 flex flex-wrap items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center space-x-3">
        <div
          class="w-9 h-9 rounded-lg flex items-center justify-center border shrink-0"
          style="background-color: rgba(99, 102, 241, 0.15); border-color: rgba(99, 102, 241, 0.3); color: #818cf8;"
        >
          <Sparkles class="w-4 h-4" />
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              量子前沿实验室 (Quantum Sandbox · v8.0 OmniMatrix 试验田)
            </h2>
            <span class="text-[10px] font-mono px-2 py-0.2 rounded-full border border-indigo-500/40 bg-indigo-500/10 text-indigo-400 font-bold">
              ISOLATED SANDBOX
            </span>
          </div>
          <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted);">
            专为前沿量化支柱打造的沙盒试验田：数据和算法在此全真验证，成熟后原子化迁入实盘引擎，绝不干扰当前现行系统。
          </p>
        </div>
      </div>

      <button
        @click="refreshActive"
        :disabled="loading"
        class="px-3 py-1.5 rounded-lg border text-xs font-mono cursor-pointer transition-all flex items-center gap-1.5"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
      >
        <RefreshCw class="w-3.5 h-3.5" :class="loading ? 'animate-spin' : ''" />
        <span>刷新试验数据</span>
      </button>
    </div>

    <!-- 3 Pillars Sub-Tabs Switcher -->
    <div
      class="rounded-xl border p-2 flex flex-wrap items-center gap-2 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <button
        @click="activePillar = 'unified_ledger'"
        class="px-3.5 py-2 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-2"
        :style="activePillar === 'unified_ledger'
          ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
          : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
      >
        <Layers class="w-3.5 h-3.5" />
        <span>支柱一：多所合并资产与持仓台账 (Unified Ledger)</span>
      </button>

      <button
        @click="activePillar = 'stat_arb'"
        class="px-3.5 py-2 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-2"
        :style="activePillar === 'stat_arb'
          ? { backgroundColor: '#10b981', color: '#ffffff' }
          : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
      >
        <DollarSign class="w-3.5 h-3.5" />
        <span>支柱二：跨所费率与基差套利监控 (Stat-Arb)</span>
      </button>

      <button
        @click="activePillar = 'chart_vision'"
        class="px-3.5 py-2 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-2"
        :style="activePillar === 'chart_vision'
          ? { backgroundColor: '#818cf8', color: '#ffffff' }
          : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
      >
        <Eye class="w-3.5 h-3.5" />
        <span>支柱三：多模态视觉 K 线渲染与核验 (Chart Vision)</span>
      </button>
    </div>

    <!-- TAB 1: UNIFIED LEDGER EXPERIMENT -->
    <div v-if="activePillar === 'unified_ledger'" class="space-y-3.5">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5">
        <div class="p-4 rounded-xl border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <span class="text-[11px] font-mono" style="color: var(--text-muted);">全球多所聚合总权益 (Total Equity)</span>
          <div class="text-2xl font-black font-mono text-emerald-400 mt-1">
            {{ ledgerData?.total_equity_usdt || '--' }} <span class="text-xs font-normal">USDT</span>
          </div>
          <div class="text-[10px] font-mono mt-1 text-slate-400">穿透 OKX、Binance 与 Gate.io 三所保证金总和</div>
        </div>
        <div class="p-4 rounded-xl border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <span class="text-[11px] font-mono" style="color: var(--text-muted);">跨所可用购买力 (Available Margin)</span>
          <div class="text-2xl font-black font-mono text-indigo-400 mt-1">
            {{ ledgerData?.total_available_usdt || '--' }} <span class="text-xs font-normal">USDT</span>
          </div>
          <div class="text-[10px] font-mono mt-1 text-slate-400">已扣除各交易所仓位占用与未成交挂单冻结</div>
        </div>
      </div>

      <!-- Accounts Table -->
      <div class="rounded-xl border overflow-hidden" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-2.5 border-b text-xs font-bold font-mono" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-main);">
          各交易所子账户资产透视
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs font-mono">
            <thead>
              <tr class="border-b text-[11px]" style="border-color: var(--border-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">交易所节点</th>
                <th class="py-2.5 px-3">动态权益 (USDT)</th>
                <th class="py-2.5 px-3">可用资金 (USDT)</th>
                <th class="py-2.5 px-3">网络环境</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="acc in ledgerData?.accounts || []" :key="acc.exchange" class="border-b last:border-b-0" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4 font-bold" style="color: var(--text-main);">{{ acc.exchange }}</td>
                <td class="py-2.5 px-3 text-emerald-400 font-bold">{{ acc.equity }}</td>
                <td class="py-2.5 px-3 text-indigo-400">{{ acc.avail_usdt }}</td>
                <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded text-[10px] border border-emerald-500/30 text-emerald-400">{{ acc.status }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Aggregate Positions Table -->
      <div class="rounded-xl border overflow-hidden" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-2.5 border-b text-xs font-bold font-mono" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-main);">
          跨所合并持仓矩阵 (Unified Net Exposure)
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs font-mono">
            <thead>
              <tr class="border-b text-[11px]" style="border-color: var(--border-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">标的</th>
                <th class="py-2.5 px-3">合并净敞口</th>
                <th class="py-2.5 px-3">方向</th>
                <th class="py-2.5 px-3">分所分布</th>
                <th class="py-2.5 px-3">未实现盈亏</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="pos in ledgerData?.unified_positions || []" :key="pos.asset" class="border-b last:border-b-0" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4 font-bold text-amber-400">{{ pos.asset }}</td>
                <td class="py-2.5 px-3 font-bold" style="color: var(--text-main);">{{ pos.net_exposure }}</td>
                <td class="py-2.5 px-3"><span :class="pos.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'">{{ pos.direction }}</span></td>
                <td class="py-2.5 px-3 text-slate-400">{{ pos.venues }}</td>
                <td class="py-2.5 px-3" :class="pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'">{{ pos.unrealized_pnl >= 0 ? '+' : '' }}{{ pos.unrealized_pnl }} U ({{ pos.roe_pct }}%)</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 2: STAT-ARB EXPERIMENT -->
    <div v-else-if="activePillar === 'stat_arb'" class="space-y-3.5">
      <div class="rounded-xl border p-4" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="text-xs font-bold font-mono mb-1 text-emerald-400 flex items-center gap-1.5">
          <TrendingUp class="w-4 h-4" />
          <span>跨所资金费率与基差套利实时探测矩阵</span>
        </div>
        <p class="text-xs font-mono" style="color: var(--text-muted);">
          实时比对三大所价差散度与资金费率（每8小时结算），自动测算 Delta-Neutral 无风险套利年化收益率。
        </p>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5">
        <div
          v-for="item in arbData?.arbitrage_matrix || []"
          :key="item.symbol"
          class="p-4 rounded-xl border flex flex-col justify-between space-y-3"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="text-sm font-black font-mono text-amber-400">{{ item.symbol }}</span>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded border" :class="item.opportunity === 'HIGH_POTENTIAL' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 font-bold' : 'border-slate-700 text-slate-400'">
                {{ item.opportunity === 'HIGH_POTENTIAL' ? '✦ 捕捉到套利机会' : '常态基差监控' }}
              </span>
            </div>

            <div class="grid grid-cols-3 gap-2 text-center text-xs font-mono mb-3">
              <div class="p-2 rounded border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] text-slate-400">OKX 价格</div>
                <div class="font-bold text-emerald-400 mt-0.5">{{ item.prices.okx }}</div>
              </div>
              <div class="p-2 rounded border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] text-slate-400">币安价格</div>
                <div class="font-bold text-amber-400 mt-0.5">{{ item.prices.binance }}</div>
              </div>
              <div class="p-2 rounded border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] text-slate-400">Gate 价格</div>
                <div class="font-bold text-blue-400 mt-0.5">{{ item.prices.gate }}</div>
              </div>
            </div>

            <div class="text-xs font-mono space-y-1" style="color: var(--text-muted);">
              <div>币安-OKX 价差偏离: <strong style="color: var(--text-main);">{{ item.spread_disparity_pct }}%</strong></div>
              <div>Gate 资金费率: <strong class="text-blue-400">{{ (item.gate_funding_rate * 100).toFixed(4) }}%</strong></div>
              <div>预估对冲套利 APR: <strong class="text-emerald-400 text-sm font-bold">{{ item.estimated_arb_apr_pct }}%</strong></div>
            </div>
          </div>

          <div class="pt-2 border-t text-[11px] font-mono text-emerald-400" style="border-color: var(--border-subtle);">
            建议策略: {{ item.action_plan }}
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3: CHART VISION EXPERIMENT -->
    <div v-else-if="activePillar === 'chart_vision'" class="space-y-3.5">
      <div class="rounded-xl border p-4 flex flex-wrap items-center justify-between gap-3" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div>
          <div class="text-xs font-bold font-mono mb-1 text-indigo-400 flex items-center gap-1.5">
            <Eye class="w-4 h-4" />
            <span>毫秒级本地视觉 K 线图渲染引擎 (PIL Ultra-Fast Engine)</span>
          </div>
          <p class="text-xs font-mono" style="color: var(--text-muted);">
            不依赖外部图表截图，仅耗时 3ms 将多所 K 线数据在内存绘制为无失真高清晰度视觉帧，专供多模态大模型双重核验。
          </p>
        </div>

        <div class="flex items-center gap-2">
          <select v-model="selectedVisionSymbol" @change="loadVision" class="rounded px-2.5 py-1 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-medium); color: var(--text-main);">
            <option value="BTC">BTC</option>
            <option value="ETH">ETH</option>
            <option value="SOL">SOL</option>
            <option value="DOGE">DOGE</option>
          </select>
          <select v-model="selectedVisionInterval" @change="loadVision" class="rounded px-2.5 py-1 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-medium); color: var(--text-main);">
            <option value="15m">15m</option>
            <option value="1h">1H</option>
            <option value="4h">4H</option>
          </select>
          <button @click="loadVision" class="px-3 py-1 rounded text-xs font-mono font-bold bg-indigo-600 text-white cursor-pointer hover:bg-indigo-500 transition-colors">
            立即渲染
          </button>
        </div>
      </div>

      <!-- Rendered Image Canvas Preview -->
      <div class="rounded-xl border p-4 flex flex-col items-center justify-center min-h-[400px]" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div v-if="visionData?.image_data_base64" class="space-y-3 text-center">
          <img :src="visionData.image_data_base64" alt="Rendered K-line Frame" class="rounded-lg border shadow-lg max-w-full" style="border-color: var(--border-subtle);" />
          <div class="text-[11px] font-mono text-slate-400">
            已成功生成视觉核验帧 · 蜡烛根数: {{ visionData.candles_rendered }} · 最新收盘: {{ visionData.latest_close }} · 格式: Base64 PNG
          </div>
        </div>
        <div v-else class="text-xs font-mono text-slate-500">
          正在渲染图表数据...
        </div>
      </div>
    </div>
  </div>
</template>
