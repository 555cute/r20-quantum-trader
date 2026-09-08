<script setup lang="ts">
import { ref, onMounted } from 'vue'
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
      <p class="text-xs font-mono" style="color: var(--text-muted);">内置插件健康状态；实盘控制面仅允许随仓库审计过的内置插件。</p>
      <span
        class="text-[11px] font-mono px-2 py-1 rounded border font-bold"
        style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
      >
        策略配置 · 2/3
      </span>
    </div>

    <div v-if="errText" class="p-3 rounded-lg text-xs font-mono border" style="background-color: var(--color-down-bg); border-color: var(--color-down-border); color: var(--color-down);">{{ errText }}</div>
    <div v-if="loading" class="py-12 text-center text-xs font-mono" style="color: var(--text-muted);"><RefreshCw class="w-5 h-5 animate-spin inline mr-1.5" style="color: var(--color-brand);" />正在加载插件状态...</div>

    <template v-else-if="data">
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center space-x-2">
            <Blocks class="w-4 h-4" style="color: var(--color-brand);" />
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">插件清单</h2>
          </div>
          <button @click="load" class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);">
            <RefreshCw class="w-3 h-3" />
            <span>刷新</span>
          </button>
        </div>

        <div class="table-scroll-container rounded-lg border my-2" style="border-color: var(--border-subtle);">
          <table class="w-full text-left text-xs font-mono whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-3">插件</th>
                <th class="py-2.5 px-3">类型</th>
                <th class="py-2.5 px-3">版本</th>
                <th class="py-2.5 px-3">权限声明</th>
                <th class="py-2.5 px-3">启用开关</th>
                <th class="py-2.5 px-3">健康状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in data.plugins" :key="p.plugin_id" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-3 font-bold" style="color: var(--text-main);">
                  {{ p.name }}
                  <div class="text-[11px] font-normal" style="color: var(--text-faint);">{{ p.plugin_id }}</div>
                </td>
                <td class="py-2.5 px-3" style="color: var(--text-muted);">{{ p.plugin_type }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint);">{{ p.version }}</td>
                <td class="py-2.5 px-3 text-[11px]" style="color: var(--text-muted);">{{ (p.permissions || []).join(', ') }}</td>
                <td class="py-2.5 px-3" style="color: var(--text-faint);">{{ p.enabled_key || '默认启用' }}</td>
                <td class="py-2.5 px-3 font-bold" :class="p.health === 'healthy' ? 'text-emerald-500' : 'text-amber-500'">
                  {{ p.health === 'healthy' ? '正常' : p.health === 'disabled' ? '已禁用' : p.health }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="rounded-xl border p-4 flex items-start gap-3 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <ShieldAlert class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
        <div>
          <h3 class="text-xs font-bold text-amber-500 font-mono mb-1">安装策略：{{ data.installation_policy === 'builtin-only' ? '仅内置插件' : data.installation_policy }}</h3>
          <p class="text-[11px] font-mono leading-relaxed" style="color: var(--text-muted);">{{ data.reason }}</p>
        </div>
      </div>
    </template>
  </div>
</template>
