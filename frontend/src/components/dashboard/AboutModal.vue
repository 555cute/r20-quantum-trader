<script setup lang="ts">
/** 关于与社区弹窗：三所对等架构 / 仓库 / 社区 / 许可与风险提示 */
import { Github, ShieldCheck, ExternalLink, Cpu, Layers } from 'lucide-vue-next';
import BaseDialog from '../base/BaseDialog.vue';
import CopyButton from '../base/CopyButton.vue';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { APP_VERSION, BRAND_REVISION, OFFICIAL_REPO } from '../../config/version';
import { VENUE_KEYS, venueLabel } from '../../utils/venueMeta';

const { aboutOpen } = useUi();
const { t, tm } = useI18n();

const QQ_GROUP = '655973677';
const QQ_PERSONAL = '1090188816';
const LINUXDO = 'https://linux.do/';

const brandColors: Record<string, string> = {
  okx: 'var(--venue-okx, #3880ff)',
  binance: 'var(--venue-binance, #f3ba2f)',
  gate: 'var(--venue-gate, #00be98)',
};
</script>

<template>
  <BaseDialog :open="aboutOpen" size="sm" @close="aboutOpen = false">
    <template #title>
      <div class="flex items-center gap-3">
        <img src="/favicon.svg" class="h-9 w-9 rounded-lg shadow-md" alt="R20" />
        <div>
          <div class="flex items-center gap-2">
            <p class="text-base font-bold leading-tight" style="color: var(--ink-strong)">{{ t('brand.name') }}</p>
            <span class="badge badge-accent text-[10px]">PRO</span>
          </div>
          <p class="text-xs text-[var(--ink-3)]">{{ t('brand.tagline') }}</p>
        </div>
      </div>
    </template>

    <!-- 三所对等执行架构 -->
    <div class="space-y-3">
      <!-- 三所徽章展示条 -->
      <div class="flex items-center justify-between rounded-lg border p-2.5" style="border-color: var(--line-1); background-color: var(--surface-2)">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-[var(--ink-1)]">
          <Layers class="h-3.5 w-3.5 text-[var(--accent)]" />
          <span>三所对等执行架构</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span
            v-for="v in VENUE_KEYS"
            :key="v"
            class="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-bold border"
            :style="{
              color: brandColors[v],
              borderColor: 'var(--line-2)',
              backgroundColor: 'var(--surface-1)',
            }"
          >
            <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: brandColors[v] }" />
            {{ venueLabel(v) }}
          </span>
        </div>
      </div>

      <!-- 架构详细亮点 -->
      <div class="card-flat p-3.5">
        <div class="section-title mb-2 !text-xs flex items-center gap-1.5">
          <ShieldCheck class="h-4 w-4" style="color: var(--accent)" />
          <span>{{ t('dash.about.arch.title') }}</span>
        </div>
        <p class="mono mb-2 text-2xs" style="color: var(--ink-3)">{{ t('dash.about.arch.stack') }}</p>
        <ul class="space-y-2">
          <li v-for="(p, i) in tm('dash.about.arch.points')" :key="i" class="flex items-start gap-2 text-xs leading-relaxed" style="color: var(--ink-2)">
            <span class="dot dot-up mt-1 shrink-0" style="width: 5px; height: 5px" />
            <span>{{ p }}</span>
          </li>
        </ul>
      </div>

      <!-- 官方开源仓库 -->
      <a
        :href="OFFICIAL_REPO"
        target="_blank"
        rel="noopener noreferrer"
        class="btn btn-primary w-full flex items-center justify-center gap-2 text-xs font-semibold"
      >
        <Github class="h-4 w-4" />
        <span>{{ t('dash.about.repo.visit') }}</span>
        <ExternalLink class="h-3.5 w-3.5 opacity-70" />
      </a>
      <p class="text-center text-[11px]" style="color: var(--ink-3)">{{ t('dash.about.repo.starHint') }}</p>

      <!-- 社区交流渠道 -->
      <div class="grid grid-cols-2 gap-2">
        <div class="card-flat flex items-center justify-between gap-2 px-3 py-2">
          <div class="min-w-0">
            <p class="t-label text-[10px]">{{ t('dash.about.community.qqGroup') }}</p>
            <p class="num truncate text-xs font-semibold" style="color: var(--ink-1)">{{ QQ_GROUP }}</p>
          </div>
          <CopyButton :text="QQ_GROUP" />
        </div>
        <div class="card-flat flex items-center justify-between gap-2 px-3 py-2">
          <div class="min-w-0">
            <p class="t-label text-[10px]">{{ t('dash.about.community.qqPersonal') }}</p>
            <p class="num truncate text-xs font-semibold" style="color: var(--ink-1)">{{ QQ_PERSONAL }}</p>
          </div>
          <CopyButton :text="QQ_PERSONAL" />
        </div>
        <a
          :href="LINUXDO"
          target="_blank"
          rel="noopener noreferrer"
          class="card-flat col-span-2 flex items-center justify-between gap-2 px-3 py-2 transition-colors hover:bg-[var(--surface-3)]"
        >
          <span class="t-label text-xs">{{ t('dash.about.community.linuxdo') }}</span>
          <span class="link text-xs font-semibold">linux.do ↗</span>
        </a>
      </div>

      <!-- 版本、协议与风险声明 -->
      <div class="space-y-1 border-t pt-2.5 text-center" style="border-color: var(--line-1)">
        <p class="num text-[11px]" style="color: var(--ink-2)">
          {{ t('dash.about.version', undefined, { v: APP_VERSION, r: BRAND_REVISION }) }}
        </p>
        <p class="text-[10px]" style="color: var(--ink-3)">{{ t('dash.about.license') }}</p>
        <p class="text-[10px] leading-relaxed" style="color: var(--ink-3)">{{ t('dash.about.risk') }}</p>
      </div>
    </div>
  </BaseDialog>
</template>
