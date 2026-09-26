import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useI18n } from '../composables/useI18n'
import { allAdminItems } from '../config/nav'

/**
 * 路由表：path 与后端钉扎路由严格一致（SEO/CF 缓存/test_docs_images_route）。
 * 前台 6 条 path 全部映射 DashboardLayout，meta.tab 区分视图。
 */
const routes: RouteRecordRaw[] = [
  { path: '/', name: 'dashboard', component: () => import('../layouts/DashboardLayout.vue'), meta: { isPublic: true } },
  { path: '/trading', name: 'dashboard-trading', component: () => import('../layouts/DashboardLayout.vue'), meta: { isPublic: true, tab: 'trading' } },
  { path: '/factors', name: 'dashboard-factors', component: () => import('../layouts/DashboardLayout.vue'), meta: { isPublic: true, tab: 'factors' } },
  { path: '/news', name: 'dashboard-news', component: () => import('../layouts/DashboardLayout.vue'), meta: { isPublic: true, tab: 'news' } },
  { path: '/lab', name: 'dashboard-lab', component: () => import('../layouts/DashboardLayout.vue'), meta: { isPublic: true, tab: 'lab' } },
  { path: '/history', name: 'dashboard-history', component: () => import('../layouts/DashboardLayout.vue'), meta: { isPublic: true, tab: 'history' } },
  { path: '/docs', name: 'docs', component: () => import('../views/docs/DocsView.vue'), meta: { isPublic: true } },
  { path: '/doc', redirect: '/docs' },
  {
    path: '/admin',
    component: () => import('../layouts/AdminLayout.vue'),
    meta: { requiresAuth: true, isPublic: false },
    children: [
      { path: '', redirect: '/admin/overview' },
      { path: 'overview', name: 'admin-overview', component: () => import('../views/admin/OverviewPage.vue') },
      { path: 'security', name: 'admin-security', component: () => import('../views/admin/SecurityPage.vue') },
      { path: 'symbols', redirect: '/admin/security' },
      { path: 'manual-trade', redirect: '/admin/security' },
      { path: 'backups', redirect: '/admin/backup' },
      { path: 'council', name: 'admin-council', component: () => import('../views/admin/CouncilPage.vue') },
      { path: 'llm', name: 'admin-llm', component: () => import('../views/admin/LlmPage.vue') },
      { path: 'notify', name: 'admin-notify', component: () => import('../views/admin/NotifyPage.vue') },
      { path: 'about', name: 'admin-about', component: () => import('../views/admin/AboutPage.vue') },
      { path: 'decisions', name: 'admin-decisions', component: () => import('../views/admin/DecisionsPage.vue') },
      { path: 'gateway', name: 'admin-gateway', component: () => import('../views/admin/GatewayPage.vue') },
      { path: 'promptlib', name: 'admin-promptlib', component: () => import('../views/admin/PromptStudioPage.vue') },
      { path: 'evolution', name: 'admin-evolution', component: () => import('../views/admin/EvolutionPage.vue') },
      { path: 'interceptors', name: 'admin-interceptors', component: () => import('../views/admin/InterceptorsPage.vue') },
      { path: 'risk', name: 'admin-risk', component: () => import('../views/admin/RiskPage.vue') },
      { path: 'policy', name: 'admin-policy', component: () => import('../views/admin/PolicySnapshotPage.vue') },
      { path: 'agents', name: 'admin-agents', component: () => import('../views/admin/AgentsPage.vue') },
      { path: 'backup', name: 'admin-backup', component: () => import('../views/admin/BackupPage.vue') },
      { path: 'plugins', name: 'admin-plugins', component: () => import('../views/admin/PluginsPage.vue') },
      { path: 'audit', name: 'admin-audit', component: () => import('../views/admin/AuditPage.vue') },
      { path: 'adminsys', name: 'admin-adminsys', component: () => import('../views/admin/AdminSysPage.vue') },
    ],
  },
  {
    path: '/admin/login',
    name: 'admin-login',
    component: () => import('../views/admin/LoginPage.vue'),
    meta: { isPublic: true },
  },
  {
    path: '/login',
    redirect: '/admin/login',
  },
  // 批 45：兜底路由。此前**没有** catch-all，拼错的地址渲染出一整页空白
  // （实测 body 文本长度 0、无标题、无回退入口）；后端对未知路径回的是
  // `{"detail":"Not Found"}` JSON，前端也曾把 SPA 外壳渲染成空白。
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFoundView.vue'),
    meta: { isPublic: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

router.onError((error) => {
  const msg = error?.message || ''
  const isChunkOrModuleError =
    /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Load failed|Unable to preload CSS|Failed to resolve module specifier/i.test(
      msg,
    )
  if (isChunkOrModuleError) {
    const key = 'r20_chunk_reload_lock'
    const attemptsKey = 'r20_chunk_reload_attempts'
    const lastReload = parseInt(sessionStorage.getItem(key) || '0', 10)
    const attempts = parseInt(sessionStorage.getItem(attemptsKey) || '0', 10)
    const now = Date.now()

    if (now - lastReload > 3000 && attempts < 2) {
      sessionStorage.setItem(key, String(now))
      sessionStorage.setItem(attemptsKey, String(attempts + 1))
      const url = new URL(window.location.href)
      url.searchParams.set('_v', String(now))
      window.location.href = url.toString()
    } else {
      sessionStorage.removeItem(attemptsKey)
    }
  }
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    // Try restore session from localStorage
    auth.restoreSession()
    if (!auth.isAuthenticated) {
      return { name: 'admin-login' }
    }
  }
  // Redirect logged-in users away from login page
  if (to.name === 'admin-login' && auth.isAuthenticated) {
    return { name: 'admin-overview' }
  }
})

/* SEO 标题：中文为主（与后端钉扎测试与 CF 缓存语义一致），后台 noindex */
const PUBLIC_TITLES: Record<string, string> = {
  '/': 'AstraQuant 量化交易系统 | 机构级加密货币波段量化终端 & AI交易主脑',
  '/trading': '实盘矩阵 | AstraQuant 量化交易系统',
  '/factors': 'AI 推演 · 决策审计 | AstraQuant 量化交易系统',
  '/news': '舆情情报 · 聪明钱 | AstraQuant 量化交易系统',
  '/lab': '自进化 · 认知中枢 | AstraQuant 量化交易系统',
  '/history': '交易台账 · 生命周期 | AstraQuant 量化交易系统',
  '/docs': '官方文档 | AstraQuant 量化交易系统',
}

export function updateDocumentTitle(to = router.currentRoute.value) {
  const { t } = useI18n()
  let title = 'AstraQuant 量化交易系统'
  let isNoIndex = false

  if (to.name === 'not-found') {
    isNoIndex = true
    const notFoundText = t('common.notFound.title') || '页面不存在'
    title = to.path.startsWith('/admin')
      ? `${notFoundText} · ${t('nav.actions.console')} · R20`
      : `${notFoundText} · R20`
  } else if (to.path === '/admin/login' || to.name === 'admin-login') {
    isNoIndex = true
    title = `${t('admin.login.submit')} · ${t('nav.actions.console')} · R20`
  } else if (to.path.startsWith('/admin')) {
    isNoIndex = true
    const hit = allAdminItems.find((item) => item.path === to.path || item.key === to.name)
    const pageName = hit ? t(hit.labelKey) : ''
    title = pageName
      ? `${pageName} · ${t('nav.actions.console')} · R20`
      : `${t('nav.actions.console')} · R20`
  } else if (PUBLIC_TITLES[to.path]) {
    title = PUBLIC_TITLES[to.path]
  }

  document.title = title

  // Ensure search engines do not index administrative routes
  let robotsMeta = document.querySelector('meta[name="robots"]') as HTMLMetaElement | null
  if (isNoIndex) {
    if (!robotsMeta) {
      robotsMeta = document.createElement('meta')
      robotsMeta.name = 'robots'
      document.head.appendChild(robotsMeta)
    }
    robotsMeta.content = 'noindex, nofollow, noarchive'
  } else if (robotsMeta) {
    robotsMeta.content = 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1'
  }
}

router.afterEach((to) => {
  updateDocumentTitle(to)
  try {
    sessionStorage.removeItem('r20_chunk_reload_attempts')
  } catch {
    // ignore
  }
})

if (typeof window !== 'undefined') {
  window.addEventListener('r20:locale-changed', () => updateDocumentTitle())
}

export default router
