<script setup lang="ts">
/** 全局确认框宿主：由 useConfirm().ask() 驱动，App.vue 挂一次。 */
import { computed, ref, watch } from 'vue';
import { AlertTriangle } from 'lucide-vue-next';
import BaseDialog from './BaseDialog.vue';
import { useConfirm } from '../../composables/useConfirm';
import { useI18n } from '../../composables/useI18n';

const { state, settle } = useConfirm();
const { t } = useI18n();

const phraseInput = ref('');
const phraseOk = computed(() => !state.value.confirmPhrase || phraseInput.value.trim() === state.value.confirmPhrase);

watch(
  () => [state.value.open, state.value.id],
  () => {
    phraseInput.value = '';
  },
);

function onConfirm() {
  if (!phraseOk.value) return;
  settle(true);
}
</script>

<template>
  <BaseDialog
    :open="state.open"
    :size="'sm'"
    :tone="state.danger ? 'danger' : 'default'"
    :show-close="false"
    @close="settle(false)"
  >
    <div class="flex items-start gap-3">
      <div
        v-if="state.danger"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full"
        style="background-color: var(--down-bg); border: 1px solid var(--down-line)"
      >
        <AlertTriangle class="h-4.5 w-4.5" style="color: var(--down)" />
      </div>
      <div class="min-w-0">
        <h3 class="text-base font-semibold" style="color: var(--ink-strong)">{{ state.title }}</h3>
        <p v-if="state.desc" class="mt-1 text-sm leading-relaxed" style="color: var(--ink-2)">{{ state.desc }}</p>
        <p v-if="state.detail" class="mono mt-2 break-all text-xs" style="color: var(--ink-3)">{{ state.detail }}</p>
      </div>
    </div>

    <div v-if="state.confirmPhrase" class="mt-4">
      <label class="form-label">
        {{ t('common.confirmPhraseHint', undefined, { phrase: state.confirmPhrase }) }}
      </label>
      <input v-model="phraseInput" class="field mono" :placeholder="state.confirmPhrase" @keyup.enter="onConfirm" />
    </div>

    <template #footer>
      <button class="btn btn-ghost" @click="settle(false)">{{ state.cancelText || t('common.cancel') }}</button>
      <button
        class="btn"
        :class="state.danger ? 'btn-danger' : 'btn-primary'"
        :disabled="!phraseOk"
        @click="onConfirm"
      >
        {{ state.okText || t('common.confirm') }}
      </button>
    </template>
  </BaseDialog>
</template>
