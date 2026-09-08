<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { Blocks, ShieldAlert, RefreshCw } from 'lucide-vue-next'

const { api } = useApi()
const data = ref<any>(null)
const loading = ref(true)
const errText = ref('')

async function load() {
  loading.value = true
  try {
    data.value = await api('/api/v1/admin/plugins')
    errText.value = ''
  } catch (e: any) {
    errText.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4 max-w-[2048px] mx-auto">
    <div class="flex items-center justify-between">
      <p class="text-xs" style="color: var(--ink-2);"> 插件清单 —— 内置插件健康状态；实盘控制面仅允许随仓库审计过的内置插件。 </p>
      <span
        class="text-[11px] px-2 py-1 rounded border font-bold"
        style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);"
      >
        策略配置 · 2/3
      </span>
    </div>

    <div v-if="errText" class="p-3 rounded-lg text-xs border" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">{{ errText }}</div>
    <div v-if="loading" class="py-12 text-center text-xs" style="color: var(--ink-2);"><RefreshCw class="w-5 h-5 animate-spin inline mr-1.5" style="color: var(--accent);" />正在加载插件状态...</div>

    <template v-else-if="data">
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center space-x-2">
            <Blocks class="w-4 h-4" style="color: var(--accent);" />
            <h2 class="text-xs font-semibold" style="color: var(--ink-1);">{{ t('nav.admin.plugins') }}</h2>
          </div>
          <button @click="load" class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">
            <RefreshCw class="w-3 h-3" />
            <span>刷新</span>
          </button>
        </div>

        <div class="table-scroll-container rounded-lg border my-2" style="border-color: var(--line-1);">
          <table class="w-full text-left text-xs whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                <th class="py-2.5 px-3">插件</th>
                <th class="py-2.5 px-3">类型</th>
                <th class="py-2.5 px-3">版本</th>
                <th class="py-2.5 px-3">权限声明</th>
                <th class="py-2.5 px-3">启用开关</th>
                <th class="py-2.5 px-3">健康状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in data.plugins" :key="p.plugin_id" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                <td class="py-2.5 px-3 font-bold" style="color: var(--ink-1);">
                  {{ p.name }}
                  <div class="text-[11px] font-normal" style="color: var(--ink-3);">{{ p.plugin_id }}</div>
                </td>
                <td class="py-2.5 px-3" style="color: var(--ink-2);">{{ p.plugin_type }}</td>
                <td class="py-2.5 px-3 num" style="color: var(--ink-3);">{{ p.version }}</td>
                <td class="py-2.5 px-3 text-[11px]" style="color: var(--ink-2);">{{ (p.permissions || []).join(', ') }}</td>
                <td class="py-2.5 px-3" style="color: var(--ink-3);">{{ p.enabled_key || '默认启用' }}</td>
                <td class="py-2.5 px-3 font-bold" :class="p.health === 'healthy' ? 'text-emerald-500' : 'text-amber-500'">
                  {{ p.health === 'healthy' ? '正常' : p.health === 'disabled' ? '已禁用' : p.health }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="rounded-xl border p-4 flex items-start gap-3 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <ShieldAlert class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
        <div>
          <h3 class="text-xs font-bold text-amber-500 mb-1">安装策略：{{ data.installation_policy === 'builtin-only' ? '仅内置插件' : data.installation_policy }}</h3>
          <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">{{ data.reason }}</p>
        </div>
      </div>
    </template>
  </div>
</template>
