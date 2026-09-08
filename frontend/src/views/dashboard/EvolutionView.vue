<script setup lang="ts">
/**
 * 自进化视图：复盘 HUD → 左·裁决与归因与行动 / 右·黄金心法库。
 * 数据源：/api/all 的 review + ai_trading_memory_md（AI 每 6 小时覆写）。
 */
import { computed } from 'vue';
import { Dna, ShieldCheck, Sparkles, BrainCircuit, ListChecks } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum } from '../../utils/format';
import PageHead from '../../components/dashboard/PageHead.vue';
import BaseStat from '../../components/base/BaseStat.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import BaseCollapse from '../../components/base/BaseCollapse.vue';
import BaseCodeBlock from '../../components/base/BaseCodeBlock.vue';

const store = useDashboardStore();
const { t } = useI18n();

const review = computed<any>(() => (store.data as any)?.review || {});
const hasReview = computed(() => !!review.value?.timestamp);

const statusKey = computed(() => String(review.value.change_status || ''));
const statusMeta = computed(() => {
  const s = statusKey.value;
  if (s === 'CHANGED') return { cls: 'badge-up', label: t('dash.evolution.hud.statuses.CHANGED') };
  if (s === 'NO_CHANGE') return { cls: 'badge-info', label: t('dash.evolution.hud.statuses.NO_CHANGE') };
  if (s === 'RUNNING') return { cls: 'badge-warn', label: t('dash.evolution.hud.statuses.RUNNING') };
  if (s) return { cls: 'badge-down', label: t('dash.evolution.hud.statuses.FAILED') };
  return { cls: '', label: '--' };
});

const insights = computed<any[]>(() => review.value.diagnosis_insights || review.value.insights || []);
const actions = computed<string[]>(() => review.value.actions_taken || []);

/** 心法解析：【标题】正文 */
const rules = computed(() => {
  const list: string[] = review.value.core_lessons || [];
  return list.map((raw) => {
    const m = String(raw).match(/^【(.+?)】([\s\S]*)$/);
    return m ? { title: m[1], body: m[2].trim() } : { title: '', body: String(raw).trim() };
  });
});
const md = computed(() => (store.data as any)?.ai_trading_memory_md || '');
</script>

