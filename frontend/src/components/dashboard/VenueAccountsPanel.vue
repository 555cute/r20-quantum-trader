<script setup lang="ts">
/**
 * US-005 · 账户区「环境优先」面板：先选实盘/模拟，再看三所同构卡。
 * 铁律：两环境数据绝不加总（后端无合计字段，前端也不求和展示）；
 * 未知显「—」+文字状态徽章（颜色不作唯一识别）；身份必须有文字。
 */
import { computed, onMounted } from 'vue';
import { FlaskConical, RefreshCw, ShieldCheck } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, utcStrToBj } from '../../utils/format';
import { numOrNull, isPlainObj } from '../../utils/venueMeta';
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

/** US-004 · 组合风险占用行（/api/all portfolio_risk，US-001 预留层口径）。
 *  铁律同账户卡：null/缺字段 = 未知显「—」，绝不以 0 冒充；两环境绝不加总。 */
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

/** 环境切换：账户与合约目录两条数据轴同步换挡（旧数据各自清空，防串显）。 */
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
      <div class="min-w-0">
        <h2 class="text-sm font-bold" style="color: var(--ink-strong)">{{ t('dash.venueAccounts.title') }}</h2>
        <p class="t-faint text-xs">{{ t('dash.venueAccounts.desc') }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <!-- 环境优先切换：文字标签 + 图标，颜色仅作辅助 -->
        <div
          class="flex items-center gap-1 rounded-lg p-1"
          style="background: var(--surface-3); border: 1px solid var(--line-1)"
          role="group"
          :aria-label="t('dash.venueAccounts.envLabel')"
          data-test="env-switch"
        >
          <button
            class="btn btn-sm" :class="isDemo ? 'btn-filled' : 'btn-ghost'"
            :aria-pressed="isDemo" data-test="env-demo" @click="switchEnvironment('demo')"
          >
            <FlaskConical class="h-3.5 w-3.5" />
            {{ t('dash.venueAccounts.envDemo') }}
          </button>
          <button
            class="btn btn-sm" :class="!isDemo ? 'btn-filled' : 'btn-ghost'"
            :aria-pressed="!isDemo" data-test="env-live" @click="switchEnvironment('live')"
          >
            <ShieldCheck class="h-3.5 w-3.5" />
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

    <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
      <VenueAccountCard
        v-for="v in VENUES"
        :key="`${store.environment}-${v}`"
        :venue="v"
        :account="store.venues?.[v] ?? null"
        :listing="listing.venues?.[v] ?? null"
        :loading="store.loading || listing.loading"
      />
    </div>

    <!-- US-004 · 组合风险占用行：总预算 / 已预留 / 可用余量（未知≠0；随环境隔离） -->
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
