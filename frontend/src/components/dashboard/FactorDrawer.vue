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
import { fmtNum, fmtPct, fmtPrice, arrow, dirClass, utcStrToBj } from '../../utils/format';
import { venueLabel, venueColor, stageLabel, decisionBadgeCls, numOrNull, isPlainObj } from '../../utils/venueMeta';

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

/** 三所对等行情与基差对照行 */
interface VenueQuoteRow {
  key: string;
  label: string;
  brandColor: string;
  price: string;
  basisText: string;
  basisTone: string;
  lsRatio: string;
  fundingText: string;
  fundingTone: string;
}

const venueQuoteComparison = computed<VenueQuoteRow[]>(() => {
  const s = cvSymbol.value;
  const basePx = Number(f.value.price || 0);

  // 1. OKX
  const okxF = f.value.fundingRate != null ? f.value.fundingRate : null;
  const okxFNum = okxF !== null && !isNaN(Number(okxF)) ? Number(okxF) : null;

  // 2. Binance
  const binPx = s?.bin_last != null ? Number(s.bin_last) : null;
  const binBasis = s?.bin_basis_pct != null ? Number(s.bin_basis_pct) : null;
  const binF = s?.bin_funding_pct != null ? Number(s.bin_funding_pct) : null;

  // 3. Gate
  const gatePx = s?.gate_last != null ? Number(s.gate_last) : null;
  const gateBasis = s?.gate_basis_pct != null ? Number(s.gate_basis_pct) : null;
  const gateF = s?.gate_funding_pct != null ? Number(s.gate_funding_pct) : null;

  return [
    {
      key: 'okx',
      label: 'OKX',
      brandColor: 'var(--venue-okx, #3880ff)',
      price: basePx > 0 ? fmtPrice(basePx) : '--',
      basisText: '基准',
      basisTone: 't-faint',
      lsRatio: asIs(f.value.lsRatio),
      fundingText: okxFNum !== null ? `${okxFNum >= 0 ? '+' : ''}${(okxFNum * 100).toFixed(4)}%` : asIs(okxF),
      fundingTone: okxFNum !== null ? (okxFNum >= 0 ? 'up' : 'down') : '',
    },
    {
      key: 'binance',
      label: 'Binance',
      brandColor: 'var(--venue-binance, #f3ba2f)',
      price: binPx !== null ? fmtPrice(binPx) : '--',
      basisText: binBasis !== null ? `${binBasis >= 0 ? '+' : ''}${(binBasis * 100).toFixed(2)}%` : '--',
      basisTone: binBasis !== null ? (binBasis >= 0 ? 'up' : 'down') : 't-faint',
      lsRatio: s?.bin_ls != null ? fmtNum(Number(s.bin_ls), 2) : '--',
      fundingText: binF !== null ? `${binF >= 0 ? '+' : ''}${(binF * 100).toFixed(4)}%` : '--',
      fundingTone: binF !== null ? (binF >= 0 ? 'up' : 'down') : '',
    },
    {
      key: 'gate',
      label: 'Gate.io',
      brandColor: 'var(--venue-gate, #00be98)',
      price: gatePx !== null ? fmtPrice(gatePx) : '--',
      basisText: gateBasis !== null ? `${gateBasis >= 0 ? '+' : ''}${(gateBasis * 100).toFixed(2)}%` : '--',
      basisTone: gateBasis !== null ? (gateBasis >= 0 ? 'up' : 'down') : 't-faint',
      lsRatio: s?.gate_ls != null ? fmtNum(Number(s.gate_ls), 2) : '--',
      fundingText: gateF !== null ? `${gateF >= 0 ? '+' : ''}${(gateF * 100).toFixed(4)}%` : '--',
      fundingTone: gateF !== null ? (gateF >= 0 ? 'up' : 'down') : '',
    },
  ];
});

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

/** US-004 选所决策证据（venue_decision）：US-003 路由层随决策落盘的纯附加字段。
 *  消费面 factor 顶层优先、decision 嵌套回退；缺数据 → null（区块优雅降级，绝不报错）。 */
