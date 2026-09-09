<script setup lang="ts">
import { pairLabel } from '../../utils/instId';
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
// 后端 /api/all 的因子对象里决策字段是平铺的（action/confidence/entry_price...），
// 没有嵌套 decision；保留 decision 优先以兼容未来结构变化。
const d = computed(() => f.value.decision || f.value);
const tp = computed(() => f.value.thought_process || {});

/** 后端行情字段可能是已格式化的字符串（"0.0054%"、"21.76亿 U"）或 N/A，直接透传展示 */
function asIs(v: unknown): string {
  if (v === null || v === undefined) return '--';
  const s = String(v).trim();
  if (!s || s === 'N/A' || s === '--' || s === '—') return '--';
  return s;
}

/** Current-market scalar: missing stays unknown; real 0 is kept. Never Number(null)→0. */
function knownNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string') {
    const s = v.trim();
    if (!s || s === 'N/A' || s === '--' || s === '—') return null;
  }
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtKnownPrice(v: unknown): string {
  const n = knownNum(v);
  return n === null ? '--' : fmtPrice(n);
}

function fmtKnownNum(v: unknown, digits: number): string {
  const n = knownNum(v);
  return n === null ? '--' : fmtNum(n, digits);
}

function fmtKnownPct(v: unknown, digits = 2, signed = false): string {
  const n = knownNum(v);
  return n === null ? '--' : fmtPct(n, digits, signed);
}

const action = computed(() => String(d.value.action || f.value.action || 'WAIT').toUpperCase());
const dir = computed<'long' | 'short' | 'flat'>(() =>
  action.value === 'BUY_LONG' ? 'long' : action.value === 'SELL_SHORT' ? 'short' : 'flat',
);

function row(label: string, value: string, cls = '') {
  return { label, value, cls };
}
const snapshot = computed(() => {
  const chg = knownNum(f.value.chg24h);
  return [
    row(t('dash.matrix.matrix.col.price'), fmtKnownPrice(f.value.price)),
    row(
      t('dash.matrix.matrix.col.chg'),
      chg === null ? '--' : `${arrow(chg)} ${fmtPct(chg, 2, false)}`,
      chg === null ? '' : dirClass(chg),
    ),
    row('24h ' + t('dash.matrix.chart.vol') + ' BASE', fmtKnownNum(f.value.vol24h, 0)),
    row('Funding', asIs(f.value.fundingRate)),
    row('OI', asIs(f.value.oiUsd)),
    row('L/S', asIs(f.value.lsRatio)),
  ];
});
const calculus = computed(() => {
  const vel = knownNum(f.value.calculus?.velocity_1h);
  const acc = knownNum(f.value.calculus?.accel_1h);
  return [
    row('v (1H)', vel === null ? '--' : fmtNum(vel, 4), vel === null ? '' : dirClass(vel)),
    row('a (1H)', acc === null ? '--' : fmtNum(acc, 5), acc === null ? '' : dirClass(acc)),
    row('jerk', fmtKnownNum(f.value.calculus?.jerk_1h, 5), ''),
    row('impulse', fmtKnownNum(f.value.calculus?.impulse_1h, 3), ''),
    row('ADX', fmtKnownNum(f.value.adx_1h, 1), ''),
    row('ATR%', fmtKnownPct(f.value.atr_pct), ''),
  ];
});
const smart = computed(() => [
  row(t('dash.matrix.matrix.col.ls'), asIs(f.value.lsRatio)),
  row('Funding', asIs(f.value.fundingRate)),
  row('OI', asIs(f.value.oiUsd)),
]);
</script>

<template>
  <BaseDrawer
    :open="!!factor"
    width="620px"
    :title="t('dash.matrix.matrix.detailTitle', undefined, { sym: pairLabel(factor?.name || '') })"
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
