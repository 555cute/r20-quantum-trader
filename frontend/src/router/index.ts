import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true },
  },
  {
    path: '/trading',
    name: 'dashboard-trading',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'trading' },
  },
  {
    path: '/factors',
    name: 'dashboard-factors',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'factors' },
  },
  {
    path: '/news',
    name: 'dashboard-news',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'news' },
  },
  {
    path: '/lab',
    name: 'dashboard-lab',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'lab' },
  },
  {
    path: '/history',
    name: 'dashboard-history',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'history' },
  },
  {
    path: '/docs',
    name: 'docs',
    component: () => import('../views/DocsView.vue'),
    meta: { isPublic: true },
  },
  {
    path: '/doc',
    redirect: '/docs',
  },
  {
    path: '/admin',
    component: () => import('../views/AdminLayout.vue'),
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

router.afterEach((to) => {
  // Dynamic SEO Title & Meta Management
  let title = 'R20 Quantum Trader | 机构级加密货币波段量化终端 & AI交易主脑'
  let isNoIndex = false

  if (to.path === '/docs' || to.path.startsWith('/docs/')) {
    title = '官方开发与使用指南 | R20 Quantum Trader 文档中心'
  } else if (to.path === '/factors') {
    title = '多因子动能矩阵 | R20 Quantum Trader'
  } else if (to.path === '/news') {
    title = '全网舆情与聪明钱雷达 | R20 Quantum Trader'
  } else if (to.path === '/lab') {
    title = 'AI 策略自进化认知中枢 | R20 Quantum Trader'
  } else if (to.path === '/history') {
    title = '实盘交易台账与复盘审计 | R20 Quantum Trader'
  } else if (to.path.startsWith('/admin')) {
    isNoIndex = true
    const adminLabels: Record<string, string> = {
      'admin-overview': '运行总览',
      'admin-security': '账户与标的池',
      'admin-council': '模型委员会',
      'admin-policy': '策略版本快照',
      'admin-llm': '模型连接与供应商',
      'admin-notify': '消息通知中心',
      'admin-promptlib': '提示词策略方案',
      'admin-evolution': '自进化认知配置',
      'admin-interceptors': '物理拦截插件中心',
      'admin-agents': '受管 Worker 运行单元',
      'admin-gateway': '任务网关与调度计划',
      'admin-decisions': '决策日志与审计流',
      'admin-backup': '备份与还原',
      'admin-plugins': '系统插件',
      'admin-audit': '操作审计记录',
      'admin-adminsys': '管理员与密码',
      'admin-about': '版本与运行栈',
      'admin-login': '管理登录',
    }
    const label = (to.name && adminLabels[to.name as string]) || '控制台'
    title = `${label} · R20 CONTROL`
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
    robotsMeta.content = 'index, follow, max-image-preview:large'
  }
})

export default router
