<script setup lang="ts">
/**
 * ⌘K 命令面板：机构级量化终端命令中枢
 * 视图 / 交易所中枢 / 控制台 / 操作 / 标的直达 五类全键盘秒级导航。
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Search, CornerDownLeft, Server } from 'lucide-vue-next';
import { publicTabs, allAdminItems } from '../../config/nav';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { VENUE_KEYS, venueLabel } from '../../utils/venueMeta';
import { pairLabel } from '../../utils/instId';

const router = useRouter();
const { cmdkOpen, peekOpen, aboutOpen, focusSymbol } = useUi();
const { t, toggleLocale } = useI18n();
const { toggleTheme, toggleCvd } = useTheme();
const store = useDashboardStore();
const venueStore = useVenueAccountsStore();

const q = ref('');
const input = ref<HTMLInputElement | null>(null);
const cursor = ref(0);

interface Cmd {
  group: string;
  label: string;
  alias: string;
  icon?: any;
  brandColor?: string;
  run: () => void;
}

const commands = computed<Cmd[]>(() => {
  const list: Cmd[] = [];

  // 1. 视图面板
  for (const tab of publicTabs) {
    list.push({
      group: t('dash.cmdk.groups.views'),
      label: t(tab.labelKey),
      alias: tab.alias || '',
      icon: tab.icon,
      run: () => router.push(tab.path),
    });
  }
  list.push(
    {
      group: t('dash.cmdk.groups.views'),
      label: t('nav.actions.docs'),
      alias: 'docs 文档 架构 指南',
      icon: null,
      run: () => router.push('/docs'),
    },
    {
      group: t('dash.cmdk.groups.views'),
      label: t('nav.actions.console'),
      alias: 'console admin 管理控制台',
      icon: null,
      run: () => router.push('/admin'),
    },
  );

  // 2. 交易所中枢（三所对等）
  const brandColors: Record<string, string> = {
    okx: 'var(--venue-okx, #3880ff)',
    binance: 'var(--venue-binance, #f3ba2f)',
    gate: 'var(--venue-gate, #00be98)',
  };

  for (const key of VENUE_KEYS) {
    const vName = venueLabel(key);
    list.push({
      group: t('dash.cmdk.groups.venues'),
      label: `${vName} · ${t('dash.cmdk.venues.' + key)}`,
      alias: `${key} ${vName} 账户 凭证 标的池 交易所`,
      icon: Server,
      brandColor: brandColors[key],
      run: () => {
        router.push('/admin/security');
      },
    });
  }

  // 3. 全局操作
  list.push(
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.toggleTheme'), alias: 'theme dark light 主题 外观', icon: null, run: toggleTheme },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.toggleLang'), alias: 'language locale 语言 中文 english', icon: null, run: toggleLocale },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.toggleCvd'), alias: 'cvd colorblind 色盲 配色', icon: null, run: toggleCvd },
    {
      group: t('dash.cmdk.groups.actions'),
      label: t('dash.cmdk.actions.refresh'),
      alias: 'refresh sync 刷新 同步',
      icon: null,
      run: () => {
        void store.fetchDashboard(false);
        void venueStore.refresh(true);
      },
    },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.peek'), alias: 'prompt peek 提示词 决策透视', icon: null, run: () => (peekOpen.value = true) },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.about'), alias: 'about community 社区 架构 关于', icon: null, run: () => (aboutOpen.value = true) },
  );

  // 4. 管理控制台子页面
  for (const item of allAdminItems) {
    list.push({
      group: t('dash.cmdk.groups.admin'),
      label: t(item.labelKey),
      alias: item.alias || '',
      icon: item.icon,
      run: () => router.push(item.path),
    });
  }

  // 5. 标的直达（通用化解析）
  for (const f of store.factors || []) {
    const rawId = String(f.instId || '');
    const univ = pairLabel(rawId) || rawId;
    const sym = univ.split('/')[0];
    list.push({
      group: t('dash.cmdk.groups.symbols'),
      label: `${f.name || sym} · ${univ} · ${t('nav.tabs.matrix')}`,
      alias: `${f.instId} ${univ} ${sym}`,
      icon: null,
      run: () => {
        focusSymbol.value = f.instId;
        router.push('/');
      },
    });
  }

  return list;
});

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase();
  if (!s) return commands.value.slice(0, 20);
  return commands.value
    .filter((c) => c.label.toLowerCase().includes(s) || c.alias.toLowerCase().includes(s))
    .slice(0, 20);
});

/** 分组渲染保持顺序 */
const grouped = computed(() => {
  const map = new Map<string, { cmd: Cmd; index: number }[]>();
  filtered.value.forEach((cmd, index) => {
    if (!map.has(cmd.group)) map.set(cmd.group, []);
    map.get(cmd.group)!.push({ cmd, index });
  });
  return Array.from(map.entries());
});

