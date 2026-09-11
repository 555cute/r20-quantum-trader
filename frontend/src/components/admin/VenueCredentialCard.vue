<script setup lang="ts">
/** 三所凭证卡外壳：所名 + 接口档 + 状态徽章 → 资金档位 → 凭证区 → 附加区 → 页脚动作。 */
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  name: string
  apiLabel: string
  statusText: string
  tone?: 'up' | 'warn' | 'down'
  envText: string
  envLabel?: string
}>(), { tone: 'warn', envLabel: '当前资金档位' })

const TONES: Record<string, Record<string, string>> = {
  up: { color: 'var(--up)', borderColor: 'var(--up-line)', backgroundColor: 'var(--up-bg)' },
  warn: { color: 'var(--warn)', borderColor: 'var(--warn-line)', backgroundColor: 'var(--warn-bg)' },
  down: { color: 'var(--down)', borderColor: 'var(--down-line)', backgroundColor: 'var(--down-bg)' },
}

const toneStyle = computed(() => TONES[props.tone] ?? TONES.warn)
</script>

<template>
  <div class="flex flex-col justify-between rounded-lg border p-3" style="background-color: var(--surface-1); border-color: var(--line-1);">
    <div class="space-y-2.5">
      <!-- 头部：所名 + 接口档 + 文字状态徽章 -->
      <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1);">
        <div class="min-w-0">
          <h4 class="text-xs font-bold truncate" style="color: var(--ink-1);">{{ name }}</h4>
          <span class="text-[10px]" style="color: var(--ink-3);">{{ apiLabel }}</span>
        </div>
        <span class="text-[10px] px-1.5 py-0.5 rounded border font-bold shrink-0" :style="toneStyle">{{ statusText }}</span>
      </div>

      <!-- 资金环境档位：三所同位，一眼看清当前凭证属于实盘还是模拟盘 -->
      <div class="flex items-center justify-between text-[10px]" style="color: var(--ink-3);">
        <span>{{ envLabel }}</span>
        <b class="num" style="color: var(--ink-1);">{{ envText }}</b>
      </div>
      <slot name="env" />

      <!-- 凭证区 -->
      <slot />

      <!-- 附加区（执行闸门 / 能力说明） -->
      <slot name="extra" />
    </div>

    <!-- 页脚：统一「检测 + 保存」动作位 -->
    <div class="pt-3 mt-2 border-t flex items-center justify-end gap-2" style="border-color: var(--line-1);">
      <slot name="footer-left" />
      <div class="ms-auto flex items-center gap-2">
        <slot name="probe" />
        <slot name="save" />
      </div>
    </div>
  </div>
</template>
