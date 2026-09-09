<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {UserCog, KeyRound, Plus, Lock, Unlock, ShieldCheck} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const users = ref<any[]>([])
const currentUserId = ref<number>(0)
const loading = ref(true)

// Password form
const pwdUserId = ref<number>(0)
const currentPassword = ref('')
const newPassword = ref('')
const changingPwd = ref(false)

// Create form
const createVisible = ref(false)
const newUsername = ref('')
const newRole = ref('admin')
const newPasswordForCreate = ref('')

async function load() {
  if (!auth.isSuperadmin) { loading.value = false; return }
  loading.value = true
  try {
    const res = await api('/api/v1/admin/users')
    users.value = res.users || []
    currentUserId.value = res.current_user_id
    pwdUserId.value = res.current_user_id
  } catch (e: any) {
    toast.err(e.message)
  } finally {
    loading.value = false
  }
}

async function changePassword() {
  if (newPassword.value.length < 12) {
    toast.err('新密码至少需要 12 位字符')
    return
  }
  changingPwd.value = true
  try {
    await api(`/api/v1/admin/users/${pwdUserId.value}/password`, {
      method: 'PUT',
      body: JSON.stringify({ current_password: currentPassword.value, new_password: newPassword.value }),
    })
    toast.ok('密码已修改，其他设备的会话已全部失效')
    currentPassword.value = ''
    newPassword.value = ''
  } catch (e: any) {
    toast.err(`修改失败：${e.message}`)
  } finally {
    changingPwd.value = false
  }
}

async function createUser() {
  if (newUsername.value.length < 3 || newPasswordForCreate.value.length < 12) {
    toast.err('账号至少 3 位，密码至少 12 位')
    return
  }
  try {
    await api('/api/v1/admin/users', {
      method: 'POST',
      body: JSON.stringify({ username: newUsername.value, password: newPasswordForCreate.value, role: newRole.value }),
    })
    toast.ok(`已创建管理员 ${newUsername.value}`)
    createVisible.value = false
    newUsername.value = ''
    newPasswordForCreate.value = ''
    await load()
  } catch (e: any) {
    toast.err(`创建失败：${e.message}`)
  }
}

