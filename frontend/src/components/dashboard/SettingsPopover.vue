<script setup lang="ts">
/** 偏好弹层：主题 / 语言 / 色盲配色 / 执行架构 —— 机构终端风格收拢偏好设置 */
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { SlidersHorizontal, BookOpen, LayoutDashboard, Eye, Cpu, ShieldCheck } from 'lucide-vue-next';
import { useRouter } from 'vue-router';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import BaseSegmented from '../base/BaseSegmented.vue';
import BaseSwitch from '../base/BaseSwitch.vue';

const { t, currentLocale, setLocale } = useI18n();
const { theme, setTheme, cvd, toggleCvd } = useTheme();

const router = useRouter();
const { peekOpen } = useUi();
const open = ref(false);
const trigger = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);

function onDocDown(e: MouseEvent) {
  const el = e.target as Node;
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
      type="button"
      class="btn btn-quiet btn-icon"
      :class="{ 'bg-[var(--surface-3)] text-[var(--ink-strong)]': open }"
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
        class="float-panel absolute end-0 top-10 z-50 w-72 p-3.5 shadow-2xl backdrop-blur-2xl"
        style="
          background-color: var(--surface-header);
          border-color: var(--line-2);
        "
        role="menu"
      >
        <div class="space-y-3">
          <!-- 标题 -->
          <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1)">
            <span class="text-xs font-semibold text-[var(--ink-strong)]">
              {{ t('dash.shell.settings.title') }}
            </span>
            <span class="badge badge-quiet text-[10px] tabular-nums">PRO</span>
          </div>

          <!-- 外观主题 -->
          <div>
            <p class="form-label mb-1.5 text-xs">{{ t('dash.shell.settings.theme') }}</p>
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

          <!-- 界面语言 -->
          <div>
            <p class="form-label mb-1.5 text-xs">{{ t('dash.shell.settings.language') }}</p>
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

          <!-- 色盲辅助模式 -->
          <label class="flex cursor-pointer items-center justify-between gap-3 py-1">
            <span>
              <span class="block text-xs font-medium" style="color: var(--ink-1)">{{ t('dash.shell.settings.cvd') }}</span>
              <span class="block text-[11px] leading-snug" style="color: var(--ink-3)">{{ t('dash.shell.settings.cvdDesc') }}</span>
            </span>
            <BaseSwitch :model-value="cvd" @update:model-value="toggleCvd()" />
          </label>

          <!-- 三所执行架构说明卡片 -->
          <div class="rounded-lg border p-2 text-xs" style="border-color: var(--line-1); background-color: var(--surface-2)">
            <div class="flex items-center gap-1.5 font-semibold text-[var(--ink-1)]">
              <Cpu class="h-3.5 w-3.5 text-[var(--accent)]" />
              <span>{{ t('dash.shell.settings.architecture') }}</span>
            </div>
            <p class="mt-1 text-[11px] leading-tight text-[var(--ink-3)]">
              {{ t('dash.shell.settings.architectureDesc') }}
            </p>
          </div>

          <!-- 快速直达 -->
          <div class="border-t pt-2.5" style="border-color: var(--line-1)">
            <p class="form-label mb-1.5 text-xs">{{ t('dash.shell.settings.goto') }}</p>
            <div class="grid grid-cols-1 gap-1">
              <button
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs transition-colors hover:bg-[var(--surface-3)]"
                style="color: var(--ink-1)"
                @click="open = false; router.push('/docs')"
              >
                <BookOpen class="h-3.5 w-3.5" style="color: var(--ink-3)" />
                <span>{{ t('nav.actions.docs') }}</span>
              </button>
              <button
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs transition-colors hover:bg-[var(--surface-3)]"
                style="color: var(--ink-1)"
                @click="open = false; router.push('/admin')"
              >
                <LayoutDashboard class="h-3.5 w-3.5" style="color: var(--ink-3)" />
                <span>{{ t('nav.actions.console') }}</span>
              </button>
              <button
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs transition-colors hover:bg-[var(--surface-3)]"
                style="color: var(--ink-1)"
                @click="open = false; peekOpen = true"
              >
                <Eye class="h-3.5 w-3.5" style="color: var(--ink-3)" />
                <span>{{ t('nav.actions.promptPeek') }}</span>
              </button>
            </div>
          </div>

          <!-- 底部快照同步提示 -->
          <p class="border-t pt-2 text-[10px] text-[var(--ink-3)] flex items-center gap-1" style="border-color: var(--line-1)">
            <ShieldCheck class="h-3 w-3 text-[var(--accent)] shrink-0" />
            <span>{{ t('dash.shell.settings.dataNote') }}</span>
          </p>
        </div>
      </div>
    </Transition>
  </div>
</template>
