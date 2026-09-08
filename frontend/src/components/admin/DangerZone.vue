<script setup lang="ts">
/** P2 shared: DangerZone — Vercel/GitHub pattern.
 *  Red-striped band stating WHAT is unrecoverable + type-to-confirm that must
 *  EXACTLY match `confirmPhrase` (the resource name) before the action unlocks.
 *  Reversible operations should NOT use this component (use an undo toast instead). */
import { ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { ShieldAlert } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  title: string
  /** what exactly is lost, unrecoverable — one blunt sentence */
  description: string
  /** resource name the user must type (e.g. account login, "ALL", filename) */
  confirmPhrase: string
  actionLabel: string
  placeholder?: string
}>(), { placeholder: '' })

const emit = defineEmits<{ (e: 'confirm'): void }>()
const { t } = useI18n()

const typed = ref('')
const unlocked = ref(false)
watch(() => props.confirmPhrase, () => { typed.value = ''; unlocked.value = false })

function onInput() {
  unlocked.value = typed.value === props.confirmPhrase
}
function act() {
  if (!unlocked.value) return
  emit('confirm')
  typed.value = ''
  unlocked.value = false
}
</script>

<template>
  <div
    class="rounded-xl border overflow-hidden"
    style="border-color: var(--color-down-border);"
  >
    <!-- hazard stripe -->
    <div class="h-1" style="background: repeating-linear-gradient(45deg, var(--color-down) 0 10px, transparent 10px 20px); opacity: .55;"></div>
    <div class="p-4" style="background-color: var(--bg-card);">
      <div class="flex items-start gap-2.5">
        <ShieldAlert class="w-4 h-4 shrink-0 mt-0.5" style="color: var(--color-down);" />
        <div class="min-w-0 flex-1">
          <h4 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--color-down);">{{ title }}</h4>
          <p class="text-[11px] font-mono mt-1 leading-relaxed" style="color: var(--text-muted);">{{ description }}</p>
          <div class="mt-3 flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
            <input
              v-model="typed"
              @input="onInput"
              type="text"
              autocomplete="off"
              :placeholder="placeholder || confirmPhrase"
              class="h-8 px-2.5 rounded-lg border text-xs font-mono outline-none transition-shadow min-w-0 flex-1"
              style="background-color: var(--bg-input); border-color: var(--border-medium); color: var(--text-main);"
              :style="unlocked ? { borderColor: 'var(--color-down)' } : {}"
            />
            <button
              @click="act"
              :disabled="!unlocked"
              class="h-8 px-3.5 rounded-lg border text-xs font-mono font-bold transition-all shrink-0 cursor-pointer disabled:cursor-not-allowed"
              :style="unlocked
                ? { backgroundColor: 'var(--color-down-bg)', borderColor: 'var(--color-down-border)', color: 'var(--color-down)' }
                : { backgroundColor: 'var(--bg-badge)', borderColor: 'var(--border-subtle)', color: 'var(--text-faint)' }"
            >
              {{ actionLabel }}
            </button>
          </div>
          <p class="text-[11px] font-mono mt-1.5" style="color: var(--text-faint);">
            {{ t('admin.dzUnlock').replace('{phrase}', confirmPhrase) }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
