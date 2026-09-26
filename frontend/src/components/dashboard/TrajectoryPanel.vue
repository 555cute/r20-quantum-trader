<script setup lang="ts">
/**
 * TrajectoryPanel.vue · AstraQuant 决策轨迹与执行日志面板
 * 实时白盒化展示多模型委员会决策推演、动力学裁决与底层执行日志
 */
import { ref, computed, onBeforeUnmount, watch } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { useModalFocus } from '../../composables/useModalFocus';
import { useRovingTabs } from '../../composables/useRovingTabs';
import {
  X,
  Activity,
  Terminal,
  ShieldCheck,
  Zap,
} from 'lucide-vue-next';

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const store = useDashboardStore();
const { t } = useI18n();

/**
 * 批 42：本面板声明了 `role="dialog" aria-modal="true"`，却**一样焦点行为都没有** ——
 * Escape 关不掉（全站唯一关不掉的模态）、Tab 会走到遮罩后的背景页、
 * 打开后焦点仍在顶栏触发按钮上、背景还能滚。改为接上 `useModalFocus`
 * （与 BaseDialog / BaseDrawer 同一份实现）。
 */
const panel = ref<HTMLElement | null>(null);
const { sync: syncModalFocus, release: releaseModalFocus } = useModalFocus(panel, () => emit('close'));
watch(() => props.open, syncModalFocus);
onBeforeUnmount(releaseModalFocus);

const activeTab = ref<'decisions' | 'logs'>('decisions');

/** 批 66：两个页签的漫游 tabindex 与方向键导航（此前两者都缺）。 */
const TAB_ORDER: Array<'decisions' | 'logs'> = ['decisions', 'logs'];
const { setRef: setTabRef, onKeydown: onTabKey, roving: tabRoving } = useRovingTabs(
  () => TAB_ORDER.length,
  (i) => { activeTab.value = TAB_ORDER[i]; },
);
const logFilter = ref<'all' | 'warn' | 'error'>('all');

// 决策流列表提取
const decisionStream = computed(() => {
  const factors = store.factors || [];
  return factors.map((f: any) => {
    const d = f.decision || {};
    const act = (d.action || f.action || 'HOLD').toUpperCase();
    const rawConf = d.confidence ?? f.confidence ?? 0;
    const conf = typeof rawConf === 'number'
      ? (rawConf <= 1 && rawConf > 0 ? Math.round(rawConf * 100) : Math.round(rawConf))
      : 0;
    return {
      instId: f.instId,
      symbol: f.symbol || f.instId?.replace('-USDT-SWAP', '') || f.instId,
      action: act,
      confidence: conf,
      price: f.price,
      reason: d.summary_reason || f.reason || t('dash.shell.panel.computing'),
      velocity: f.calculus?.velocity_1h ?? 0,
      acceleration: f.calculus?.accel_1h ?? 0,
      adx: f.adx_1h ?? 0,
      leverage: d.leverage,
      tp: d.take_profit_price,
      sl: d.stop_loss_price,
      reasoning: d.reasoning || '',
      marketStructure: d.market_structure || '',
      calculusDynamics: d.calculus_dynamics || '',
      mathProbRationale: d.math_prob_rationale || '',
      updatedAt: f.updated_at || new Date().toISOString(),
    };
  });
});

const marketRegime = computed(() => (store.data as any)?.market_regime || null);

// 日志流提取与过滤
const filteredLogs = computed(() => {
  const rawLogs = store.logs || [];
  if (logFilter.value === 'all') return rawLogs;
  return rawLogs.filter((line: string) => {
    const l = line.toLowerCase();
    if (logFilter.value === 'error') return l.includes('err') || l.includes('fail') || l.includes('except');
    if (logFilter.value === 'warn') return l.includes('warn') || l.includes('err') || l.includes('fail');
    return true;
  });
});

function actionBadgeClass(action: string) {
  if (action === 'BUY' || action === 'LONG') {
    return 'bg-[var(--up-bg)] text-[var(--up)] border-[var(--up-line)]';
  }
  if (action === 'SELL' || action === 'SHORT') {
    return 'bg-[var(--down-bg)] text-[var(--down)] border-[var(--down-line)]';
  }
  return 'bg-[var(--surface-3)] text-[var(--ink-2)] border-[var(--line-1)]';
}
</script>

