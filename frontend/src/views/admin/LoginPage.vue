<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useTheme } from '../../composables/useTheme'
import { useI18n } from '../../composables/useI18n'
import { LogIn, AlertCircle, RefreshCw, Sun, Moon, ArrowLeft, Globe } from 'lucide-vue-next'

const auth = useAuthStore()
const router = useRouter()
const { theme, toggleTheme } = useTheme()
const { t, isEn, toggleLocale } = useI18n()

const username = ref('admin')
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  loading.value = true
  const ok = await auth.login(username.value, password.value)
  loading.value = false
  if (ok) {
    router.push('/admin/overview')
  }
}
</script>

<template>
  <div
    class="min-h-screen flex flex-col justify-between p-4 sm:p-6 transition-colors selection:bg-blue-500/30"
    style="background-color: var(--bg-app); color: var(--text-main);"
  >
    <!-- Top Bar: Back to Terminal & Theme Toggle -->
    <div class="max-w-md w-full mx-auto flex items-center justify-between">
      <a
        href="/"
        class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono transition-colors shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
      >
        <ArrowLeft class="w-3.5 h-3.5" />
        <span>{{ isEn ? 'Back to Terminal' : '返回实盘终端' }}</span>
      </a>

      <div class="flex items-center space-x-2">
        <button
          @click="toggleLocale"
          class="flex items-center h-8 space-x-1 px-2.5 rounded-lg border transition-all cursor-pointer shadow-xs font-bold text-xs font-mono select-none"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
        >
          <Globe class="w-3.5 h-3.5 text-indigo-400" />
          <span class="uppercase tracking-wider">{{ isEn ? 'EN' : '中' }}</span>
        </button>

        <button
          @click="toggleTheme"
          class="flex items-center justify-center w-8 h-8 rounded-lg border transition-all cursor-pointer shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="theme === 'dark' ? '切换为亮色模式' : '切换为暗色模式'"
        >
          <Sun v-if="theme === 'dark'" class="w-4 h-4 text-amber-400 hover:rotate-45 transition-transform" />
          <Moon v-else class="w-4 h-4 text-slate-700 hover:-rotate-12 transition-transform" />
        </button>
      </div>
    </div>

    <!-- Center: Login Card -->
    <div class="w-full max-w-md mx-auto my-auto py-8">
      <!-- Brand Header -->
      <div class="flex flex-col items-center mb-6 text-center">
        <div
          class="w-12 h-12 rounded-xl flex items-center justify-center font-mono font-black text-xl border shadow-xs mb-3"
          style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);"
        >
          R
        </div>
        <div class="text-base font-black font-mono tracking-wide" style="color: var(--text-main);">
          {{ t('admin.loginTitle') }}
        </div>
        <div class="text-xs font-mono mt-0.5" style="color: var(--text-muted);">
          {{ t('admin.loginSubtitle') }}
        </div>
      </div>

      <!-- Main Login Panel -->
      <div
        class="rounded-xl border p-6 sm:p-7 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div
          v-if="auth.error"
          class="mb-4 p-3 rounded-lg border text-xs font-mono flex items-start gap-2"
          style="background-color: var(--color-down-bg); border-color: var(--color-down-border); color: var(--color-down);"
        >
          <AlertCircle class="w-4 h-4 shrink-0 mt-0.5" />
          <span>{{ auth.error }}</span>
        </div>

        <div class="space-y-4">
          <div>
            <label class="block text-xs font-mono font-bold mb-1.5" style="color: var(--text-muted);">
              {{ t('admin.username') }}
            </label>
            <input
              v-model="username"
              type="text"
              autocomplete="username"
              class="w-full rounded-lg px-3.5 py-2.5 text-xs font-mono outline-none border transition-colors"
              style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <div>
            <label class="block text-xs font-mono font-bold mb-1.5" style="color: var(--text-muted);">
              {{ t('admin.password') }}
            </label>
            <input
              v-model="password"
              type="password"
              autocomplete="current-password"
              :placeholder="t('admin.password')"
              class="w-full rounded-lg px-3.5 py-2.5 text-xs font-mono outline-none border transition-colors"
              style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
              @keyup.enter="handleLogin"
            />
          </div>

          <button
            @click="handleLogin"
            :disabled="loading"
            class="w-full flex items-center justify-center space-x-2 font-mono font-bold text-xs py-2.5 rounded-lg border transition-all cursor-pointer shadow-xs disabled:opacity-50 disabled:cursor-not-allowed mt-2 hover:opacity-90"
            style="background-color: var(--text-main); border-color: var(--text-main); color: var(--bg-app);"
          >
            <LogIn v-if="!loading" class="w-3.5 h-3.5" />
            <RefreshCw v-else class="w-3.5 h-3.5 animate-spin" />
            <span>{{ loading ? t('admin.loggingIn') : t('admin.loginBtn') }}</span>
          </button>
        </div>

        <p class="mt-4 text-[10px] font-mono leading-relaxed" style="color: var(--text-faint);">
          {{ isEn ? 'Default account: admin. Automatically locks for 15 minutes after 5 consecutive failures. All login attempts and IPs are permanently recorded in audit logs.' : '默认账号为 admin；连续失败 5 次会自动临时锁定 15 分钟。所有登录动作与 IP 将持久化记录于操作审计日志中。' }}
        </p>
      </div>
    </div>

    <!-- Bottom Footer -->
    <div class="max-w-md w-full mx-auto text-center text-[11px] font-mono" style="color: var(--text-faint);">
      R20 QUANTUM TRADER · ENTERPRISE CONTROL PLANE
    </div>
  </div>
</template>
