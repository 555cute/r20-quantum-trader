<script setup lang="ts">
import { ref } from 'vue'
import { X, Cpu, FileText, CheckCircle2, ShieldAlert, Zap, TrendingUp } from 'lucide-vue-next'

const props = defineProps<{
  visible: boolean
  instrument: any | null
  fullPromptText?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const activeTab = ref<'reasoning' | 'prompt'>('reasoning')

function copyPrompt() {
  if (!props.fullPromptText) return
  navigator.clipboard.writeText(props.fullPromptText)
  alert('实发 Prompt 原文已复制到剪贴板！')
}
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[60] bg-black/70 backdrop-blur-sm flex justify-end transition-opacity overflow-hidden"
    @click.self="emit('close')"
  >
    <div
      class="w-full max-w-2xl border-l h-[100dvh] flex flex-col shadow-2xl overflow-hidden animate-slide-in"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Drawer Header -->
      <div
        class="p-4 border-b flex items-center justify-between shrink-0"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
      >
        <div class="flex items-center space-x-3">
          <div
            class="w-8 h-8 rounded-lg border flex items-center justify-center font-black font-mono"
            style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);"
          >
            {{ instrument?.name || 'AI' }}
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <h3 class="font-black text-sm font-mono" style="color: var(--text-main);">
                {{ instrument?.name }} 深度认知推演全景
              </h3>
              <span
                class="text-[11px] font-mono px-1.5 py-0.2 rounded border"
                style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted);"
              >
                {{ instrument?.instId }}
              </span>
            </div>
            <div class="text-[11px] font-mono mt-0.5" style="color: var(--text-faint);">
              100% 审计溯源 · 微积分物理定积分证明 · 链上聪明钱博弈
            </div>
          </div>
        </div>

        <button
          @click="emit('close')"
          class="p-1.5 rounded-lg border transition-colors cursor-pointer"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-faint);"
        >
          <X class="w-4 h-4" />
        </button>
      </div>

      <!-- Segmented View Selector -->
      <div
        class="flex border-b px-4 pt-2 shrink-0 gap-2 font-mono text-xs"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
      >
        <button
          @click="activeTab = 'reasoning'"
          class="flex items-center space-x-1.5 px-3 py-2 border-b-2 font-bold transition-all cursor-pointer"
          :style="activeTab === 'reasoning'
            ? { borderColor: 'var(--text-main)', color: 'var(--text-main)' }
            : { borderColor: 'transparent', color: 'var(--text-muted)' }"
        >
          <Cpu class="w-3.5 h-3.5" />
          <span>五重数学推演证据</span>
        </button>
        <button
          @click="activeTab = 'prompt'"
          class="flex items-center space-x-1.5 px-3 py-2 border-b-2 font-bold transition-all cursor-pointer"
          :style="activeTab === 'prompt'
            ? { borderColor: 'var(--text-main)', color: 'var(--text-main)' }
            : { borderColor: 'transparent', color: 'var(--text-muted)' }"
        >
          <FileText class="w-3.5 h-3.5" />
          <span>当轮实发 Prompt 原文对照</span>
        </button>
      </div>

      <!-- Drawer Content (Scrollable with mobile bottom padding) -->
      <div class="flex-1 overflow-y-auto overscroll-contain p-4 pb-36 sm:pb-8 space-y-3.5">
        <!-- TAB 1: 五重数学与微积分动能推演 -->
        <div v-if="activeTab === 'reasoning'" class="space-y-3">
          <!-- Decision Summary Banner -->
          <div
            class="rounded-xl border p-3.5"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs font-mono" style="color: var(--text-faint);">决策输出</span>
              <span
                class="px-2 py-0.5 rounded text-xs font-bold font-mono border"
                :style="{
                  backgroundColor: instrument?.action === 'BUY_LONG' ? 'var(--color-up-bg)' : instrument?.action === 'SELL_SHORT' ? 'var(--color-down-bg)' : 'var(--bg-badge)',
                  borderColor: instrument?.action === 'BUY_LONG' ? 'var(--color-up-border)' : instrument?.action === 'SELL_SHORT' ? 'var(--color-down-border)' : 'var(--border-subtle)',
                  color: instrument?.action === 'BUY_LONG' ? 'var(--color-up)' : instrument?.action === 'SELL_SHORT' ? 'var(--color-down)' : 'var(--text-muted)'
                }"
              >
                {{ instrument?.action || 'WAIT' }}
              </span>
            </div>
            <p class="text-xs leading-relaxed font-sans font-medium" style="color: var(--text-main);">
              {{ instrument?.reason || '全市场宏观多因子评估中' }}
            </p>
          </div>

          <!-- Section 1: Calculus Dynamics -->
          <div
            class="rounded-xl border p-3.5"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center space-x-2 text-xs font-mono font-bold mb-2" style="color: var(--text-main);">
              <Zap class="w-4 h-4" />
              <span>1. 高阶微积分物理动能证据 (Calculus Dynamics)</span>
            </div>
            <p
              class="text-xs font-mono leading-relaxed p-2.5 rounded-lg border"
              style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              {{ instrument?.thought_process?.calculus_dynamics || '等待模型解析微积分动力学矩阵...' }}
            </p>
          </div>

          <!-- Section 2: Mathematical Probability Rationale -->
          <div
            class="rounded-xl border p-3.5"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center space-x-2 text-xs font-mono font-bold mb-2" style="color: var(--text-main);">
              <TrendingUp class="w-4 h-4" />
              <span>2. 积分作用量与概率胜率评估 (Mathematical Rationale)</span>
            </div>
            <p
              class="text-xs font-mono leading-relaxed p-2.5 rounded-lg border"
              style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              {{ instrument?.thought_process?.math_prob_rationale || '等待模型输出定积分与VaR概率证据...' }}
            </p>
          </div>

          <!-- Section 3: Market Structure & Volume/OI -->
          <div
            class="rounded-xl border p-3.5"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center space-x-2 text-xs font-mono font-bold mb-2" style="color: var(--text-main);">
              <ShieldAlert class="w-4 h-4" />
              <span>3. 多周期时空结构与持仓异动 (Market Structure & Flow)</span>
            </div>
            <div class="space-y-2 text-xs font-mono">
              <div
                class="p-2 rounded border"
                style="background-color: var(--bg-card); border-color: var(--border-subtle);"
              >
                <span class="text-[11px] block" style="color: var(--text-faint);">周期共振结构：</span>
                <span style="color: var(--text-main);">{{ instrument?.thought_process?.market_structure || '--' }}</span>
              </div>
              <div
                class="p-2 rounded border"
                style="background-color: var(--bg-card); border-color: var(--border-subtle);"
              >
                <span class="text-[11px] block" style="color: var(--text-faint);">持仓与量能异动：</span>
                <span style="color: var(--text-main);">{{ instrument?.thought_process?.volume_and_oi || '--' }}</span>
              </div>
            </div>
          </div>

          <!-- Section 4: Risk-Reward 2R Evaluation -->
          <div
            class="rounded-xl border p-3.5"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center space-x-2 text-xs font-mono font-bold mb-2" style="color: var(--color-up);">
              <CheckCircle2 class="w-4 h-4" />
              <span>4. 严格盈亏比验证 (Risk-Reward Evaluation)</span>
            </div>
            <p
              class="text-xs font-mono leading-relaxed p-2.5 rounded-lg border"
              style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              {{ instrument?.thought_process?.risk_reward_evaluation || '目标 R:R ≥ 2.5；执行底线 2.0。未达 2R 执行层一律安全降级拒绝开仓。' }}
            </p>
          </div>
        </div>

        <!-- TAB 2: 当轮实发 Prompt 原文对照 -->
        <div v-else class="space-y-3">
          <div class="flex items-center justify-between">
            <span class="text-xs font-mono" style="color: var(--text-muted);">发送至大模型网关的完整提示词</span>
            <button
              @click="copyPrompt"
              class="px-2.5 py-1 rounded border text-xs font-mono cursor-pointer transition-colors"
              style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              复制原文
            </button>
          </div>
          <pre
            class="rounded-xl p-4 text-xs font-mono select-text whitespace-pre-wrap leading-relaxed max-h-[600px] overflow-y-auto border"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
          >{{ fullPromptText || '等待下一次 15 分钟交易周期写入实发提示词...' }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes slide-in {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
.animate-slide-in {
  animation: slide-in 0.2s ease-out;
}
</style>
