<script setup lang="ts">
import { ref, computed, onMounted , watch} from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import SaveBar from '../../components/admin/SaveBar.vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { HardDrive, RefreshCw, PlugZap, Save, PlayCircle, Archive, AlertCircle, Download, Upload, RotateCcw } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const loading = ref(true)
const busy = ref<'test' | 'save' | 'run' | 'restore' | 'upload' | ''>('')
const bannerMsg = ref<{ text: string; type: 'ok' | 'warn' | 'err' } | null>(null)
const bannerSeq = ref(0)
watch(bannerMsg, () => { bannerSeq.value++ })
const downloadingArchive = ref<string>('')

const simple = ref<any>(null)
const targetTypes = ref<any[]>([])
const status = ref<any>(null)
const uploadFileInput = ref<HTMLInputElement | null>(null)

const enabled = ref(false)
const scheduleTime = ref('02:00')
const destination = ref('local')
const retention = ref(3)
const endpoint = ref('')
const bucket = ref('')
const credentials = ref<Record<string, string>>({})

const remoteDest = computed(() => ['s3', 'oss', 'webdav', 'baidu_oauth'].includes(destination.value))
const needsBucket = computed(() => destination.value === 's3' || destination.value === 'oss')
const credentialFields = computed(() => {
  if (['s3', 'oss'].includes(destination.value)) return ['access_key_id', 'secret_access_key']
  if (['webdav', 'aliyundrive', 'quark'].includes(destination.value)) return ['username', 'password']
  if (destination.value === 'baidu_oauth') return ['app_key', 'app_secret', 'refresh_token']
  return []
})

