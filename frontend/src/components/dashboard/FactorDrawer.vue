<script setup lang="ts">
/** 因子详情抽屉：行情快照 / 动力学 / 聪明钱 / AI 裁决与理由 */
import { computed } from 'vue';
import { LineChart } from 'lucide-vue-next';
import BaseDrawer from '../base/BaseDrawer.vue';
import BaseCollapse from '../base/BaseCollapse.vue';
import BaseCodeBlock from '../base/BaseCodeBlock.vue';
import ConfBadge from '../base/ConfBadge.vue';
import DirTag from '../base/DirTag.vue';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtPct, fmtPrice, arrow, dirClass } from '../../utils/format';

const props = defineProps<{ factor: any | null }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'pick-symbol', instId: string): void }>();

const { t } = useI18n();
const f = computed(() => props.factor || {});
const d = computed(() => f.value.decision || {});
const tp = computed(() => f.value.thought_process || {});

const action = computed(() => String(d.value.action || f.value.action || 'WAIT').toUpperCase());
const dir = computed<'long' | 'short' | 'flat'>(() =>
  action.value === 'BUY_LONG' ? 'long' : action.value === 'SELL_SHORT' ? 'short' : 'flat',
);

function row(label: string, value: string, cls = '') {
  return { label, value, cls };
}
const snapshot = computed(() => [
  row(t('dash.matrix.matrix.col.price'), fmtPrice(f.value.price)),
  row(t('dash.matrix.matrix.col.chg'), `${arrow(f.value.chg24h)} ${fmtPct(f.value.chg24h, 2, false)}`, dirClass(f.value.chg24h)),
  row('24h ' + t('dash.matrix.chart.vol'), fmtNum(f.value.vol24h, 0)),
  row('Funding', f.value.fundingRate != null ? fmtPct(Number(f.value.fundingRate) * 100, 4) : '--'),
  row('OI', f.value.oiUsd != null ? fmtNum(Number(f.value.oiUsd) / 1e6, 1) + 'M' : '--'),
  row('L/S', f.value.lsRatio != null ? fmtNum(f.value.lsRatio, 2) : '--'),
]);
const calculus = computed(() => [
  row('v (1H)', fmtNum(f.value.calculus?.velocity_1h, 4), dirClass(f.value.calculus?.velocity_1h)),
  row('a (1H)', fmtNum(f.value.calculus?.accel_1h, 5), dirClass(f.value.calculus?.accel_1h)),
  row('jerk', fmtNum(f.value.calculus?.jerk_1h, 5), ''),
  row('impulse', fmtNum(f.value.calculus?.impulse_1h, 3), ''),
  row('ADX', fmtNum(f.value.adx_1h, 1), ''),
  row('ATR%', fmtPct(f.value.atr_pct, 2, false), ''),
]);
const smart = computed(() => [
  row(t('dash.matrix.matrix.col.smart'), f.value.smart_money?.weighted_long_pct != null ? fmtNum(f.value.smart_money.weighted_long_pct, 1) + '%' : '--'),
  row(t('dash.news.smart.netFlow'), f.value.smart_money?.net_flow_usdt != null ? fmtNum(f.value.smart_money.net_flow_usdt, 0) + ' U' : '--', dirClass(f.value.smart_money?.net_flow_usdt)),
  row(t('dash.news.smart.winRate'), f.value.smart_money?.top_win_rate != null ? f.value.smart_money.top_win_rate + '%' : '--'),
]);
</script>

