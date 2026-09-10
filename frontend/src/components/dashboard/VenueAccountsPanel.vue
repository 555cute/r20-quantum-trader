<script setup lang="ts">
/**
 * US-005 三所账户对称卡：环境优先（实盘/模拟）→ OKX/Binance/Gate 同构卡。
 * 契约：数据源 GET /api/v1/venue_accounts（后端 fail-closed）；
 * 未知值渲染「—」+ 文字徽章（颜色不作唯一识别）；两环境永不加总，本组件无任何合计字段。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { RefreshCw, CircleCheck, CircleHelp, CircleDashed, ShieldAlert } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { useEnvironmentStore, type VenueEnvironment } from '../../stores/environment';
import { fmtNum } from '../../utils/format';
import BaseSegmented from '../base/BaseSegmented.vue';
import TimeAgo from '../base/TimeAgo.vue';

type VenueStatus = 'ready' | 'unavailable' | 'not_implemented' | 'degraded' | 'error';

interface VenueCard {
  status: VenueStatus;
  equity: number | null;
  available: number | null;
  positions_count: number | null;
  open_orders_count: number | null;
  last_sync_ts: number | null;
  reason: string;
}

const VENUES = ['okx', 'binance', 'gate'] as const;
type Venue = (typeof VENUES)[number];
const VENUE_LABEL: Record<Venue, string> = { okx: 'OKX', binance: 'Binance', gate: 'Gate' };

const { t } = useI18n();
const envStore = useEnvironmentStore();

const cards = ref<Record<Venue, VenueCard | null>>({ okx: null, binance: null, gate: null });
const loading = ref(false);
const fetchError = ref<string | null>(null);
const capturedAt = ref<number | null>(null);

const envOptions = computed(() => [
  {
    value: 'demo' as VenueEnvironment,
    label: t('dash.matrix.venueAccounts.envDemo', '模拟盘'),
    title: t('dash.matrix.venueAccounts.envDemoTip', '交易所模拟资金环境（Demo/Testnet）'),
  },
  {
    value: 'live' as VenueEnvironment,
    label: t('dash.matrix.venueAccounts.envLive', '实盘'),
    title: t('dash.matrix.venueAccounts.envLiveTip', '真实资金环境——数据与模拟盘完全隔离'),
  },
]);

async function load() {
  loading.value = true;
  fetchError.value = null;
  try {
    const r = await fetch(`/api/v1/venue_accounts?environment=${envStore.environment}`);
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const body = await r.json();
        if (body?.detail) detail = String(body.detail);
      } catch {
        /* 非 JSON 错误体保留状态码 */
      }
      fetchError.value = detail;
      for (const v of VENUES) cards.value[v] = null;
      capturedAt.value = null;
    } else {
      const d = await r.json();
      for (const v of VENUES) cards.value[v] = (d.venues?.[v] ?? null) as VenueCard | null;
      capturedAt.value = typeof d.captured_at_ms === 'number' ? d.captured_at_ms : null;
    }
  } catch (e) {
    fetchError.value = e instanceof Error ? e.message : String(e);
    for (const v of VENUES) cards.value[v] = null;
    capturedAt.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => envStore.environment, load);

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : fmtNum(v, 2);
}
function count(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : String(v);
}

interface StatusMeta {
  icon: typeof CircleCheck;
  text: string;
  tone: string;
}
function statusMeta(card: VenueCard | null): StatusMeta {
  const base = (s: VenueStatus): StatusMeta => {
    switch (s) {
      case 'ready':
        return { icon: CircleCheck, text: t('dash.matrix.venueAccounts.stReady', '已同步'), tone: 'var(--up)' };
      case 'degraded':
        return { icon: CircleDashed, text: t('dash.matrix.venueAccounts.stDegraded', '部分未知'), tone: 'var(--warn)' };
      case 'unavailable':
        return { icon: CircleHelp, text: t('dash.matrix.venueAccounts.stUnavailable', '不可用'), tone: 'var(--ink-2)' };
      case 'not_implemented':
        return { icon: CircleDashed, text: t('dash.matrix.venueAccounts.stNotImpl', '未实装'), tone: 'var(--ink-2)' };
      default:
        return { icon: ShieldAlert, text: t('dash.matrix.venueAccounts.stError', '读取失败'), tone: 'var(--warn)' };
    }
  };
  return base(fetchError.value && !card ? 'error' : (card?.status ?? 'error'));
}

const syncedCount = computed(() => VENUES.filter((v) => cards.value[v]?.status === 'ready').length);
const showPartialNote = computed(() => VENUES.some((v) => cards.value[v]?.status !== 'ready'));
</script>

