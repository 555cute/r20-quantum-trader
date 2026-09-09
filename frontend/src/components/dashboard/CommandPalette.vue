<script setup lang="ts">
/**
 * ⌘K 命令面板：页面 / 控制台 / 操作 / 币种直达 四类结果，键盘全导航。
 * 热键由 App 全局绑定；本组件只负责面板本身。
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Search, CornerDownLeft } from 'lucide-vue-next';
import { publicTabs, allAdminItems } from '../../config/nav';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import { useDashboardStore } from '../../stores/dashboard';

const router = useRouter();
const { cmdkOpen, peekOpen, aboutOpen, focusSymbol } = useUi();
const { t, toggleLocale } = useI18n();
const { toggleTheme, toggleCvd } = useTheme();
const store = useDashboardStore();

const q = ref('');
const input = ref<HTMLInputElement | null>(null);
const cursor = ref(0);

interface Cmd {
  group: string;
  label: string;
  alias: string;
  icon?: any;
  run: () => void;
}

const commands = computed<Cmd[]>(() => {
  const list: Cmd[] = [];
  for (const tab of publicTabs) {
    list.push({ group: t('dash.cmdk.groups.views'), label: t(tab.labelKey), alias: tab.alias || '', icon: tab.icon, run: () => router.push(tab.path) });
  }
  list.push(
    { group: t('dash.cmdk.groups.views'), label: t('nav.actions.docs'), alias: 'docs 文档', icon: null, run: () => router.push('/docs') },
    { group: t('dash.cmdk.groups.admin'), label: t('nav.actions.console'), alias: 'console admin', icon: null, run: () => router.push('/admin') },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.toggleTheme'), alias: 'theme dark light 主题', icon: null, run: toggleTheme },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.toggleLang'), alias: 'language locale 语言', icon: null, run: toggleLocale },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.toggleCvd'), alias: 'cvd colorblind 色盲', icon: null, run: toggleCvd },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.refresh'), alias: 'refresh sync 刷新', icon: null, run: () => store.fetchDashboard(false) },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.peek'), alias: 'prompt peek 提示词', icon: null, run: () => (peekOpen.value = true) },
    { group: t('dash.cmdk.groups.actions'), label: t('dash.cmdk.actions.about'), alias: 'about community 社区', icon: null, run: () => (aboutOpen.value = true) },
  );
  for (const item of allAdminItems) {
    list.push({ group: t('dash.cmdk.groups.admin'), label: t(item.labelKey), alias: item.alias || '', icon: item.icon, run: () => router.push(item.path) });
  }
  for (const f of store.factors || []) {
    const sym = String(f.instId || '').split('-')[0];
    list.push({
      group: t('dash.cmdk.groups.symbols'),
      label: `${f.name || sym} · ${t('nav.tabs.matrix')}`,
      alias: `${f.instId} ${sym}`,
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
  if (!s) return commands.value.slice(0, 18);
  return commands.value
    .filter((c) => c.label.toLowerCase().includes(s) || c.alias.toLowerCase().includes(s))
    .slice(0, 18);
});

/** 分组渲染顺序保持插入序 */
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
        class="fixed inset-0 flex items-start justify-center p-4"
        style="z-index: var(--z-cmdk); padding-top: 12vh"
        @mousedown.self="cmdkOpen = false"
      >
        <div class="fixed inset-0" style="background-color: var(--overlay-scrim)" />
        <Transition name="pop" appear>
          <div
            class="float-panel relative w-full overflow-hidden"
            style="max-width: 560px"
            @keydown="onKeydown"
          >
            <div class="flex items-center gap-2.5 px-4" style="border-bottom: 1px solid var(--line-1)">
              <Search class="h-4 w-4 shrink-0" style="color: var(--ink-3)" />
              <input
                ref="input"
                v-model="q"
                class="h-12 w-full border-0 bg-transparent text-sm outline-none"
                style="color: var(--ink-1)"
                :placeholder="t('dash.cmdk.placeholder')"
                @input="cursor = 0"
              />
              <kbd>Esc</kbd>
            </div>

            <div class="scroll-y max-h-[46vh] py-2">
              <template v-for="[group, rows] in grouped" :key="group">
                <p class="t-label px-4 pb-1 pt-2.5">{{ group }}</p>
                <button
                  v-for="{ cmd, index } in rows"
                  :key="cmd.label + index"
                  class="flex w-full cursor-pointer items-center gap-2.5 px-4 py-2 text-left text-sm transition-colors"
                  :style="
                    cursor === index
                      ? { backgroundColor: 'var(--surface-1)', color: 'var(--ink-strong)' }
                      : { color: 'var(--ink-1)' }
                  "
                  @mouseenter="cursor = index"
                  @click="runCmd(cmd)"
                >
                  <component :is="cmd.icon" v-if="cmd.icon" class="h-4 w-4 shrink-0" style="color: var(--ink-2)" />
                  <span class="flex-1 truncate">{{ cmd.label }}</span>
                  <CornerDownLeft v-if="cursor === index" class="h-3.5 w-3.5 shrink-0" style="color: var(--ink-3)" />
                </button>
              </template>
              <p v-if="!filtered.length" class="px-4 py-8 text-center text-sm" style="color: var(--ink-3)">
                {{ t('dash.cmdk.empty') }}
              </p>
            </div>

            <div
              class="flex items-center gap-3 px-4 py-2 text-xs"
              style="border-top: 1px solid var(--line-1); color: var(--ink-3)"
            >
              <span>{{ t('dash.cmdk.hintNav') }}</span>
              <span>{{ t('dash.cmdk.hintOpen') }}</span>
              <span>{{ t('dash.cmdk.hintClose') }}</span>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
