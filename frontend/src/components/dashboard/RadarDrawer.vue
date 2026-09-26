<script setup lang="ts">
/**
 * RadarDrawer.vue · DeepSeek Harness 风格决策白盒透视抽屉
 * 包含：宏观综合研判、机会与持仓调度、投委会多模型博弈流（各交易员提案/辩论/CIO终审）、三所价差与原始 JSON
 */
import { computed, ref, watch } from 'vue';
import { fmtDateTime, fmtNum, fmtPrice } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';
import { useDashboardStore } from '../../stores/dashboard';
import { Scale, Activity, Zap, Landmark } from 'lucide-vue-next';
import BaseDrawer from '../base/BaseDrawer.vue';
import BaseTabs from '../base/BaseTabs.vue';
import BaseCodeBlock from '../base/BaseCodeBlock.vue';
import BaseEmpty from '../base/BaseEmpty.vue';
import DirTag from '../base/DirTag.vue';
import ConfBadge from '../base/ConfBadge.vue';
import CryptoLogo from './CryptoLogo.vue';

const dash = useDashboardStore();
const props = defineProps<{ cycle: any | null }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const { t } = useI18n();
const tab = ref('macro');

const c = computed(() => props.cycle || {});

function promptChars(row: any): number | null {
  const text = row?.ai_last_prompt;
  if (typeof text === 'string' && text.length > 0) return text.length;
  const chars = row?.ai_last_prompt_chars;
  return typeof chars === 'number' && chars > 0 ? chars : null;
}

const opps = computed<any[]>(() => c.value.top_opportunities || []);
const posMgmt = computed<any[]>(() => c.value.position_management || []);
const transcript = computed(() => c.value.council_transcript);
const councilStatus = computed<any>(() => c.value.council_status || null);

/* 席位显示名（批 32：此前写死中文，英文模式下漏中文）。键仍是后端 id，
   未知 id 原样回显，绝不显示空白。 */
const SEAT_KEYS: Record<string, string> = {
  trader_trend: 'dash.radar.seat.traderTrend',
  trader_momentum: 'dash.radar.seat.traderMomentum',
  trader_quant: 'dash.radar.seat.traderQuant',
  cio: 'dash.radar.seat.cio',
  REJECT_ALL: 'dash.radar.seat.rejectAll',
};
function seatLabel(id: any): string {
  const s = String(id || '');
  return SEAT_KEYS[s] ? t(SEAT_KEYS[s]) : (s || '');
}

const advisorList = computed<any[]>(() => Object.values(transcript.value?.advisors || {}));
const arbitrator = computed<any>(() => transcript.value?.arbitrator || null);
const modeLabel = computed(() => transcript.value?.consensus_mode === 'cross_examination'
  ? t('dash.radar.council.cross') : t('dash.radar.council.standard'));

const xvenueRows = computed(() => {
  const byAsset: any = c.value?.cross_venue?.by_asset
    || (dash.data as any)?.cross_venue?.symbols || {};
  return Object.entries(byAsset).map(([sym, data]: [string, any]) => ({
    symbol: sym,
    okx_last: typeof data?.okx_last === 'number' ? data.okx_last : data?.okx,
    bin_last: data?.bin_last,
    bin_basis_pct: data?.bin_basis_pct,
    gate_last: data?.gate_last,
    gate_basis_pct: data?.gate_basis_pct,
    bin_ls: data?.bin_ls,
    gate_ls: data?.gate_ls,
    bin_funding_pct: data?.bin_funding_pct,
    gate_funding_pct: data?.gate_funding_pct,
  }));
});

const tabs = computed(() => {
  const items = [
    { key: 'macro', label: t('dash.radar.detail.macro') },
    { key: 'quotes', label: t('dash.radar.detail.quotes'), count: opps.value.length + posMgmt.value.length },
  ];
  if (transcript.value || councilStatus.value) items.push({ key: 'council', label: t('dash.radar.council.title') });
  if (xvenueRows.value.length > 0) items.push({ key: 'xvenue', label: t('dash.radar.detail.xvenue'), count: xvenueRows.value.length });
  items.push({ key: 'raw', label: t('dash.radar.detail.raw') });
  return items;
});

watch(() => props.cycle, () => { tab.value = 'macro'; });
watch(tabs, (items) => {
  const keys = items.map((i) => i.key);
  if (!keys.includes(tab.value)) tab.value = 'macro';
});

function dirOf(a: string): 'long' | 'short' | 'flat' {
  const u = String(a).toUpperCase();
  return u.includes('LONG') ? 'long' : u.includes('SHORT') ? 'short' : 'flat';
}

