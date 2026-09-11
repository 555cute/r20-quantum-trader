<script setup lang="ts">
/**
 * 自进化认知中枢实验室（Evolution & Cognitive Adaptation Lab）
 * 1. 认知适应度 HUD：样本量、胜率、盈亏比 PF、记忆突变状态
 * 2. 假设回测与数理快照审计脉络 (Hypothesis Backtest Visualizer & Snapshot Pipeline)
 * 3. 归因洞察矩阵与行动计划 (Diagnosis Insights & Actions)
 * 4. 黄金心法突变与宪法保护体系 (Core Lessons & Constitutional Protection)
 */
import { computed } from 'vue';
import {
  Dna,
  ShieldCheck,
  Sparkles,
  BrainCircuit,
  ListChecks,
  Activity,
  GitCommit,
  CheckCircle2,
  AlertTriangle,
  Flame,
} from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtDateTime } from '../../utils/format';
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

function insTitle(it: any): string {
  if (typeof it === 'string') {
    const i = it.indexOf('：');
    if (i > 0 && i <= 24) return it.slice(0, i);
    return '';
  }
  return it?.dimension || it?.title || '';
}

function insBody(it: any): string {
  if (typeof it === 'string') {
    const i = it.indexOf('：');
    return i > 0 && i <= 24 ? it.slice(i + 1) : it;
  }
  const direct = it?.observation || it?.detail || it?.text || it?.action || it?.analysis
    || it?.content || it?.finding || it?.summary || it?.description || it?.reason;
  if (direct) return String(direct);
  if (it && typeof it === 'object') {
    return Object.entries(it)
      .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
      .map(([k, v]) => `${k}: ${v}`)
      .join('；');
  }
  return String(it ?? '');
}

const actions = computed<any[]>(() => review.value.actions_taken || []);
const snapAudit = computed<any>(() => review.value.snapshot_audit || null);

function actText(a: any): string {
  if (typeof a === 'string') return a;
  if (a && typeof a === 'object') {
    const text = a.action || a.text || a.content || a.summary || a.description;
    if (text) return a.action_type ? `【${a.action_type}】${text}` : String(text);
    return Object.entries(a)
      .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
      .map(([k, v]) => `${k}: ${v}`)
      .join('；');
  }
  return String(a ?? '');
}

const rules = computed(() => {
  const list: string[] = review.value.core_lessons || [];
  return list.map((raw) => {
    const m = String(raw).match(/^【(.+?)】([\s\S]*)$/);
    return m ? { title: m[1], body: m[2].trim() } : { title: '', body: String(raw).trim() };
  });
});

const md = computed(() => (store.data as any)?.ai_trading_memory_md || '');

// 适应度评估分数（胜率 × 盈亏比归一化）
const fitnessScore = computed(() => {
  const wr = Number(review.value.win_rate ?? 0);
  const pf = Number(review.value.profit_factor ?? 0);
  if (wr <= 0 && pf <= 0) return null;
  // 简易综合适应度: (wr% * 0.5) + (min(pf, 3) / 3 * 50)
  const score = Math.round((wr * 0.5) + (Math.min(pf, 3) / 3 * 50));
  return Math.max(0, Math.min(100, score));
});
</script>

