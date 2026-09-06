<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Sparkles, Brain, Cpu, AlertTriangle } from 'lucide-vue-next'

const store = useDashboardStore()
const review = computed(() => store.data?.review || {})
const memoryMd = computed(() => store.data?.ai_trading_memory_md || '')
</script>

<template>
  <div class="space-y-3.5 2xl:space-y-5">
    <!-- Lab Header -->
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5 2xl:space-x-3">
        <div class="panel-banner-icon">
          <Sparkles class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
        </div>
        <div>
          <h2 class="text-xs sm:text-[13px] 2xl:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
            AI 策略自进化与认知提炼中心
          </h2>
          <p class="text-[11px] 2xl:text-xs font-mono mt-0.5" style="color: var(--text-muted);">
            基于实盘胜率、盈亏比与动力学反馈，每 6 小时全自主修正参数与策略心法
          </p>
        </div>
      </div>
      <div class="flex items-center space-x-2 text-xs 2xl:text-sm font-mono h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-[4px] border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
        <span style="color: var(--text-muted);">自进化主脑:</span>
        <span class="font-bold font-mono" style="color: var(--color-brand);">{{ store.llmRuntime.model }}</span>
      </div>
    </div>

    <!-- Upstream LLM failure notice: explains why the memory library is unchanged -->
    <div
      v-if="review.llm_error"
      class="rounded-xl border p-3 sm:p-3.5 flex items-start space-x-2 font-mono text-[11px] 2xl:text-xs"
      style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);"
    >
      <AlertTriangle class="w-3.5 h-3.5 shrink-0 mt-0.5" />
      <div class="space-y-0.5">
        <div class="font-bold">最近一轮 {{ review.timestamp || '--' }} 复盘未能完成：大模型网关返回错误，本轮按 NO_CHANGE 保留原有心法，记忆库因此没有新增条目。</div>
        <div style="color: var(--text-muted);">错误详情：{{ review.llm_error }}</div>
        <div style="color: var(--text-faint);">系统已自动重试；下一周期（每 6 小时）将再次尝试提炼。可在后台「AI 模型」切换可用模型后立即手动触发。</div>
      </div>
    </div>

    <!-- Dual Layout: Realtime Memory MD & Factor Library -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-3.5 2xl:gap-5">
      <!-- 1. Realtime Trading Memory (Markdown) -->
      <div
        class="rounded-xl border p-4 sm:p-5 2xl:p-6 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div>
          <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
            <div class="flex items-center space-x-2">
              <Brain class="w-4 h-4 2xl:w-4.5 2xl:h-4.5" style="color: var(--color-brand);" />
              <h3 class="text-xs 2xl:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
                实战经验记忆库 (Trading Memory)
              </h3>
            </div>
            <span
              class="text-[10px] 2xl:text-xs font-mono px-2 py-0.5 rounded border font-bold"
              style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              每6小时自动沉淀
            </span>
          </div>
          <div
            class="p-3.5 2xl:p-4.5 rounded-lg border text-xs 2xl:text-sm font-mono leading-relaxed max-h-[360px] 2xl:max-h-[480px] overflow-y-auto whitespace-pre-wrap select-text"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
          >
            {{ memoryMd || '正在读取长期心法知识库...' }}
          </div>
        </div>
      </div>

      <!-- 2. Dynamic Factor Weights & Parameters -->
      <div
        class="rounded-xl border p-4 sm:p-5 2xl:p-6 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div>
          <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
            <div class="flex items-center space-x-2">
              <Cpu class="w-4 h-4 2xl:w-4.5 2xl:h-4.5" style="color: var(--color-brand);" />
              <h3 class="text-xs 2xl:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
                动态因子权重与量化自适应参数
              </h3>
            </div>
            <span
              class="text-[10px] 2xl:text-xs font-mono px-2 py-0.5 rounded border font-bold"
              style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              动态反馈
            </span>
          </div>

          <div class="space-y-3 2xl:space-y-4 font-mono text-xs 2xl:text-sm">
            <div class="p-3 2xl:p-4 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
              <div class="text-[10px] 2xl:text-xs uppercase mb-1 font-bold" style="color: var(--text-faint);">最近复盘结论</div>
              <p class="text-xs 2xl:text-sm font-sans leading-relaxed" style="color: var(--text-main);">
                {{ review.summary || '当前市场因子权重处于最优稳态区间，微积分动能结合保本移损锁死期望值优势。' }}
              </p>
            </div>

            <div class="grid grid-cols-2 2xl:grid-cols-4 gap-2 2xl:gap-3 text-center">
              <div class="p-2.5 2xl:p-3.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] 2xl:text-xs" style="color: var(--text-faint);">微积分动能权重</div>
                <div class="font-bold text-sm 2xl:text-base mt-0.5 num-tabular" style="color: var(--color-up);">35%</div>
              </div>
              <div class="p-2.5 2xl:p-3.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] 2xl:text-xs" style="color: var(--text-faint);">聪明钱流向权重</div>
                <div class="font-bold text-sm 2xl:text-base mt-0.5 num-tabular" style="color: var(--text-main);">30%</div>
              </div>
              <div class="p-2.5 2xl:p-3.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] 2xl:text-xs" style="color: var(--text-faint);">多周期结构共振</div>
                <div class="font-bold text-sm 2xl:text-base mt-0.5 num-tabular" style="color: var(--text-main);">25%</div>
              </div>
              <div class="p-2.5 2xl:p-3.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="text-[10px] 2xl:text-xs" style="color: var(--text-faint);">全网舆情过滤</div>
                <div class="font-bold text-sm 2xl:text-base mt-0.5 num-tabular" style="color: var(--color-warn);">10%</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