watch(cmdkOpen, async (v) => {
  if (v) {
    q.value = '';
    cursor.value = 0;
    await nextTick();
    input.value?.focus();
  }
});

function runCmd(cmd: Cmd) {
  cmdkOpen.value = false;
  cmd.run();
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    cursor.value = Math.min(cursor.value + 1, filtered.value.length - 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    cursor.value = Math.max(cursor.value - 1, 0);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const hit = filtered.value[cursor.value];
    if (hit) runCmd(hit);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    cmdkOpen.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="cmdkOpen"
        class="fixed inset-0 flex items-start justify-center p-3 sm:p-4"
        style="z-index: var(--z-cmdk); padding-top: 10vh"
        @mousedown.self="cmdkOpen = false"
      >
        <div class="fixed inset-0 backdrop-blur-md" style="background-color: var(--overlay-scrim)" />
        <Transition name="pop" appear>
          <div
            class="float-panel relative w-full overflow-hidden shadow-2xl border"
            style="max-width: 600px; background-color: var(--surface-header); border-color: var(--line-2)"
            @keydown="onKeydown"
          >
            <!-- 搜索框 -->
            <div class="flex items-center gap-3 px-4 py-1" style="border-bottom: 1px solid var(--line-1)">
              <Search class="h-4 w-4 shrink-0" style="color: var(--accent)" />
              <input
                ref="input"
                v-model="q"
                class="h-12 w-full border-0 bg-transparent text-sm outline-none placeholder:text-[var(--ink-3)]"
                style="color: var(--ink-strong)"
                :placeholder="t('dash.cmdk.placeholder')"
                @input="cursor = 0"
              />
              <kbd class="badge badge-quiet text-[10px] uppercase font-mono">Esc</kbd>
            </div>

            <!-- 命令列表 -->
            <div class="scroll-y max-h-[50vh] py-2">
              <template v-for="[group, rows] in grouped" :key="group">
                <div class="sticky top-0 z-10 px-4 py-1 backdrop-blur-md" style="background-color: var(--surface-header)">
                  <span class="text-[10px] font-bold uppercase tracking-wider text-[var(--ink-3)]">{{ group }}</span>
                </div>
                <button
                  v-for="{ cmd, index } in rows"
                  :key="cmd.label + index"
                  type="button"
                  class="group relative flex w-full cursor-pointer items-center gap-3 px-4 py-2 text-left text-xs transition-colors"
                  :style="
                    cursor === index
                      ? { backgroundColor: 'var(--surface-2)', color: 'var(--ink-strong)' }
                      : { color: 'var(--ink-1)' }
                  "
                  @mouseenter="cursor = index"
                  @click="runCmd(cmd)"
                >
                  <!-- 选中态左侧高光条 -->
                  <span
                    v-if="cursor === index"
                    class="absolute inset-y-1.5 left-1 w-0.5 rounded-full bg-[var(--accent)] shadow-[0_0_6px_var(--accent)]"
                  />

                  <component
                    :is="cmd.icon"
                    v-if="cmd.icon"
                    class="h-4 w-4 shrink-0 transition-transform group-hover:scale-110"
                    :style="{ color: cmd.brandColor || 'var(--ink-2)' }"
                  />
                  <span
                    v-else
                    class="h-1.5 w-1.5 shrink-0 rounded-full"
                    :style="{ backgroundColor: cmd.brandColor || 'var(--ink-3)' }"
                  />

                  <span class="flex-1 truncate font-medium">{{ cmd.label }}</span>
                  <CornerDownLeft
                    v-if="cursor === index"
                    class="h-3.5 w-3.5 shrink-0 text-[var(--accent)]"
                  />
                </button>
              </template>

              <div v-if="!filtered.length" class="px-4 py-10 text-center text-xs" style="color: var(--ink-3)">
                {{ t('dash.cmdk.empty') }}
              </div>
            </div>

            <!-- 快捷键底栏提示 -->
            <div
              class="flex items-center justify-between px-4 py-2.5 text-[11px]"
              style="border-top: 1px solid var(--line-1); color: var(--ink-3); background-color: var(--surface-1)"
            >
              <div class="flex items-center gap-3">
                <span class="flex items-center gap-1">
                  <kbd class="badge badge-quiet !text-[9px]">↑↓</kbd>
                  <span>{{ t('dash.cmdk.hintNav') }}</span>
                </span>
                <span class="flex items-center gap-1">
                  <kbd class="badge badge-quiet !text-[9px]">↵</kbd>
                  <span>{{ t('dash.cmdk.hintOpen') }}</span>
                </span>
                <span class="flex items-center gap-1">
                  <kbd class="badge badge-quiet !text-[9px]">ESC</kbd>
                  <span>{{ t('dash.cmdk.hintClose') }}</span>
                </span>
              </div>
              <span class="text-[10px] text-[var(--accent)] font-mono tracking-tight">R20 TERMINAL</span>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