<template>
  <Teleport to="body">
    <!-- 抽屉遮罩 -->
    <Transition name="fade">
      <div
        v-if="open"
        class="fixed inset-0 z-[var(--z-drawer)] bg-black/60 backdrop-blur-xs cursor-pointer"
        @click="emit('close')"
      />
    </Transition>

    <!-- 右侧滑出抽屉 -->
    <Transition name="slide-right">
      <aside
        v-if="open"
        ref="panel"
        tabindex="-1"
        class="fixed inset-y-0 right-0 z-[var(--z-drawer)] flex w-full max-w-[500px] flex-col border-s shadow-2xl outline-none transition-transform"
        style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1)"
        role="dialog"
        aria-modal="true"
        :aria-label="t('dash.shell.panel.aria')"
      >
        <!-- 面板头部 -->
        <header
          class="flex items-center justify-between border-b px-4 py-3"
          style="background-color: var(--surface-head); border-color: var(--line-1)"
        >
          <div class="flex items-center gap-2.5">
            <span class="flex h-7 w-7 items-center justify-center rounded border" style="background-color: var(--surface-2); border-color: var(--line-1)">
              <Activity class="h-4 w-4 text-[var(--accent)]" />
            </span>
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-sm font-semibold tracking-tight" style="color: var(--ink-strong)">
                  {{ t('dash.shell.panel.streamTitle') }}
                </h2>
                <span class="dsh-status-dot active" :title="t('dash.shell.nav.liveDot')" />
              </div>
              <p class="text-3xs" style="color: var(--ink-3)">
                {{ t('dash.shell.panel.streamDesc') }}
              </p>
            </div>
          </div>

          <div class="flex items-center gap-1.5">
            <kbd class="hidden sm:inline-flex">Esc</kbd>
            <button type="button"
              class="btn btn-quiet btn-icon cursor-pointer h-7 w-7"
              :title="`${t('dash.shell.panel.closeAria')} (Esc)`"
              :aria-label="t('dash.shell.panel.closeAria')"
              @click="emit('close')"
            >
              <X class="h-4 w-4" />
            </button>
          </div>
        </header>

        <!-- 选项卡与控制条 -->
        <div
          class="flex items-center justify-between border-b px-4 py-2"
          style="background-color: var(--surface-2); border-color: var(--line-1)"
        >
          <!-- Tabs（批 44：页签给语义，读屏器才知道"当前在第几个视图"） -->
          <div class="flex items-center gap-1" role="tablist" :aria-label="t('dash.shell.panel.decisionFlow')">
            <button
              type="button"
              role="tab"
              :ref="setTabRef(0)"
              :tabindex="tabRoving(activeTab === 'decisions')"
              :aria-selected="activeTab === 'decisions'"
              class="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium cursor-pointer transition-colors"
              :class="
                activeTab === 'decisions'
                  ? 'bg-[var(--surface-3)] text-[var(--ink-strong)]'
                  : 'text-[var(--ink-2)] hover:bg-[var(--ds-color-bg-hover)] hover:text-[var(--ink-1)]'
              "
              @click="activeTab = 'decisions'"
              @keydown="onTabKey($event, 0)"
            >
              <Zap class="h-3.5 w-3.5" />
              {{ t('dash.shell.panel.decisionFlow') }}
              <span class="rounded px-1 text-3xs font-mono" style="background-color: var(--surface-1); color: var(--ink-2)">
                {{ decisionStream.length }}
              </span>
            </button>

            <button
              type="button"
              role="tab"
              :ref="setTabRef(1)"
              :tabindex="tabRoving(activeTab === 'logs')"
              :aria-selected="activeTab === 'logs'"
              class="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium cursor-pointer transition-colors"
              :class="
                activeTab === 'logs'
                  ? 'bg-[var(--surface-3)] text-[var(--ink-strong)]'
                  : 'text-[var(--ink-2)] hover:bg-[var(--ds-color-bg-hover)] hover:text-[var(--ink-1)]'
              "
              @click="activeTab = 'logs'"
              @keydown="onTabKey($event, 1)"
            >
              <Terminal class="h-3.5 w-3.5" />
              {{ t('dash.shell.panel.liveLog') }}
            </button>
          </div>

          <!-- 日志过滤器 -->
          <div v-if="activeTab === 'logs'" class="flex items-center gap-1">
            <button type="button"
              v-for="filter in ['all', 'warn', 'error'] as const"
              :key="filter"
              class="rounded border px-2 py-0.5 text-3xs font-medium uppercase cursor-pointer transition-colors min-h-[var(--h-sm)]"
              :class="
                logFilter === filter
                  ? 'bg-[var(--accent-bg)] text-[var(--accent)] border-[var(--accent-line)]'
                  : 'text-[var(--ink-3)] border-transparent hover:bg-[var(--ds-color-bg-hover)] hover:text-[var(--ink-1)]'
              "
              @click="logFilter = filter"
            >
              {{ filter }}
            </button>
          </div>
        </div>

        <!-- 面板内容区 -->
        <div class="flex-1 overflow-y-auto p-4">
          <!-- TAB 1: 决策流 -->
          <div v-if="activeTab === 'decisions'" class="space-y-3">
            <!-- 宏观市场体制自适应徽章 -->
            <div v-if="marketRegime" class="rounded border p-2.5 text-xs font-mono" style="background-color: var(--surface-2); border-color: var(--line-1)">
              <div class="flex items-center justify-between">
                <span class="font-bold flex items-center gap-1.5 text-[var(--accent)]">
                  <ShieldCheck class="h-3.5 w-3.5" />
                  {{ marketRegime.regime_name || t('dash.shell.panel.marketRegime') }}
                </span>
                <span class="text-3xs px-1.5 py-0.5 rounded border border-[var(--line-1)] text-[var(--ink-2)]">
                  {{ marketRegime.regime_tag }}
                </span>
              </div>
              <p class="mt-1 text-3xs text-[var(--ink-2)] font-sans leading-relaxed">
                {{ marketRegime.recommended_action }}
              </p>
              <div class="mt-1.5 flex items-center justify-between text-4xs text-[var(--ink-3)] font-mono">
                <span>{{ t('dash.shell.panel.velocity').split(' ')[0] }}: {{ marketRegime.trend_score }}</span>
                <span>VOL: {{ marketRegime.volatility_score }}</span>
                <span>OSC: {{ marketRegime.oscillation_score }}</span>
                <span>DIR: {{ marketRegime.dominant_direction }}</span>
              </div>
            </div>

            <div
              v-for="item in decisionStream"
              :key="item.instId"
              class="dsh-card-sub p-3 transition-colors hover:border-[var(--line-2)]"
            >
              <!-- 行1：标的、方向、置信度 -->
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-xs font-bold" style="color: var(--ink-strong)">
                    {{ item.symbol }}
                  </span>
                  <span
                    class="rounded border px-1.5 py-0.5 text-3xs font-semibold uppercase"
                    :class="actionBadgeClass(item.action)"
                  >
                    {{ item.action }}
                  </span>
                </div>

                <div class="flex items-center gap-2">
                  <div class="flex items-center gap-1 text-3xs" style="color: var(--ink-2)">
                    <span>{{ t('dash.shell.panel.confidence') }}</span>
                    <span class="font-mono font-semibold" style="color: var(--ink-strong)">{{ item.confidence }}%</span>
                  </div>
                  <div
                    class="h-1.5 w-14 overflow-hidden rounded-full"
                    style="background-color: var(--surface-3)"
                    role="progressbar"
                    :aria-valuenow="item.confidence"
                    aria-valuemin="0"
                    aria-valuemax="100"
                    :aria-label="t('dash.shell.panel.confidence')"
                    :aria-valuetext="`${item.confidence}%`"
                  >
                    <div
                      class="h-full rounded-full transition-all duration-500"
                      :style="{
                        width: `${item.confidence}%`,
                        backgroundColor: item.confidence > 70 ? 'var(--up)' : 'var(--warn)',
                      }"
                    />
                  </div>
                </div>
              </div>

              <!-- 行2：推演结论 -->
              <p class="mt-2 text-xs leading-body" style="color: var(--ink-1)">
                {{ item.reason }}
              </p>

              <!-- 行3：微积分指标微缩表 -->
              <div
                class="mt-2.5 flex items-center justify-between rounded px-2 py-1 text-3xs font-mono"
                style="background-color: var(--surface-head); border: 1px solid var(--line-1); color: var(--ink-2)"
              >
                <div>{{ t('dash.shell.panel.velocity') }} <span :class="item.velocity >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'">{{ Number(item.velocity).toFixed(3) }}</span></div>
                <div>{{ t('dash.shell.panel.acceleration') }} <span :class="item.acceleration >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'">{{ Number(item.acceleration).toFixed(3) }}</span></div>
                <div>ADX: <span style="color: var(--ink-1)">{{ Number(item.adx).toFixed(1) }}</span></div>
                <div v-if="item.leverage">{{ t('dash.shell.panel.leverage') }} <span style="color: var(--ink-1)">{{ item.leverage }}x</span></div>
              </div>

              <!-- 行4：思维心流与数理推演展开 (CoT) -->
              <details
                v-if="item.reasoning || item.calculusDynamics || item.mathProbRationale || item.marketStructure"
                class="mt-2 text-3xs text-[var(--ink-3)] cursor-pointer"
              >
                <summary class="hover:text-[var(--accent)] select-none font-mono">
                  {{ t('dash.shell.panel.viewCot') }}
                </summary>
                <div class="mt-1.5 p-2 rounded border space-y-1.5 text-3xs leading-relaxed font-sans select-text" style="background-color: var(--surface-head); border-color: var(--line-1); color: var(--ink-2)">
                  <div v-if="item.marketStructure">
                    <span class="font-bold font-mono" style="color: var(--ink-strong)">{{ t('dash.shell.panel.structure') }}: </span>
                    <span>{{ item.marketStructure }}</span>
                  </div>
                  <div v-if="item.calculusDynamics">
                    <span class="font-bold font-mono" style="color: var(--ink-strong)">{{ t('dash.shell.panel.dynamics') }}: </span>
                    <span>{{ item.calculusDynamics }}</span>
                  </div>
                  <div v-if="item.mathProbRationale">
                    <span class="font-bold font-mono" style="color: var(--ink-strong)">{{ t('dash.shell.panel.mathProb') }}: </span>
                    <span>{{ item.mathProbRationale }}</span>
                  </div>
                  <div v-if="item.reasoning" class="pt-1 border-t" style="border-color: var(--line-1)">
                    <span class="font-bold text-[var(--accent)] font-mono">{{ t('dash.shell.panel.draft') }}: </span>
                    <pre tabindex="0" class="whitespace-pre-wrap font-mono text-4xs mt-0.5 outline-none" :aria-label="t('dash.shell.panel.draft')" style="color: var(--ink-2)">{{ item.reasoning }}</pre>
                  </div>
                </div>
              </details>
            </div>

            <!-- 空态 -->
            <div v-if="decisionStream.length === 0" class="py-12 text-center" style="color: var(--ink-3)">
              <Activity class="mx-auto h-8 w-8 opacity-40" />
              <p class="mt-2 text-xs">{{ t('dash.shell.panel.noTrajectory') }}</p>
            </div>
          </div>

          <!-- TAB 2: 系统日志流 -->
          <div v-else class="h-full flex flex-col">
            <!-- 批 68：纯文本滚动区，内部没有任何可聚焦元素 —— 不加 tabindex
                 键盘用户根本无法滚动这段日志（WCAG 2.1.1）。role="log" 声明其语义。 -->
            <div
              class="flex-1 overflow-y-auto rounded p-2.5 font-mono text-3xs space-y-1"
              role="log"
              tabindex="0"
              :aria-label="t('dash.shell.panel.liveLog')"
              style="background-color: var(--surface-input); border: 1px solid var(--line-1); color: var(--ink-2)"
            >
              <div
                v-for="(log, i) in filteredLogs"
                :key="i"
                class="dsh-log-entry select-text whitespace-pre-wrap break-all"
                :class="{
                  'text-[var(--down)]': log.toLowerCase().includes('error') || log.toLowerCase().includes('fail'),
                  'text-[var(--warn)]': log.toLowerCase().includes('warn'),
                  'text-[var(--ink-1)]': !log.toLowerCase().includes('error') && !log.toLowerCase().includes('warn'),
                }"
              >
                {{ log }}
              </div>

              <div v-if="filteredLogs.length === 0" class="py-8 text-center" style="color: var(--ink-3)">
                {{ t('dash.shell.panel.noLogMatch') }}
              </div>
            </div>
          </div>
        </div>

        <!-- 面板底栏 -->
        <footer
          class="flex items-center justify-between border-t px-4 py-2.5 text-3xs"
          style="background-color: var(--surface-head); border-color: var(--line-1); color: var(--ink-3)"
        >
          <div class="flex items-center gap-2">
            <ShieldCheck class="h-3.5 w-3.5 text-[var(--up)]" />
            <span>{{ t('dash.shell.panel.guardReady') }}</span>
          </div>
          <span class="font-mono">R20 Core Engine</span>
        </footer>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 批 93：`0.24s cubic-bezier(0.16, 1, 0.3, 1)` 就是 `--dur-slow` + `--ease-out`
   的字面写法（曲线逐字相同）—— 改用令牌后**时序完全不变**。
   `.fade-*` 的 `0.2s ease` 一并收到 `--dur-base` + `--ease-out`。 */
.slide-right-enter-active,
.slide-right-leave-active {
  transition: transform var(--dur-slow) var(--ease-out);
}
.slide-right-enter-from,
.slide-right-leave-to {
  transform: translateX(100%);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--dur-base) var(--ease-out);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