<template>
  <section class="card" data-test="venue-accounts-panel" aria-label="三所账户">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2" style="border-color: var(--line-1)">
      <div class="flex items-center gap-2">
        <h2 class="t-label m-0">{{ t('dash.matrix.venueAccounts.title', '三所账户 · 环境隔离') }}</h2>
        <span v-if="showPartialNote" class="text-[11px]" style="color: var(--warn)" data-test="partial-note">
          {{ t('dash.matrix.venueAccounts.partialNote', '部分场所未同步：各所独立展示，不提供跨所/跨环境合计') }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <BaseSegmented
          :model-value="envStore.environment"
          :options="envOptions"
          data-test="env-switch"
          @update:model-value="envStore.setEnvironment($event as VenueEnvironment)"
        />
        <button
          type="button"
          class="btn-ghost inline-flex items-center gap-1 px-2 py-1 text-xs"
          :disabled="loading"
          :title="t('dash.matrix.venueAccounts.refresh', '刷新')"
          data-test="venue-refresh"
          @click="load"
        >
          <RefreshCw :size="13" :class="loading && 'animate-spin'" />
          <span>{{ syncedCount }}/{{ VENUES.length }}</span>
        </button>
      </div>
    </div>

    <p v-if="fetchError" class="px-3 pt-2 text-xs" style="color: var(--down)" data-test="venue-fetch-error">
      {{ fetchError }}
    </p>

    <div class="grid grid-cols-1 gap-2 p-2.5 md:grid-cols-3 xl:gap-2.5">
      <div
        v-for="venue in VENUES"
        :key="venue"
        class="rounded-lg border p-3"
        style="border-color: var(--line-1)"
        :data-test="`venue-card-${venue}`"
      >
        <div class="mb-1.5 flex items-center justify-between gap-2">
          <span class="text-sm font-semibold" :data-test="`venue-name-${venue}`">{{ VENUE_LABEL[venue] }}</span>
          <span
            class="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[11px]"
            :style="{ color: statusMeta(cards[venue]).tone, borderColor: 'var(--line-1)' }"
            :data-test="`venue-status-${venue}`"
          >
            <component :is="statusMeta(cards[venue]).icon" :size="12" aria-hidden="true" />
            {{ statusMeta(cards[venue]).text }}
          </span>
        </div>
        <dl class="m-0 grid grid-cols-2 gap-x-2 gap-y-1 text-xs">
          <dt class="truncate" style="color: var(--ink-2)">{{ t('dash.matrix.venueAccounts.equity', '总权益') }}</dt>
          <dd class="num m-0 text-right" data-test="venue-equity">{{ money(cards[venue]?.equity) }}</dd>
          <dt class="truncate" style="color: var(--ink-2)">{{ t('dash.matrix.venueAccounts.available', '可用保证金') }}</dt>
          <dd class="num m-0 text-right" data-test="venue-available">{{ money(cards[venue]?.available) }}</dd>
          <dt class="truncate" style="color: var(--ink-2)">{{ t('dash.matrix.venueAccounts.positions', '持仓数') }}</dt>
          <dd class="num m-0 text-right" data-test="venue-positions">{{ count(cards[venue]?.positions_count) }}</dd>
          <dt class="truncate" style="color: var(--ink-2)">{{ t('dash.matrix.venueAccounts.openOrders', '挂单数') }}</dt>
          <dd class="num m-0 text-right" data-test="venue-orders">{{ count(cards[venue]?.open_orders_count) }}</dd>
        </dl>
        <div class="mt-1.5 flex items-center justify-between gap-2 text-[11px]" style="color: var(--ink-2)">
          <span v-if="cards[venue]?.last_sync_ts" class="inline-flex items-center gap-1">
            <TimeAgo :time="cards[venue]!.last_sync_ts!" />
          </span>
          <span v-else>—</span>
        </div>
        <p
          v-if="cards[venue] && cards[venue]!.status !== 'ready' && cards[venue]!.reason"
          class="mt-1 break-words text-[11px]"
          style="color: var(--ink-2)"
          :data-test="`venue-reason-${venue}`"
        >
          {{ cards[venue]!.reason }}
        </p>
      </div>
    </div>

    <p class="px-3 pb-2 text-[11px]" style="color: var(--ink-2)" data-test="env-isolation-note">
      {{ t('dash.matrix.venueAccounts.envTip', '实盘与模拟数据永久隔离、永不加总；读不到的值显示「—」，不以 0 冒充。') }}
    </p>
  </section>
</template>
