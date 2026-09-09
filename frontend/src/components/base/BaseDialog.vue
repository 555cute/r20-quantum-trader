<script setup lang="ts">
/**
 * 全局对话框原语：Teleport 挂 body、焦点陷阱、ESC/遮罩关闭、滚动锁。
 * 规则：编辑/表单用 Dialog，详情透视用 Drawer，删除确认用 useConfirm。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { X } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';

const { t } = useI18n();

const props = withDefaults(
  defineProps<{
    open: boolean;
    title?: string;
    desc?: string;
    size?: 'sm' | 'md' | 'lg' | 'xl';
    closeOnScrim?: boolean;
    showClose?: boolean;
    /** 顶部强调条语义（danger 时用于编辑危险表单） */
    tone?: 'default' | 'danger';
  }>(),
  { closeOnScrim: true, showClose: true, tone: 'default' },
);

const emit = defineEmits<{ (e: 'close'): void }>();

const width = computed(
  () => ({ sm: '400px', md: '560px', lg: '760px', xl: '960px' })[props.size || 'md'],
);

const panel = ref<HTMLElement | null>(null);
let lastFocused: Element | null = null;

function focusables(): HTMLElement[] {
  if (!panel.value) return [];
  return Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => el.offsetParent !== null);
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation();
    emit('close');
    return;
  }
  if (e.key === 'Tab') {
    const els = focusables();
    if (!els.length) return;
    const first = els[0];
    const last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      lastFocused = document.activeElement;
      document.body.style.overflow = 'hidden';
      await nextTick();
      panel.value?.addEventListener('keydown', onKeydown);
      const els = focusables();
      (els[0] || panel.value)?.focus?.();
    } else {
      document.body.style.overflow = '';
      panel.value?.removeEventListener('keydown', onKeydown);
      (lastFocused as HTMLElement | null)?.focus?.();
    }
  },
);

onBeforeUnmount(() => {
  document.body.style.overflow = '';
});
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="fixed inset-0 flex items-start justify-center overflow-y-auto p-4 sm:p-6"
        style="z-index: var(--z-dialog)"
        :style="{ paddingTop: 'max(10vh, 24px)' }"
        @mousedown.self="closeOnScrim && emit('close')"
      >
        <div class="fixed inset-0" style="background-color: var(--overlay-scrim)" aria-hidden="true" />
        <Transition name="pop" appear>
          <div
            ref="panel"
            role="dialog"
            aria-modal="true"
            tabindex="-1"
            class="float-panel relative w-full outline-none"
            :style="{ maxWidth: width, outline: tone === 'danger' ? '1px solid var(--down-line)' : undefined }"
          >
            <!-- 头部 -->
            <div
              v-if="title || $slots.title || showClose"
              class="flex items-start justify-between gap-4 px-5 pt-4 pb-3"
              style="border-bottom: 1px solid var(--line-1)"
            >
              <div class="min-w-0">
                <h3 class="text-base font-semibold" style="color: var(--ink-strong)">
                  <slot name="title">{{ title }}</slot>
                </h3>
                <p v-if="desc" class="mt-0.5 text-xs" style="color: var(--ink-2)">{{ desc }}</p>
              </div>
              <button
                v-if="showClose"
                class="btn btn-quiet btn-icon shrink-0 -me-1.5"
                :aria-label="t('common.close')"
                @click="emit('close')"
              >
                <X />
              </button>
            </div>

            <!-- 内容 -->
            <div class="scroll-y px-5 py-4" :style="{ maxHeight: 'min(68vh, 640px)' }">
              <slot />
            </div>

            <!-- 底部 -->
            <div
              v-if="$slots.footer"
              class="flex items-center justify-end gap-2 px-5 py-3.5"
              style="border-top: 1px solid var(--line-1)"
            >
              <slot name="footer" />
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
