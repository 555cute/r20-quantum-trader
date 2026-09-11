<script setup lang="ts">
/**
 * 管理员登录页：机构级高安全量化终端美学
 * 采用深邃微网格背景、品牌橙聚焦环、敏感凭据脱敏防窥与防暴力破解速率提示。
 */
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { LogIn, AlertCircle, Loader2, Eye, EyeOff, ArrowLeft, ShieldCheck, Lock, Terminal } from 'lucide-vue-next';
import { useAuthStore } from '../../stores/auth';
import { useTheme } from '../../composables/useTheme';
import { useI18n } from '../../composables/useI18n';
import { APP_VERSION, APP_NAME } from '../../config/version';

const auth = useAuthStore();
const router = useRouter();
const { theme, toggleTheme } = useTheme();
const { t } = useI18n();

const username = ref('');
const password = ref('');
const showPwd = ref(false);
const loading = ref(false);

async function handleLogin() {
  if (!username.value || !password.value || loading.value) return;
  loading.value = true;
  const ok = await auth.login(username.value, password.value);
  loading.value = false;
  if (ok) router.push('/admin/overview');
}
</script>

<template>
  <div
    class="relative flex min-h-screen flex-col items-center justify-center p-4 selection:bg-amber-500/30 overflow-hidden"
    style="background-color: var(--surface-0); color: var(--ink-1)"
  >
    <!-- 极客终端背景微网格 (Subtle Grid) -->
    <div
      class="absolute inset-0 pointer-events-none opacity-20"
      style="
        background-image: linear-gradient(to right, var(--line-1) 1px, transparent 1px),
                          linear-gradient(to bottom, var(--line-1) 1px, transparent 1px);
        background-size: 32px 32px;
      "
    />

    <!-- 角落导航工具条 -->
    <div class="absolute inset-x-4 top-4 flex items-center justify-between z-10 sm:inset-x-8 sm:top-6">
      <a href="/" class="btn btn-ghost btn-sm flex items-center gap-1.5 text-xs font-semibold">
        <ArrowLeft class="h-3.5 w-3.5" />
        <span>{{ t('admin.login.backToScreen') }}</span>
      </a>
      <button
        type="button"
        class="btn btn-quiet btn-icon"
        :title="t('dash.shell.settings.theme')"
        @click="toggleTheme"
      >
        <svg v-if="theme === 'dark'" viewBox="0 0 24 24" class="h-4 w-4 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>
        <svg v-else viewBox="0 0 24 24" class="h-4 w-4 text-[var(--ink-2)]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
      </button>
    </div>

    <!-- 登录终端卡片主体 -->
    <div class="relative w-full max-w-[400px] z-10">
      <!-- 头部标识 -->
      <div class="mb-5 flex flex-col items-center text-center">
        <div class="relative mb-3 flex items-center justify-center">
          <img src="/favicon.svg" class="h-12 w-12 rounded-xl shadow-lg shadow-black/40" alt="R20" />
          <span class="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--surface-1)] border border-[var(--line-2)]">
            <Lock class="h-2.5 w-2.5 text-[var(--accent)]" />
          </span>
        </div>
        <h1 class="text-xl font-bold tracking-tight text-[var(--ink-strong)]">
          {{ APP_NAME }}
        </h1>
        <p class="mt-1 text-xs text-[var(--ink-2)] flex items-center gap-1.5">
          <Terminal class="h-3 w-3 text-[var(--accent)]" />
          <span>机构级全自动量化控制台</span>
        </p>
      </div>

      <!-- 凭据表单卡片 -->
      <div
        class="card p-6 shadow-2xl border backdrop-blur-xl"
        style="background-color: var(--surface-1); border-color: var(--line-2)"
      >
        <!-- 错误提示 -->
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
            <label class="form-label text-xs font-semibold mb-1" for="login-user">
              {{ t('admin.login.username') }}
            </label>
            <input
              id="login-user"
              v-model="username"
              type="text"
              autocomplete="username"
              class="field h-9 text-xs"
              placeholder="输入管理员账号…"
              required
            />
          </div>

          <div>
            <label class="form-label text-xs font-semibold mb-1" for="login-pwd">
              {{ t('admin.login.password') }}
            </label>
            <div class="relative">
              <input
                id="login-pwd"
                v-model="password"
                :type="showPwd ? 'text' : 'password'"
                autocomplete="current-password"
                class="field h-9 pe-10 text-xs font-mono"
                placeholder="输入访问密钥…"
                required
              />
              <button
                type="button"
                class="btn btn-quiet btn-icon absolute end-1 top-1/2 h-7 w-7 -translate-y-1/2 cursor-pointer"
                :aria-label="showPwd ? 'hide password' : 'show password'"
                @click="showPwd = !showPwd"
              >
                <EyeOff v-if="showPwd" class="h-3.5 w-3.5 text-[var(--ink-2)]" />
                <Eye v-else class="h-3.5 w-3.5 text-[var(--ink-3)]" />
              </button>
            </div>
          </div>

          <button
            type="submit"
            class="btn btn-primary w-full h-9 text-xs font-bold flex items-center justify-center gap-1.5 cursor-pointer shadow-md"
            :disabled="loading || !username || !password"
          >
            <LogIn v-if="!loading" class="h-3.5 w-3.5" />
            <Loader2 v-else class="h-3.5 w-3.5 animate-spin" />
            <span>{{ loading ? t('admin.login.submitting') : t('admin.login.submit') }}</span>
          </button>
        </form>

        <div class="mt-4 flex items-center gap-1.5 border-t pt-3 text-[11px] text-[var(--ink-3)]" style="border-color: var(--line-1)">
          <ShieldCheck class="h-3.5 w-3.5 text-[var(--up)] shrink-0" />
          <span>{{ t('admin.login.rateHint') }}</span>
        </div>
      </div>

      <!-- 底部安全与版本提示 -->
      <div class="mt-5 text-center space-y-1 text-2xs text-[var(--ink-3)]">
        <p class="num font-mono">
          {{ APP_NAME }} · {{ APP_VERSION }} · {{ t('admin.login.secured') }}
        </p>
        <p class="text-[10px] opacity-70">
          OKX · Binance · Gate 三所对等执行基准 · Fail-Closed 物理风控
        </p>
      </div>
    </div>
  </div>
</template>
