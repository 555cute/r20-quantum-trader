<script setup lang="ts">
/** 舆情情报：左·币种多空温度带，右·快讯流（AI 消息面输入白盒） */
import { computed } from 'vue';
import { ExternalLink } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum } from '../../utils/format';
import PageHead from '../../components/dashboard/PageHead.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import TimeAgo from '../../components/base/TimeAgo.vue';
import CryptoLogo from '../../components/dashboard/CryptoLogo.vue';

const store = useDashboardStore();
const { t } = useI18n();

const ni = computed<any>(() => (store.data as any)?.news_intelligence || {});
const macro = computed(() => ni.value.macro_sentiment || '');
const news = computed<any[]>(() => ni.value.latest_news || []);

const coins = computed(() => {
  const cs = ni.value.coins_sentiment || {};
  return Object.entries(cs)
    .map(([sym, v]: [string, any]) => ({
      sym,
      label: v.label,
      bull: Math.max(0, Math.min(100, parseFloat(v.bullish_ratio) || 0)),
      bear: Math.max(0, Math.min(100, parseFloat(v.bearish_ratio) || 0)),
      ls: v.long_short_ratio,
      mentions: v.mentions,
    }))
    .sort((a, b) => (b.mentions || 0) - (a.mentions || 0));
});

function labelCls(l: string): string {
  return l === 'bullish' ? 'up' : l === 'bearish' ? 'down' : '';
}
function labelTxt(l: string): string {
  return l === 'bullish' ? t('dash.news.band.bull') : l === 'bearish' ? t('dash.news.band.bear') : t('dash.news.band.neutral');
}
function impCls(i: string): string {
  return i === 'high' ? 'badge-down' : i === 'mid' ? 'badge-warn' : '';
}
function impTxt(i: string): string {
  return i === 'high' ? t('dash.news.feed.impacts.high') : i === 'mid' ? t('dash.news.feed.impacts.mid') : t('dash.news.feed.impacts.low');
}
</script>

<template>
  <div class="space-y-3">
    <PageHead :title="t('dash.news.title')" :desc="t('dash.news.desc')" />

    <div class="grid grid-cols-1 gap-3 xl:grid-cols-12">
      <!-- 温度带 -->
      <div class="card overflow-hidden xl:col-span-4">
        <div class="flex items-center justify-between border-b px-3.5 py-2.5" style="border-color: var(--line-1)">
          <div>
            <h2 class="text-sm font-semibold" style="color: var(--ink-strong)">{{ t('dash.news.band.title') }}</h2>
            <p class="t-faint text-xs">{{ t('dash.news.band.desc') }}</p>
          </div>
          <span v-if="macro" class="badge badge-accent">{{ macro }}</span>
        </div>
        <BaseEmpty v-if="!coins.length" :text="t('dash.news.feed.empty')" />
        <div v-else class="divide-y-0 space-y-0 p-1.5">
          <div v-for="c in coins" :key="c.sym" class="flex items-center gap-3 rounded-lg px-2.5 py-2">
            <span class="flex w-16 shrink-0 items-center gap-1.5">
              <CryptoLogo :symbol="c.sym" :size="16" />
              <span class="num text-xs font-semibold">{{ c.sym }}</span>
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex h-2 overflow-hidden rounded-full" style="background-color: var(--surface-1)">
                <div :style="{ width: c.bull + '%', backgroundColor: 'var(--up)' }" />
                <div :style="{ width: (100 - c.bull - c.bear) + '%', backgroundColor: 'var(--line-2)' }" />
                <div :style="{ width: c.bear + '%', backgroundColor: 'var(--down)' }" />
              </div>
              <div class="num mt-1 flex justify-between text-2xs" style="color: var(--ink-3)">
                <span class="up">{{ fmtNum(c.bull, 0) }}%</span>
                <span>{{ c.mentions ?? 0 }}</span>
                <span class="down">{{ fmtNum(c.bear, 0) }}%</span>
              </div>
            </div>
            <span class="w-10 shrink-0 text-right text-xs font-semibold" :class="labelCls(c.label)">{{ labelTxt(c.label) }}</span>
          </div>
        </div>
      </div>

      <!-- 快讯流 -->
      <div class="card overflow-hidden xl:col-span-8">
        <div class="flex items-center justify-between border-b px-3.5 py-2.5" style="border-color: var(--line-1)">
          <h2 class="text-sm font-semibold" style="color: var(--ink-strong)">{{ t('dash.news.feed.title') }}</h2>
          <span class="t-faint text-xs">{{ news.length }} {{ t('common.unitCount') }}</span>
        </div>
        <BaseEmpty v-if="!news.length" :text="t('dash.news.feed.empty')" />
        <div v-else class="max-h-[640px] divide-y overflow-y-auto" style="--tw-divide-y-reverse:0">
          <a
            v-for="item in news"
            :key="item.id"
            :href="item.url || '#'"
            target="_blank"
            rel="noopener noreferrer"
            class="group block px-3.5 py-3 transition-colors hover:bg-[var(--surface-3)]"
            style="border-color: var(--line-1)"
          >
            <div class="flex items-center gap-2">
              <span class="badge" :class="impCls(item.importance)">{{ impTxt(item.importance) }}</span>
              <span v-for="cc in (item.coins || []).slice(0, 3)" :key="cc" class="badge badge-mono">{{ cc }}</span>
              <span class="t-faint ms-auto text-xs">{{ item.time?.slice(11, 16) || '' }} · <TimeAgo :time="item.time" /></span>
            </div>
            <p class="mt-1.5 text-sm font-medium leading-snug group-hover:text-[var(--accent)]" style="color: var(--ink-1)">
              {{ item.title }}
              <ExternalLink class="ms-1 inline h-3 w-3 opacity-40" />
            </p>
            <p v-if="item.summary" class="mt-1 line-clamp-2 text-xs leading-relaxed" style="color: var(--ink-2)">
              {{ item.summary }}
            </p>
          </a>
        </div>
      </div>
    </div>
  </div>
</template>
