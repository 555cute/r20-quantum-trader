<script setup lang="ts">
/**
 * 右侧滑出抽屉 —— 详情透视专用：列表上下文不丢，看完即关。
 * 规则：任何"看详情"一律 Drawer，禁止全屏跳转或嵌套弹窗。
 * 视觉：浮层最高台阶 surface-4 + 左侧品牌光边 + 遮罩毛玻璃，头部吸顶。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { X } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';

const { t } = useI18n();

const props = withDefaults(
  defineProps<{
    open: boolean;
    title?: string;
    subtitle?: string;
    width?: string;
    size?: 'sm' | 'md' | 'lg' | 'xl';
    closeOnScrim?: boolean;
  }>(),
  { closeOnScrim: true, size: 'md' },
);

const SIZE_W: Record<string, string> = { sm: '420px', md: '580px', lg: '760px', xl: '980px' };
const resolvedWidth = computed(() => props.width || SIZE_W[props.size || 'md']);

const emit = defineEmits<{ (e: 'close'): void }>();

const panel = ref<HTMLElement | null>(null);
let lastFocused: Element | null = null;

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation();
    emit('close');
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
      panel.value?.focus?.();
    } else {
      document.body.style.overflow = '';
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
      <div v-if="open" class="fixed inset-0" style="z-index: var(--z-drawer)">
        <div
          class="absolute inset-0"
          style="background-color: var(--overlay-scrim); backdrop-filter: blur(4px) saturate(120%)"
          @mousedown="closeOnScrim && emit('close')"
        />
        <Transition name="drawer" appear>
          <aside
            v-if="open"
            ref="panel"
            tabindex="-1"
            role="dialog"
            aria-modal="true"
            class="drawer-panel absolute inset-y-0 right-0 flex flex-col outline-none"
            :style="{ width: `min(${resolvedWidth}, 96vw)` }"
          >
            <header class="drawer-head flex shrink-0 items-start justify-between gap-4 px-5 py-3.5">
              <div class="min-w-0">
                <h3 class="truncate text-md font-semibold" style="color: var(--ink-strong)">
                  <slot name="title">{{ title }}</slot>
                </h3>
                <p v-if="subtitle || $slots.subtitle" class="mt-0.5 truncate text-xs" style="color: var(--ink-2)">
                  <slot name="subtitle">{{ subtitle }}</slot>
                </p>
              </div>
              <div class="flex shrink-0 items-center gap-1">
                <slot name="actions" />
                <button class="btn btn-quiet btn-icon" :aria-label="t('common.close')" @click="emit('close')"><X /></button>
              </div>
            </header>
            <div class="scroll-y flex-1 px-5 py-4">
              <slot />
            </div>
            <footer
              v-if="$slots.footer"
              class="drawer-foot flex shrink-0 items-center justify-end gap-2 px-5 py-3.5"
            >
              <slot name="footer" />
            </footer>
          </aside>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-panel {
  background-color: var(--surface-4);
  border-left: 1px solid var(--line-2);
  box-shadow: var(--shadow-dialog), inset 1px 0 0 var(--line-hair-strong);
}
/* 左侧品牌光边：让抽屉与画布之间有一条可感知的光缝 */
.drawer-panel::before {
  content: '';
  position: absolute;
  inset-block: 0;
  left: 0;
  width: 1px;
  background: linear-gradient(180deg, var(--accent-line), transparent 55%);
  pointer-events: none;
}
.drawer-head {
  border-bottom: 1px solid var(--line-1);
  background-image: linear-gradient(180deg, color-mix(in srgb, var(--surface-3) 60%, transparent), transparent);
}
.drawer-foot { border-top: 1px solid var(--line-1); background-color: var(--surface-3); }

.drawer-enter-active,
.drawer-leave-active {
  transition: transform var(--dur-slow) var(--ease-out), opacity var(--dur-slow) var(--ease-out);
}
.drawer-enter-from,
.drawer-leave-to {
  transform: translateX(28px);
  opacity: 0.4;
}
</style>
