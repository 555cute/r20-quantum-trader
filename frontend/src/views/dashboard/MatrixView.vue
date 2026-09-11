<script setup lang="ts">
/**
 * 实盘矩阵视图：KPI 带 → 左图表(8) + 右持仓挂单(4) → 因子矩阵。
 * 交互主线：任何位置的选币（矩阵行 / 持仓行 / ⌘K）都汇聚到图表。
 */
import { computed, ref, watch } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { symOf } from '../../utils/instId';
import { useI18n } from '../../composables/useI18n';
import { useUi } from '../../composables/useUi';
import PageHead from '../../components/dashboard/PageHead.vue';
import KpiRibbon from '../../components/dashboard/KpiRibbon.vue';
import VenueAccountsPanel from '../../components/dashboard/VenueAccountsPanel.vue';
import ChartWorkstation from '../../components/dashboard/ChartWorkstation.vue';
import PositionsOrdersPanel from '../../components/dashboard/PositionsOrdersPanel.vue';
import FactorMatrix from '../../components/dashboard/FactorMatrix.vue';

const store = useDashboardStore();
const { t } = useI18n();
const { focusSymbol } = useUi();

const chart = ref<InstanceType<typeof ChartWorkstation> | null>(null);

/** 初始选中：优先当前持仓，其次 BTC（池内恒定存在） */
const initialSymbol = computed(() => {
  const p = store.positions[0];
  return p ? symOf(String(p.instId)) : 'BTC';
});

function pick(instId: string) {
  const sym = String(instId || '').split('-')[0].toUpperCase();
  if (sym) chart.value?.selectSymbol(sym);
}

watch(focusSymbol, (v) => {
  if (v) {
    pick(v);
    focusSymbol.value = null;
  }
});
</script>

<template>
  <div class="space-y-2.5 sm:space-y-3">
    <PageHead :title="t('dash.matrix.title')" :desc="t('dash.matrix.desc')" />

    <KpiRibbon />

    <VenueAccountsPanel />

    <div class="grid grid-cols-1 gap-3 xl:grid-cols-12">
      <div class="xl:col-span-8">
        <ChartWorkstation ref="chart" :initial-symbol="initialSymbol" />
      </div>
      <div class="xl:col-span-4">
        <PositionsOrdersPanel @pick-symbol="pick" />
      </div>
    </div>

    <FactorMatrix @pick-symbol="pick" />
  </div>
</template>
