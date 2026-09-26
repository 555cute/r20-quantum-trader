<script setup lang="ts">
/** 关于与社区弹窗：架构 / 仓库 / QQ / LINUX DO / 许可与风险提示 */
import { onMounted } from 'vue';
import { Github, ShieldCheck, ExternalLink, Globe } from 'lucide-vue-next';
import BaseDialog from '../base/BaseDialog.vue';
import CopyButton from '../base/CopyButton.vue';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { useReferralChannels } from '../../composables/useReferralChannels';
import { APP_VERSION, BRAND_REVISION, OFFICIAL_REPO, OFFICIAL_SITE } from '../../config/version';

const { aboutOpen } = useUi();
const { t, tm } = useI18n();

const QQ_GROUP = '655973677';
const QQ_PERSONAL = '1090188816';
const LINUXDO = 'https://linux.do/';

// 通道来自后端（公开只读），**不再在前端写死** —— 两份字面量会让
// "用环境变量替换成自己的通道"能力在用户唯一看得见的地方失效。
const { channels, load: loadChannels } = useReferralChannels();
onMounted(loadChannels);
</script>

<template>
  <BaseDialog
    :open="aboutOpen"
    :title="t('brand.name')"
    :desc="t('brand.tagline')"
    size="sm"
    @close="aboutOpen = false"
  >
    <template #title>
      <div class="flex items-center gap-2.5">
        <img src="/favicon.svg" class="h-8 w-8 rounded-lg" alt="" />
        <div>
          <p class="text-base font-bold leading-tight" style="color: var(--ink-strong)">{{ t('brand.name') }}</p>
          <p class="text-xs" style="color: var(--ink-3)">{{ t('brand.tagline') }}</p>
        </div>
      </div>
    </template>

    <!-- 架构 -->
    <div class="card-flat p-3.5">
      <p class="section-title mb-2 !text-sm">
        <ShieldCheck class="h-4 w-4" style="color: var(--accent)" />
        {{ t('dash.about.arch.title') }}
      </p>
      <p class="mono mb-2 text-xs" style="color: var(--ink-3)">{{ t('dash.about.arch.stack') }}</p>
      <ul class="space-y-1.5">
        <li v-for="(p, i) in tm('dash.about.arch.points')" :key="i" class="flex gap-2 text-xs leading-body" style="color: var(--ink-2)">
          <span class="dot dot-up mt-1.5" aria-hidden="true" />{{ p }}
        </li>
      </ul>
    </div>

    <!-- 仓库 -->
    <a :href="OFFICIAL_REPO" target="_blank" rel="noopener noreferrer" class="btn btn-primary mt-3 w-full">
      <Github aria-hidden="true" />
      {{ t('dash.about.repo.visit') }}
      <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
      <ExternalLink class="h-3.5 w-3.5 opacity-70" aria-hidden="true" />
    </a>
    <p class="mt-1.5 text-center text-xs" style="color: var(--ink-3)">{{ t('dash.about.repo.starHint') }}</p>

    <!-- 自有官网：品牌主页（自有域名，不依赖任何第三方托管） -->
    <a
      :href="OFFICIAL_SITE"
      target="_blank"
      rel="noopener noreferrer"
      class="btn btn-quiet mt-2 w-full"
    >
      <Globe aria-hidden="true" />
      astraquant.tech
      <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
      <ExternalLink class="h-3.5 w-3.5 opacity-70" aria-hidden="true" />
    </a>

    <!-- 社区 -->
    <div class="mt-3 grid grid-cols-2 gap-2">
      <div class="card-flat flex items-center justify-between gap-2 px-3 py-2.5">
        <div class="min-w-0">
          <p class="t-label">{{ t('dash.about.community.qqGroup') }}</p>
          <p class="num truncate text-sm font-semibold" style="color: var(--ink-1)">{{ QQ_GROUP }}</p>
        </div>
        <CopyButton :text="QQ_GROUP" />
      </div>
      <div class="card-flat flex items-center justify-between gap-2 px-3 py-2.5">
        <div class="min-w-0">
          <p class="t-label">{{ t('dash.about.community.qqPersonal') }}</p>
          <p class="num truncate text-sm font-semibold" style="color: var(--ink-1)">{{ QQ_PERSONAL }}</p>
        </div>
        <CopyButton :text="QQ_PERSONAL" />
      </div>
      <a
        :href="LINUXDO"
        target="_blank"
        rel="noopener noreferrer"
        class="card-flat col-span-2 flex items-center justify-between gap-2 px-3 py-2.5 transition-colors hover:bg-[var(--surface-3)]"
      >
        <span class="t-label">{{ t('dash.about.community.linuxdo') }}</span>
        <span class="link text-sm font-semibold">
          linux.do <span aria-hidden="true">↗</span>
          <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
        </span>
      </a>
      <!-- 卡片本体是 div：复制按钮是 <button>，塞进 <a> 里就是交互元素嵌套（a11y 违规）。
           故"打开"与"复制"并排两个控件，各自可聚焦、各自有名字。 -->
      <div
        v-for="ch in channels"
        :key="ch.key"
        class="card-flat flex items-center justify-between gap-2 px-3 py-2.5"
      >
        <div class="min-w-0">
          <p class="t-label">{{ t('dash.about.community.channel', undefined, { venue: ch.name }) }}</p>
          <p class="num truncate text-sm font-semibold" style="color: var(--brand, #3b82f6)">
            {{ ch.code || ch.name }}
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <a
            :href="ch.invite_url"
            target="_blank"
            rel="noopener noreferrer"
            class="btn btn-quiet btn-sm"
            :title="t('dash.about.community.open')"
            :aria-label="`${ch.name} · ${t('dash.about.community.open')}`"
          >
            <ExternalLink class="h-3.5 w-3.5" aria-hidden="true" />
            <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
          </a>
          <CopyButton :text="ch.invite_url" />
        </div>
      </div>
    </div>

    <div class="mt-4 space-y-1 border-t pt-3 text-center" style="border-color: var(--line-1)">
      <p class="num text-xs" style="color: var(--ink-3)">
        {{ t('dash.about.version', undefined, { v: APP_VERSION, r: BRAND_REVISION }) }}
      </p>
      <p class="text-xs" style="color: var(--ink-3)">{{ t('dash.about.license') }}</p>
      <p class="text-xs leading-body" style="color: var(--ink-3)">{{ t('dash.about.risk') }}</p>
    </div>
  </BaseDialog>
</template>
