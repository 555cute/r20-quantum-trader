<script setup lang="ts">
/**
 * VenueAccountsPanel.vue · DeepSeek Harness 三所账户与组合风控面板
 * 低饱和黑白/深灰主题、分层卡片结构、环境切换与多所风险占用透视
 */
import { computed, onMounted, ref } from 'vue';
import { FlaskConical, RefreshCw, ShieldCheck, Activity } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, utcStrToBj } from '../../utils/format';
import { numOrNull, isPlainObj, venueColor } from '../../utils/venueMeta';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore, type VenueKey } from '../../stores/venueAccounts';
import { useListingStatusStore } from '../../stores/listingStatus';
import VenueAccountCard from './VenueAccountCard.vue';

const store = useVenueAccountsStore();
const listing = useListingStatusStore();
const dash = useDashboardStore();
const { t } = useI18n();

const VENUES: VenueKey[] = ['okx', 'gate', 'binance'];
const isDemo = computed(() => store.environment === 'demo');
const isMobileExpanded = ref(false);

/** 跨所健康与延迟监控 */
const crossVenue = computed(() => {
  const cv = (dash.data as any)?.cross_venue;
  return isPlainObj(cv) ? cv : null;
});
const venueLatencies = computed(() => {
  const v = crossVenue.value?.venues || {};
  return {
    okx: v.okx?.avg_ms ?? (v.okx?.ok?.length ? '<50' : null),
    gate: v.gate?.avg_ms ?? null,
    binance: v.binance?.avg_ms ?? null,
  };
});

/** 组合风险占用行 */
const portfolio = computed(() => {
  const raw = (dash.data as any)?.portfolio_risk;
  return isPlainObj(raw) ? raw : null;
});
const pTotal = computed(() => (portfolio.value ? numOrNull(portfolio.value.total_budget_usdt) : null));
const pUncapped = computed(() => String(portfolio.value?.budget_mode || '') === 'uncapped');
const pReferenceCap = computed(() => (portfolio.value ? numOrNull(portfolio.value.reference_cap_usdt) : null));
const pReserved = computed(() => (portfolio.value ? numOrNull(portfolio.value.reserved_usdt) : null));
const pAvailable = computed(() => {
  if (!portfolio.value) return null;
  const given = numOrNull(portfolio.value.available_usdt);
  if (given !== null) return given;
  if (pTotal.value !== null && pReserved.value !== null) return pTotal.value - pReserved.value;
  return null;
});
const pUsage = computed(() =>
  pTotal.value !== null && pTotal.value > 0 && pReserved.value !== null
    ? Math.min(100, Math.max(0, Math.round((pReserved.value / pTotal.value) * 100)))
    : null,
);
const pUsageTone = computed(() =>
  pUsage.value === null ? 'var(--ink-3)' : pUsage.value >= 100 ? 'var(--down)' : pUsage.value >= 80 ? 'var(--warn)' : 'var(--up)',
);
const pEnvMismatch = computed(() => {
  const env = String(portfolio.value?.environment || '').trim().toLowerCase();
  return !!env && !env.includes(store.environment);
});

function pMoney(v: number | null | undefined): string {
  return v === null || v === undefined ? t('dash.venueAccounts.unknown') : fmtNum(v, 2);
}

onMounted(() => {
  void store.refresh();
  void listing.refresh(store.environment);
});

function switchEnvironment(env: 'demo' | 'live'): void {
  store.setEnvironment(env);
  void listing.refresh(env);
}

function refreshAll(): void {
  void store.refresh();
  void listing.refresh();
}
</script>

