<script setup lang="ts">
/**
 * FirstRunGuide.vue · 陌生人的第一公里（2026-09）
 *
 * ## 它解决什么
 *
 * 一个陌生人把仓库拉下来跑起来，看到的是**一屏空数据**：熔断器没数据、持仓 0、
 * 因子空。`DataStatus` 只在顶栏给一个"未配置"小圆点 —— **它报状态，但不指路**。
 * 用户不知道是网络问题、没配 Key、还是程序坏了，于是就走不到"接上自己的 OKX"这一步。
 *
 * 本组件在**读到账户数据失败时**（`data_health.status` 为 `NOT_READY` / `OFFLINE`）
 * 顶到主工作区最上方，把四步走清楚，并把注册通道一并放在手边（第 1 步就要用）。
 * 一旦就绪（`LIVE`/`STALE`）自动消失，不占常驻位置。
 *
 * ## 为什么"注册通道"就放在这里
 *
 * 这套程序**免费开放**，作者的收入来自交易所返佣：用户经通道开户后，其交易产生的
 * 手续费里交易所分一部分给作者。程序不向用户收任何费用。放在这里是因为**这一步
 * 本来就是新用户的必经之路**（没有账户就没有 API Key），顺手且不打扰；就绪后即消失。
 *
 * ⚠️ 与订单上的经纪商 `tag` 是两件事：`tag` 随每笔订单发出、负责把成交归属到作者
 * （与用户是否点过这里的链接**无关**）；本处的链接只是给用户**开户**用的入口。
 */
import { computed, onMounted } from 'vue';
import { KeyRound, BookOpen, Github, ExternalLink, RefreshCw } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { useReferralChannels } from '../../composables/useReferralChannels';
import { OFFICIAL_REPO } from '../../config/version';
import CopyButton from '../base/CopyButton.vue';

const store = useDashboardStore();
const { t, tm } = useI18n();
const { channels, load: loadChannels } = useReferralChannels();

onMounted(loadChannels);

const status = computed(() =>
  String((store.data as any)?.data_health?.status || '').toUpperCase());

/** 只在"读不到账户数据"时出现；陈旧（STALE）说明早就配好了，不该再教一遍。 */
const visible = computed(() => status.value === 'NOT_READY' || status.value === 'OFFLINE');

const steps = computed(() => tm('dash.firstRun.steps'));
</script>

<template>
  <section
    v-if="visible"
    class="card-flat mb-3 border p-3.5"
    style="border-color: var(--warn-line); background-color: var(--warn-bg)"
    aria-labelledby="first-run-title"
  >
    <header class="mb-2 flex flex-wrap items-center gap-2">
      <KeyRound class="h-4 w-4 shrink-0" style="color: var(--warn)" aria-hidden="true" />
      <h2 id="first-run-title" class="section-title !mb-0 !text-sm" style="color: var(--ink-strong)">
        {{ t('dash.firstRun.title') }}
      </h2>
      <span class="dsh-pill text-3xs" style="color: var(--warn); border-color: var(--warn-line)">
        {{ t('dash.firstRun.badge') }}
      </span>
    </header>

    <p class="mb-3 text-xs leading-body" style="color: var(--ink-2)">{{ t('dash.firstRun.lead') }}</p>

    <ol class="mb-3 space-y-1.5">
      <li
        v-for="(step, i) in steps"
        :key="i"
        class="flex gap-2 text-xs leading-body"
        style="color: var(--ink-2)"
      >
        <span class="num shrink-0 font-semibold" style="color: var(--warn)">{{ Number(i) + 1 }}.</span>
        <span>{{ step }}</span>
      </li>
    </ol>

    <!-- 第 1 步就要用到的开户入口：注册通道（后端出值，见 useReferralChannels） -->
    <div v-if="channels.length" class="mb-3">
      <p class="t-label mb-1.5">{{ t('dash.firstRun.channelsTitle') }}</p>
      <div class="flex flex-wrap gap-2">
        <div
          v-for="ch in channels"
          :key="ch.key"
          class="card-flat flex items-center gap-2 px-2.5 py-1.5"
        >
          <span class="text-xs font-semibold" style="color: var(--ink-1)">{{ ch.name }}</span>
          <span v-if="ch.code" class="num text-3xs" style="color: var(--ink-3)">{{ ch.code }}</span>
          <a
            :href="ch.invite_url"
            target="_blank"
            rel="noopener noreferrer"
            class="btn btn-quiet btn-sm"
            :title="t('dash.firstRun.openChannel')"
            :aria-label="`${ch.name} · ${t('dash.firstRun.openChannel')}`"
          >
            <ExternalLink class="h-3.5 w-3.5" aria-hidden="true" />
            <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
          </a>
          <CopyButton :text="ch.invite_url" />
        </div>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <a href="/admin/security" class="btn btn-primary btn-sm">
        <KeyRound class="h-3.5 w-3.5" aria-hidden="true" />
        {{ t('dash.firstRun.ctaAccount') }}
      </a>
      <a href="/docs" class="btn btn-quiet btn-sm">
        <BookOpen class="h-3.5 w-3.5" aria-hidden="true" />
        {{ t('dash.firstRun.ctaDocs') }}
      </a>
      <a
        :href="OFFICIAL_REPO"
        target="_blank"
        rel="noopener noreferrer"
        class="btn btn-quiet btn-sm"
      >
        <Github class="h-3.5 w-3.5" aria-hidden="true" />
        {{ t('dash.firstRun.ctaRepo') }}
        <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
      </a>
      <button type="button" class="btn btn-quiet btn-sm" @click="store.fetchDashboard?.()">
        <RefreshCw class="h-3.5 w-3.5" aria-hidden="true" />
        {{ t('dash.firstRun.ctaRefresh') }}
      </button>
    </div>

    <p class="mt-2 text-3xs leading-body" style="color: var(--ink-3)">
      {{ t('dash.firstRun.disclosure') }}
    </p>
  </section>
</template>