<template>
  <BaseDrawer
    :open="!!factor"
    width="620px"
    :title="t('dash.matrix.matrix.detailTitle', undefined, { sym: (factor?.name || '') + '/USDT' })"
    :subtitle="factor?.desc || ''"
    @close="emit('close')"
  >
    <template #actions>
      <button class="btn btn-ghost btn-sm" @click="factor && emit('pick-symbol', factor.instId)">
        <LineChart />{{ t('dash.matrix.chart.title') }}
      </button>
    </template>

    <div class="space-y-4">
      <!-- AI 裁决 -->
      <div class="card-flat p-3.5">
        <div class="flex flex-wrap items-center gap-2">
          <DirTag :dir="dir" />
          <ConfBadge :value="d.confidence" />
          <span v-if="d.risk_reward_ratio" class="badge num">R:R {{ d.risk_reward_ratio }}</span>
          <span v-if="d.leverage" class="badge num">{{ d.leverage }}x · {{ fmtNum(d.margin_usdt, 0) }} U</span>
        </div>
        <div v-if="dir !== 'flat'" class="mt-2.5 grid grid-cols-3 gap-2 text-center">
          <div>
            <p class="t-label">{{ t('dash.matrix.matrix.entry') }}</p>
            <p class="num text-sm font-semibold">{{ fmtPrice(d.entry_price) }}</p>
          </div>
          <div>
            <p class="t-label" style="color: var(--down)">{{ t('dash.matrix.matrix.sl') }}</p>
            <p class="num down text-sm font-semibold">{{ fmtPrice(d.stop_loss_price) }}</p>
          </div>
          <div>
            <p class="t-label" style="color: var(--up)">{{ t('dash.matrix.matrix.tp') }}</p>
            <p class="num up text-sm font-semibold">{{ fmtPrice(d.take_profit_price) }}</p>
          </div>
        </div>
        <p v-else class="t-muted mt-2 text-xs">{{ t('dash.matrix.matrix.noDecision') }}</p>
        <p v-if="d.summary_reason || f.reason" class="mt-2.5 border-t pt-2.5 text-xs leading-relaxed" style="color: var(--ink-2); border-color: var(--line-1)">
          {{ d.summary_reason || f.reason }}
        </p>
      </div>

      <!-- 数据组 -->
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div v-for="grp in [
          { title: t('dash.matrix.matrix.market'), items: snapshot },
          { title: t('dash.matrix.matrix.calculus'), items: calculus },
        ]" :key="grp.title" class="card-flat p-3">
          <p class="t-label mb-2">{{ grp.title }}</p>
          <dl class="space-y-1.5">
            <div v-for="r in grp.items" :key="r.label" class="flex items-baseline justify-between gap-3 text-xs">
              <dt style="color: var(--ink-3)">{{ r.label }}</dt>
              <dd class="num font-semibold" :class="r.cls" style="color: var(--ink-1)">{{ r.value }}</dd>
            </div>
          </dl>
        </div>
      </div>

      <div class="card-flat p-3">
        <p class="t-label mb-2">{{ t('dash.news.smart.title') }}</p>
        <dl class="grid grid-cols-3 gap-2 text-center">
          <div v-for="r in smart" :key="r.label">
            <dt class="t-label truncate">{{ r.label }}</dt>
            <dd class="num text-sm font-semibold" :class="r.cls">{{ r.value }}</dd>
          </div>
        </dl>
      </div>

      <!-- 推演过程 -->
      <BaseCollapse v-if="Object.keys(tp).length">
        <template #head><span class="text-sm font-medium">{{ t('dash.radar.detail.verdict') }} · thought_process</span></template>
        <div class="space-y-2.5 p-3.5">
          <div v-for="(v, k) in tp" :key="k">
            <p class="t-label">{{ k }}</p>
            <p class="text-xs leading-relaxed" style="color: var(--ink-2)">{{ v }}</p>
          </div>
        </div>
      </BaseCollapse>

      <BaseCollapse>
        <template #head><span class="text-sm font-medium" style="color: var(--ink-2)">{{ t('dash.radar.detail.raw') }}</span></template>
        <div class="p-2"><BaseCodeBlock :code="JSON.stringify(factor, null, 2)" max-height="300px" /></div>
      </BaseCollapse>
    </div>
  </BaseDrawer>
</template>
