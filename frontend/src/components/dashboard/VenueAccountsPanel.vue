<script setup lang="ts">
/** 三所账户面板：环境切换 + 账户卡 + 组合风险占用。 */
import { computed, onMounted, ref } from 'vue';
import { FlaskConical, RefreshCw, ShieldCheck } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, utcStrToBj } from '../../utils/format';
import { numOrNull, isPlainObj, VENUE_KEYS, venueLabel } from '../../utils/venueMeta';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore, type VenueKey } from '../../stores/venueAccounts';
import { useListingStatusStore } from '../../stores/listingStatus';
import VenueAccountCard from './VenueAccountCard.vue';

const store = useVenueAccountsStore();
const listing = useListingStatusStore();
const dash = useDashboardStore();
const { t } = useI18n();

const VENUES = VENUE_KEYS;
const isDemo = computed(() => store.environment === 'demo');
const isMobileExpanded = ref(false);

/** 组合风险占用行。 */
const portfolio = computed(() => {
  const raw = (dash.data as any)?.portfolio_risk;
  return isPlainObj(raw) ? raw : null;
});
const pTotal = computed(() => (portfolio.value ? numOrNull(portfolio.value.total_budget_usdt) : null));
const pReserved = computed(() => (portfolio.value ? numOrNull(portfolio.value.reserved_usdt) : null));
const pAvailable = computed(() => {
  if (!portfolio.value) return null;
  const given = numOrNull(portfolio.value.available_usdt);
  if (given !== null) return given;
  // 后端漏给余量但预算/占用俱在 → 诚实差值推导；否则保持 null 显「—」
  if (pTotal.value !== null && pReserved.value !== null) return pTotal.value - pReserved.value;
  return null;
});
/** 占用率仅在总预算与已预留均为真实数值时派生（未知不画进度条） */
const pUsage = computed(() =>
  pTotal.value !== null && pTotal.value > 0 && pReserved.value !== null
    ? Math.min(100, Math.max(0, Math.round((pReserved.value / pTotal.value) * 100)))
    : null,
);
const pUsageTone = computed(() =>
  pUsage.value === null ? 'var(--ink-3)' : pUsage.value >= 100 ? 'var(--down)' : pUsage.value >= 80 ? 'var(--warn)' : 'var(--up)',
);
/** 环境比对：后端标了环境且与当前所选明显不一致 → 警示（缺 environment 字段则不猜） */
const pEnvMismatch = computed(() => {
  const env = String(portfolio.value?.environment || '').trim().toLowerCase();
  return !!env && !env.includes(store.environment);
});
function pMoney(v: number | null): string {
  return v === null ? t('dash.venueAccounts.unknown') : fmtNum(v, 2);
}

onMounted(() => {
  void store.refresh();
  void listing.refresh(store.environment);
});

