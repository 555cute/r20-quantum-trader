<script setup lang="ts">
/** 因子动能矩阵：桌面表格 / 移动卡片；行点击 → 详情抽屉 */
import { computed, ref } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtPct, fmtPrice, arrow, dirClass } from '../../utils/format';
import BaseEmpty from '../base/BaseEmpty.vue';
import ConfBadge from '../base/ConfBadge.vue';
import FactorDrawer from './FactorDrawer.vue';
import CryptoLogo from './CryptoLogo.vue';

const emit = defineEmits<{ (e: 'pick-symbol', instId: string): void }>();

const store = useDashboardStore();
const { t } = useI18n();

const rows = computed(() => store.factors || []);
const detail = ref<any>(null);

function actionOf(f: any): string {
  return String(f.decision?.action || f.action || 'WAIT').toUpperCase();
}
function actionMeta(a: string): { cls: string; label: string } {
  if (a === 'BUY_LONG') return { cls: 'dir-long', label: t('common.dir.long') };
  if (a === 'SELL_SHORT') return { cls: 'dir-short', label: t('common.dir.short') };
  return { cls: 'dir-flat', label: t('common.dir.flat') };
}
function regimeOf(f: any): string {
  const r = String(f.market_regime || f.calculus?.state_1h || '').toLowerCase();
  if (r.includes('up') || r.includes('bull')) return t('dash.matrix.matrix.regime.trendUp');
  if (r.includes('down') || r.includes('bear')) return t('dash.matrix.matrix.regime.trendDown');
  if (r.includes('range')) return t('dash.matrix.matrix.regime.range');
  return '';
}
/** 多空比：仅展示所选适配器字段；缺失显示 --，0 保留为 0 */
function lsOf(f: { lsRatio?: number | string | null }): string {
  if (f.lsRatio === null || f.lsRatio === undefined || f.lsRatio === '') return '--';
  const v = Number(f.lsRatio);
  return Number.isFinite(v) ? v.toFixed(2) : '--';
}
function openDetail(f: any) {
  detail.value = f;
}
</script>

<template>
  <div class="card overflow-hidden">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b px-3.5 py-2.5" style="border-color: var(--line-1)">
      <div>
        <h2 class="text-sm font-semibold" style="color: var(--ink-strong)">{{ t('dash.matrix.matrix.title') }}</h2>
        <p class="t-faint text-xs">{{ t('dash.matrix.matrix.desc') }}</p>
      </div>
      <span class="badge num">{{ rows.length }} {{ t('common.unitCoin') }}</span>
    </div>

    <BaseEmpty v-if="!rows.length" :text="t('dash.matrix.matrix.empty')" />

    <template v-else>
    <!-- 桌面表格 -->
    <div class="hidden overflow-x-auto lg:block">
      <table class="table">
        <thead>
          <tr>
            <th>{{ t('dash.matrix.matrix.col.symbol') }}</th>
            <th class="col-num">{{ t('dash.matrix.matrix.col.price') }}</th>
            <th class="col-num">{{ t('dash.matrix.matrix.col.chg') }}</th>
            <th class="col-num" :title="t('dash.matrix.matrix.col.vel') + ' · ' + t('dash.matrix.matrix.velTip')">v (1H)</th>
            <th class="col-num" :title="t('dash.matrix.matrix.col.acc') + ' · ' + t('dash.matrix.matrix.accTip')">a (1H)</th>
            <th class="col-num" :title="t('dash.matrix.matrix.adxTip')">ADX</th>
            <th class="col-num" :title="t('dash.matrix.matrix.lsTip')">{{ t('dash.matrix.matrix.col.ls') }}</th>
            <th>{{ t('dash.matrix.matrix.col.decision') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in rows" :key="f.instId" class="clickable" @click="openDetail(f)">
            <td>
              <div class="flex items-center gap-2">
                <CryptoLogo :symbol="f.name" :size="18" />
                <span class="num font-semibold" style="color: var(--ink-strong)">{{ f.name }}</span>
                <span v-if="regimeOf(f)" class="t-faint hidden text-2xs 2xl:inline">{{ regimeOf(f) }}</span>
              </div>
            </td>
            <td class="col-num">{{ fmtPrice(f.price) }}</td>
            <td class="col-num" :class="dirClass(f.chg24h)">{{ arrow(f.chg24h) }} {{ fmtPct(f.chg24h, 2, false) }}</td>
            <td class="col-num" :class="dirClass(f.calculus?.velocity_1h)">{{ fmtNum(f.calculus?.velocity_1h, 3) }}</td>
            <td class="col-num" :class="dirClass(f.calculus?.accel_1h)">{{ fmtNum(f.calculus?.accel_1h, 4) }}</td>
            <td class="col-num" :style="{ color: Number.isFinite(Number(f.adx_1h)) && Number(f.adx_1h) < 18 ? 'var(--ink-3)' : 'var(--ink-1)' }">{{ f.adx_1h == null ? '--' : fmtNum(f.adx_1h, 1) }}</td>
            <td class="col-num">{{ lsOf(f) }}</td>
            <td>
              <div class="flex items-center gap-1.5">
                <span class="dir" :class="actionMeta(actionOf(f)).cls">{{ actionMeta(actionOf(f)).label }}</span>
                <ConfBadge v-if="actionOf(f) !== 'WAIT'" :value="f.decision?.confidence ?? f.confidence" />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 移动卡片 -->
    <div class="grid grid-cols-1 gap-2 p-2.5 sm:grid-cols-2 lg:hidden">
      <button
        v-for="f in rows"
        :key="f.instId"
        class="card-flat flex cursor-pointer flex-col gap-1.5 p-3 text-left transition-colors hover:bg-[var(--surface-3)]"
        @click="openDetail(f)"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="flex items-center gap-2 text-sm font-bold" style="color: var(--ink-strong)">
            <CryptoLogo :symbol="f.name" :size="16" />{{ f.name }}
          </span>
          <span class="dir" :class="actionMeta(actionOf(f)).cls">{{ actionMeta(actionOf(f)).label }}</span>
        </div>
        <div class="flex items-baseline justify-between text-xs">
          <span class="num font-semibold">{{ fmtPrice(f.price) }}</span>
          <span class="num" :class="dirClass(f.chg24h)">{{ arrow(f.chg24h) }} {{ fmtPct(f.chg24h, 2, false) }}</span>
        </div>
        <div class="t-faint flex justify-between text-2xs">
          <span class="num">v {{ fmtNum(f.calculus?.velocity_1h, 3) }} · a {{ fmtNum(f.calculus?.accel_1h, 4) }}</span>
          <span class="num">ADX {{ fmtNum(f.adx_1h, 1) }}</span>
        </div>
      </button>
    </div>
    </template>

    <FactorDrawer :factor="detail" @close="detail = null" @pick-symbol="(id: string) => { detail = null; emit('pick-symbol', id) }" />
  </div>
</template>
