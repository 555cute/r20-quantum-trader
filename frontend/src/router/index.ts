import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

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
  { path: '/docs', name: 'docs', component: () => import('../views/DocsView.vue'), meta: { isPublic: true } },
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
      { path: 'news', name: 'admin-news', component: () => import('../views/admin/NewsPage.vue') },
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
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
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
  '/': 'R20量子交易系统 | 机构级加密货币波段量化终端 & AI交易主脑',
  '/trading': '实盘矩阵 | R20量子交易系统',
  '/factors': 'AI 推演 · 决策审计 | R20量子交易系统',
  '/news': '舆情情报 · 聪明钱 | R20量子交易系统',
  '/lab': '自进化 · 认知中枢 | R20量子交易系统',
  '/history': '交易台账 · 生命周期 | R20量子交易系统',
  '/docs': '官方文档 | R20量子交易系统',
}

router.afterEach((to) => {
  let title = 'R20 量子交易系统'
  let isNoIndex = false

  if (to.path.startsWith('/admin')) {
    isNoIndex = true
    title = '管理控制台 · R20'
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
})

export default router
