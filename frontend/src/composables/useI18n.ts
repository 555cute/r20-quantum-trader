import { ref, computed } from 'vue'

export type Locale = 'zh' | 'en'

const getInitialLocale = (): Locale => {
  if (typeof window === 'undefined') return 'zh'
  try {
    const saved = localStorage.getItem('r20_lang')
    if (saved === 'en' || saved === 'zh') return saved
    const browserLang = navigator.language || ''
    if (browserLang.toLowerCase().startsWith('zh')) return 'zh'
    return 'en'
  } catch {
    return 'zh'
  }
}

const currentLocale = ref<Locale>(getInitialLocale())

export const messages: Record<Locale, Record<string, string>> = {
  zh: {
    terminal: 'MASTER TERMINAL',
    terminalSub: '主交易台',
    radar: 'RADAR & DYNAMICS',
    radarSub: '微积分动能',
    news: 'NEWS & FLOWS',
    newsSub: '全网舆情',
    labs: 'QUANTUM LABS',
    labsSub: '前沿试验田',
    ledger: 'TRADING LEDGER',
    ledgerSub: '交易台账',
    admin: '管理控制台',
    adminSub: '风控治理',
    docs: '开发文档',
    modeDesktop: '机构桌面端',
    modeMobile: '移动终端',
    masterEquity: '官方主账户总权益',
    availMargin: '可用保证金',
    benchmarkNet: '基准累计净盈亏',
    initialBase: '基准本金',
    sessionPerf: '今日已结净收益',
    sessionTrades: '今日成交',
    winrate: '胜率',
    unrealizedUpl: '持仓浮动盈亏',
    livePositions: '笔持仓',
    failClosed: 'FAIL-CLOSED 硬防线',
    liveChart: '实盘专业 K 线视窗',
    tradingViewEngine: 'TradingView 原生引擎',
    collapseChart: '折叠图表',
    expandChart: '展开图表',
    activePositions: '当前持仓',
    pendingOrders: '在途限价与条件单',
    switchLang: '切换语言',
    quickNav: '快速功能导航',
    settings: '系统设置',
    themeLight: '亮色模式',
    themeDark: '暗色模式',
    refresh: '刷新数据',
    logout: '退出登录',
    overview: '系统总览',
    promptStudio: '提示词工作室',
    council: '多模型委员会',
    evolution: 'AI自进化中心',
    interceptors: '物理拦截管线',
    llmConnections: '模型与供应商',
    securityVenues: '交易所与标的池',
    notify: '多渠道推送告警',
    backups: '灾备与全量恢复',
    auditLogs: '操作审计日志',
    systemEngine: '底层执行网关',
  },
  en: {
    terminal: 'MASTER TERMINAL',
    terminalSub: 'Main Desk',
    radar: 'RADAR & DYNAMICS',
    radarSub: 'Dynamics Radar',
    news: 'NEWS & FLOWS',
    newsSub: 'News Intel',
    labs: 'QUANTUM LABS',
    labsSub: 'Quantum Labs',
    ledger: 'TRADING LEDGER',
    ledgerSub: 'Audit Ledger',
    admin: 'ADMIN CONSOLE',
    adminSub: 'Governance',
    docs: 'Docs',
    modeDesktop: 'INSTITUTIONAL',
    modeMobile: 'MOBILE DESK',
    masterEquity: 'MASTER EQUITY',
    availMargin: 'Available Margin',
    benchmarkNet: 'BENCHMARK CUMULATIVE',
    initialBase: 'Initial Baseline',
    sessionPerf: 'SESSION PERFORMANCE',
    sessionTrades: 'Session Trades',
    winrate: 'Win Rate',
    unrealizedUpl: 'UNREALIZED UPL',
    livePositions: 'Positions',
    failClosed: 'FAIL-CLOSED HARD DEFENSE',
    liveChart: 'PRO CANDLESTICK WORKSTATION',
    tradingViewEngine: 'TradingView Native Engine',
    collapseChart: 'Collapse Chart',
    expandChart: 'Expand Chart',
    activePositions: 'Active Positions',
    pendingOrders: 'Pending Orders',
    switchLang: 'Switch Language',
    quickNav: 'Quick Navigation',
    settings: 'Settings',
    themeLight: 'Light Mode',
    themeDark: 'Dark Mode',
    refresh: 'Refresh',
    logout: 'Logout',
    overview: 'Overview',
    promptStudio: 'Prompt Studio',
    council: 'Trading Desk Council',
    evolution: 'Evolution Engine',
    interceptors: 'Safety Interceptors',
    llmConnections: 'LLM Connections',
    securityVenues: 'Exchanges & Pairs',
    notify: 'Alert Notifications',
    backups: 'Backup & Recovery',
    auditLogs: 'Audit Logs',
    systemEngine: 'Gateway Engine',
  }
}

export function useI18n() {
  function setLocale(lang: Locale) {
    currentLocale.value = lang
    try {
      localStorage.setItem('r20_lang', lang)
    } catch {
      // fallback
    }
  }

  function toggleLocale() {
    setLocale(currentLocale.value === 'zh' ? 'en' : 'zh')
  }

  const t = computed(() => {
    return messages[currentLocale.value] || messages.zh
  })

  return {
    locale: currentLocale,
    t,
    setLocale,
    toggleLocale,
  }
}
