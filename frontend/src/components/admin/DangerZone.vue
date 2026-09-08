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
    style="border-color: var(--down-line);"
  >
    <!-- hazard stripe -->
    <div class="h-1" style="background: repeating-linear-gradient(45deg, var(--down) 0 10px, transparent 10px 20px); opacity: .55;"></div>
    <div class="p-4" style="background-color: var(--surface-2);">
      <div class="flex items-start gap-2.5">
        <ShieldAlert class="w-4 h-4 shrink-0 mt-0.5" style="color: var(--down);" />
        <div class="min-w-0 flex-1">
          <h4 class="text-xs font-black uppercase tracking-wide" style="color: var(--down);">{{ title }}</h4>
          <p class="text-[11px] mt-1 leading-relaxed" style="color: var(--ink-2);">{{ description }}</p>
          <div class="mt-3 flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
            <input
              v-model="typed"
              @input="onInput"
              type="text"
              autocomplete="off"
              :placeholder="placeholder || confirmPhrase"
              class="h-8 px-2.5 rounded-lg border text-xs outline-none transition-shadow min-w-0 flex-1"
              style="background-color: var(--surface-input); border-color: var(--line-2); color: var(--ink-1);"
              :style="unlocked ? { borderColor: 'var(--down)' } : {}"
            />
            <button
              @click="act"
              :disabled="!unlocked"
              class="h-8 px-3.5 rounded-lg border text-xs font-bold transition-all shrink-0 cursor-pointer disabled:cursor-not-allowed"
              :style="unlocked
                ? { backgroundColor: 'var(--down-bg)', borderColor: 'var(--down-line)', color: 'var(--down)' }
                : { backgroundColor: 'var(--surface-3)', borderColor: 'var(--line-1)', color: 'var(--ink-3)' }"
            >
              {{ actionLabel }}
            </button>
          </div>
          <p class="text-[11px] mt-1.5" style="color: var(--ink-3);">
            {{ t('admin.dzUnlock').replace('{phrase}', confirmPhrase) }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