<template>
  <div class="space-y-3">
    <PageHead :title="t('dash.evolution.title')" :desc="t('dash.evolution.desc')" />

    <!-- 复盘 HUD -->
    <div class="card grid grid-cols-2 gap-2 p-2 md:grid-cols-5 xl:gap-0 xl:p-0">
      <BaseStat
        :label="t('dash.evolution.hud.at')"
        :value="review.timestamp ? review.timestamp.slice(5, 16) : '--'"
       
      />
      <BaseStat
        :label="t('dash.evolution.hud.sample')"
        :value="review.total_trades != null ? `${fmtNum(review.total_trades, 0)} ${t('common.unitCount')}` : '--'"
       
      />
      <BaseStat
        :label="t('dash.evolution.hud.winRate')"
        :value="review.win_rate != null ? fmtNum(review.win_rate, 1) + '%' : '--'"
        :delta-tone="(review.win_rate ?? 0) >= 50 ? 'up' : 'down'"
       
      />
      <BaseStat
        :label="t('dash.evolution.hud.pf')"
        :value="review.profit_factor != null ? fmtNum(review.profit_factor, 2) : '--'"
        :delta="(review.profit_factor ?? 0) >= 1 ? t('common.ge') + ' 1' : undefined"
        :delta-tone="(review.profit_factor ?? 0) >= 1 ? 'up' : 'down'"
        :hint="t('dash.ledger.summary.tipPf')"
       
      />
      <BaseStat :label="t('dash.evolution.hud.status')" value="">
        <template #extra>
          <span class="badge" :class="statusMeta.cls">{{ statusMeta.label }}</span>
        </template>
      </BaseStat>
    </div>

    <BaseEmpty v-if="!hasReview" :text="t('dash.evolution.hud.empty')" />

    <div v-else class="grid grid-cols-1 gap-3 xl:grid-cols-12">
      <!-- 左：裁决 / 归因 / 行动 -->
      <div class="space-y-3 xl:col-span-7">
        <div class="section">
          <div class="section-head">
            <div>
              <h2 class="section-title"><BrainCircuit class="h-4 w-4" style="color: var(--accent)" />{{ t('dash.evolution.rationale.title') }}</h2>
              <p class="section-desc">{{ t('dash.evolution.rationale.desc') }}</p>
            </div>
            <span v-if="review.mode" class="badge badge-mono hidden sm:inline-flex">{{ review.mode }}</span>
          </div>
          <div class="section-body">
            <p class="text-sm leading-relaxed" style="color: var(--ink-1)">
              {{ review.memory_overwrites_reason || '--' }}
            </p>
            <div
              v-if="review.llm_error"
              class="mt-3 rounded-lg border p-2.5 text-xs"
              style="border-color: var(--down-line); background-color: var(--down-bg); color: var(--down)"
            >
              {{ review.llm_error }}
            </div>
          </div>
        </div>

        <div class="section">
          <div class="section-head">
            <div>
              <h2 class="section-title"><Sparkles class="h-4 w-4" style="color: var(--accent)" />{{ t('dash.evolution.insights.title') }}</h2>
              <p class="section-desc">{{ t('dash.evolution.insights.desc') }}</p>
            </div>
            <span class="badge num">{{ insights.length }}</span>
          </div>
          <div class="section-body space-y-2">
            <BaseEmpty v-if="!insights.length" :text="t('dash.evolution.insights.empty')" />
            <div v-for="(it, i) in insights" :key="i" class="card-flat p-3">
              <p class="text-sm font-semibold" style="color: var(--ink-strong)">
                <span class="num me-1.5 t-faint">{{ String(i + 1).padStart(2, '0') }}</span>{{ it.dimension }}
              </p>
              <p class="mt-1 text-xs leading-relaxed" style="color: var(--ink-2)">{{ it.observation }}</p>
            </div>
          </div>
        </div>

        <div class="section">
          <div class="section-head">
            <h2 class="section-title"><ListChecks class="h-4 w-4" style="color: var(--accent)" />{{ t('dash.evolution.actions.title') }}</h2>
            <span class="badge num">{{ actions.length }}</span>
          </div>
          <div class="section-body">
            <p v-if="!actions.length" class="t-faint text-sm">{{ t('dash.evolution.actions.empty') }}</p>
            <ol v-else class="space-y-2">
              <li v-for="(a, i) in actions" :key="i" class="flex gap-2.5 text-sm leading-relaxed" style="color: var(--ink-1)">
                <span class="num t-faint shrink-0">{{ i + 1 }}.</span>
                <span>{{ a }}</span>
              </li>
            </ol>
          </div>
        </div>
      </div>

      <!-- 右：心法库 + 护栏 -->
      <div class="space-y-3 xl:col-span-5">
        <div class="section">
          <div class="section-head">
            <div>
              <h2 class="section-title"><Dna class="h-4 w-4" style="color: var(--accent)" />{{ t('dash.evolution.memory.title') }}</h2>
              <p class="section-desc">{{ t('dash.evolution.memory.desc') }}</p>
            </div>
            <span class="badge badge-accent num">{{ t('dash.evolution.memory.rules', undefined, { n: rules.length }) }}</span>
          </div>
          <div class="section-body space-y-2">
            <BaseEmpty v-if="!rules.length" :text="t('dash.evolution.memory.empty')" />
            <div v-for="(r, i) in rules" :key="i" class="card-flat p-3" style="border-left: 2px solid var(--accent-line)">
              <p class="text-sm font-semibold" style="color: var(--ink-strong)">{{ r.title || t('dash.evolution.memory.dimension') }}</p>
              <p class="mt-1 text-xs leading-relaxed" style="color: var(--ink-2)">{{ r.body }}</p>
            </div>
            <BaseCollapse>
              <template #head><span class="text-xs" style="color: var(--ink-2)">{{ t('dash.evolution.memory.dev') }} · {{ t('dash.evolution.memory.devDesc') }}</span></template>
              <div class="p-2"><BaseCodeBlock :code="md" max-height="320px" /></div>
            </BaseCollapse>
          </div>
        </div>

        <div class="section">
          <div class="section-body flex items-center gap-3">
            <ShieldCheck class="h-5 w-5 shrink-0" :style="{ color: review.memory_preserved !== false ? 'var(--up)' : 'var(--warn)' }" />
            <div class="min-w-0">
              <p class="text-sm font-semibold" style="color: var(--ink-strong)">
                {{ t('dash.evolution.guard.title') }}
                <span class="badge badge-up ms-1">{{ t('dash.evolution.guard.on') }}</span>
              </p>
              <p class="t-faint text-xs">{{ t('dash.evolution.guard.desc') }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
