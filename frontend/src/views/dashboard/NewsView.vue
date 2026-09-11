<script setup lang="ts">
/**
 * US-008 · 舆情快讯与重大黑天鹅情报看板：
 * 1. 置顶突发情报/黑天鹅熔断预警高亮卡 (Warning Glow)
 * 2. 币种情绪极性矩阵 (Coin Sentiment Polarity Matrix) 与点击联动筛选 (Click-to-filter)
 * 3. 多源公开快讯抓取 (CoinDesk / Cointelegraph / Binance CMS) 与新鲜度刷新指示
 */
import { computed, ref } from 'vue';
import {
  ExternalLink,
  ShieldAlert,
  ShieldCheck,
  RefreshCw,
  Radio,
  Filter,
  X,
  Flame,
  TrendingUp,
  TrendingDown,
} from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtHM } from '../../utils/format';
import PageHead from '../../components/dashboard/PageHead.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import TimeAgo from '../../components/base/TimeAgo.vue';
import CryptoLogo from '../../components/dashboard/CryptoLogo.vue';

const store = useDashboardStore();
const { t } = useI18n();

// 选中的币种过滤状态（null 为不过滤展示全部）
const selectedCoin = ref<string | null>(null);
// 选中的来源过滤状态（all 为全部，支持 '加密快讯' | 'OKX官方' | '金十数据' | '全球宏观'）
const selectedSource = ref<string>('all');

const ni = computed<any>(() => (store.data as any)?.news_intelligence || {});
const macro = computed(() => ni.value.macro_sentiment || '偏多震荡');
const rawNews = computed<any[]>(() => ni.value.latest_news || []);
const freshAt = computed(() => ni.value.news_fresh_at || ni.value.timestamp || '');
const sourceReason = computed(() => ni.value.source_reason || '加密货币快讯 + 金十数据宏观快讯');
const isSourceActive = computed(() => ni.value.source_available !== false);

// 黑天鹅熔断状态
const circuitBreaker = computed<any>(() => {
  const cb = ni.value.circuit_breaker;
  if (cb && typeof cb === 'object') return cb;
  return { active: false };
});
const isCbActive = computed(() => Boolean(circuitBreaker.value?.active));

// 币种情绪极性矩阵数据
const coins = computed(() => {
  const cs = ni.value.coins_sentiment || {};
  return Object.entries(cs)
    .map(([sym, v]: [string, any]) => {
      const bull = Math.max(0, Math.min(100, parseFloat(v.bullish_ratio) || 0));
      const bear = Math.max(0, Math.min(100, parseFloat(v.bearish_ratio) || 0));
      const score = typeof v.sentiment_factor_score === 'number' ? v.sentiment_factor_score : 0;
      return {
        sym,
        label: v.label || 'neutral',
        bull,
        bear,
        score,
        ls: v.long_short_ratio,
        mentions: v.mentions || 0,
      };
    })
    .sort((a, b) => (b.mentions || 0) - (a.mentions || 0));
});

// 置顶快讯（首条高危或重要快讯）
const pinnedNews = computed(() => {
  if (!rawNews.value.length) return null;
  // 优先取 importance === 'high' 的第一条，否则取最新第一条
  const high = rawNews.value.find((n) => n.importance === 'high');
  return high || rawNews.value[0];
});

// 按选中币种与来源过滤后的快讯流
const filteredNews = computed(() => {
  let list = rawNews.value;
  if (selectedSource.value !== 'all') {
    list = list.filter((item) => (item.platforms || []).some((p: string) => p.includes(selectedSource.value)));
  }
  if (!selectedCoin.value) return list;
  const target = selectedCoin.value.toUpperCase();
  return list.filter((item) => {
    const coinList = (item.coins || []).map((c: string) => String(c).toUpperCase());
    if (coinList.includes(target)) return true;
    const title = String(item.title || '').toUpperCase();
    const summary = String(item.summary || '').toUpperCase();
    return title.includes(target) || summary.includes(target);
  });
});

function toggleCoinFilter(sym: string) {
  if (selectedCoin.value === sym) {
    selectedCoin.value = null;
  } else {
    selectedCoin.value = sym;
  }
}

// 来源筛选按钮激活态：「加密快讯」用专属翡翠绿高亮，与金十红形成区隔
function sourceBtnStyle(key: string): Record<string, string> {
  if (selectedSource.value !== key) return { color: 'var(--ink-3)' };
  if (key === '加密快讯') {
    return { backgroundColor: '#10b98128', color: '#10b981', fontWeight: 'bold' };
  }
  return { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)', fontWeight: 'bold' };
}

