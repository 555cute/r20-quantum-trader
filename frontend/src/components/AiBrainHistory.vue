<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Brain, ChevronDown, Users, ShieldCheck } from 'lucide-vue-next'

const store = useDashboardStore()
const history = computed<any[]>(() => (store.data?.ai_brain_history || []).slice(0, 24))
const expanded = ref<Set<number>>(new Set())

function toggle(i: number) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}
</script>

<template>
  <div
    class="rounded-xl border p-3 sm:p-4 transition-all shadow-xs space-y-3"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Header -->
    <div class="flex items-center justify-between pb-2.5 border-b" style="border-color: var(--border-subtle);">
      <div class="flex items-center space-x-2.5">
        <div class="panel-banner-icon">
          <Brain class="w-3.5 h-3.5" />
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h2 class="text-xs sm:text-[13px] font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              AI 宏观多周期推演基调与决策审计
            </h2>
            <span class="text-[10px] font-mono px-1.5 py-0.5 rounded border" style="background-color: var(--bg-badge); color: var(--color-brand); border-color: var(--border-subtle);">
              15m 周期
            </span>
          </div>
          <p class="text-[11px] font-mono mt-0.5" style="color: var(--text-muted);">
            宏观大盘研判、多模型辩论实录与在途持仓管理指令
          </p>
        </div>
      </div>
      <div class="hidden sm:flex items-center space-x-1 text-xs font-mono" style="color: var(--text-muted);">
        <span>最近保留 {{ history.length }} 轮决策</span>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-if="history.length === 0"
      class="py-16 text-center text-xs font-mono rounded-xl border border-dashed"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      暂无历史决策记录，等待下一次 15 分钟推演周期
    </div>

    <!-- History List -->
    <div v-else class="space-y-2.5 max-h-[720px] overflow-y-auto pr-1">
      <div
        v-for="(item, i) in history"
        :key="i"
        class="rounded-xl border p-3.5 transition-all"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
      >
        <button @click="toggle(i)" class="w-full flex items-center justify-between text-left cursor-pointer gap-2">
          <div class="flex items-center space-x-2.5 min-w-0">
            <span class="font-mono font-bold text-xs shrink-0 num-tabular" style="color: var(--text-main);">
              {{ item.time }}
            </span>
            <span
              v-if="item.council_transcript"
              class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border shrink-0"
              style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--text-main);"
            >
              🏛️ 委员会决策
            </span>
            <span class="text-xs font-sans truncate" style="color: var(--text-muted);">
              {{ item.macro_assessment || '宏观中性震荡' }}
            </span>
          </div>
          <ChevronDown
            class="w-4 h-4 shrink-0 transition-transform"
            style="color: var(--text-faint);"
            :class="expanded.has(i) ? 'rotate-180' : ''"
          />
        </button>

        <div v-if="expanded.has(i)" class="mt-3 space-y-3 border-t pt-3" style="border-color: var(--border-subtle);">
          <!-- Macro Summary -->
          <div>
            <div class="text-[10px] font-bold font-mono uppercase mb-1" style="color: var(--text-faint);">
              宏观研判总结:
            </div>
            <p class="text-xs font-sans leading-relaxed" style="color: var(--text-main);">
              {{ item.macro_assessment || '宏观中性震荡' }}
            </p>
          </div>

          <!-- Multi-Agent Council Transcript -->
          <div
            v-if="item.council_transcript"
            class="p-3.5 rounded-xl border space-y-2.5 font-mono"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs font-bold" style="color: var(--text-main);">
                <Users class="w-4 h-4" />
                <span>【多角色模型现场辩论纪要】</span>
              </div>
              <span class="text-[10px] font-mono" style="color: var(--text-faint);">
                协作总时延: {{ item.council_transcript.total_duration_ms }}ms
              </span>
            </div>

            <!-- Advisors viewpoints -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 pt-1">
              <div
                v-for="(adv, advKey) in item.council_transcript.advisors || {}"
                :key="advKey"
                class="p-2.5 rounded-lg border space-y-1 text-xs"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
              >
                <div class="flex items-center justify-between font-bold">
                  <span style="color: var(--text-main);">{{ adv.role_name }}</span>
                  <span class="text-[10px]" style="color: var(--text-faint);">{{ adv.model_used }}</span>
                </div>
                <p class="text-[11px] leading-relaxed whitespace-pre-wrap max-h-36 overflow-y-auto pr-0.5 select-text" style="color: var(--text-muted);">
                  {{ adv.content }}
                </p>
              </div>
            </div>

            <!-- Arbitrator summary -->
            <div class="mt-1 pt-2 border-t text-xs font-bold flex items-center justify-between" style="border-color: var(--border-subtle); color: var(--color-up);">
              <span>⚖️ 首席仲裁官裁决收口: 采纳专家参谋核心论点，生成统一发单指令</span>
              <span class="text-[10px] font-normal" style="color: var(--text-faint);">
                终审模型: {{ item.council_transcript.arbitrator?.model_used }}
              </span>
            </div>
          </div>

          <!-- In-flight Position Management Instructions -->
          <div
            v-if="item.position_management?.length"
            class="p-3 rounded-xl border space-y-1.5 font-mono text-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] font-bold block uppercase" style="color: var(--text-faint);">在途持仓管理指令</span>
            <div v-for="(p, j) in item.position_management" :key="j" class="flex flex-wrap items-center gap-x-2 gap-y-0.5" style="color: var(--text-muted);">
              <strong style="color: var(--text-main);">{{ p.instId }}</strong>
              <span
                class="px-2 py-0.5 rounded font-bold border text-[10px]"
                :style="{
                  backgroundColor: p.action?.includes('HOLD') ? 'var(--bg-badge)' : 'var(--color-warn-bg)',
                  borderColor: p.action?.includes('HOLD') ? 'var(--border-subtle)' : 'var(--color-warn-border)',
                  color: p.action?.includes('HOLD') ? 'var(--text-main)' : 'var(--color-warn)'
                }"
              >
                {{ p.action }}
              </span>
              <span v-if="p.reason" class="text-[11px]" style="color: var(--text-muted);">{{ p.reason }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
