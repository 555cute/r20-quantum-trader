<script setup lang="ts">
/** 三所同构账户卡：同一组件三实例；未知值渲染「—」+文字徽章，颜色不作唯一识别。 */
import { computed } from 'vue';
import { AlertTriangle, CheckCircle2, Info, PlugZap } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { fmtNum } from '../../utils/format';
import { listingMeta, type ListingVenueStatus } from '../../utils/listingMeta';
import TimeAgo from '../base/TimeAgo.vue';
import type { VenueAccount, VenueKey } from '../../stores/venueAccounts';

const props = defineProps<{
  venue: VenueKey;
  account: VenueAccount | null;
  listing?: ListingVenueStatus | null;
  loading?: boolean;
}>();

const { t } = useI18n();

const venueName = computed(() => t(`dash.venueAccounts.venueNames.${props.venue}`));

const brandColor = computed(() => {
  const map: Record<VenueKey, string> = {
    okx: 'var(--venue-okx, #3880ff)',
    binance: 'var(--venue-binance, #f3ba2f)',
    gate: 'var(--venue-gate, #00be98)',
  };
  return map[props.venue];
});

const statusMeta = computed(() => {
  const s = props.account?.status;
  if (!s) return { icon: Info, cls: 'dot-warn', label: t('dash.venueAccounts.status.unknown') };
  switch (s) {
    case 'ready': return { icon: CheckCircle2, cls: 'dot-live', label: t('dash.venueAccounts.status.ready') };
    case 'unavailable': return { icon: PlugZap, cls: 'dot-down', label: t('dash.venueAccounts.status.unavailable') };
    case 'degraded': return { icon: AlertTriangle, cls: 'dot-warn', label: t('dash.venueAccounts.status.degraded') };
    default: return { icon: Info, cls: 'dot-warn', label: t('dash.venueAccounts.status.not_implemented') };
  }
});

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? t('dash.venueAccounts.unknown') : fmtNum(Number(v), 2);
}
function count(v: number | null | undefined, unit = true): string {
  if (v === null || v === undefined) return t('dash.venueAccounts.unknown');
  return unit ? t('dash.venueAccounts.fields.unitN', undefined, { n: v }) : String(v);
}

/** US-007 · 合约目录对账徽章：fail-open（目录不可用）只提示不吓人，未知显「—」。 */
const listingMeta_ = computed(() => listingMeta(props.listing));
const listingLabel = computed(() => {
  const m = listingMeta_.value;
  if (m.tone === 'ok') return t('dash.venueAccounts.listing.ok', undefined, { n: m.listedCount ?? 0 });
  if (m.tone === 'warn') return m.reason || t('dash.venueAccounts.listing.unavailable');
  return t('dash.venueAccounts.unknown');
});
const listingTitle = computed(() => {
  const m = listingMeta_.value;
  const parts: string[] = [];
  if (m.reason) parts.push(m.reason);
  if (props.listing?.checked_at) parts.push(`${t('dash.venueAccounts.captured')}: ${props.listing.checked_at}`);
  if (props.listing?.source && props.listing.source !== 'unavailable') parts.push(`source: ${props.listing.source}`);
  return parts.join(' · ');
});
</script>

<template>
  <div
    class="card card-pad flex min-w-0 flex-col gap-2.5 transition-all duration-200 hover:border-[var(--line-2)]"
    data-test="venue-card"
    :data-venue="venue"
  >
    <!-- 卡片头部：品牌名 + 品牌点 + 状态 -->
    <div class="flex flex-wrap items-center justify-between gap-2 border-b pb-2" style="border-color: var(--line-1)">
      <div class="flex items-center gap-1.5">
        <span class="h-2 w-2 rounded-full" :style="{ backgroundColor: brandColor }" />
        <span class="t-label font-bold truncate text-xs sm:text-sm" :style="{ color: brandColor }">
          {{ venueName }}
        </span>
      </div>
      <span class="chip" :title="account?.reason || ''">
        <span class="dot" :class="statusMeta.cls" />
        <component :is="statusMeta.icon" class="h-3 w-3 shrink-0 opacity-80" />
        {{ loading && !account ? t('dash.venueAccounts.loading') : statusMeta.label }}
      </span>
    </div>

    <!-- 关键指标四格 -->
    <dl class="grid grid-cols-2 gap-x-3 gap-y-2 py-0.5">
      <div class="min-w-0">
        <dt class="t-faint truncate text-2xs">{{ t('dash.venueAccounts.fields.equity') }}</dt>
        <dd class="num truncate text-sm sm:text-base font-bold" style="color: var(--ink-strong)" data-test="cell-equity">
          {{ money(account?.equity) }}
        </dd>
      </div>
      <div class="min-w-0">
        <dt class="t-faint truncate text-2xs">{{ t('dash.venueAccounts.fields.available') }}</dt>
        <dd class="num truncate text-sm sm:text-base font-semibold" style="color: var(--ink-1)" data-test="cell-available">
          {{ money(account?.available) }}
        </dd>
      </div>
      <div class="min-w-0">
        <dt class="t-faint truncate text-2xs">{{ t('dash.venueAccounts.fields.positions') }}</dt>
        <dd class="num truncate text-xs sm:text-sm font-semibold" style="color: var(--ink-1)" data-test="cell-positions">
          {{ count(account?.positions_count) }}
        </dd>
      </div>
      <div class="min-w-0">
        <dt class="t-faint truncate text-2xs">{{ t('dash.venueAccounts.fields.openOrders') }}</dt>
        <dd class="num truncate text-xs sm:text-sm font-semibold" style="color: var(--ink-1)" data-test="cell-orders">
          {{ count(account?.open_orders_count) }}
        </dd>
      </div>
    </dl>

    <!-- 合约目录对账状态 -->
    <div class="flex min-w-0 items-center justify-between gap-2 border-t pt-2" style="border-color: var(--line-1)" data-test="cell-listing">
      <dt class="t-faint shrink-0 text-2xs">{{ t('dash.venueAccounts.listing.label') }}</dt>
      <dd class="min-w-0 truncate text-right" :title="listingTitle">
        <span v-if="listingMeta_.tone === 'ok'" class="badge" style="color: var(--up)">
          <span class="dot dot-live" />{{ listingLabel }}
        </span>
        <span v-else-if="listingMeta_.tone === 'warn'" class="badge" style="color: var(--warn)">
          <span class="dot dot-warn" />{{ listingLabel }}
        </span>
        <span v-else class="t-faint">{{ listingLabel }}</span>
      </dd>
    </div>

    <!-- 底部状态说明与心跳时间 -->
    <div class="mt-auto flex min-w-0 items-center justify-between gap-2 border-t pt-1.5 text-2xs" style="border-color: var(--line-1)">
      <span class="t-faint min-w-0 truncate" :title="account?.reason || ''">{{ account?.reason || '' }}</span>
      <span v-if="account?.last_sync_ts" class="t-faint shrink-0">
        <TimeAgo :time="account.last_sync_ts" />
      </span>
    </div>
  </div>
</template>