async function toggleEnabled(u: any) {
  try {
    await api(`/api/v1/admin/users/${u.id}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled: !u.enabled }) })
    await load()
  } catch (e: any) {
    toast.err(e.message)
  }
}

async function unlockUser(u: any) {
  const phrase = prompt(`解锁 ${u.username} 需输入确认短语：UNLOCK ADMIN ${u.id}`)
  if (!phrase) return
  try {
    await api(`/api/v1/admin/users/${u.id}/unlock`, { method: 'POST', body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }) })
    toast.ok(`${u.username} 已解锁`)
    await load()
  } catch (e: any) {
    toast.err(e.message)
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-xs text-[var(--ink-3)]">PBKDF2-SHA256 加盐哈希 · 连续失败 5 次锁定 15 分钟 · 会话 12 小时。</p>
      <span class="text-[11px] text-blue-400 bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">治理 · 2/3</span>
    </div>
    <!-- Change Password -->
    <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
      <div class="flex items-center space-x-2 mb-4 pb-3 border-b" style="border-color: var(--line-1);">
        <KeyRound class="w-4 h-4 text-amber-500" />
        <h2 class="text-sm font-bold" style="color: var(--ink-1);">{{ t('nav.admin.adminsys') }}</h2>
        <p class="text-[11px] mt-0.5" style="color: var(--ink-2);"> 修改密码 </p>
        <span class="text-[11px] ml-2" style="color: var(--ink-3);">当前账号：{{ auth.user?.username }}（修改后需重新登录）</span>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label class="block text-[11px] mb-1" style="color: var(--ink-2);">当前密码</label>
          <input v-model="currentPassword" type="password" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
        </div>
        <div>
          <label class="block text-[11px] mb-1" style="color: var(--ink-2);">新密码 (≥12 位)</label>
          <input v-model="newPassword" type="password" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
        </div>
        <div class="flex items-end">
          <button @click="changePassword" :disabled="changingPwd" class="w-full flex items-center justify-center space-x-1.5 px-4 py-2 rounded-lg text-xs font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs" style="background-color: var(--accent); color: var(--accent-ink);">
            <ShieldCheck class="w-3.5 h-3.5" /><span>{{ changingPwd ? '修改中...' : '确认修改' }}</span>
          </button>
        </div>
      </div>
      <p class="mt-2 text-[11px]" style="color: var(--ink-3);">超级管理员可在下方用户列表为其他账号重置密码（无需旧密码）。</p>
    </div>

    <!-- Users List -->
    <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
      <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-1);">
        <div class="flex items-center space-x-2">
          <UserCog class="w-4 h-4 text-blue-400" />
          <h2 class="text-xs font-semibold" style="color: var(--ink-1);">管理员账号与权限</h2>
        </div>
        <button v-if="auth.isSuperadmin" @click="createVisible = true" class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer shadow-xs" style="background-color: var(--accent); color: var(--accent-ink);">
          <Plus class="w-3.5 h-3.5" />
          <span>新建管理员</span>
        </button>
      </div>

      <div v-if="!auth.isSuperadmin" class="py-8 text-center text-xs border border-dashed rounded-lg m-4" style="color: var(--ink-2); border-color: var(--line-1);">
        仅超级管理员可查看与管理团队账号。
      </div>

      <div v-else class="table-scroll-container">
        <table class="w-full text-left text-xs whitespace-nowrap">
          <thead>
            <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
              <th class="py-2.5 px-4">UID</th>
              <th class="py-2.5 px-3">账号名</th>
              <th class="py-2.5 px-3">授权角色</th>
              <th class="py-2.5 px-3">账号状态</th>
              <th class="py-2.5 px-3">最近登录时间</th>
              <th class="py-2.5 px-4 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
              <td class="py-2.5 px-4 num" style="color: var(--ink-3);">{{ u.id }}</td>
              <td class="py-2.5 px-3 font-bold" style="color: var(--ink-1);">
                {{ u.username }}
                <span v-if="u.id === currentUserId" class="px-1 py-0.2 rounded text-[11px] font-bold border ml-1" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">(当前会话)</span>
              </td>
              <td class="py-2.5 px-3">
                <span class="px-2 py-0.5 rounded text-[11px] font-bold border" :style="u.role === 'superadmin' ? { backgroundColor: 'var(--accent-bg)', borderColor: 'var(--accent-line)', color: 'var(--accent)' } : { backgroundColor: 'var(--surface-3)', borderColor: 'var(--line-1)', color: 'var(--ink-2)' }">
                  {{ u.role === 'superadmin' ? '超级管理员' : '普通管理员' }}
                </span>
              </td>
              <td class="py-2.5 px-3 font-bold" :class="u.enabled ? (u.locked_until ? 'text-amber-500' : 'text-emerald-500') : 'text-rose-500'">
                {{ !u.enabled ? '已停用' : (u.locked_until && new Date(u.locked_until) > new Date() ? '已锁定' : '正常启用') }}
              </td>
              <td class="py-2.5 px-3 num" style="color: var(--ink-3);">{{ u.last_login_at || '从未登录' }}</td>
              <td class="py-2.5 px-4 text-right whitespace-nowrap space-x-1.5">
                <button v-if="u.id !== currentUserId" @click="toggleEnabled(u)" class="px-2.5 py-1 rounded-md border text-[11px] transition-all cursor-pointer shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">
                  <component :is="u.enabled ? Lock : Unlock" class="w-3 h-3 inline" /> {{ u.enabled ? '停用' : '启用' }}
                </button>
                <button v-if="u.locked_until" @click="unlockUser(u)" class="px-2.5 py-1 rounded-md border text-[11px] cursor-pointer transition-colors" style="background-color: var(--warn-bg); border-color: var(--warn-line); color: var(--warn);">解锁</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Create Modal -->
    <div v-if="createVisible" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="createVisible = false">
      <div class="rounded-xl border p-5 sm:p-6 w-full max-w-[420px] max-h-[88dvh] overflow-y-auto shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <h3 class="text-sm font-bold mb-4" style="color: var(--ink-1);">新建管理员</h3>
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">账号 (3-32 位)</label>
        <input v-model="newUsername" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-3" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">角色</label>
        <select v-model="newRole" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-3 cursor-pointer" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);">
          <option value="admin">管理员（日常运维）</option>
          <option value="superadmin">超级管理员（全部权限）</option>
        </select>
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">初始密码 (≥12 位)</label>
        <input v-model="newPasswordForCreate" type="password" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-4" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
        <div class="flex justify-end space-x-2">
          <button @click="createVisible = false" class="px-3 py-2 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">取消</button>
          <button @click="createUser" class="px-3 py-2 rounded-lg text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--accent); color: var(--accent-ink);">创建</button>
        </div>
      </div>
    </div>
  </div>
</template>
