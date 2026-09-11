<script setup lang="ts">
/**
 * 前台壳层：固定顶栏 + v-show 五视图（切换零重绘、图表状态保留）+ 移动端底栏 + 全局浮层。
 * 路由 / /trading /factors /news /lab /history 全部映射到这里（meta.tab 区分）。
 */
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useDashboardStore } from '../stores/dashboard';
import { useVenueAccountsStore } from '../stores/venueAccounts';
import { useListingStatusStore } from '../stores/listingStatus';
import { useI18n } from '../composables/useI18n';
import { APP_NAME, APP_VERSION } from '../config/version';
import { OFFICIAL_REPO } from '../config/version';
import { useUi } from '../composables/useUi';
import { useHotkeys } from '../composables/useHotkeys';
import TopBar from '../components/dashboard/TopBar.vue';
import MobileTabBar from '../components/dashboard/MobileTabBar.vue';
import MatrixView from '../views/dashboard/MatrixView.vue';
import RadarView from '../views/dashboard/RadarView.vue';
import NewsView from '../views/dashboard/NewsView.vue';
import EvolutionView from '../views/dashboard/EvolutionView.vue';
import LedgerView from '../views/dashboard/LedgerView.vue';
import AboutModal from '../components/dashboard/AboutModal.vue';

const route = useRoute();
const store = useDashboardStore();
const venueAccounts = useVenueAccountsStore();
const listingStatus = useListingStatusStore();
const { t } = useI18n();
const { aboutOpen, cmdkOpen } = useUi();

/* 全局 ⌘K / Ctrl+K */
useHotkeys({
  'mod+k': { handler: () => (cmdkOpen.value = true), allowInInput: true },
});

const activeTab = computed(() => (route.meta?.tab as string) || 'trading');

// 保持 store.activeTab 同步
watch(activeTab, (v) => (store.activeTab = v as any), { immediate: true });

onMounted(() => {
  store.startPolling(3000);
  void venueAccounts.refresh();
  void listingStatus.refresh(venueAccounts.environment);
});
onUnmounted(() => store.stopPolling());
</script>

<template>
  <div class="min-h-screen flex flex-col" style="background-color: var(--surface-0); color: var(--ink-1)">
    <!-- 磨砂顶栏 -->
    <TopBar />
    <div class="h-12 shrink-0" />

    <!-- 主体视图容器：各视图平滑展开 -->
    <main class="mx-auto w-full max-w-[2048px] flex-1 space-y-3.5 px-3 pb-20 pt-3.5 sm:px-5 lg:pb-8">
      <KeepAlive :max="5">
        <MatrixView v-if="activeTab === 'trading'" key="trading" />
        <RadarView v-else-if="activeTab === 'factors'" key="factors" />
        <NewsView v-else-if="activeTab === 'news'" key="news" />
        <EvolutionView v-else-if="activeTab === 'lab'" key="lab" />
        <LedgerView v-else-if="activeTab === 'history'" key="history" />
      </KeepAlive>
    </main>

    <!-- 页脚（桌面端）：对等三所架构提示 + 牌照开源说明 -->
    <footer
      class="hidden border-t py-4 text-center text-xs backdrop-blur-sm lg:block"
      style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-3)"
    >
      <div class="mx-auto flex max-w-[2048px] items-center justify-between px-5">
        <div class="flex items-center gap-2">
          <span class="h-1.5 w-1.5 rounded-full bg-[var(--up)] shadow-[0_0_6px_var(--up)]" />
          <span class="tracking-tight text-[11px] text-[var(--ink-2)]">
            OKX · Binance · Gate 三所对等执行架构 · Fail-Closed 物理风控
          </span>
        </div>

        <div class="flex items-center gap-3">
          <button
            type="button"
            class="cursor-pointer transition-colors hover:text-[var(--accent)]"
            @click="aboutOpen = true"
          >
            {{ APP_NAME }} {{ APP_VERSION }}
          </button>
          <span>·</span>
          <span>{{ t('brand.license') }}</span>
          <span>·</span>
          <a
            :href="OFFICIAL_REPO"
            target="_blank"
            rel="noopener noreferrer"
            class="transition-colors hover:text-[var(--accent)]"
          >
            GitHub
          </a>
        </div>
      </div>
    </footer>

    <!-- 移动端底栏 -->
    <MobileTabBar />
    <!-- 关于与架构弹窗 -->
    <AboutModal />
  </div>
</template>