function posActionBadge(action: string): { label: string; class: string } {
  const a = String(action || '').toUpperCase();
  if (a === 'UPDATE_SL') {
    return { label: '🔒 保本移损', class: 'text-[var(--accent)] border-[var(--accent-line)] bg-[var(--accent-bg)]' };
  }
  if (a === 'CLOSE_MARKET') {
    return { label: '💰 市价平仓', class: 'text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]' };
  }
  if (a === 'HOLD') {
    return { label: '🛡️ 顺势持有', class: 'text-[var(--ink-2)] border-[var(--line-1)] bg-[var(--surface-2)]' };
  }
  if (a === 'SCALE_OUT' || a === 'PARTIAL_TP') {
    return { label: '🎯 分批锁利', class: 'text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]' };
  }
  return { label: a || '—', class: 'text-[var(--ink-3)] border-[var(--line-1)] bg-[var(--surface-2)]' };
}
</script>

<template>
  <BaseDrawer
    :open="!!cycle"
    width="680px"
    :title="t('dash.radar.detail.title', undefined, { t: fmtDateTime(cycle?.time) })"
    :subtitle="cycle?.policy_version || 'AstraQuant Multi-Agent System'"
    @close="emit('close')"
  >
    <BaseTabs
      v-model="tab"
      base-id="radar-detail"
      :items="tabs"
      :label="t('dash.radar.detail.tabsAria')"
      class="mb-3.5"
    />

    <!-- 1. 宏观综合研判 -->
    <div
      v-if="tab === 'macro'"
      id="radar-detail-panel-macro"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="radar-detail-tab-macro"
      class="space-y-3"
    >
      <!-- 委员会运行状态 -->
      <div class="flex flex-wrap items-center gap-2">
        <span
          v-if="councilStatus?.ran"
          class="dsh-pill text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]"
        >
          <Landmark class="w-3 h-3" /> {{ t('dash.radar.council.done') }} · {{ ((councilStatus.duration_ms || 0) / 1000).toFixed(1) }}s
        </span>
        <span
          v-else-if="councilStatus"
          class="dsh-pill text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]"
          :title="String(councilStatus.reason || '')"
        >
          <Zap class="w-3 h-3" /> {{ t('dash.radar.council.degraded') }}
        </span>
      </div>

      <p
        v-if="councilStatus && !councilStatus.ran && councilStatus.reason"
        class="dsh-card-sub p-3 text-xs leading-body text-[var(--warn)] border-[var(--warn-line)]"
      >
        {{ t('dash.radar.council.reason') }}{{ t('common.punct.colon') }}{{ councilStatus.reason }}
      </p>

      <!-- 宏观综述 -->
      <div class="dsh-card-sub p-4 text-xs leading-body text-[var(--ink-1)]">
        <h4 class="text-3xs font-bold uppercase tracking-wider text-[var(--ink-3)] mb-2">{{ t('dash.radar.macroReview') }}</h4>
        <p class="whitespace-pre-wrap font-sans text-xs leading-body">{{ c.macro_assessment || '--' }}</p>

        <p v-if="promptChars(c)" class="num font-mono text-3xs text-[var(--ink-3)] mt-3 border-t pt-2" style="border-color: var(--line-1)">
          {{ t('dash.shell.peek.chars', undefined, { n: promptChars(c) ?? 0 }) }} · {{ t('dash.shell.peek.title') }}
          <span v-if="c.ai_last_prompt_elided">{{ t('common.punct.parenOpen') }}{{ t('dash.radar.detail.promptElided') }}{{ t('common.punct.parenClose') }}</span>
        </p>
      </div>
    </div>

    <!-- 2. 机会与持仓调度 -->
    <div
      v-else-if="tab === 'quotes'"
      id="radar-detail-panel-quotes"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="radar-detail-tab-quotes"
      class="space-y-3"
    >
      <!-- 持仓管理指令 -->
      <div v-if="posMgmt.length" class="space-y-2">
        <h4 class="text-3xs font-bold uppercase tracking-wider text-[var(--ink-3)] flex items-center gap-1.5">
          <Activity class="h-3 w-3 text-[var(--accent)]" />
          {{ t('dash.radar.posMgmt') }}
        </h4>
        <div
          v-for="p in posMgmt"
          :key="p.instId"
          class="dsh-card-sub p-3 space-y-1.5"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="font-mono font-bold text-xs text-[var(--ink-strong)]">{{ p.instId }}</span>
              <span class="rounded px-1.5 py-0.5 border text-3xs font-mono font-medium" :class="posActionBadge(p.action).class">
                {{ posActionBadge(p.action).label }}
              </span>
              <ConfBadge :value="p.confidence" />
            </div>
            <span v-if="p.suggested_sl_price > 0" class="text-3xs font-mono text-[var(--down)]">
              SL: {{ fmtPrice(p.suggested_sl_price) }}
            </span>
            <span v-else class="text-3xs font-mono text-[var(--ink-3)]">{{ p.action }}</span>
          </div>
          <p class="text-xs text-[var(--ink-2)] leading-body">{{ p.reason || p.reasoning || '--' }}</p>
        </div>
      </div>

      <!-- 潜力机会 Top -->
      <div v-if="opps.length" class="space-y-2">
        <h4 class="text-3xs font-bold uppercase tracking-wider text-[var(--ink-3)] flex items-center gap-1.5">
          <Zap class="h-3 w-3 text-[var(--accent)]" />
          {{ t('dash.radar.opportunities') }}
        </h4>
        <div
          v-for="o in opps"
          :key="o.inst"
          class="dsh-card-sub p-3 space-y-2"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <CryptoLogo :symbol="o.inst" :size="16" />
              <span class="font-mono font-bold text-xs text-[var(--ink-strong)]">{{ o.inst }}</span>
              <DirTag :dir="dirOf(o.action)" />
              <ConfBadge :value="o.confidence" />
              <span
                v-if="o.scale_out_tp"
                class="rounded px-1.5 py-0.5 border text-4xs font-mono font-medium text-[var(--accent)] border-[var(--accent-line)] bg-[var(--accent-bg)]"
              >
                {{ t('dash.radar.scaleOutTag') }}
              </span>
            </div>
            <span class="rounded px-1.5 py-0.5 border text-3xs font-mono text-[var(--ink-2)]" style="background-color: var(--surface-2); border-color: var(--line-1)">
              {{ o.suggested_leverage || (o.leverage ? o.leverage + 'x' : '5x') }}
            </span>
          </div>

          <p class="text-xs text-[var(--ink-2)] leading-body">{{ o.reason || o.reasoning || '--' }}</p>

          <!-- 挂单点位：包含分批止盈 TP1 则为 4 列阶梯；否则维持经典 3 列 -->
          <div
            v-if="o.scale_out_tp"
            class="grid grid-cols-4 gap-2 pt-2 border-t text-3xs font-mono"
            style="border-color: var(--line-1)"
          >
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.entry') }}</span>
              <span class="font-bold text-[var(--ink-1)]">{{ fmtPrice(o.target_entry_price ?? o.entry_price) }}</span>
            </div>
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.stopLoss') }}</span>
              <span class="font-bold text-[var(--down)]">{{ fmtPrice(o.stop_loss_price) }}</span>
            </div>
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.scaleOutTp') }}</span>
              <span class="font-bold text-[var(--up)]">{{ fmtPrice(o.scale_out_tp) }}</span>
            </div>
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.finalTp') }}</span>
              <span class="font-bold text-[var(--up)]">{{ fmtPrice(o.take_profit_price) }}</span>
            </div>
          </div>
          <div
            v-else
            class="grid grid-cols-3 gap-2 pt-2 border-t text-3xs font-mono"
            style="border-color: var(--line-1)"
          >
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.entry') }}</span>
              <span class="font-bold text-[var(--ink-1)]">{{ fmtPrice(o.target_entry_price ?? o.entry_price) }}</span>
            </div>
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.stopLoss') }}</span>
              <span class="font-bold text-[var(--down)]">{{ fmtPrice(o.stop_loss_price) }}</span>
            </div>
            <div>
              <span class="text-[var(--ink-3)] block">{{ t('dash.radar.takeProfit') }}</span>
              <span class="font-bold text-[var(--up)]">{{ fmtPrice(o.take_profit_price) }}</span>
            </div>
          </div>
        </div>
      </div>

      <BaseEmpty v-if="!opps.length && !posMgmt.length" :text="t('dash.radar.empty')" />
    </div>

    <!-- 3. 投委会博弈纪要 -->
    <div
      v-else-if="tab === 'council'"
      id="radar-detail-panel-council"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="radar-detail-tab-council"
      class="space-y-3"
    >
      <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1)">
        <span class="text-xs font-bold uppercase tracking-wider text-[var(--ink-strong)]">
          {{ modeLabel }}
        </span>
        <span
          v-if="councilStatus?.duration_ms"
          class="text-3xs font-mono text-[var(--ink-3)]"
        >
          {{ t('dash.radar.totalTime', undefined, { n: (councilStatus.duration_ms / 1000).toFixed(2) }) }}
        </span>
      </div>

      <!-- CIO 仲裁决议 -->
      <div v-if="arbitrator" class="dsh-card-sub p-3.5 border-[var(--accent)]" style="border-width: 1px">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-2">
            <Scale class="h-4 w-4 text-[var(--accent)]" />
            <span class="text-xs font-bold text-[var(--ink-strong)]">{{ t('dash.radar.cioVerdict') }}</span>
            <span
              v-if="transcript?.adopted_role"
              class="rounded px-1.5 py-0.5 border text-3xs font-mono text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]"
            >
              {{ t('dash.radar.adopted', undefined, { role: seatLabel(transcript.adopted_role) }) }}
            </span>
          </div>
          <ConfBadge :value="arbitrator.confidence" />
        </div>
        <p class="text-xs text-[var(--ink-1)] leading-body whitespace-pre-wrap">{{ arbitrator.reasoning || arbitrator.summary || '--' }}</p>
      </div>

      <!-- 各交易员提案列表 -->
      <div class="space-y-2">
        <h4 class="text-3xs font-bold uppercase tracking-wider text-[var(--ink-3)]">{{ t('dash.radar.seatsIndependent') }}</h4>
        <div
          v-for="adv in advisorList"
          :key="adv.role_id || adv.name"
          class="dsh-card-sub p-3 space-y-2"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="font-mono font-bold text-xs text-[var(--ink-strong)]">{{ seatLabel(adv.role_id || adv.name) }}</span>
              <span v-if="adv.model_used" class="text-3xs font-mono text-[var(--ink-3)]">({{ adv.model_used }})</span>
              <span v-if="adv.status === 'error'" role="alert" class="text-3xs px-1.5 py-0.5 rounded bg-[var(--down-bg)] text-[var(--down)]">{{ t('dash.radar.statusDegraded') }}</span>
              <DirTag v-if="adv.action" :dir="dirOf(adv.action)" />
              <ConfBadge v-if="adv.confidence" :value="adv.confidence" />
            </div>
            <span v-if="adv.inst" class="text-3xs font-mono text-[var(--ink-2)]">{{ adv.inst }}</span>
          </div>
          <!-- 优先展示方案正文 content，若无则展示 reasoning/view -->
          <div v-if="adv.status === 'error'" role="alert" class="text-xs text-[var(--down)] bg-[var(--down-bg)]/30 p-2 rounded leading-body whitespace-pre-wrap">
            {{ adv.content || '--' }}
          </div>
          <div v-else class="space-y-1.5">
            <p v-if="adv.content" class="text-xs text-[var(--ink-1)] leading-body whitespace-pre-wrap font-mono select-text">{{ adv.content }}</p>
            <p v-else-if="adv.reasoning || adv.view" class="text-xs text-[var(--ink-2)] leading-body whitespace-pre-wrap">{{ adv.reasoning || adv.view }}</p>
            <p v-else class="text-xs text-[var(--ink-3)] leading-body">--</p>
            <details v-if="adv.content && adv.reasoning" class="text-3xs text-[var(--ink-3)] pt-1 cursor-pointer">
              <summary class="hover:text-[var(--accent)] select-none">{{ t('dash.radar.viewReasoning') }}</summary>
              <div class="mt-1 p-2 rounded bg-[var(--bg-sub)] text-xs text-[var(--ink-2)] leading-body whitespace-pre-wrap">{{ adv.reasoning }}</div>
            </details>
          </div>
        </div>
      </div>
    </div>

    <!-- 4. 三所价差与费率 -->
    <div
      v-else-if="tab === 'xvenue'"
      id="radar-detail-panel-xvenue"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="radar-detail-tab-xvenue"
      class="space-y-3"
    >
      <div class="overflow-x-auto">
        <table class="table w-full" :aria-label="t('dash.radar.detail.xvenue')">
          <thead>
            <tr>
              <th scope="col">{{ t('dash.radar.thSymbol') }}</th>
              <th scope="col" class="col-num">{{ t('dash.radar.xvenue.okxPrice') }}</th>
              <th scope="col" class="col-num">{{ t('dash.radar.thBasis') }}</th>
              <th scope="col" class="col-num">{{ t('dash.radar.thBasisGate') }}</th>
              <th scope="col" class="col-num">{{ t('dash.radar.thLs') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="xv in xvenueRows" :key="xv.symbol">
              <td class="font-mono font-bold text-xs text-[var(--ink-strong)]">{{ xv.symbol }}</td>
              <td class="col-num font-mono">{{ fmtPrice(xv.okx_last) }}</td>
              <td class="col-num font-mono" :class="Number(xv.bin_basis_pct || 0) >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'">
                {{ fmtNum(xv.bin_basis_pct, 3) }}%
              </td>
              <td class="col-num font-mono" :class="Number(xv.gate_basis_pct || 0) >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'">
                {{ fmtNum(xv.gate_basis_pct, 3) }}%
              </td>
              <td class="col-num font-mono text-[var(--ink-2)]">
                {{ fmtNum(xv.bin_ls, 2) }} / {{ fmtNum(xv.gate_ls, 2) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 5. 原始记录 -->
    <div
      v-else-if="tab === 'raw'"
      id="radar-detail-panel-raw"
      role="tabpanel"
      tabindex="0"
      aria-labelledby="radar-detail-tab-raw"
    >
      <BaseCodeBlock :code="JSON.stringify(c, null, 2)" />
    </div>
  </BaseDrawer>
</template>
