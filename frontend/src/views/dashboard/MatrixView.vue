<script setup lang="ts">
/**
 * 实盘矩阵大板：Chart-First 机构级双翼操盘终端
 * 1. 顶层极简 KPI HUD
 * 2. 核心操盘区：左·量价工作站 (8) + 右·在手持仓与在途委托 (4)
 * 3. 资产管理区：三所对等账户卡与组合风险
 * 4. 动力学矩阵：全市场因子动能
 */
import { computed, ref, watch } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { symOf } from '../../utils/instId';
import { useUi } from '../../composables/useUi';
import KpiRibbon from '../../components/dashboard/KpiRibbon.vue';
import ChartWorkstation from '../../components/dashboard/ChartWorkstation.vue';
import PositionsOrdersPanel from '../../components/dashboard/PositionsOrdersPanel.vue';
import VenueAccountsPanel from '../../components/dashboard/VenueAccountsPanel.vue';
import FactorMatrix from '../../components/dashboard/FactorMatrix.vue';

const store = useDashboardStore();
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
  <div class="space-y-3">
    <!-- 1. 顶层极简资产风控 HUD -->
    <KpiRibbon />

    <!-- 2. 首屏核心双翼操盘台 (Chart-first: 左·K线量价工作站 67% + 右·在手持仓与委托 33%) -->
    <div class="grid grid-cols-1 gap-3 xl:grid-cols-12">
      <div class="xl:col-span-8">
        <ChartWorkstation ref="chart" :initial-symbol="initialSymbol" />
      </div>
      <div class="xl:col-span-4">
        <PositionsOrdersPanel @pick-symbol="pick" />
      </div>
    </div>

    <!-- 3. 三所对等账户与资金风控舱 -->
    <VenueAccountsPanel />

    <!-- 4. 因子动能与决策矩阵 -->
    <FactorMatrix @pick-symbol="pick" />
  </div>
</template>
