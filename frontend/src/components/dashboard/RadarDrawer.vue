<script setup lang="ts">
/** 决策审计抽屉：宏观研判 / 机会与持仓指令 / 委员会纪要 / 原始记录 */
import { computed, ref } from 'vue';
import BaseDrawer from '../base/BaseDrawer.vue';
import BaseTabs from '../base/BaseTabs.vue';
import BaseCollapse from '../base/BaseCollapse.vue';
import BaseCodeBlock from '../base/BaseCodeBlock.vue';
import BaseEmpty from '../base/BaseEmpty.vue';
import DirTag from '../base/DirTag.vue';
import ConfBadge from '../base/ConfBadge.vue';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtPrice } from '../../utils/format';

const props = defineProps<{ cycle: any | null }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const { t } = useI18n();
const tab = ref('macro');

const c = computed(() => props.cycle || {});
const opps = computed<any[]>(() => c.value.top_opportunities || []);
const posMgmt = computed<any[]>(() => c.value.position_management || []);
const transcript = computed(() => c.value.council_transcript);
const councilStatus = computed<any>(() => c.value.council_status || null);

const SEAT_LABELS: Record<string, string> = {
  trader_trend: '交易员A', trader_momentum: '交易员B', trader_quant: '交易员C',
  cio: 'CIO', REJECT_ALL: '全员驳回',
};
function seatLabel(id: any): string {
  const s = String(id || '');
  return SEAT_LABELS[s] || s || '';
}
const advisorList = computed<any[]>(() => Object.values(transcript.value?.advisors || {}));
const arbitrator = computed<any>(() => transcript.value?.arbitrator || null);
const modeLabel = computed(() => transcript.value?.consensus_mode === 'cross_examination'
  ? t('dash.radar.council.cross') : t('dash.radar.council.standard'));

const tabs = computed(() => {
  const items = [
    { key: 'macro', label: t('dash.radar.detail.macro') },
    { key: 'quotes', label: t('dash.radar.detail.quotes'), count: opps.value.length + posMgmt.value.length },
  ];
  if (transcript.value || councilStatus.value) items.push({ key: 'council', label: t('dash.radar.council.title') });
  items.push({ key: 'raw', label: t('dash.radar.detail.raw') });
  return items;
});

function dirOf(a: string): 'long' | 'short' | 'flat' {
  const u = String(a).toUpperCase();
  return u.includes('LONG') ? 'long' : u.includes('SHORT') ? 'short' : 'flat';
}
</script>

