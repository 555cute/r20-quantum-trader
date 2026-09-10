<script setup lang="ts">
import { fmtDateTime } from '../../utils/format';
/** 决策透视抽屉：本周期实发给大模型的完整提示词（白盒承诺） */
import { computed } from 'vue';
import BaseDrawer from '../base/BaseDrawer.vue';
import BaseEmpty from '../base/BaseEmpty.vue';
import CopyButton from '../base/CopyButton.vue';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { useDashboardStore } from '../../stores/dashboard';
import { fmtNum } from '../../utils/format';

const { peekOpen } = useUi();
const { t } = useI18n();
const store = useDashboardStore();

const prompt = computed(() => store.data?.ai_last_prompt || '');
const chars = computed(() => prompt.value.length);
const tokens = computed(() => Math.round(prompt.value.length / 2.6)); // 中英混合粗估
</script>

<template>
  <BaseDrawer
    :open="peekOpen"
    width="780px"
    :title="t('dash.shell.peek.title')"
    :subtitle="t('dash.shell.peek.desc')"
    @close="peekOpen = false"
  >
    <template #actions>
      <CopyButton v-if="prompt" :text="prompt" label />
    </template>

    <BaseEmpty v-if="!prompt" :text="t('dash.shell.peek.empty')" />
    <pre v-else class="code-block max-h-[62vh] whitespace-pre-wrap">{{ prompt }}</pre>

    <div v-if="prompt" class="mt-2 flex items-center justify-between text-xs" style="color: var(--ink-3)">
      <span>{{ t('dash.shell.peek.chars', undefined, { n: fmtNum(chars, 0) }) }} · {{ t('dash.shell.peek.tokens', undefined, { n: fmtNum(tokens, 0) }) }}</span>
      <span>{{ t('dash.shell.peek.generatedAt', undefined, { t: fmtDateTime(store.data?.timestamp || '--') }) }}</span>
    </div>
  </BaseDrawer>
</template>
