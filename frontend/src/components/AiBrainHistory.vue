<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
import { Brain, ChevronDown, Users } from 'lucide-vue-next'

const store = useDashboardStore()
const i18n = useI18nStore()
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
    class="rounded-xl border p-4 sm:p-5 transition-all shadow-xs space-y-4 font-mono"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Header -->
    <div class="flex items-center space-x-3 pb-3 border-b" style="border-color: var(--border-subtle);">
      <div
        class="w-9 h-9 rounded-lg flex items-center justify-center border shrink-0"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
      >
        <Brain class="w-4 h-4" />
      </div>
      <div>
        <h2 class="text-xs sm:text-sm font-black uppercase tracking-wide" style="color: var(--text-main);">
          {{ i18n.t.councilHeader }}
        </h2>
        <p class="text-xs mt-0.5" style="color: var(--text-muted);">
          {{ i18n.locale === 'zh' ? '每 15 分钟周期宏观研判、多模型辩论实录与风控裁决' : '15-min cycle macro assessment, council transcript & risk verdict' }}
        </p>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-if="history.length === 0"
      class="py-16 text-center text-xs rounded-xl border border-dashed"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      {{ i18n.locale === 'zh' ? '暂无历史决策记录，等待下一次推演周期' : 'No decision history yet. Waiting for next cycle.' }}
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
            <span class="font-bold text-xs shrink-0 num-tabular" style="color: var(--text-main);">
              {{ item.time }}
            </span>
            <span
              v-if="item.council_transcript"
              class="px-2 py-0.5 rounded text-[10px] font-bold border shrink-0"
              style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--text-main);"
            >
              🏛️ {{ i18n.locale === 'zh' ? '委员会决策' : 'Council Pro' }}
            </span>
            <span class="text-xs truncate" style="color: var(--text-muted);">
              {{ item.macro_assessment || (i18n.locale === 'zh' ? '宏观中性震荡' : 'Macro Neutral Oscillator') }}
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
            <div class="text-[10px] font-bold uppercase mb-1" style="color: var(--text-faint);">
              {{ i18n.t.macroAssessment }}:
            </div>
            <p class="text-xs leading-relaxed" style="color: var(--text-main);">
              {{ item.macro_assessment || (i18n.locale === 'zh' ? '宏观中性震荡' : 'Macro Neutral Oscillator') }}
            </p>
          </div>

          <!-- Multi-Agent Council Transcript -->
          <div
            v-if="item.council_transcript"
            class="p-3.5 rounded-xl border space-y-2.5"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs font-bold" style="color: var(--text-main);">
                <Users class="w-4 h-4" />
                <span>{{ i18n.locale === 'zh' ? '【多角色模型现场辩论纪要】' : '[Multi-Model Debate Transcript]' }}</span>
              </div>
              <span class="text-[10px]" style="color: var(--text-faint);">
                Latency: {{ item.council_transcript.total_duration_ms }}ms
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

            <!-- CIO Final Verdict -->
            <div
              v-if="item.council_transcript.leader"
              class="p-3 rounded-lg border space-y-1 text-xs mt-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--color-brand-border);"
            >
              <div class="flex items-center justify-between font-bold">
                <span style="color: var(--color-brand);">👑 {{ item.council_transcript.leader.role_name }} (CIO裁决)</span>
                <span class="text-[10px]" style="color: var(--text-faint);">{{ item.council_transcript.leader.model_used }}</span>
              </div>
              <p class="text-[11px] leading-relaxed whitespace-pre-wrap select-text font-sans" style="color: var(--text-main);">
                {{ item.council_transcript.leader.content }}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