<template>
  <div class="space-y-3.5">
    <PageHead :title="t('dash.evolution.title')" :desc="t('dash.evolution.desc')" />

    <!-- 1. 认知适应度 HUD (Cognitive Fitness Hub) -->
    <div class="card grid grid-cols-2 gap-2 p-2 sm:p-2.5 md:grid-cols-5 xl:gap-0 xl:p-0">
      <BaseStat
        :label="t('dash.evolution.hud.at')"
        :value="review.timestamp ? fmtDateTime(review.timestamp).slice(5, 16) : '--'"
        hint="最近一次自进化心法复盘时间"
      />
      <BaseStat
        :label="t('dash.evolution.hud.sample')"
        :value="review.total_trades != null ? `${fmtNum(review.total_trades, 0)} ${t('common.unitCount')}` : '--'"
        hint="闭环台账实盘样本切片"
      />
      <BaseStat
        :label="t('dash.evolution.hud.winRate')"
        :value="review.win_rate != null ? fmtNum(review.win_rate, 1) + '%' : '--'"
        :delta-tone="(review.win_rate ?? 0) >= 50 ? 'up' : 'down'"
        hint="周期内实盘胜率"
      />
      <BaseStat
        :label="t('dash.evolution.hud.pf')"
        :value="review.profit_factor != null ? fmtNum(review.profit_factor, 2) : '--'"
        :delta="(review.profit_factor ?? 0) >= 1 ? t('common.ge') + ' 1.0' : undefined"
        :delta-tone="(review.profit_factor ?? 0) >= 1 ? 'up' : 'down'"
        :hint="t('dash.ledger.summary.tipPf')"
      />
      <BaseStat :label="t('dash.evolution.hud.status')" value="" hint="参数与记忆突变状态">
        <template #extra>
          <div class="flex items-center gap-1.5">
            <span class="badge" :class="statusMeta.cls">{{ statusMeta.label }}</span>
            <span v-if="fitnessScore !== null" class="badge badge-mono text-3xs font-bold" :class="fitnessScore >= 60 ? 'badge-up' : 'badge-warn'">
              FIT {{ fitnessScore }}
            </span>
          </div>
        </template>
      </BaseStat>
    </div>

    <BaseEmpty v-if="!hasReview" :text="t('dash.evolution.hud.empty')" />

    <div v-else class="space-y-3.5">
      <!-- 2. 假设回测与数理快照进化脉络条 (Evolution Pipeline Visualizer) -->
      <div class="card p-3.5 border" style="border-color: var(--line-1); background-color: var(--surface-1)">
        <div class="flex items-center justify-between border-b pb-2 mb-3" style="border-color: var(--line-1)">
          <div class="flex items-center gap-1.5 text-xs font-bold text-[var(--ink-strong)]">
            <Activity class="h-4 w-4 text-[var(--accent)]" />
            <span>自进化认知适应性流水线</span>
          </div>
          <span class="badge text-3xs" style="background: var(--surface-3); color: var(--ink-2)">
            6小时自动进化触发
          </span>
        </div>

        <!-- 四步阶段脉络条 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-2xs">
          <!-- 阶段 1 -->
          <div class="p-2.5 rounded-lg border flex flex-col justify-between gap-1" style="background-color: var(--surface-2); border-color: var(--line-1)">
            <div class="flex items-center justify-between">
              <span class="t-label">01 样本切片提取</span>
              <CheckCircle2 class="h-3.5 w-3.5 text-[var(--up)]" />
            </div>
            <div class="num font-bold text-xs" style="color: var(--ink-1)">
              {{ review.total_trades != null ? `${review.total_trades} 笔台账入库` : '--' }}
            </div>
            <span class="t-faint text-[10px]">穿透历史胜率与盈亏比</span>
          </div>

          <!-- 阶段 2 -->
          <div class="p-2.5 rounded-lg border flex flex-col justify-between gap-1" style="background-color: var(--surface-2); border-color: var(--line-1)">
            <div class="flex items-center justify-between">
              <span class="t-label">02 数理快照审计</span>
              <CheckCircle2 class="h-3.5 w-3.5 text-[var(--up)]" />
            </div>
            <div class="num font-bold text-xs" style="color: var(--ink-1)">
              {{ snapAudit ? `${snapAudit.math_observable ?? 0} 可观测指标` : '数理对齐完毕' }}
            </div>
            <span class="t-faint text-[10px]">v / a / j / I 确定性动力学</span>
          </div>

          <!-- 阶段 3 -->
          <div class="p-2.5 rounded-lg border flex flex-col justify-between gap-1" style="background-color: var(--surface-2); border-color: var(--line-1)">
            <div class="flex items-center justify-between">
              <span class="t-label">03 适应度评估</span>
              <CheckCircle2 class="h-3.5 w-3.5 text-[var(--up)]" />
            </div>
            <div class="num font-bold text-xs" style="color: var(--ink-1)">
              {{ statusMeta.label }}
            </div>
            <span class="t-faint text-[10px]">综合置信度与回撤门禁</span>
          </div>

          <!-- 阶段 4 -->
          <div class="p-2.5 rounded-lg border flex flex-col justify-between gap-1" style="background-color: var(--surface-2); border-color: var(--line-1)">
            <div class="flex items-center justify-between">
              <span class="t-label">04 长期记忆突变</span>
              <ShieldCheck class="h-3.5 w-3.5 text-[var(--accent)]" />
            </div>
            <div class="num font-bold text-xs" style="color: var(--ink-1)">
              {{ rules.length }} 条心法 · 宪法保护
            </div>
            <span class="t-faint text-[10px]">大模型无权破坏基准心法</span>
          </div>
        </div>
      </div>

      <!-- 3. 主体分栏：左·裁决与归因与行动 / 右·黄金心法库 -->
      <div class="grid grid-cols-1 gap-3.5 xl:grid-cols-12">
        <!-- 左：裁决 / 归因 / 行动 -->
        <div class="space-y-3.5 xl:col-span-7">
          <!-- 裁决逻辑 -->
          <div class="section">
            <div class="section-head">
              <div>
                <h2 class="section-title">
                  <BrainCircuit class="h-4 w-4" style="color: var(--accent)" />
                  {{ t('dash.evolution.rationale.title') }}
                </h2>
                <p class="section-desc">{{ t('dash.evolution.rationale.desc') }}</p>
              </div>
              <span v-if="review.mode" class="badge badge-mono hidden sm:inline-flex">{{ review.mode }}</span>
            </div>
            <div class="section-body">
              <p class="text-xs md:text-sm leading-relaxed" style="color: var(--ink-1)">
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

          <!-- 归因洞察 -->
          <div class="section">
            <div class="section-head">
              <div>
                <h2 class="section-title">
                  <Sparkles class="h-4 w-4" style="color: var(--accent)" />
                  {{ t('dash.evolution.insights.title') }}
                </h2>
                <p class="section-desc">{{ t('dash.evolution.insights.desc') }}</p>
              </div>
              <span class="badge num">{{ insights.length }}</span>
            </div>
            <div class="section-body space-y-2">
              <BaseEmpty v-if="!insights.length" :text="t('dash.evolution.insights.empty')" />
              <div v-for="(it, i) in insights" :key="i" class="card-flat p-3 transition-colors hover:bg-[var(--surface-2)]">
                <p class="text-xs sm:text-sm font-semibold" style="color: var(--ink-strong)">
                  <span class="num me-1.5 t-faint">{{ String(i + 1).padStart(2, '0') }}</span>{{ insTitle(it) }}
                </p>
                <p class="mt-1 text-2xs sm:text-xs leading-relaxed" style="color: var(--ink-2)">{{ insBody(it) }}</p>
              </div>
            </div>
          </div>

          <!-- 落地行动项 -->
          <div class="section">
            <div class="section-head">
              <h2 class="section-title">
                <ListChecks class="h-4 w-4" style="color: var(--accent)" />
                {{ t('dash.evolution.actions.title') }}
              </h2>
              <span class="badge num">{{ actions.length }}</span>
            </div>
            <div class="section-body">
              <p v-if="!actions.length" class="t-faint text-xs">{{ t('dash.evolution.actions.empty') }}</p>
              <ol v-else class="space-y-2">
                <li
                  v-for="(a, i) in actions"
                  :key="i"
                  class="flex items-start gap-2.5 text-xs sm:text-sm leading-relaxed p-2 rounded-lg"
                  style="background-color: var(--surface-1)"
                >
                  <span class="num t-faint shrink-0 font-bold mt-0.5">{{ i + 1 }}.</span>
                  <span style="color: var(--ink-1)">{{ actText(a) }}</span>
                </li>
              </ol>
            </div>
          </div>
        </div>

        <!-- 右：心法库 + 宪法护栏 -->
        <div class="space-y-3.5 xl:col-span-5">
          <!-- 黄金心法库 -->
          <div class="section">
            <div class="section-head">
              <div>
                <h2 class="section-title">
                  <Dna class="h-4 w-4" style="color: var(--accent)" />
                  {{ t('dash.evolution.memory.title') }}
                </h2>
                <p class="section-desc">{{ t('dash.evolution.memory.desc') }}</p>
              </div>
              <span class="badge badge-accent num">{{ t('dash.evolution.memory.rules', undefined, { n: rules.length }) }}</span>
            </div>
            <div class="section-body space-y-2">
              <BaseEmpty v-if="!rules.length" :text="t('dash.evolution.memory.empty')" />
              <div
                v-for="(r, i) in rules"
                :key="i"
                class="card-flat p-3 border-l-2 transition-colors hover:bg-[var(--surface-2)]"
                style="border-left-color: var(--accent)"
              >
                <div class="flex items-center gap-1.5">
                  <Flame class="h-3.5 w-3.5 text-[var(--accent)]" />
                  <p class="text-xs sm:text-sm font-bold" style="color: var(--ink-strong)">
                    {{ r.title || t('dash.evolution.memory.dimension') }}
                  </p>
                </div>
                <p class="mt-1.5 text-2xs sm:text-xs leading-relaxed" style="color: var(--ink-2)">{{ r.body }}</p>
              </div>

              <!-- 原始 Markdown 快照展开 -->
              <BaseCollapse>
                <template #head>
                  <span class="text-xs font-medium" style="color: var(--ink-2)">
                    {{ t('dash.evolution.memory.dev') }} · {{ t('dash.evolution.memory.devDesc') }}
                  </span>
                </template>
                <div class="p-2"><BaseCodeBlock :code="md" max-height="320px" /></div>
              </BaseCollapse>
            </div>
          </div>

          <!-- 宪法保护安全哨兵卡片 -->
          <div class="section">
            <div class="section-body flex items-start gap-3">
              <ShieldCheck
                class="h-5 w-5 shrink-0 mt-0.5"
                :style="{ color: review.memory_preserved !== false ? 'var(--up)' : 'var(--warn)' }"
              />
              <div class="min-w-0">
                <p class="text-xs sm:text-sm font-semibold" style="color: var(--ink-strong)">
                  {{ t('dash.evolution.guard.title') }}
                  <span class="badge badge-up ms-1 text-2xs">{{ t('dash.evolution.guard.on') }}</span>
                </p>
                <p class="t-faint text-2xs mt-0.5">{{ t('dash.evolution.guard.desc') }}</p>
                <p v-if="snapAudit" class="mt-1.5 text-2xs" style="color: var(--ink-2)">
                  {{ t('dash.evolution.guard.snapshot') }}：
                  {{ t('dash.evolution.guard.snapshotCounts', undefined, { observed: snapAudit.math_observable ?? 0, total: snapAudit.total ?? 0, priceOnly: snapAudit.PRICE_ONLY ?? 0, none: snapAudit.NONE ?? 0 }) }}
                  <span v-if="(review.baseline_memory_protected ?? 0) > 0" class="badge badge-warn ms-1 text-3xs">
                    {{ t('dash.evolution.guard.baselineProtected', undefined, { n: review.baseline_memory_protected }) }}
                  </span>
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