async function load() {
  loading.value = true
  try {
    const [s, t, st] = await Promise.all([
      api('/api/v1/admin/backups/simple'),
      api('/api/v1/admin/backup-target-types'),
      api('/api/v1/admin/backups'),
    ])
    simple.value = s
    targetTypes.value = t.target_types || []
    status.value = st
    enabled.value = s.enabled
    destination.value = s.destination
    scheduleTime.value = s.schedule_time || '02:00'
    retention.value = s.retention || 3
    endpoint.value = s.target?.endpoint || ''
    bucket.value = s.target?.bucket || ''
  } catch (e: any) {
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function payload() {
  return {
    enabled: enabled.value,
    schedule_time: scheduleTime.value,
    destination: destination.value,
    retention: Number(retention.value) || 3,
    endpoint: endpoint.value.trim(),
    bucket: bucket.value.trim(),
    credentials: credentials.value,
  }
}

async function testConnection() {
  busy.value = 'test'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/backups/simple/test', { method: 'POST', body: JSON.stringify(payload()) })
    bannerMsg.value = { text: `✅ ${res.detail}`, type: 'ok' }
  } catch (e: any) {
    bannerMsg.value = { text: `测试失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function save() {
  busy.value = 'save'
  bannerMsg.value = null
  try {
    await api('/api/v1/admin/backups/simple', { method: 'PUT', body: JSON.stringify(payload()) })
    bannerMsg.value = { text: '✅ 灾备配置已保存，每天北京时间 ' + scheduleTime.value + ' 自动执行', type: 'ok' }
    await load()
  } catch (e: any) {
    bannerMsg.value = { text: `保存失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function runNow() {
  const phrase = prompt('立即执行完整灾备（打包并按已启用目标上传）需输入确认短语：BACKUP R20')
  if (!phrase) return
  busy.value = 'run'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/backups/run', { method: 'POST', body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }) })
    bannerMsg.value = { text: `✅ 灾备执行完成（${(res.output || '').length} 字符输出已记录）`, type: 'ok' }
    await load()
  } catch (e: any) {
    bannerMsg.value = { text: `灾备失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function downloadArchive(archiveName: string) {
  const clean = archiveName.split('/').pop() || archiveName
  downloadingArchive.value = clean
  bannerMsg.value = { text: `正在连接并准备下载归档文件 ${clean}...`, type: 'ok' }

  const token = auth.token || localStorage.getItem('r20.admin.session.id') || ''
  const directUrl = `/api/v1/admin/backups/download/${encodeURIComponent(clean)}${token ? `?token=${encodeURIComponent(token)}` : ''}`

  try {
    // 双通道策略 1：通过 Fetch Blob 在内存中获取并检查状态
    const resp = await fetch(directUrl, {
      headers: {
        ...(token ? { 'X-R20-Session': token } : {})
      }
    })

    if (!resp.ok) {
      let errMsg = `HTTP ${resp.status}`
      try {
        const errJson = await resp.json()
        errMsg = errJson.detail || errMsg
      } catch {}
      throw new Error(errMsg)
    }

    const blob = await resp.blob()
    const blobUrl = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = clean
    document.body.appendChild(a)
    a.click()
    setTimeout(() => {
      a.remove()
      window.URL.revokeObjectURL(blobUrl)
    }, 2000)

    bannerMsg.value = { text: `✅ 归档文件 ${clean} 已成功触发下载`, type: 'ok' }
  } catch (e: any) {
    // 双通道策略 2：若 Blob 或 Fetch 产生跨域或浏览器安全拦截，降级采用原生链接直连触发
    try {
      const fallbackA = document.createElement('a')
      fallbackA.href = directUrl
      fallbackA.download = clean
      fallbackA.target = '_blank'
      document.body.appendChild(fallbackA)
      fallbackA.click()
      setTimeout(() => fallbackA.remove(), 1000)
      bannerMsg.value = { text: `✅ 已切换直接下载通道触发归档 ${clean} 下载`, type: 'ok' }
    } catch (fallbackErr: any) {
      bannerMsg.value = { text: `下载失败：${e.message}`, type: 'err' }
    }
  } finally {
    downloadingArchive.value = ''
  }
}

function triggerUpload() {
  if (uploadFileInput.value) {
    uploadFileInput.value.click()
  }
}

async function onFileSelected(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  busy.value = 'upload'
  bannerMsg.value = null
  try {
    const formData = new FormData()
    formData.append('file', file)
    const resp = await fetch('/api/v1/admin/backups/upload', {
      method: 'POST',
      headers: {
        ...(auth.token ? { 'X-R20-Session': auth.token } : {})
      },
      body: formData
    })
    const res = await resp.json()
    if (!resp.ok) {
      throw new Error(res.detail || `上传失败 HTTP ${resp.status}`)
    }
    bannerMsg.value = { text: `✅ 备份包 ${file.name} 上传成功！`, type: 'ok' }
    await load()
  } catch (err: any) {
    bannerMsg.value = { text: `上传备份失败：${err.message}`, type: 'err' }
  } finally {
    busy.value = ''
    if (target) target.value = ''
  }
}

async function restoreArchive(archiveName: string) {
  const clean = archiveName.split('/').pop() || archiveName
  const phrase = prompt(`警告：恢复备份将解压覆盖当前系统配置、历史数据与策略。\n如确认恢复归档【${clean}】，请输入确认短语：RESTORE R20`)
  if (!phrase) return
  if (phrase.trim().toUpperCase() !== 'RESTORE R20') {
    alert('确认短语不正确，已取消恢复！')
    return
  }
  busy.value = 'restore'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/backups/restore', {
      method: 'POST',
      body: JSON.stringify({
        archive_name: clean,
        confirmation: 'RESTORE R20'
      })
    })
    bannerMsg.value = { text: `✅ 备份 ${clean} 恢复成功！共解压 ${res.restored_count} 个核心文件。请重启或刷新服务使新状态接管。`, type: 'ok' }
    await load()
  } catch (e: any) {
    bannerMsg.value = { text: `恢复失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

function fmtBytes(n: number) {
  if (!n) return '--'
  return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB'
}
function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-xs text-[var(--ink-3)]">支持本地/云端全量数据灾备、备份打包直接下载、本地备份上传与一键全量恢复。</p>
      <span class="text-[11px] text-blue-400 bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">集成与保障 · 2/3</span>
    </div>

    <SaveBar
          :type="bannerMsg?.type || 'ok'"
          :text="bannerMsg?.text || ''"
          :nonce="bannerSeq"
          @dismiss="bannerMsg = null"
        />
    <div v-if="loading" class="py-12 text-center text-xs" style="color: var(--ink-2);"><RefreshCw class="w-5 h-5 animate-spin inline mr-1.5" style="color: var(--accent);" />正在加载灾备配置...</div>

    <template v-else-if="simple">
      <!-- Simple Config -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors space-y-4" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center space-x-2">
            <HardDrive class="w-4 h-4" style="color: var(--accent);" />
            <h2 class="text-sm font-bold" style="color: var(--ink-1);">{{ t('admin.nBackup') }}</h2>
        <p class="text-[11px] mt-0.5" style="color: var(--ink-2);"> 自动灾备 </p>
          </div>
          <label class="flex items-center space-x-2 text-xs cursor-pointer">
            <input v-model="enabled" type="checkbox" class="accent-blue-500 w-4 h-4" :disabled="!auth.isSuperadmin" />
            <span :class="enabled ? 'text-emerald-500 font-bold' : 'text-zinc-500'">{{ enabled ? '每日自动灾备已启用' : '已停用' }}</span>
          </label>
        </div>

        <div v-if="simple.legacy_bypy" class="p-2.5 rounded-lg border text-[11px]" style="background-color: var(--warn-bg); border-color: var(--warn-line); color: var(--warn);">⚠ {{ simple.migration_note }}</div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">1. 备份内容</label>
            <select disabled class="w-full rounded-lg px-3 py-2 text-xs opacity-70 border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);">
              <option>R20 系统、策略、配置与运行数据</option>
            </select>
          </div>
          <div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">2. 保存位置</label>
            <select v-model="destination" :disabled="!auth.isSuperadmin" class="w-full rounded-lg px-3 py-2 text-xs outline-none border cursor-pointer" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);">
              <option value="local">本地滚动归档</option>
              <option value="s3">S3 兼容存储</option>
              <option value="oss">阿里云 OSS</option>
              <option value="webdav">WebDAV / OpenList</option>
              <option value="baidu_oauth">百度网盘（官方 OAuth）</option>
            </select>
          </div>
          <div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">3. 每天执行时间（北京时间）</label>
            <input v-model="scheduleTime" type="time" :disabled="!auth.isSuperadmin" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          </div>
          <div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">4. 保留最近几份{{ destination === 'local' ? '（本地）' : '' }}</label>
            <input v-model="retention" type="number" min="1" max="365" :disabled="!auth.isSuperadmin" class="w-full rounded-lg px-3 py-2 text-xs outline-none border num" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          </div>
        </div>

        <!-- Remote Credentials -->
        <div v-if="remoteDest" class="mt-4 p-3.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
          <div class="text-[11px] mb-2" style="color: var(--ink-2);">连接信息（保存进本机加密密文库，不回显明文）</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div v-if="destination !== 'baidu_oauth'">
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">Endpoint</label>
              <input v-model="endpoint" :disabled="!auth.isSuperadmin" placeholder="https://s3.us-west-004.backblazeb2.com" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
            </div>
            <div v-if="needsBucket">
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">Bucket</label>
              <input v-model="bucket" :disabled="!auth.isSuperadmin" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
            </div>
            <div v-for="f in credentialFields" :key="f">
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">{{ f }}</label>
              <input v-model="credentials[f]" type="password" :disabled="!auth.isSuperadmin" :placeholder="simple.configured ? '留空保持现有值' : ''" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
            </div>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2 mt-4">
          <template v-if="auth.isSuperadmin">
            <button @click="testConnection" :disabled="busy !== ''" class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-xs cursor-pointer disabled:opacity-40 transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"><PlugZap class="w-3.5 h-3.5" /><span>{{ busy === 'test' ? '测试中...' : '测试连接' }}</span></button>
            <button @click="save" :disabled="busy !== ''" class="flex items-center space-x-1 px-3 py-2 rounded-lg text-xs font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs" style="background-color: var(--ink-1); color: var(--surface-2);"><Save class="w-3.5 h-3.5" /><span>{{ busy === 'save' ? '保存中...' : '保存灾备' }}</span></button>
            <button @click="runNow" :disabled="busy !== ''" class="flex items-center space-x-1 px-3 py-2 rounded-lg text-xs font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);"><PlayCircle class="w-3.5 h-3.5" /><span>{{ busy === 'run' ? '执行中（最长10分钟）...' : '立即备份' }}</span></button>

            <!-- Hidden file input for upload -->
            <input ref="uploadFileInput" type="file" accept=".tar.gz,.tgz" class="hidden" @change="onFileSelected" />
            <button @click="triggerUpload" :disabled="busy !== ''" class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-xs font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--accent);"><Upload class="w-3.5 h-3.5" /><span>{{ busy === 'upload' ? '正在上传...' : '上传备份包' }}</span></button>
          </template>
          <span v-else class="text-[11px]" style="color: var(--ink-3);">只读视图 · 修改需超级管理员登录</span>
          <span class="ml-auto text-[11px] font-bold" :class="simple.configured ? 'text-emerald-500' : 'text-amber-500'">{{ simple.configured ? '● 目标已配置' : '● 目标未配置' }}</span>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Latest -->
        <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <h2 class="text-xs font-bold uppercase mb-3" style="color: var(--ink-1);">最近一次灾备</h2>
          <div v-if="simple.latest" class="space-y-1.5 text-xs">
            <div class="flex justify-between border rounded-lg px-3 py-2" style="background-color: var(--surface-1); border-color: var(--line-1);"><span style="color: var(--ink-2);">时间</span><span style="color: var(--ink-1);">{{ simple.latest.created_at || simple.latest.time || JSON.stringify(simple.latest).slice(0, 60) }}</span></div>
            <div class="flex justify-between border rounded-lg px-3 py-2" style="background-color: var(--surface-1); border-color: var(--line-1);"><span style="color: var(--ink-2);">状态</span><span class="text-emerald-500 font-bold">{{ simple.latest.status || 'success' }}</span></div>
          </div>
          <div v-else class="py-6 text-center text-xs" style="color: var(--ink-3);">尚无匹配的灾备清单记录</div>
          <div class="text-[11px] mt-3 leading-relaxed" style="color: var(--ink-3);">{{ status?.schedule }}</div>
        </div>

        <!-- Local archives -->
        <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-1);">
            <div class="flex items-center space-x-2">
              <Archive class="w-4 h-4 text-cyan-400" />
              <h2 class="text-xs font-semibold uppercase tracking-wide" style="color: var(--ink-1);">备份归档清单 ({{ status?.local_archives?.length ?? 0 }})</h2>
            </div>
            <span class="text-[11px]" style="color: var(--ink-3);">支持直接下载与一键恢复</span>
          </div>
          <div class="table-scroll-container">
            <table v-if="status?.local_archives?.length" class="w-full text-left text-xs whitespace-nowrap">
              <thead>
                <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                  <th class="py-2.5 px-4">归档文件</th>
                  <th class="py-2.5 px-3 text-right">大小</th>
                  <th class="py-2.5 px-4 text-right">创建时间</th>
                  <th class="py-2.5 px-4 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="a in status.local_archives.slice(0, 10)" :key="a.name" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                  <td class="py-2.5 px-4 font-medium truncate max-w-[200px]" style="color: var(--ink-1);" :title="a.name">{{ a.name }}</td>
                  <td class="py-2.5 px-3 text-right num" style="color: var(--ink-2);">{{ fmtBytes(a.bytes) }}</td>
                  <td class="py-2.5 px-4 text-right num" style="color: var(--ink-3);">{{ fmtTime(a.mtime) }}</td>
                  <td class="py-2.5 px-4 text-center">
                    <div class="flex items-center justify-center space-x-2">
                      <button
                        @click="downloadArchive(a.name)"
                        :disabled="downloadingArchive === (a.name.split('/').pop() || a.name)"
                        class="p-1 rounded hover:bg-[var(--surface-3)] text-[var(--accent)] transition-colors cursor-pointer disabled:opacity-50"
                        title="下载归档到本地"
                      >
                        <RefreshCw v-if="downloadingArchive === (a.name.split('/').pop() || a.name)" class="w-3.5 h-3.5 animate-spin" />
                        <Download v-else class="w-3.5 h-3.5" />
                      </button>
                      <button
                        v-if="auth.isSuperadmin"
                        @click="restoreArchive(a.name)"
                        :disabled="busy === 'restore'"
                        class="p-1 rounded hover:bg-[var(--surface-3)] text-amber-500 transition-colors cursor-pointer"
                        title="恢复此备份到系统"
                      >
                        <RotateCcw class="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="py-8 text-center text-xs" style="color: var(--ink-2);">暂无本地待清归档，可点击「立即备份」生成完整镜像包或「上传备份包」</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
