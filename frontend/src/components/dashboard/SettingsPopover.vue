<script setup lang="ts">
/** 偏好弹层：主题 / 语言 / 色盲配色 —— 收进一个 ⚙，顶栏不再散落按钮 */
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { SlidersHorizontal } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import BaseSegmented from '../base/BaseSegmented.vue';
import BaseSwitch from '../base/BaseSwitch.vue';

const { t, currentLocale, setLocale } = useI18n();
const { theme, setTheme, cvd, toggleCvd } = useTheme();

const open = ref(false);
const trigger = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);

function onDocDown(e: MouseEvent) {
  const el = e.target as Node;
  // 守卫同时检查触发器与面板，避免 capture 阶段先关后丢 handler（P1 教训）
  if (open.value && !trigger.value?.contains(el) && !panel.value?.contains(el)) open.value = false;
}
function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false;
}
onMounted(() => {
  document.addEventListener('mousedown', onDocDown);
  window.addEventListener('keydown', onEsc);
});
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown);
  window.removeEventListener('keydown', onEsc);
});
</script>

<template>
  <div class="relative">
    <button
      ref="trigger"
      class="btn btn-quiet btn-icon"
      :aria-expanded="open"
      :title="t('dash.shell.settings.title')"
      @click="open = !open"
    >
      <SlidersHorizontal class="h-4 w-4" />
    </button>
    <Transition name="pop">
      <div
        v-if="open"
        ref="panel"
        class="float-panel absolute end-0 top-10 w-64 p-3"
        role="menu"
      >
        <div class="space-y-3">
          <div>
            <p class="form-label mb-1.5">{{ t('dash.shell.settings.theme') }}</p>
            <BaseSegmented
              class="w-full"
              :model-value="theme"
              :options="[
                { value: 'dark', label: t('dash.shell.settings.themeDark') },
                { value: 'light', label: t('dash.shell.settings.themeLight') },
              ]"
              @update:model-value="(v: any) => setTheme(v)"
            />
          </div>
          <div>
            <p class="form-label mb-1.5">{{ t('dash.shell.settings.language') }}</p>
            <BaseSegmented
              class="w-full"
              :model-value="currentLocale"
              :options="[
                { value: 'zh-CN', label: '中文' },
                { value: 'en-US', label: 'English' },
              ]"
              @update:model-value="(v: any) => setLocale(v)"
            />
          </div>
          <label class="flex cursor-pointer items-center justify-between gap-3 py-0.5">
            <span>
              <span class="block text-sm font-medium" style="color: var(--ink-1)">{{ t('dash.shell.settings.cvd') }}</span>
              <span class="block text-xs leading-snug" style="color: var(--ink-3)">{{ t('dash.shell.settings.cvdDesc') }}</span>
            </span>
            <BaseSwitch :model-value="cvd" @update:model-value="toggleCvd()" />
          </label>
          <p class="t-faint border-t pt-2 text-xs" style="border-color: var(--line-1)">
            {{ t('dash.shell.settings.dataNote') }}
          </p>
        </div>
      </div>
    </Transition>
  </div>
</template>
