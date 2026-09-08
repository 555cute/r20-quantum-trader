<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import { Brain, ChevronDown, Users, ShieldCheck } from 'lucide-vue-next'

const store = useDashboardStore()
const { t } = useI18n()
const history = computed<any[]>(() => (store.data?.ai_brain_history || []).slice(0, 24))
const expanded = ref<Set<number>>(new Set())


function dominantAction(item: any): string {
  const acts = (item.position_management || []).map((p: any) => String(p.action || ''))
  if (acts.some((a: string) => a.includes('LONG') || a.includes('BUY'))) return 'long'
  if (acts.some((a: string) => a.includes('SHORT') || a.includes('SELL'))) return 'short'
  return 'hold'
}
function accentColor(item: any) {
  const d = dominantAction(item)
  return d === 'long' ? 'var(--color-up)' : d === 'short' ? 'var(--color-down)' : 'var(--border-medium)'
}

function toggle(i: number) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}
</script>

<template>
  <div
    class="rounded-xl border p-3 sm:p-4 2xl:p-6 transition-all shadow-xs space-y-3 2xl:space-y-4"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Header -->
    <div class="flex items-center justify-between pb-2.5 border-b" style="border-color: var(--border-subtle);">
      <div class="flex items-center space-x-2.5 2xl:space-x-3">
        <div class="panel-banner-icon">
          <Brain class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h2 class="text-xs sm:text-[13px] 2xl:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              {{ t('radar.title') }}
            </h2>
            <span class="text-[11px] 2xl:text-[11px] font-mono px-1.5 py-0.5 rounded border" style="background-color: var(--bg-badge); color: var(--color-brand); border-color: var(--border-subtle);">
              {{ t('radar.cycle') }}
            </span>
          </div>
          <p class="text-[11px] 2xl:text-xs font-mono mt-0.5" style="color: var(--text-muted);">
            {{ t('radar.subtitle') }}
          </p>
        </div>
      </div>
      <div class="hidden sm:flex items-center space-x-1 text-xs 2xl:text-sm font-mono" style="color: var(--text-muted);">
        <span>{{ t('radar.kept').replace('{n}', String(history.length)) }}</span>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-if="history.length === 0"
      class="py-16 2xl:py-24 text-center text-xs 2xl:text-sm font-mono rounded-xl border border-dashed"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      {{ t('radar.empty') }}
    </div>

    <!-- History List -->
    <div v-else class="space-y-2.5 2xl:space-y-3.5 max-h-[720px] 2xl:max-h-[840px] overflow-y-auto pr-1">
      <div
        v-for="(item, i) in history"
        :key="i"
        class="rounded-xl border border-l-2 p-3.5 2xl:p-4.5 transition-all"
        :style="{ backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', borderLeftColor: accentColor(item) }"
      >
        <button @click="toggle(i)" class="w-full flex items-center justify-between text-left cursor-pointer gap-2">
          <div class="flex items-center space-x-2.5 2xl:space-x-3 min-w-0">
            <span class="font-mono font-bold text-xs 2xl:text-sm shrink-0 num-tabular" style="color: var(--text-main);">
              {{ item.time }}
            </span>
            <span
              v-if="item.council_transcript"
              class="px-2 py-0.5 rounded text-[11px] 2xl:text-xs font-mono font-bold border shrink-0"
              style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--text-main);"
            >
              🏛️ {{ t('radar.council') }}
            </span>
            <span class="text-xs 2xl:text-sm font-sans truncate" style="color: var(--text-muted);">
              {{ item.macro_assessment || t('radar.neutral') }}
            </span>
          </div>
          <ChevronDown
            class="w-4 h-4 shrink-0 transition-transform"
            style="color: var(--text-faint);"
            :class="expanded.has(i) ? 'rotate-180' : ''"
          />
        </button>

        <div v-if="expanded.has(i)" class="mt-3 2xl:mt-4 space-y-3 2xl:space-y-4 border-t pt-3 2xl:pt-4" style="border-color: var(--border-subtle);">
          <!-- Macro Summary -->
          <div>
            <div class="text-[11px] 2xl:text-xs font-bold font-mono uppercase mb-1" style="color: var(--text-faint);">
              {{ t('radar.macroSummary') }}:
            </div>
            <p class="text-xs 2xl:text-sm font-sans leading-relaxed" style="color: var(--text-main);">
              {{ item.macro_assessment || t('radar.neutral') }}
            </p>
          </div>

          <!-- Multi-Agent Council Transcript -->
          <div
            v-if="item.council_transcript"
            class="p-3.5 2xl:p-4.5 rounded-xl border space-y-2.5 2xl:space-y-3.5 font-mono"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs 2xl:text-sm font-bold" style="color: var(--text-main);">
                <Users class="w-4 h-4 2xl:w-4.5 2xl:h-4.5" />
                <span>【{{ t('radar.debate') }}】</span>
              </div>
              <span class="text-[11px] 2xl:text-xs font-mono" style="color: var(--text-faint);">
                {{ t('radar.latency') }}: {{ item.council_transcript.total_duration_ms }}ms
              </span>
            </div>

            <!-- Advisors viewpoints -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 2xl:gap-4 pt-1">
              <div
                v-for="(adv, advKey) in item.council_transcript.advisors || {}"
                :key="advKey"
                class="p-2.5 2xl:p-3.5 rounded-lg border space-y-1 text-xs 2xl:text-sm"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
              >
                <div class="flex items-center justify-between font-bold">
                  <span style="color: var(--text-main);">{{ adv.role_name }}</span>
                  <span class="text-[11px] 2xl:text-xs" style="color: var(--text-faint);">{{ adv.model_used }}</span>
                </div>
                <p class="text-[11px] 2xl:text-xs leading-relaxed whitespace-pre-wrap max-h-36 2xl:max-h-52 overflow-y-auto pr-0.5 select-text" style="color: var(--text-muted);">
                  {{ adv.content }}
                </p>
              </div>
            </div>

            <!-- Arbitrator summary -->
            <div class="mt-1 pt-2 border-t text-xs 2xl:text-sm font-bold flex items-center justify-between" style="border-color: var(--border-subtle); color: var(--color-up);">
              <span>⚖️ {{ t('radar.verdict') }}</span>
              <span class="text-[11px] 2xl:text-xs font-normal" style="color: var(--text-faint);">
                {{ t('radar.finalModel') }}: {{ item.council_transcript.arbitrator?.model_used }}
              </span>
            </div>
          </div>

          <!-- In-flight Position Management Instructions -->
          <div
            v-if="item.position_management?.length"
            class="p-3 2xl:p-4 rounded-xl border space-y-1.5 2xl:space-y-2 font-mono text-xs 2xl:text-sm"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[11px] 2xl:text-xs font-bold block uppercase" style="color: var(--text-faint);">{{ t('radar.posMgmt') }}</span>
            <div v-for="(p, j) in item.position_management" :key="j" class="flex flex-wrap items-center gap-x-2 gap-y-0.5 2xl:gap-x-3" style="color: var(--text-muted);">
              <strong style="color: var(--text-main);">{{ p.instId }}</strong>
              <span
                class="px-2 py-0.5 rounded font-bold border text-[11px] 2xl:text-xs"
                :style="{
                  backgroundColor: p.action?.includes('HOLD') ? 'var(--bg-badge)' : 'var(--color-warn-bg)',
                  borderColor: p.action?.includes('HOLD') ? 'var(--border-subtle)' : 'var(--color-warn-border)',
                  color: p.action?.includes('HOLD') ? 'var(--text-main)' : 'var(--color-warn)'
                }"
              >
                {{ p.action }}
              </span>
              <span v-if="p.reason" class="text-[11px] 2xl:text-xs" style="color: var(--text-muted);">{{ p.reason }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