<template>
  <section class="dsh-card" data-test="venue-accounts-panel">
    <!-- 面板头部 -->
    <header class="dsh-card-header">
      <div class="flex flex-wrap items-center gap-2 sm:gap-3">
        <h2 class="text-xs font-bold uppercase tracking-wider text-[var(--ink-strong)] flex items-center gap-1.5">
          <Activity class="h-3.5 w-3.5 text-[var(--accent)]" />
          {{ t('dash.venueAccounts.title') }}
        </h2>

        <!-- 网关延迟徽标群 -->
        <div
          v-if="crossVenue"
          class="hidden sm:flex items-center gap-2 text-3xs font-mono rounded px-2 py-0.5"
          style="background-color: var(--surface-2); border: 1px solid var(--line-1)"
        >
          <span class="inline-flex items-center gap-1">
            <span class="h-1.5 w-1.5 rounded-full bg-[var(--up)]" />
            <span style="color: var(--ink-2)">OKX</span>
            <span style="color: var(--ink-1)">{{ venueLatencies.okx ? `${venueLatencies.okx}ms` : t('dash.venueAccounts.ready') }}</span>
          </span>
          <span class="inline-flex items-center gap-1">
            <span class="h-1.5 w-1.5 rounded-full" :class="venueLatencies.binance ? 'bg-[var(--up)]' : 'bg-[var(--warn)]'" />
            <span style="color: var(--ink-2)">BN</span>
            <span style="color: var(--ink-1)">{{ venueLatencies.binance ? `${venueLatencies.binance}ms` : '--' }}</span>
          </span>
          <span class="inline-flex items-center gap-1">
            <span class="h-1.5 w-1.5 rounded-full" :class="venueLatencies.gate ? 'bg-[var(--up)]' : 'bg-[var(--warn)]'" />
            <span style="color: var(--ink-2)">Gate</span>
            <span style="color: var(--ink-1)">{{ venueLatencies.gate ? `${venueLatencies.gate}ms` : '--' }}</span>
          </span>
        </div>
      </div>

      <!-- 控制区：环境换挡与刷新 -->
      <div class="flex items-center gap-2">
        <div
          class="flex items-center gap-0.5 rounded p-0.5"
          style="background-color: var(--surface-2); border: 1px solid var(--line-1)"
          role="group"
          :aria-label="t('dash.venueAccounts.envLabel')"
          data-test="env-switch"
        >
          <button type="button"
            class="px-2 py-1 rounded text-3xs font-medium cursor-pointer transition-colors flex items-center gap-1"
            :class="
              isDemo
                ? 'bg-[var(--surface-3)] text-[var(--ink-strong)] font-semibold'
                : 'text-[var(--ink-3)] hover:bg-[var(--ds-color-bg-hover)] hover:text-[var(--ink-1)]'
            "
            :aria-pressed="isDemo"
            data-test="env-demo"
            @click="switchEnvironment('demo')"
          >
            <FlaskConical class="h-3 w-3" :style="{ color: isDemo ? 'var(--warn)' : 'currentColor' }" />
            {{ t('dash.venueAccounts.envDemo') }}
          </button>
          <button type="button"
            class="px-2 py-1 rounded text-3xs font-medium cursor-pointer transition-colors flex items-center gap-1"
            :class="
              !isDemo
                ? 'bg-[var(--surface-3)] text-[var(--ink-strong)] font-semibold'
                : 'text-[var(--ink-3)] hover:bg-[var(--ds-color-bg-hover)] hover:text-[var(--ink-1)]'
            "
            :aria-pressed="!isDemo"
            data-test="env-live"
            @click="switchEnvironment('live')"
          >
            <ShieldCheck class="h-3 w-3" :style="{ color: !isDemo ? 'var(--up)' : 'currentColor' }" />
            {{ t('dash.venueAccounts.envLive') }}
          </button>
        </div>

        <button type="button"
          class="btn btn-ghost btn-icon h-7 w-7"
          :title="t('dash.venueAccounts.refresh')"
          data-test="venue-refresh"
          :disabled="store.loading || listing.loading"
          @click="refreshAll()"
        >
          <RefreshCw class="h-3.5 w-3.5 shrink-0" :class="store.loading && 'animate-spin'" />
        </button>
      </div>
    </header>

    <!-- 主体：三所同构卡片与风控行 -->
    <div class="p-3 space-y-3">
      <div v-if="store.needsAuth" class="dsh-pill" data-test="needs-auth">
        <span class="dsh-status-dot warn" aria-hidden="true" />
        {{ t('dash.venueAccounts.needsAuth') }}
      </div>

      <!-- 移动端紧凑三所资产条 -->
      <div class="block md:hidden rounded p-2" style="background-color: var(--surface-2); border: 1px solid var(--line-1)">
        <div class="flex items-center justify-between text-2xs mb-1.5">
          <span class="font-bold text-[var(--ink-1)]">{{ t('dash.venueAccounts.mobileSummary') }}</span>
          <button type="button"
            class="text-3xs font-medium px-2 py-1 rounded cursor-pointer transition-colors bg-[var(--surface-3)] text-[var(--ink-2)] hover:bg-[var(--ds-color-bg-hover)] hover:text-[var(--ink-1)]"
            :aria-expanded="isMobileExpanded"
            :aria-controls="'venue-accounts-grid'"
            @click="isMobileExpanded = !isMobileExpanded"
          >
            {{ isMobileExpanded ? t('dash.venueAccounts.collapseCards') : t('dash.venueAccounts.expandCards') }}
          </button>
        </div>
        <div class="grid grid-cols-3 gap-1.5 text-center">
          <div class="p-1.5 rounded" style="background-color: var(--surface-1)">
            <span :style="{ color: venueColor('okx') }" class="block text-3xs font-bold">OKX</span>
            <span class="num font-bold text-xs text-[var(--ink-strong)]">{{ pMoney(store.venues?.okx?.equity) }}</span>
          </div>
          <div class="p-1.5 rounded" style="background-color: var(--surface-1)">
            <span :style="{ color: venueColor('binance') }" class="block text-3xs font-bold">Binance</span>
            <span class="num font-bold text-xs text-[var(--ink-strong)]">{{ pMoney(store.venues?.binance?.equity) }}</span>
          </div>
          <div class="p-1.5 rounded" style="background-color: var(--surface-1)">
            <span :style="{ color: venueColor('gate') }" class="block text-3xs font-bold">Gate</span>
            <span class="num font-bold text-xs text-[var(--ink-strong)]">{{ pMoney(store.venues?.gate?.equity) }}</span>
          </div>
        </div>
      </div>

      <!-- 三所卡片网格 -->
      <div id="venue-accounts-grid" :class="['gap-2.5 md:grid md:grid-cols-3', isMobileExpanded ? 'grid grid-cols-1' : 'hidden md:grid']">
        <VenueAccountCard
          v-for="v in VENUES"
          :key="`${store.environment}-${v}`"
          :venue="v"
          :account="store.venues?.[v] ?? null"
          :listing="listing.venues?.[v] ?? null"
          :loading="store.loading || listing.loading"
        />
      </div>

      <!-- 组合风险占用行 -->
      <div
        class="rounded p-2.5"
        style="background-color: var(--surface-2); border: 1px solid var(--line-1)"
        data-test="portfolio-risk"
        :title="t('dash.venueAccounts.portfolio.tip')"
      >
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-3xs font-semibold uppercase tracking-wider text-[var(--ink-2)]">
            {{ t('dash.venueAccounts.portfolio.label') }}
          </p>
          <span
            v-if="pEnvMismatch"
            class="rounded px-1.5 py-0.5 border text-3xs font-medium text-[var(--warn)] border-[var(--warn)]"
            data-test="portfolio-env-mismatch"
          >
            {{ t('dash.venueAccounts.portfolio.envMismatch') }}
          </span>
          <span v-if="portfolio?.updated_utc" class="num ms-auto text-3xs font-mono text-[var(--ink-3)]">
            {{ utcStrToBj(String(portfolio.updated_utc), true) }} {{ t('dash.venueAccounts.beijing') }}
          </span>
        </div>

        <div v-if="portfolio" class="mt-3 grid grid-cols-3 text-left">
          <div class="min-w-0 pe-4">
            <p class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.portfolio.total') }}</p>
            <p class="num font-mono text-md font-bold text-[var(--ink-strong)]" data-test="portfolio-total">{{ pMoney(pTotal) }}</p>
            <p v-if="pUncapped" class="text-3xs leading-snug text-[var(--ink-3)]" data-test="portfolio-uncapped">
              {{ t('dash.venueAccounts.portfolio.uncapped') }}
              <span v-if="pReferenceCap !== null" class="num">· {{ t('dash.venueAccounts.portfolio.uncappedRef', undefined, { cap: fmtNum(pReferenceCap, 0) }) }}</span>
            </p>
          </div>
          <div class="min-w-0 border-s border-[var(--line-2)] px-4">
            <p class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.portfolio.reserved') }}</p>
            <p class="num font-mono text-md font-bold text-[var(--ink-strong)]" data-test="portfolio-reserved">{{ pMoney(pReserved) }}</p>
          </div>
          <div class="min-w-0 border-s border-[var(--line-2)] ps-4">
            <p class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.portfolio.available') }}</p>
            <p class="num font-mono text-md font-bold text-[var(--ink-strong)]" data-test="portfolio-available">{{ pMoney(pAvailable) }}</p>
          </div>
        </div>

        <div v-if="pUsage !== null" class="mt-2">
          <div
            class="h-1 w-full overflow-hidden rounded-full"
            style="background-color: var(--surface-3)"
            role="progressbar"
            :aria-valuenow="Math.round(pUsage)"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-label="t('dash.venueAccounts.portfolio.usage', undefined, { pct: pUsage })"
            :aria-valuetext="`${pUsage}%`"
          >
            <div class="h-full rounded-full transition-all duration-300" :style="{ width: `${pUsage}%`, backgroundColor: pUsageTone }" />
          </div>
          <p class="num mt-1 font-mono text-3xs" :style="{ color: pUsageTone }">
            {{ t('dash.venueAccounts.portfolio.usage', undefined, { pct: pUsage }) }}
          </p>
        </div>

        <p v-if="!portfolio" class="mt-1.5 text-3xs text-[var(--ink-3)]" data-test="portfolio-pending">
          {{ t('dash.venueAccounts.portfolio.pending') }}
        </p>
      </div>

      <!-- 底部元数据说明 -->
      <div class="flex items-center justify-between text-3xs text-[var(--ink-3)]">
        <div class="flex items-center gap-2">
          <span>{{ isDemo ? t('dash.venueAccounts.envDemo') : t('dash.venueAccounts.envLive') }}</span>
          <span v-if="store.capturedAt">· {{ t('dash.venueAccounts.captured') }} {{ new Date(store.capturedAt).toLocaleTimeString() }}</span>
        </div>
        <span v-if="store.error && !store.needsAuth" role="status" aria-live="polite" data-test="fetch-error" class="text-[var(--warn)]">
          {{ store.error }}
        </span>
      </div>
    </div>
  </section>
</template>