<template>
  <BaseDrawer
    :open="!!cycle"
    width="680px"
    :title="t('dash.radar.detail.title', undefined, { t: cycle?.time || '' })"
    :subtitle="cycle?.policy_version || ''"
    @close="emit('close')"
  >
    <BaseTabs v-model="tab" :items="tabs" class="mb-4" />

    <!-- 宏观研判 -->
    <div v-if="tab === 'macro'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <span v-if="councilStatus?.ran" class="badge badge-up">🏛️ {{ t('dash.radar.council.done') }} · {{ ((councilStatus.duration_ms || 0) / 1000).toFixed(1) }}s</span>
        <span v-else-if="councilStatus" class="badge badge-warn" :title="String(councilStatus.reason || '')">⚡ {{ t('dash.radar.council.degraded') }}</span>
      </div>
      <p v-if="councilStatus && !councilStatus.ran && councilStatus.reason" class="card-flat p-2.5 text-xs leading-relaxed" style="color: var(--warn, #d97706)">
        {{ t('dash.radar.council.reason') }}：{{ councilStatus.reason }}
      </p>
      <div class="card-flat p-3.5 text-sm leading-relaxed" style="color: var(--ink-1)">
        {{ c.macro_assessment || '--' }}
        <p v-if="c.ai_last_prompt" class="num t-faint mt-3 border-t pt-2 text-xs" style="border-color: var(--line-1)">
          {{ t('dash.shell.peek.chars', undefined, { n: (c.ai_last_prompt || '').length }) }} · {{ t('dash.shell.peek.title') }}
        </p>
      </div>
    </div>

    <!-- 机会与指令 -->
    <div v-else-if="tab === 'quotes'" class="space-y-4">
      <div v-if="posMgmt.length">
        <p class="t-label mb-1.5">{{ t('dash.radar.detail.verdict') }} · position_management</p>
        <div class="card overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
                <th>{{ t('dash.radar.col.action') }}</th>
                <th class="col-num">{{ t('dash.matrix.positions.col.sl') }}</th>
                <th>{{ t('dash.matrix.matrix.reason') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(m, i) in posMgmt" :key="'pm' + i">
                <td class="num font-semibold">{{ String(m.instId || '').split('-')[0] }}</td>
                <td><DirTag :dir="dirOf(m.action)" /></td>
                <td class="col-num t-faint">{{ m.suggested_sl_price ? fmtPrice(m.suggested_sl_price) : '--' }}</td>
                <td class="max-w-[300px] truncate text-xs" style="color: var(--ink-2)" :title="m.reason">{{ m.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="opps.length">
        <p class="t-label mb-1.5">{{ t('dash.radar.detail.quotes') }} · top_opportunities</p>
        <div class="card overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
                <th>{{ t('dash.radar.col.action') }}</th>
                <th class="col-num">{{ t('dash.radar.detail.plan') }}</th>
                <th class="col-num">R:R</th>
                <th>{{ t('dash.matrix.matrix.col.conf') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(o, i) in opps" :key="'op' + i">
                <td class="num font-semibold">{{ String(o.inst || '').split('-')[0] }}</td>
                <td>
                  <DirTag :dir="dirOf(o.action)" />
                  <span v-if="transcript && (o as any).council_adopted" class="badge badge-mono ms-1 text-2xs" :title="t('dash.radar.council.adopted')">
                    🏛️ {{ seatLabel((o as any).council_adopted) }}
                  </span>
                </td>
                <td class="col-num t-faint">{{ fmtNum(o.margin_usdt, 0) }} U · {{ o.leverage }}x</td>
                <td class="col-num">{{ o.risk_reward_ratio || '--' }}</td>
                <td><ConfBadge :value="o.confidence" /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <BaseEmpty v-if="!posMgmt.length && !opps.length" :text="t('dash.radar.detail.waitNote')" />
    </div>

    <!-- 委员会纪要 -->
    <!-- 委员会纪要 -->
    <div v-else-if="tab === 'council'" class="space-y-2">
      <template v-if="transcript">
        <div class="card-flat flex flex-wrap items-center gap-2 p-3 text-xs">
          <span class="badge badge-info">🏛️ {{ modeLabel }}</span>
          <span class="t-faint">{{ t('dash.radar.council.totalTime', undefined, { n: ((transcript.total_duration_ms || 0) / 1000).toFixed(1) }) }}</span>
          <span v-if="advisorList.length" class="t-faint">
            {{ t('dash.radar.council.seatsOk', undefined, { ok: advisorList.filter((a: any) => a.status !== 'error').length, n: advisorList.length }) }}
          </span>
        </div>
        <div v-for="(a, i) in advisorList" :key="'adv' + i" class="card-flat p-3"
             :style="a.status === 'error' ? 'border-left: 2px solid var(--down)' : 'border-left: 2px solid var(--accent-line)'">
          <p class="text-xs font-bold" :style="{ color: a.status === 'error' ? 'var(--down)' : 'var(--ink-strong)' }">
            {{ a.role_name }}
            <span v-if="a.model_used" class="badge badge-mono ms-1 text-2xs">{{ a.model_used }}</span>
            <span v-if="a.latency_ms" class="t-faint fw-normal ms-1">· {{ (a.latency_ms / 1000).toFixed(1) }}s</span>
          </p>
          <p class="mt-1 text-xs leading-relaxed whitespace-pre-wrap" style="color: var(--ink-2)">{{ String(a.content || '--').slice(0, 1200) }}<span v-if="String(a.content || '').length > 1200"> …</span></p>
        </div>
        <div v-if="arbitrator" class="card-flat p-3" style="border-left: 2px solid var(--accent)">
          <p class="text-xs font-bold" style="color: var(--ink-strong)">
            ⚖️ {{ arbitrator.role_name }}
            <span v-if="arbitrator.model_used" class="badge badge-mono ms-1 text-2xs">{{ arbitrator.model_used }}</span>
          </p>
          <p class="mt-1 text-xs leading-relaxed whitespace-pre-wrap" style="color: var(--ink-1)">{{ arbitrator.reasoning || '--' }}</p>
        </div>
        <BaseCollapse>
          <template #head><span class="text-xs" style="color: var(--ink-2)">{{ t('dash.radar.council.raw') }}</span></template>
          <div class="p-2"><BaseCodeBlock :code="JSON.stringify(transcript, null, 2)" max-height="36vh" /></div>
        </BaseCollapse>
      </template>
      <div v-else class="card-flat p-3 text-xs leading-relaxed" style="color: var(--warn, #d97706)">
        {{ t('dash.radar.council.notRan') }}：{{ councilStatus?.reason || '--' }}
      </div>
    </div>

    <!-- 原始记录 -->
    <div v-else>
      <BaseCollapse :default-open="true">
        <template #head><span class="text-sm">{{ t('dash.radar.detail.raw') }} JSON</span></template>
        <div class="p-2"><BaseCodeBlock :code="JSON.stringify(cycle, null, 2)" max-height="52vh" /></div>
      </BaseCollapse>
    </div>
  </BaseDrawer>
</template>