/** 环境切换：账户与合约目录两条数据轴同步换挡。 */
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
  <section class="card card-pad space-y-3" data-test="venue-accounts-panel">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-sm font-bold" style="color: var(--ink-strong)">{{ t('dash.venueAccounts.title') }}</h2>
      <div class="flex flex-wrap items-center gap-2">
        <!-- 环境切换药丸 -->
        <div
          class="flex items-center gap-0.5 rounded-md p-0.5"
          style="background: var(--surface-2); border: 1px solid var(--line-1)"
          role="group"
          :aria-label="t('dash.venueAccounts.envLabel')"
          data-test="env-switch"
        >
          <button
            class="px-2.5 py-1 rounded text-2xs transition-all duration-200 flex items-center gap-1.5"
            :style="isDemo ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)', fontWeight: 'bold', boxShadow: '0 1px 2px rgba(0,0,0,0.15)' } : { color: 'var(--ink-3)' }"
            :aria-pressed="isDemo" data-test="env-demo" @click="switchEnvironment('demo')"
          >
            <FlaskConical class="h-3 w-3" :style="{ color: isDemo ? 'var(--warn)' : 'currentColor' }" />
            {{ t('dash.venueAccounts.envDemo') }}
          </button>
          <button
            class="px-2.5 py-1 rounded text-2xs transition-all duration-200 flex items-center gap-1.5"
            :style="!isDemo ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)', fontWeight: 'bold', boxShadow: '0 1px 2px rgba(0,0,0,0.15)' } : { color: 'var(--ink-3)' }"
            :aria-pressed="!isDemo" data-test="env-live" @click="switchEnvironment('live')"
          >
            <ShieldCheck class="h-3 w-3" :style="{ color: !isDemo ? 'var(--up)' : 'currentColor' }" />
            {{ t('dash.venueAccounts.envLive') }}
          </button>
        </div>
        <button
          class="btn btn-ghost btn-icon btn-sm" :title="t('dash.venueAccounts.refresh')"
          data-test="venue-refresh" :disabled="store.loading || listing.loading" @click="refreshAll()"
        >
          <RefreshCw :class="store.loading && 'animate-spin'" />
        </button>
      </div>
    </div>

    <div v-if="store.needsAuth" class="chip w-fit" data-test="needs-auth">
      <span class="dot dot-warn" />
      {{ t('dash.venueAccounts.needsAuth') }}
    </div>

    <!-- 移动端紧凑三所资产条 (桌面端隐藏，手机端大幅降低竖向屏高) -->
    <div class="block md:hidden rounded-lg p-2 border" style="background: var(--surface-2); border-color: var(--line-1)">
      <div class="flex items-center justify-between text-2xs mb-1.5">
        <span class="font-bold" style="color: var(--ink-1)">三所资产一览</span>
        <button
          class="text-3xs font-medium px-2 py-0.5 rounded cursor-pointer transition-colors"
          style="background: var(--surface-3); color: var(--ink-2)"
          @click="isMobileExpanded = !isMobileExpanded"
        >
          {{ isMobileExpanded ? '收起明细 ▲' : '展开三所卡片 ▼' }}
        </button>
      </div>
      <div class="grid grid-cols-3 gap-1.5 text-center">
        <div
          v-for="v in VENUES"
          :key="v"
          class="p-1.5 rounded"
          style="background: var(--surface-1)"
        >
          <span
            class="block text-3xs font-bold"
            :style="{ color: `var(--venue-${v})` }"
          >
            {{ venueLabel(v) }}
          </span>
          <span class="num font-bold text-xs" style="color: var(--ink-strong)">{{ pMoney(store.venues?.[v]?.equity) }}</span>
        </div>
      </div>
    </div>

    <!-- 三所同构大卡片 (移动端折叠控制，桌面端 md:grid 平铺) -->
    <div :class="['gap-3 md:grid md:grid-cols-3', isMobileExpanded ? 'grid grid-cols-1' : 'hidden md:grid']">
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
    <div class="border-t pt-2.5" style="border-color: var(--line-1)" data-test="portfolio-risk" :title="t('dash.venueAccounts.portfolio.tip')">
      <div class="flex flex-wrap items-center gap-2">
        <p class="t-label shrink-0">{{ t('dash.venueAccounts.portfolio.label') }}</p>
        <span v-if="pEnvMismatch" class="badge" style="color: var(--warn); border-color: currentColor" data-test="portfolio-env-mismatch">
          {{ t('dash.venueAccounts.portfolio.envMismatch') }}
        </span>
        <span v-if="portfolio?.updated_utc" class="num ml-auto text-[10px]" style="color: var(--ink-3)">
          {{ utcStrToBj(String(portfolio.updated_utc), true) }} 北京
        </span>
      </div>
      <div v-if="portfolio" class="mt-2 grid grid-cols-3 gap-2 text-center">
        <div class="min-w-0">
          <p class="t-faint truncate">{{ t('dash.venueAccounts.portfolio.total') }}</p>
          <p class="num text-sm font-bold" style="color: var(--ink-strong)" data-test="portfolio-total">{{ pMoney(pTotal) }}</p>
        </div>
        <div class="min-w-0">
          <p class="t-faint truncate">{{ t('dash.venueAccounts.portfolio.reserved') }}</p>
          <p class="num text-sm font-semibold" style="color: var(--ink-1)" data-test="portfolio-reserved">{{ pMoney(pReserved) }}</p>
        </div>
        <div class="min-w-0">
          <p class="t-faint truncate">{{ t('dash.venueAccounts.portfolio.available') }}</p>
          <p class="num text-sm font-semibold" style="color: var(--ink-1)" data-test="portfolio-available">{{ pMoney(pAvailable) }}</p>
        </div>
      </div>
      <div v-if="pUsage !== null" class="mt-1.5">
        <div class="h-1 w-full overflow-hidden rounded-full" style="background: var(--surface-3)">
          <div class="h-full rounded-full" :style="{ width: `${pUsage}%`, background: pUsageTone }" />
        </div>
        <p class="num mt-1 text-[10px]" :style="{ color: pUsageTone }">{{ t('dash.venueAccounts.portfolio.usage', undefined, { pct: pUsage }) }}</p>
      </div>
      <p v-if="!portfolio" class="t-faint mt-1.5 text-xs" data-test="portfolio-pending">
        {{ t('dash.venueAccounts.portfolio.pending') }}
      </p>
    </div>

    <p class="t-faint flex items-center gap-2 text-[11px]">
      <span>{{ isDemo ? t('dash.venueAccounts.envDemo') : t('dash.venueAccounts.envLive') }}</span>
      <span v-if="store.capturedAt">· {{ t('dash.venueAccounts.captured') }} {{ new Date(store.capturedAt).toLocaleString() }}</span>
      <span v-if="store.error && !store.needsAuth" data-test="fetch-error" style="color: var(--warn)">· {{ store.error }}</span>
    </p>
  </section>
</template>
