<script setup lang="ts">
/**
 * 顶栏三所连接中枢胶囊（Tri-Venue Connectivity Capsule）
 * 消除 OKX 单所特权，以对等三柱布局呈现 OKX / Binance / Gate 实时心跳、延迟与账户就绪态。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { RefreshCw, Server, ShieldCheck, Zap } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore, type VenueKey } from '../../stores/venueAccounts';
import { VENUE_KEYS, venueLabel, venueShortLabel } from '../../utils/venueMeta';

const { t } = useI18n();
const dashStore = useDashboardStore();
const venueStore = useVenueAccountsStore();

const open = ref(false);
const triggerRef = ref<HTMLElement | null>(null);
const panelRef = ref<HTMLElement | null>(null);
const isRefreshing = ref(false);

interface VenueStatusItem {
  key: VenueKey;
  label: string;
  short: string;
  brandColor: string;
  dotClass: string;
  statusText: string;
  latencyText: string;
  latencyMs: number | null;
  okCount: number;
  failCount: number;
  isTestnet: boolean;
  isReady: boolean;
}

const venueItems = computed<VenueStatusItem[]>(() => {
  const healthList = dashStore.crossVenueHealth || [];
  return VENUE_KEYS.map((key) => {
    const health = healthList.find((h) => h.key === key);
    const acc = venueStore.venues?.[key];
    const avgMs = health?.avgMs ?? null;

    let dotClass = 'bg-[var(--ink-3)]';
    let statusText = t('dash.shell.venueCapsule.pending');
    let isReady = false;

    if (acc?.status === 'ready' || (health?.okCount ?? 0) > 0) {
      dotClass = 'bg-[var(--up)] shadow-[0_0_8px_var(--up)]';
      statusText = t('dash.shell.venueCapsule.ready');
      isReady = true;
    } else if (acc?.status === 'degraded' || (health?.failCount ?? 0) > 0) {
      dotClass = 'bg-[var(--warn)] shadow-[0_0_8px_var(--warn)]';
      statusText = t('dash.shell.venueCapsule.degraded');
    } else if (acc?.status === 'unavailable') {
      dotClass = 'bg-[var(--down)] shadow-[0_0_8px_var(--down)]';
      statusText = t('dash.shell.venueCapsule.unavailable');
    }

    const latencyText = avgMs !== null ? `${Math.round(avgMs)}ms` : isReady ? '<50ms' : '--';

    const brandColors: Record<VenueKey, string> = {
      okx: 'var(--venue-okx, #3880ff)',
      binance: 'var(--venue-binance, #f3ba2f)',
      gate: 'var(--venue-gate, #00be98)',
    };

    return {
      key,
      label: venueLabel(key),
      short: venueShortLabel(key),
      brandColor: brandColors[key],
      dotClass,
      statusText,
      latencyText,
      latencyMs: avgMs !== null ? Math.round(avgMs) : null,
      okCount: health?.okCount ?? 0,
      failCount: health?.failCount ?? 0,
      isTestnet: !!health?.testnet,
      isReady,
    };
  });
});

async function handleRefresh(e: MouseEvent) {
  e.stopPropagation();
  if (isRefreshing.value) return;
  isRefreshing.value = true;
  try {
    await Promise.all([
      venueStore.refresh(true),
      dashStore.fetchDashboard(false),
    ]);
  } finally {
    isRefreshing.value = false;
  }
}

function onDocDown(e: MouseEvent) {
  const el = e.target as Node;
  if (open.value && !triggerRef.value?.contains(el) && !panelRef.value?.contains(el)) {
    open.value = false;
  }
}

function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false;
}

onMounted(() => {
  document.addEventListener('mousedown', onDocDown);
  window.addEventListener('keydown', onEsc);
});

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown);
  window.removeEventListener('keydown', onEsc);
});
</script>

<template>
  <div class="relative inline-flex items-center">
    <!-- 胶囊触发器 -->
    <button
      ref="triggerRef"
      type="button"
      class="group relative flex h-7 cursor-pointer items-center gap-2 rounded-full border px-2.5 text-2xs font-medium tracking-tight transition-all hover:border-[var(--line-2)] hover:bg-[var(--surface-3)]"
      :class="open ? 'border-[var(--line-2)] bg-[var(--surface-3)]' : 'border-[var(--line-1)] bg-[var(--surface-2)]/80'"
      :title="t('dash.shell.venueCapsule.title')"
      @click="open = !open"
    >
      <div class="flex items-center gap-1.5 sm:gap-2">
        <span
          v-for="item in venueItems"
          :key="item.key"
          class="flex items-center gap-1 transition-opacity group-hover:opacity-100"
        >
          <span class="h-1.5 w-1.5 rounded-full transition-all duration-300" :class="item.dotClass" />
          <span class="font-semibold hidden xs:inline sm:inline" :style="{ color: item.brandColor }">{{ item.short }}</span>
          <span class="num text-[10px] tabular-nums hidden xl:inline text-[var(--ink-3)]">
            {{ item.latencyText }}
          </span>
        </span>
      </div>
    </button>

    <!-- 下拉详情面板 -->
    <Transition name="pop">
      <div
        v-if="open"
        ref="panelRef"
        class="float-panel absolute right-0 top-9 z-50 w-72 p-3 shadow-2xl backdrop-blur-2xl"
        style="background-color: var(--surface-header); border-color: var(--line-2)"
        role="dialog"
      >
        <!-- 面板头部 -->
        <div class="mb-2.5 flex items-center justify-between border-b pb-2" style="border-color: var(--line-1)">
          <div class="flex items-center gap-1.5">
            <Server class="h-3.5 w-3.5 text-[var(--accent)]" />
            <span class="text-xs font-semibold text-[var(--ink-strong)]">
              {{ t('dash.shell.venueCapsule.title') }}
            </span>
          </div>
          <button
            type="button"
            class="btn btn-quiet btn-icon !h-6 !w-6 cursor-pointer"
            :title="t('dash.cmdk.actions.refresh')"
            @click="handleRefresh"
          >
            <RefreshCw class="h-3 w-3 text-[var(--ink-2)]" :class="{ 'animate-spin': isRefreshing }" />
          </button>
        </div>

        <!-- 三所状态卡片列表 -->
        <div class="space-y-1.5">
          <div
            v-for="item in venueItems"
            :key="item.key"
            class="flex items-center justify-between rounded-lg border p-2 text-xs transition-colors hover:bg-[var(--surface-2)]"
            style="border-color: var(--line-1); background-color: var(--surface-1)"
          >
            <!-- 左侧：品牌与状态 -->
            <div class="flex items-center gap-2">
              <span class="h-2 w-2 rounded-full" :class="item.dotClass" />
              <div>
                <div class="flex items-center gap-1.5">
                  <span class="font-bold text-[var(--ink-strong)]" :style="{ color: item.brandColor }">
                    {{ item.label }}
                  </span>
                  <span
                    v-if="item.isTestnet"
                    class="rounded px-1 py-0.2 text-[9px] font-semibold text-[var(--warn)] bg-[var(--surface-3)]"
                  >
                    TEST
                  </span>
                </div>
                <span class="text-[10px] text-[var(--ink-3)]">{{ item.statusText }}</span>
              </div>
            </div>

            <!-- 右侧：延迟与采样数 -->
            <div class="text-right">
              <div class="num font-semibold text-[var(--ink-1)]">
                <span class="flex items-center justify-end gap-1 text-[11px]">
                  <Zap class="h-2.5 w-2.5 text-[var(--accent)]" />
                  {{ item.latencyText }}
                </span>
              </div>
              <div class="text-[10px] text-[var(--ink-3)] tabular-nums">
                {{ item.okCount }} 采样正常
              </div>
            </div>
          </div>
        </div>

        <!-- 底部平权架构声明 -->
        <div class="mt-2.5 flex items-center gap-1.5 border-t pt-2 text-[10px] text-[var(--ink-3)]" style="border-color: var(--line-1)">
          <ShieldCheck class="h-3.5 w-3.5 shrink-0 text-[var(--accent)]" />
          <span>{{ t('dash.shell.venueCapsule.parityNote') }}</span>
        </div>
      </div>
    </Transition>
  </div>
</template>
