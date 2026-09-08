<script setup lang="ts">
/** 一键复制：点击后 2s 绿色"已复制"反馈 */
import { ref } from 'vue';
import { Check, Copy } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';

const props = withDefaults(defineProps<{ text: string; label?: boolean }>(), { label: false });
const { t } = useI18n();
const done = ref(false);
let timer = 0;

async function copy() {
  try {
    await navigator.clipboard.writeText(props.text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = props.text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  done.value = true;
  window.clearTimeout(timer);
  timer = window.setTimeout(() => (done.value = false), 2000);
}
</script>

<template>
  <button
    class="btn"
    :class="label ? 'btn-ghost btn-sm' : 'btn-quiet btn-icon btn-sm'"
    :title="t('common.copy')"
    @click="copy"
  >
    <Check v-if="done" style="color: var(--up)" />
    <Copy v-else />
    <span v-if="label">{{ done ? t('common.copied') : t('common.copy') }}</span>
  </button>
</template>