function labelCls(l: string): string {
  return l === 'bullish' ? 'up' : l === 'bearish' ? 'down' : '';
}
function labelTxt(l: string): string {
  return l === 'bullish' ? '偏多' : l === 'bearish' ? '偏空' : '震荡';
}
function impCls(i: string): string {
  return i === 'high' ? 'badge-down' : i === 'mid' ? 'badge-warn' : 'badge-mono';
}
function impTxt(i: string): string {
  return i === 'high' ? '重大' : i === 'mid' ? '关注' : '快讯';
}

async function refreshNews() {
  await store.fetchDashboard(false);
}
</script>

<template>
  <div class="space-y-3.5">
    <!-- 头部与数据源新鲜度状态条 -->
    <div class="flex flex-wrap items-center justify-between gap-2.5">
      <PageHead :title="t('dash.news.title')" :desc="t('dash.news.desc')" />

      <!-- 数据源与刷新指示器 (Auto-refresh & Freshness Indicators) -->
      <div class="flex items-center gap-2 text-2xs" style="color: var(--ink-3)">
        <div class="flex items-center gap-1.5 rounded-full px-2.5 py-1 border" style="background-color: var(--surface-1); border-color: var(--line-1)">
          <span class="relative flex h-2 w-2">
            <span v-if="isSourceActive" class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background-color: var(--up)"></span>
            <span class="relative inline-flex rounded-full h-2 w-2" :style="{ backgroundColor: isSourceActive ? 'var(--up)' : 'var(--warn)' }"></span>
          </span>
          <span class="font-medium" style="color: var(--ink-2)">{{ sourceReason }}</span>
          <span class="mx-1 text-[10px] opacity-30">|</span>
          <span>新鲜度: <b class="num font-semibold" style="color: var(--ink-1)">{{ freshAt || '刚刚' }}</b></span>
        </div>

        <button
          class="btn btn-quiet btn-icon btn-sm"
          :disabled="store.isRefreshing"
          title="立即刷新舆情数据"
          @click="refreshNews"
        >
          <RefreshCw class="h-3.5 w-3.5" :class="store.isRefreshing && 'animate-spin'" />
        </button>
      </div>
    </div>

    <!-- 1. 置顶突发重大情报 / 黑天鹅预警卡片 (Warning Glow Highlight Card) -->
    <div
      class="card relative overflow-hidden p-3.5 transition-all duration-300 border"
      :style="isCbActive
        ? {
            borderColor: 'var(--down)',
            backgroundColor: 'var(--surface-1)',
            boxShadow: '0 0 20px rgba(239, 68, 68, 0.35)',
          }
        : {
            borderColor: 'var(--line-1)',
            backgroundColor: 'var(--surface-1)',
            boxShadow: '0 0 16px rgba(56, 128, 255, 0.08)',
          }"
    >
      <!-- 黑天鹅熔断触发态 -->
      <div v-if="isCbActive" class="flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
        <div class="flex items-start gap-3">
          <div class="p-2 rounded-lg" style="background-color: rgba(239, 68, 68, 0.15)">
            <ShieldAlert class="h-6 w-6" style="color: var(--down)" />
          </div>
          <div>
            <div class="flex items-center gap-2">
              <span class="badge badge-down font-bold text-xs">🚨 黑天鹅熔断机制已激活</span>
              <span class="text-2xs num t-faint">{{ circuitBreaker.triggered_at }}</span>
            </div>
            <h3 class="text-sm font-bold mt-1" style="color: var(--ink-strong)">
              {{ circuitBreaker.headline || '检测到极端市场不可抗力冲击' }}
            </h3>
            <p class="text-xs mt-0.5 t-faint">
              触发高危识别词：<b class="down">{{ circuitBreaker.keyword || '突发恶性异动' }}</b> ·
              防御动作：<span class="font-medium" style="color: var(--ink-2)">{{ circuitBreaker.action || '暂停新开仓 30 分钟，启动存量持仓保本防御' }}</span>
            </p>
          </div>
        </div>
        <div class="shrink-0 flex items-center gap-2">
          <span class="badge font-semibold" style="color: var(--down); border-color: var(--down-line); background-color: var(--down-bg)">
            开仓通道已硬冻结
          </span>
        </div>
      </div>

      <!-- 常态置顶突发情报态 (Breaking Macro Intelligence) -->
      <div v-else-if="pinnedNews" class="flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
        <div class="flex items-start gap-3 min-w-0">
          <div class="p-2 rounded-lg shrink-0" style="background-color: rgba(56, 128, 255, 0.1)">
            <Radio class="h-5 w-5" style="color: var(--accent)" />
          </div>
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="badge badge-accent font-bold text-2xs">🔥 置顶突发 BREAKING</span>
              <span class="badge badge-mono text-2xs" v-for="p in (pinnedNews.platforms || []).slice(0, 2)" :key="p">{{ p }}</span>
              <span class="t-faint text-2xs num">{{ fmtHM(pinnedNews.time) }} · <TimeAgo :time="pinnedNews.time" /></span>
              <span class="badge badge-accent text-2xs">宏观：{{ macro }}</span>
            </div>
            <a
              :href="pinnedNews.url || '#'"
              target="_blank"
              rel="noopener noreferrer"
              class="group block mt-1 text-xs md:text-sm font-bold truncate leading-snug hover:text-[var(--accent)] transition-colors"
              style="color: var(--ink-strong)"
            >
              {{ pinnedNews.title }}
              <ExternalLink class="ms-1 inline h-3 w-3 opacity-40 group-hover:opacity-100" />
            </a>
            <p v-if="pinnedNews.summary" class="text-2xs t-faint line-clamp-1 mt-0.5">
              {{ pinnedNews.summary }}
            </p>
          </div>
        </div>
        <div class="shrink-0 flex items-center gap-2">
          <div class="flex items-center gap-1.5 text-2xs font-semibold px-2 py-1 rounded border" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up)">
            <ShieldCheck class="h-3.5 w-3.5" />
            黑天鹅哨兵 7×24H 防御中
          </div>
        </div>
      </div>
    </div>

    <!-- 2. 主体分栏：左·币种情绪极性矩阵，右·快讯流 -->
    <div class="grid grid-cols-1 gap-3.5 xl:grid-cols-12">
      <!-- 左：币种情绪极性矩阵 (Coin Sentiment Polarity Matrix) -->
      <div class="card overflow-hidden xl:col-span-4 flex flex-col">
        <div class="flex items-center justify-between border-b px-3.5 py-2.5" style="border-color: var(--line-1)">
          <div>
            <h2 class="text-sm font-bold flex items-center gap-1.5" style="color: var(--ink-strong)">
              <Flame class="h-4 w-4" style="color: var(--accent)" />
              {{ t('dash.news.band.title') }}
            </h2>
            <p class="t-faint text-2xs">点击币种可联动筛选快讯流</p>
          </div>
          <div v-if="selectedCoin" class="flex items-center gap-1 text-2xs">
            <span class="badge badge-accent font-bold">{{ selectedCoin }}</span>
            <button class="btn btn-ghost btn-icon btn-sm" title="清除筛选" @click="selectedCoin = null">
              <X class="h-3 w-3" />
            </button>
          </div>
        </div>

        <BaseEmpty v-if="!coins.length" :text="t('dash.news.feed.empty')" />
        <div v-else class="divide-y-0 space-y-1 p-2 flex-1 overflow-y-auto max-h-[600px]">
          <div
            v-for="c in coins"
            :key="c.sym"
            class="flex items-center gap-3 rounded-lg px-2.5 py-2 cursor-pointer transition-all border"
            :style="selectedCoin === c.sym
              ? {
                  borderColor: 'var(--accent)',
                  backgroundColor: 'var(--surface-3)',
                }
              : {
                  borderColor: 'transparent',
                  backgroundColor: 'var(--surface-2)',
                }"
            :title="`点击筛选 ${c.sym} 快讯`"
            @click="toggleCoinFilter(c.sym)"
          >
            <!-- 币种标识 -->
            <span class="flex w-14 shrink-0 items-center gap-1.5">
              <CryptoLogo :symbol="c.sym" :size="18" />
              <span class="num text-xs font-bold" style="color: var(--ink-strong)">{{ c.sym }}</span>
            </span>

            <!-- 双极性多空能量槽 (Polarity Energy Bar) -->
            <div class="min-w-0 flex-1">
              <div class="flex h-2 overflow-hidden rounded-full" style="background-color: var(--surface-1)">
                <div :style="{ width: c.bull + '%', backgroundColor: 'var(--up)' }" :title="`多头占比: ${c.bull}%`" />
                <div :style="{ width: (100 - c.bull - c.bear) + '%', backgroundColor: 'var(--line-2)' }" />
                <div :style="{ width: c.bear + '%', backgroundColor: 'var(--down)' }" :title="`空头占比: ${c.bear}%`" />
              </div>
              <div class="num mt-1 flex justify-between text-2xs" style="color: var(--ink-3)">
                <span class="up font-semibold flex items-center gap-0.5">
                  <TrendingUp class="h-2.5 w-2.5" /> {{ fmtNum(c.bull, 0) }}%
                </span>
                <span class="text-3xs" style="color: var(--ink-faint)">{{ c.mentions ?? 0 }} 篇</span>
                <span class="down font-semibold flex items-center gap-0.5">
                  {{ fmtNum(c.bear, 0) }}% <TrendingDown class="h-2.5 w-2.5" />
                </span>
              </div>
            </div>

            <!-- 极性状态与因子得分 -->
            <div class="w-14 shrink-0 text-right">
              <span class="block text-xs font-bold" :class="labelCls(c.label)">{{ labelTxt(c.label) }}</span>
              <span class="block num text-3xs font-medium" :class="c.score >= 0 ? 'up' : 'down'">
                {{ c.score > 0 ? '+' : '' }}{{ fmtNum(c.score, 2) }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右：快讯流 (Intelligence Feed) -->
      <div class="card overflow-hidden xl:col-span-8 flex flex-col">
        <div class="flex items-center justify-between border-b px-3.5 py-2.5 flex-wrap gap-2" style="border-color: var(--line-1)">
          <div class="flex items-center gap-2 flex-wrap">
            <h2 class="text-sm font-bold" style="color: var(--ink-strong)">{{ t('dash.news.feed.title') }}</h2>
            <!-- 来源分类筛选按钮 -->
            <div class="flex items-center gap-0.5 p-0.5 rounded-md text-2xs" style="background-color: var(--surface-2); border: 1px solid var(--line-1);">
              <button
                v-for="s in [
                  { key: 'all', label: '全部' },
                  { key: '加密快讯', label: '加密快讯' },
                  { key: '金十数据', label: '金十数据' },
                  { key: '全球宏观', label: '宏观快讯' },
                  { key: 'OKX官方', label: 'OKX风控' },
                ]"
                :key="s.key"
                class="px-2 py-0.5 rounded transition-colors"
                :style="sourceBtnStyle(s.key)"
                @click="selectedSource = s.key"
              >
                {{ s.label }}
              </button>
            </div>
            <span v-if="selectedCoin" class="badge badge-accent text-2xs flex items-center gap-1">
              <Filter class="h-2.5 w-2.5" /> {{ selectedCoin }} 过滤
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="t-faint text-2xs">共 {{ filteredNews.length }} 条快讯</span>
            <button
              v-if="selectedCoin || selectedSource !== 'all'"
              class="btn btn-ghost btn-sm text-2xs"
              @click="selectedCoin = null; selectedSource = 'all'"
            >
              重置筛选
            </button>
          </div>
        </div>

        <BaseEmpty v-if="!filteredNews.length" :text="selectedCoin ? `暂无与 ${selectedCoin} 相关的快讯` : t('dash.news.feed.empty')" />
        <div v-else class="flex-1 max-h-[640px] divide-y overflow-y-auto" style="--tw-divide-y-reverse:0">
          <a
            v-for="item in filteredNews"
            :key="item.id"
            :href="item.url || '#'"
            target="_blank"
            rel="noopener noreferrer"
            class="group block px-3.5 py-3 transition-colors hover:bg-[var(--surface-3)]"
            style="border-color: var(--line-1)"
          >
            <!-- 顶栏：影响度 + 来源平台 + 币种标签 + 时间 -->
            <div class="flex items-center gap-2 flex-wrap">
              <span class="badge font-bold" :class="impCls(item.importance)">{{ impTxt(item.importance) }}</span>
              <span
                v-for="plat in (item.platforms || [])"
                :key="plat"
                class="badge text-3xs font-bold"
                :style="plat === '加密快讯'
                  ? { backgroundColor: '#10b98118', borderColor: '#10b98138', color: '#10b981' }
                  : plat === 'OKX官方'
                  ? { backgroundColor: '#3880ff15', borderColor: '#3880ff33', color: '#3880ff' }
                  : plat === '金十数据'
                  ? { backgroundColor: '#e0242415', borderColor: '#e0242433', color: '#e02424' }
                  : { backgroundColor: 'var(--surface-3)', borderColor: 'var(--line-1)', color: 'var(--ink-2)' }"
              >
                {{ plat }}
              </span>
              <span
                v-for="cc in (item.coins || []).slice(0, 3)"
                :key="cc"
                class="badge badge-mono text-3xs"
                :class="selectedCoin === cc ? 'badge-accent' : ''"
              >
                {{ cc }}
              </span>
              <span class="t-faint ms-auto text-2xs num">{{ fmtHM(item.time) }} · <TimeAgo :time="item.time" /></span>
            </div>

            <!-- 标题 -->
            <p class="mt-1.5 text-xs md:text-sm font-medium leading-snug group-hover:text-[var(--accent)] transition-colors" style="color: var(--ink-strong)">
              {{ item.title }}
              <ExternalLink class="ms-1 inline h-3 w-3 opacity-30 group-hover:opacity-100" />
            </p>

            <!-- 摘要正文 -->
            <p v-if="item.summary" class="mt-1 line-clamp-2 text-2xs leading-relaxed" style="color: var(--ink-2)">
              {{ item.summary }}
            </p>
          </a>
        </div>
      </div>
    </div>
  </div>
</template>
