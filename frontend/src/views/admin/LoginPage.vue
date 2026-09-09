<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { LogIn, AlertCircle, Loader2, Eye, EyeOff, ArrowLeft } from 'lucide-vue-next'
import { useAuthStore } from '../../stores/auth'
import { useTheme } from '../../composables/useTheme'
import { useI18n } from '../../composables/useI18n'
import { APP_VERSION } from '../../config/version'

const auth = useAuthStore()
const router = useRouter()
const { theme, toggleTheme } = useTheme()
const { t } = useI18n()

const username = ref('')
const password = ref('')
const showPwd = ref(false)
const loading = ref(false)

async function handleLogin() {
  if (!username.value || !password.value || loading.value) return
  loading.value = true
  const ok = await auth.login(username.value, password.value)
  loading.value = false
  if (ok) router.push('/admin/overview')
}
</script>

<template>
  <div class="relative flex min-h-screen flex-col items-center justify-center p-4" style="background-color: var(--surface-0); color: var(--ink-1)">
    <!-- 角落工具 -->
    <div class="absolute inset-x-4 top-4 flex items-center justify-between">
      <a href="/" class="btn btn-ghost btn-sm">
        <ArrowLeft />{{ t('admin.login.backToScreen') }}
      </a>
      <button class="btn btn-quiet btn-icon" :title="t('dash.shell.settings.theme')" @click="toggleTheme">
        <svg v-if="theme === 'dark'" viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>
        <svg v-else viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
      </button>
    </div>

    <!-- 登录卡 -->
    <div class="w-full max-w-[380px]">
      <div class="mb-5 flex flex-col items-center text-center">
        <img src="/favicon.svg" class="mb-3 h-12 w-12 rounded-xl" alt="" />
        <h1 class="text-xl font-bold tracking-tight" style="color: var(--ink-strong)">{{ t('admin.login.title') }}</h1>
        <p class="mt-1 text-xs" style="color: var(--ink-2)">{{ t('admin.login.desc') }}</p>
      </div>

      <div class="card p-5 sm:p-6">
        <div
          v-if="auth.error"
          class="mb-4 flex items-start gap-2 rounded-lg border p-3 text-xs leading-relaxed"
          style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down)"
          role="alert"
        >
          <AlertCircle class="mt-0.5 h-4 w-4 shrink-0" />
          <span>{{ auth.error }}</span>
        </div>

        <form class="space-y-4" @submit.prevent="handleLogin">
          <div>
            <label class="form-label" for="login-user">{{ t('admin.login.username') }}</label>
            <input id="login-user" v-model="username" type="text" autocomplete="username" class="field" :placeholder="t('admin.shell.common.inputPlaceholder')" />
          </div>
          <div>
            <label class="form-label" for="login-pwd">{{ t('admin.login.password') }}</label>
            <div class="relative">
              <input
                id="login-pwd"
                v-model="password"
                :type="showPwd ? 'text' : 'password'"
                autocomplete="current-password"
                class="field pe-10"
              />
              <button
                type="button"
                class="btn btn-quiet btn-icon absolute end-1 top-1/2 h-7 w-7 -translate-y-1/2"
                :aria-label="showPwd ? 'hide password' : 'show password'"
                @click="showPwd = !showPwd"
              >
                <EyeOff v-if="showPwd" class="h-3.5 w-3.5" />
                <Eye v-else class="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <button type="submit" class="btn btn-primary w-full" :disabled="loading || !username || !password">
            <LogIn v-if="!loading" />
            <Loader2 v-else class="animate-spin" />
            {{ loading ? t('admin.login.submitting') : t('admin.login.submit') }}
          </button>
        </form>

        <p class="mt-4 text-xs leading-relaxed" style="color: var(--ink-3)">
          {{ t('admin.login.rateHint') }}
        </p>
      </div>

      <p class="num mt-5 text-center text-2xs" style="color: var(--ink-3)">
        R20 Quantum Trader · {{ APP_VERSION }} · {{ t('admin.login.secured') }}
      </p>
    </div>
  </div>
</template>
