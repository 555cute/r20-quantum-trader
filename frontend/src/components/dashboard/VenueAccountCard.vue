<script setup lang="ts">
/**
 * VenueAccountCard.vue · DeepSeek Harness 开发者工作台交易所账户微卡
 * 低饱和黑白/深灰主题、分层卡片、清晰状态指示与等宽数值
 */
import { computed } from 'vue';
import { AlertTriangle, CheckCircle2, Info, PlugZap } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { fmtNum } from '../../utils/format';
import { venueColor } from '../../utils/venueMeta';
import { listingMeta, type ListingVenueStatus } from '../../utils/listingMeta';
import type { VenueAccount, VenueKey } from '../../stores/venueAccounts';

const props = defineProps<{
  venue: VenueKey;
  account: VenueAccount | null;
  listing?: ListingVenueStatus | null;
  loading?: boolean;
}>();

const { t } = useI18n();

const venueName = computed(() => t(`dash.venueAccounts.venueNames.${props.venue}`));

const statusMeta = computed(() => {
  const s = props.account?.status;
  const reason = props.account?.reason || '';
  if (!s) return { icon: Info, statusDot: 'warn', label: t('dash.venueAccounts.status.unknown') };
  switch (s) {
    case 'ready': return { icon: CheckCircle2, statusDot: 'active', label: t('dash.venueAccounts.status.ready') };
    case 'unavailable':
      if (reason.includes('跨档') || reason.includes('不符')) {
        return { icon: PlugZap, statusDot: 'warn', label: t('dash.venueAccounts.status.mismatch') };
      }
      if (reason.includes('凭证无效') || reason.includes('无效') || reason.includes('过期')) {
        return { icon: PlugZap, statusDot: 'error', label: t('dash.venueAccounts.status.invalidKey') };
      }
      return { icon: PlugZap, statusDot: 'error', label: t('dash.venueAccounts.status.unavailable') };
    case 'degraded': return { icon: AlertTriangle, statusDot: 'warn', label: t('dash.venueAccounts.status.degraded') };
    default: return { icon: Info, statusDot: 'warn', label: t('dash.venueAccounts.status.not_implemented') };
  }
});

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? t('dash.venueAccounts.unknown') : fmtNum(Number(v), 2);
}
function count(v: number | null | undefined, unit = true): string {
  if (v === null || v === undefined) return t('dash.venueAccounts.unknown');
  return unit ? t('dash.venueAccounts.fields.unitN', undefined, { n: v }) : String(v);
}

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
    class="dsh-card-sub flex min-w-0 flex-col gap-2.5 p-3 transition-colors hover:border-[var(--line-2)]"
    data-test="venue-card"
    :data-venue="venue"
  >
    <!-- 卡片头部：场所标识与连接状态 -->
    <div class="flex items-center justify-between gap-2 border-b pb-2" style="border-color: var(--line-1)">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="h-2 w-2 rounded-full" :style="{ backgroundColor: venueColor(venue) }" />
        <span class="truncate font-semibold text-xs text-[var(--ink-strong)]">{{ venueName }}</span>
      </div>

      <div
        class="dsh-pill"
        :title="account?.reason || ''"
      >
        <span class="dsh-status-dot" :class="statusMeta.statusDot" aria-hidden="true" />
        <span>{{ loading && !account ? t('dash.venueAccounts.loading') : statusMeta.label }}</span>
      </div>
    </div>

    <!-- 资产与仓位数据格 -->
    <dl class="grid grid-cols-2 gap-2 text-2xs">
      <div class="min-w-0 rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]" style="background-color: var(--surface-1); border: 1px solid var(--line-1)">
        <dt class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.fields.equity') }}</dt>
        <dd class="num truncate font-mono text-xs font-bold text-[var(--ink-strong)]" data-test="cell-equity">
          {{ money(account?.equity) }}
        </dd>
      </div>

      <div class="min-w-0 rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]" style="background-color: var(--surface-1); border: 1px solid var(--line-1)">
        <dt class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.fields.available') }}</dt>
        <dd class="num truncate font-mono text-xs font-semibold text-[var(--ink-1)]" data-test="cell-available">
          {{ money(account?.available) }}
        </dd>
      </div>

      <div class="min-w-0 rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]" style="background-color: var(--surface-1); border: 1px solid var(--line-1)">
        <dt class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.fields.positions') }}</dt>
        <dd class="num truncate font-mono text-xs font-semibold text-[var(--ink-1)]" data-test="cell-positions">
          {{ count(account?.positions_count) }}
        </dd>
      </div>

      <div class="min-w-0 rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]" style="background-color: var(--surface-1); border: 1px solid var(--line-1)">
        <dt class="truncate text-3xs text-[var(--ink-3)]">{{ t('dash.venueAccounts.fields.openOrders') }}</dt>
        <dd class="num truncate font-mono text-xs font-semibold text-[var(--ink-1)]" data-test="cell-orders">
          {{ count(account?.open_orders_count) }}
        </dd>
      </div>
    </dl>

    <!-- 批 73：场所**为什么**不可用此前只挂在上方胶囊的 :title 上 ——
         键盘与触摸够不到，而"未接入 / 降级"这句话本身不说明任何问题。
         与同卡片 listing 的既有语汇一致：非就绪态把原因直接显示出来。 -->
    <p
      v-if="account?.reason && account.status !== 'ready'"
      class="rounded px-1.5 py-1 text-3xs leading-snug"
      :class="account.status === 'degraded' ? 'text-[var(--warn)]' : 'text-[var(--ink-2)]'"
      data-test="venue-reason"
    >
      {{ account.reason }}
    </p>

    <!-- 合约对账徽标与底栏说明 -->
    <div
      class="mt-auto flex min-w-0 items-center justify-between gap-2 border-t pt-1.5 text-3xs"
      style="border-color: var(--line-1)"
      data-test="cell-listing"
    >
      <dt class="text-[var(--ink-3)] shrink-0">{{ t('dash.venueAccounts.listing.label') }}</dt>
      <dd class="min-w-0 truncate text-right font-mono" :title="listingTitle">
        <span v-if="listingMeta_.tone === 'ok'" class="text-[var(--up)]">
          ● {{ listingLabel }}
        </span>
        <span v-else-if="listingMeta_.tone === 'warn'" class="text-[var(--warn)]">
          ▲ {{ listingLabel }}
        </span>
        <span v-else class="text-[var(--ink-3)]">{{ listingLabel }}</span>
      </dd>
    </div>
  </div>
</template>