const vd = computed(() => {
  const raw = f.value.venue_decision ?? d.value.venue_decision ?? null;
  return isPlainObj(raw) ? raw : null;
});
const vdReasons = computed<string[]>(() => {
  const arr = vd.value?.reasons;
  return Array.isArray(arr) ? arr.map((x: unknown) => String(x ?? '')).filter(Boolean) : [];
});
const vdRejected = computed<Record<string, any>[]>(() => {
  const arr = vd.value?.rejected;
  return Array.isArray(arr) ? arr.filter(isPlainObj) : [];
});
const vdAllocation = computed<Record<string, any>[]>(() => {
  const arr = vd.value?.allocation;
  return Array.isArray(arr) ? arr.filter(isPlainObj) : [];
});
const vdBudget = computed(() => (isPlainObj(vd.value?.budget) ? vd.value.budget : null));
const vdBadgeCls = computed(() => decisionBadgeCls(vd.value));
const vdIsAuto = computed(() => String(vd.value?.preferred_venue || 'auto').trim().toLowerCase() === 'auto');
/** 预算预留行文案：limit/前占/本次 逐值 null-safe（"--" ≠ 0） */
const vdBudgetText = computed(() => {
  const b = vdBudget.value;
  if (!b) return '';
  const parts: string[] = [];
  const limit = numOrNull(b.limit_usdt);
  parts.push(`上限 ${limit === null ? '--' : fmtNum(limit, 0) + 'U'}`);
  const before = numOrNull(b.reserved_before_usdt);
  if (before !== null) parts.push(`预留前占 ${fmtNum(before, 2)}U`);
  const thisAmt = numOrNull(b.amount_usdt ?? b.margin_usdt);
  parts.push(`本次预留 ${thisAmt === null ? '--' : fmtNum(thisAmt, 2) + 'U'}`);
  if (b.state) parts.push(`态 ${String(b.state)}`);
  if (b.error) parts.push(String(b.error));
  return parts.join(' · ');
});
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

      <!-- 选所决策证据（US-004：消费 US-003 venue_decision 落盘段；缺数据优雅降级） -->
      <div class="card-flat p-3.5" data-test="venue-decision">
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <p class="t-label">选所决策 · venue_decision</p>
          <span v-if="vd?.decided_utc" class="num text-[10px]" style="color: var(--ink-3)">
            {{ utcStrToBj(String(vd.decided_utc), true) }} 北京
          </span>
        </div>
        <template v-if="vd">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="badge" :style="{ color: vd.venue ? venueColor(vd.venue) : 'var(--ink-3)', borderColor: 'currentColor' }">
              <span class="dot" :style="{ backgroundColor: vd.venue ? venueColor(vd.venue) : 'var(--ink-3)' }" />
              中选 {{ vd.venue ? venueLabel(vd.venue) : '未选中' }}
            </span>
            <span :class="vdBadgeCls">{{ vd.reason_code || '原因码 --' }}</span>
            <span v-if="!vdIsAuto" class="badge">手选优先 · {{ venueLabel(vd.preferred_venue) }}</span>
            <span v-if="vd.hysteresis_applied" class="badge">滞回保留现任</span>
            <span v-if="vd.outcome" class="badge">{{ vd.outcome }}</span>
          </div>
          <p v-if="vd.skip_reason" class="mt-2 text-xs leading-relaxed" style="color: var(--down)">{{ vd.skip_reason }}</p>
          <ul v-if="vdReasons.length" class="mt-2 space-y-1 border-t pt-2" style="border-color: var(--line-1)">
            <li v-for="(r, i) in vdReasons" :key="'vd-r-' + i" class="text-xs leading-relaxed" style="color: var(--ink-2)">{{ r }}</li>
          </ul>
          <div v-if="vdAllocation.length" class="mt-2 flex flex-wrap gap-1.5">
            <span v-for="(a, i) in vdAllocation" :key="'vd-a-' + i" class="badge num text-[10px]">
              分配 {{ venueLabel(a.venue) }} {{ fmtNum(numOrNull(a.amount_usdt), 2) }}U
            </span>
          </div>
          <!-- 被淘汰候选：移动端横滑不折列 -->
          <div v-if="vdRejected.length" class="mt-2.5">
            <p class="t-label mb-1">被淘汰候选 · {{ vdRejected.length }} 所</p>
            <div class="table-scroll-container rounded-lg" style="border: 1px solid var(--line-1)">
              <table class="table">
                <thead>
                  <tr><th>交易所</th><th>淘汰阶段</th><th>原因</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(r, i) in vdRejected" :key="'vd-j-' + i">
                    <td class="font-semibold" :style="{ color: venueColor(r.venue) }">{{ venueLabel(r.venue) }}</td>
                    <td><span class="badge">{{ stageLabel(r.stage) }}</span></td>
                    <td class="max-w-[320px] truncate text-xs" style="color: var(--ink-2)" :title="String(r.reason || '')">{{ r.reason || '--' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <p v-if="vdBudgetText" class="mt-2 border-t pt-2 text-[11px] leading-relaxed" style="border-color: var(--line-1); color: var(--ink-3)">{{ vdBudgetText }}</p>
        </template>
        <p v-else class="t-muted text-xs">暂无选所决策证据——该信号本周期未走选所路由链路（接线周期生成后自动展示）。</p>
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

      <!-- 三所对等行情与基差费率全览表 -->
      <div class="card-flat p-3.5 space-y-2.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-1.5">
            <span class="t-label font-bold text-xs" style="color: var(--ink-strong)">三所实时行情与基差费率对照</span>
            <span class="badge text-3xs" style="background: var(--surface-3); color: var(--ink-3)">对等采样</span>
          </div>
          <span v-if="cvUpdated" class="num text-[10px]" style="color: var(--ink-3)">
            {{ utcStrToBj(cvUpdated, true) }} 北京
          </span>
        </div>

        <!-- 三所健康度微徽章 -->
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="h in cvHealth"
            :key="h.key"
            class="badge num text-[10px]"
            :style="h.testnet ? 'color:#56B4E9;border-color:currentColor' : (h.fail > 0 ? 'color:var(--warn, #F0B90B);border-color:currentColor' : (h.fresh ? 'color:var(--up);border-color:currentColor' : 'color:var(--ink-3);border-color:currentColor'))"
          >
            {{ h.key.toUpperCase() }} {{ h.fresh ? `${h.ok}/${h.ok + h.fail}` : '--' }}
            <template v-if="h.avg"> · {{ h.avg }}ms</template>
            <template v-if="h.testnet"> · TN</template>
          </span>
        </div>

        <!-- 对照表格 -->
        <div class="overflow-x-auto rounded-lg border" style="border-color: var(--line-1)">
          <table class="table !text-xs">
            <thead>
              <tr>
                <th>交易所</th>
                <th class="col-num">现价</th>
                <th class="col-num">相对基差</th>
                <th class="col-num">多空比</th>
                <th class="col-num">资金费率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="q in venueQuoteComparison" :key="q.key">
                <td>
                  <div class="flex items-center gap-1.5">
                    <span class="h-2 w-2 rounded-full" :style="{ backgroundColor: q.brandColor }" />
                    <span class="font-bold" :style="{ color: q.brandColor }">{{ q.label }}</span>
                  </div>
                </td>
                <td class="col-num font-semibold" style="color: var(--ink-1)">{{ q.price }}</td>
                <td class="col-num" :class="q.basisTone">{{ q.basisText }}</td>
                <td class="col-num">{{ q.lsRatio }}</td>
                <td class="col-num" :class="q.fundingTone">{{ q.fundingText }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!cvSymbol" class="t-muted text-[11px]">该币暂无跨所快照——等待下一个 15 分钟决策周期生成。</p>
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
