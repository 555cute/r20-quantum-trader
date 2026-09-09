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

const props = defineProps<{ factor: any | null; crossVenue?: any | null }>();
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
  if (!s || s === 'N/A' || s === '--') return '--';
  return s;
}

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
  row('24h ' + t('dash.matrix.chart.vol'), f.value.vol24h != null ? fmtNum(f.value.vol24h, 0) : '--'),
  row('Funding', asIs(f.value.fundingRate)),
  row('OI', asIs(f.value.oiUsd)),
  row('L/S', asIs(f.value.lsRatio)),
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
  row(t('dash.matrix.matrix.col.ls'), asIs(f.value.lsRatio)),
  row('Funding', asIs(f.value.fundingRate)),
  row('OI', asIs(f.value.oiUsd)),
]);

/** US-007 跨所块：本币 OKX/币安/Gate 价与基差 + 双所大户比/费率 + 三所取数健康。
 * 消费面统一走后端合并好的 by_asset（symbols 仅作旧快照兼容回退）；缺值 "--"。 */
const cvSymbol = computed(() => {
  const name = String(f.value.name || '').toUpperCase();
  const cv = props.crossVenue || {};
  return (cv.by_asset || {})[name] || (cv.symbols || {})[name] || null;
});
function _missing(v: unknown): boolean {
  return v === null || v === undefined || v === '' || !Number.isFinite(Number(v));
}
function _num(v: unknown, digits = 2): string {
  return _missing(v) ? '--' : fmtNum(Number(v), digits);
}
function _px(v: unknown): string {
  return _missing(v) ? '--' : fmtPrice(Number(v));
}
function _basis(v: unknown): string {
  return _missing(v) ? '' : ` (${fmtPct(Number(v), 2, false)})`;
}
const cvRows = computed(() => {
  const s = cvSymbol.value;
  return [
    row('OKX', fmtPrice(f.value.price), ''),
    row('Binance', s ? _px(s.bin_last) + _basis(s.bin_basis_pct) : '--', dirClass(s?.bin_basis_pct)),
    row('Gate', s ? _px(s.gate_last) + _basis(s.gate_basis_pct) : '--', dirClass(s?.gate_basis_pct)),
    row('L/S 币安/Gate', s ? `${_num(s.bin_ls)} / ${_num(s.gate_ls)}` : '--', ''),
    row('Fund% 币安/Gate', s ? `${_num(s.bin_funding_pct, 4)} / ${_num(s.gate_funding_pct, 4)}` : '--', ''),
  ];
});
const cvHealth = computed(() => {
  const v = props.crossVenue?.venues || {};
  return ['okx', 'binance', 'gate'].map((k) => {
    const x = v[k] || {};
    const okN = Array.isArray(x.ok) ? x.ok.length : 0;
    const failN = x.failed ? Object.keys(x.failed).length : 0;
    return {
      key: k, ok: okN, fail: failN,
      avg: x.avg_ms || 0, testnet: !!x.testnet,
      fresh: okN + failN > 0,
    };
  });
});
const cvUpdated = computed(() => String(props.crossVenue?.updated_utc || ''));
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
        <p class="t-label mb-2">{{ t('dash.news.smart.title') }} · OKX</p>
        <dl class="grid grid-cols-3 gap-2 text-center">
          <div v-for="r in smart" :key="r.label">
            <dt class="t-label truncate">{{ r.label }}</dt>
            <dd class="num text-sm font-semibold" :class="r.cls">{{ r.value }}</dd>
          </div>
        </dl>
      </div>

      <!-- 跨所协调（US-007：/api/all cross_venue 消费端） -->
      <div class="card-flat p-3">
        <div class="mb-2 flex items-baseline justify-between gap-2">
          <p class="t-label">跨所 · Binance / Gate</p>
          <span v-if="cvUpdated" class="num text-[10px]" style="color: var(--ink-3)">{{ cvUpdated }} UTC</span>
        </div>
        <div class="mb-2 flex flex-wrap gap-1.5">
          <span v-for="h in cvHealth" :key="h.key"
                class="badge num text-[10px]"
                :style="h.testnet ? 'color:#56B4E9;border-color:currentColor' : (h.fail > 0 ? 'color:var(--warn, #F0B90B);border-color:currentColor' : (h.fresh ? 'color:var(--up);border-color:currentColor' : 'color:var(--ink-3);border-color:currentColor'))">
            {{ h.key.toUpperCase() }} {{ h.fresh ? `${h.ok}/${h.ok + h.fail}` : '--' }}<template v-if="h.avg"> · {{ h.avg }}ms</template><template v-if="h.testnet"> · TN</template>
          </span>
        </div>
        <dl class="space-y-1.5">
          <div v-for="r in cvRows" :key="r.label" class="flex items-baseline justify-between gap-3 text-xs">
            <dt style="color: var(--ink-3)">{{ r.label }}</dt>
            <dd class="num font-semibold" :class="r.cls" style="color: var(--ink-1)">{{ r.value }}</dd>
          </div>
        </dl>
        <p v-if="!cvSymbol" class="t-muted mt-2 text-[11px]">该币暂无跨所快照——等待下一个 15 分钟决策周期生成。</p>
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
