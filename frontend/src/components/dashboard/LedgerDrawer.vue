<script setup lang="ts">
import { fmtDateTime } from '../../utils/format';
import { pairLabel } from '../../utils/instId';
/** 单笔生命周期抽屉：开平仓 / 费用构成 / 盈亏结构 / 策略与归因 */
import { computed } from 'vue';
import BaseDrawer from '../base/BaseDrawer.vue';
import DirTag from '../base/DirTag.vue';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, fmtPrice, dirClass, cleanReason } from '../../utils/format';

const props = defineProps<{ trade: any | null }>();
const emit = defineEmits<{ (e: 'close'): void }>();
const { t } = useI18n();

const x = computed(() => props.trade || {});
const holding = computed(() => x.value.status === 'holding');

/** 投委会溯源（仅持仓行有该数据；历史平仓行无开仓期委员会记录，诚实留空） */
const SEAT_LABELS: Record<string, string> = {
  trader_trend: 'A·顺势', trader_momentum: 'B·动能', trader_quant: 'C·数理', cio: 'CIO', REJECT_ALL: '驳回',
};
const councilNote = computed(() => {
  const cc = x.value.council;
  if (!cc || !holding.value) return '';
  if (!cc.ran) return t('dash.ledger.council.degraded');
  const seat = SEAT_LABELS[String(cc.adopted_role || '')] || String(cc.adopted_role || '');
  return seat ? t('dash.ledger.council.adopted', { seat }) : t('dash.ledger.council.ran');
});

const cells = computed(() => [
  { label: t('dash.ledger.col.entry'), value: fmtPrice(x.value.open_px), cls: '' },
  { label: t('dash.ledger.col.exit'), value: holding.value ? t('status.running') : fmtPrice(x.value.close_px), cls: '' },
  { label: t('dash.matrix.positions.col.margin'), value: fmtNum(x.value.margin, 2) + ' U', cls: '' },
  { label: t('dash.ledger.col.qty'), value: fmtNum(x.value.sz, 2) === '0.00' ? fmtNum(x.value.sz, 4) : fmtNum(x.value.sz, 2), cls: '' },
  { label: t('dash.ledger.lifecycle.grossPnl'), value: fmtSigned(x.value.gross_pnl), cls: dirClass(x.value.gross_pnl) },
  { label: t('dash.ledger.lifecycle.netPnl'), value: fmtSigned(x.value.net_pnl), cls: dirClass(x.value.net_pnl) },
  { label: t('dash.ledger.col.roi'), value: fmtPct(x.value.roi_pct), cls: dirClass(x.value.roi_pct) },
  { label: t('dash.ledger.col.fees'), value: '-' + fmtNum(Math.abs(Number(x.value.fee) || 0), 4), cls: 'down' },
]);
</script>

<template>
  <BaseDrawer
    :open="!!trade"
    width="560px"
    :title="t('dash.ledger.lifecycle.title', undefined, { sym: pairLabel(x.inst || ''), dir: x.side || '' })"
    :subtitle="`${x.strategy || ''} · ${x.lever || ''}${councilNote ? ' · ' + councilNote : ''}`"
    @close="emit('close')"
  >
    <div class="space-y-4">
      <!-- 时间线 -->
      <div class="card-flat p-3.5">
        <div class="flex items-center gap-2">
          <DirTag :dir="x.side === '多' ? 'long' : 'short'" />
          <span class="text-sm font-semibold" style="color: var(--ink-strong)">{{ x.inst }}</span>
          <span class="badge ms-auto" :class="holding ? 'badge-warn' : ''">
            {{ holding ? t('status.running') : t('dash.ledger.status.closed') }}
          </span>
        </div>
        <dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          <div>
            <dt class="t-label">{{ t('dash.ledger.lifecycle.open') }}</dt>
            <dd class="num mt-0.5" style="color: var(--ink-1)">{{ fmtDateTime(x.open_time) }}</dd>
          </div>
          <div>
            <dt class="t-label">{{ t('dash.ledger.lifecycle.close') }}</dt>
            <dd class="num mt-0.5" style="color: var(--ink-1)">{{ holding ? '--' : fmtDateTime(x.close_time) }}</dd>
          </div>
          <div>
            <dt class="t-label">{{ t('dash.ledger.col.hold') }}</dt>
            <dd class="num mt-0.5" style="color: var(--ink-1)">{{ x.duration || '--' }}</dd>
          </div>
          <div>
            <dt class="t-label">{{ t('dash.ledger.col.exitReason') }}</dt>
            <dd class="mt-0.5 leading-snug" style="color: var(--ink-1)">{{ cleanReason(x.exit_reason) }}</dd>
          </div>
        </dl>
      </div>

      <!-- 数值网格 -->
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div v-for="c in cells" :key="c.label" class="card-flat px-3 py-2.5">
          <p class="t-label truncate">{{ c.label }}</p>
          <p class="num mt-0.5 text-sm font-semibold" :class="c.cls" style="color: var(--ink-1)">{{ c.value }}</p>
        </div>
      </div>

      <!-- 费用构成 -->
      <div class="card-flat p-3.5">
        <p class="t-label mb-2">{{ t('dash.ledger.lifecycle.feesBreak') }}</p>
        <dl class="space-y-1.5 text-xs">
          <div class="flex justify-between">
            <dt style="color: var(--ink-3)">{{ t('dash.ledger.lifecycle.makerFee') }} (open)</dt>
            <dd class="num down">{{ fmtNum(Math.abs(Number(x.open_fee) || 0), 4) }}</dd>
          </div>
          <div class="flex justify-between">
            <dt style="color: var(--ink-3)">{{ t('dash.ledger.lifecycle.takerFee') }} (close)</dt>
            <dd class="num down">{{ fmtNum(Math.abs(Number(x.close_fee) || 0), 4) }}</dd>
          </div>
          <div class="flex justify-between border-t pt-1.5" style="border-color: var(--line-1)">
            <dt class="font-semibold" style="color: var(--ink-2)">{{ t('common.total') }}</dt>
            <dd class="num font-semibold down">{{ fmtNum(Math.abs(Number(x.fee) || 0), 4) }}</dd>
          </div>
        </dl>
      </div>
    </div>
  </BaseDrawer>
</template>
