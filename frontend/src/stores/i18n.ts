import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export type Locale = 'zh' | 'en'

export const zhMessages: Record<string, string> = {
  // 品牌与模式
  appName: 'R20 量子操盘系统',
  appMode: '机构专业版',
  appModeMobile: '移动专业版',
  systemStatus: '系统运行正常',
  hardDefense: '熔断拦截硬防线',

  // 顶栏五大核心分页（全新严谨命名）
  tabTrading: '综合操盘',
  tabTradingSub: '实时图表与委托执行',
  tabAiBrain: '决策中枢',
  tabAiBrainSub: '投委会辩论与微积分动能',
  tabMarket: '市场全息',
  tabMarketSub: '多交易所舆情与资金流',
  tabLabs: '量子实验室',
  tabLabsSub: '跨所合并资产与统计套利',
  tabLedger: '审计台账',
  tabLedgerSub: '历史成交与策略快照',

  // 顶部四大核心财务指标卡
  totalEquity: '主账户总权益',
  availMargin: '可用保证金',
  benchReturn: '基准累计收益',
  benchBase: '基准初始本金',
  todayPnl: '今日已结盈亏',
  todayTrades: '今日成交笔数',
  winRate: '系统胜率',
  unrealizedPnl: '当前持仓浮盈',
  positionCount: '持仓笔数',
  ocoProtection: '云端保护挂单',

  // 综合操盘页
  chartTitle: '专业多周期行情工作站',
  tradingViewEngine: '交易所直连图表引擎',
  collapse: '收起图表',
  expand: '展开图表',
  activePositions: '当前活动持仓',
  pendingOrders: '在途限价与条件委托',
  noPositions: '当前无活跃仓位，严格遵守入场条件',
  noOrders: '当前无挂起委托，系统处于待命状态',
  entryPrice: '开仓均价',
  markPrice: '当前标记价',
  liquidationPrice: '预估强平价',
  positionSize: '持仓规模',
  marginRatio: '保证金率',
  takeProfit: '止盈触发价',
  stopLoss: '止损触发价',
  actionBuy: '顺势做多',
  actionSell: '顺势做空',
  actionWait: '空仓观望',

  // 决策中枢与动能雷达
  councilHeader: '投委会三方方案交叉质询与裁决',
  macroAssessment: '宏观大级别推演基调',
  dynamicsRadar: '主力标的微积分动能矩阵',
  velocity: '变化速度',
  acceleration: '动能加速度',
  jerk: '波动冲击率',
  trendStrength: '趋势强度',
  smartMoney: '聪明钱偏向',
  netInflow: '净流动资金',
  confidence: '置信度评分',
  clickToInspect: '点击卡片下钻深入推演详情',

  // 市场全息页
  newsTitle: '多交易所全要素实时行情与舆情情报网',
  newsSubtitle: '汇聚官方公告、突发新闻与链上异动监控',
  filterAll: '全平台全息',
  filterOkx: 'OKX 快讯',
  filterBinance: '币安官方公告',
  filterGate: 'Gate.io 异动',
  noNews: '当前筛选维度下暂无突发消息',
  originalLink: '查看原文',
  circuitBreakerActive: '黑天鹅熔断保护生效中',
  circuitBreakerNormal: '常态化情绪监控中',

  // 实验室
  labTitle: '前沿量化试验田',
  labSubtitle: '沙盒隔离运行 · 跨代算法成熟后原子化合入实盘',
  labPillar1: '跨所合并资产与头寸台账',
  labPillar2: '跨交易所资金费率与价差套利',
  labPillar3: '毫秒级本地视觉蜡烛图渲染',
  refreshLab: '重新计算试验数据',

  // 审计台账
  ledgerTitle: '已平仓历史交易与决策复盘台账',
  ledgerSubtitle: '每笔交易严格绑定策略快照哈希与风控拦截指纹，全量防篡改可追溯',
  tradeTime: '成交时间',
  symbol: '交易标的',
  direction: '交易方向',
  closePnl: '实现净盈亏',
  policySnapshot: '策略快照版本',

  // 通用操作与后台
  adminConsole: '管理控制台',
  docs: '系统文档',
  refresh: '刷新数据',
  logout: '退出系统',
  themeLight: '亮色模式',
  themeDark: '暗色模式',
  switchLang: '切换语言',
  langName: 'English',
  menu: '功能导航',
  saveSuccess: '保存配置成功',
  saveFailed: '保存配置失败',

  // 后台四大模块
  adminNavGroup1: '监控总览',
  adminNavGroup2: '策略与模型',
  adminNavGroup3: '风控与网关',
  adminNavGroup4: '运维与安全',
  adminOverview: '运行概览',
  adminDecisions: '决策日志',
  adminPrompt: '提示词工作室',
  adminLlm: '模型供应商连接',
  adminCouncil: '投委会架构配置',
  adminEvolution: 'AI自进化中枢',
  adminInterceptors: '物理拦截管线',
  adminVenues: '交易所与标的池',
  adminGateway: '任务执行网关',
  adminPolicy: '策略版本快照',
  adminNotify: '多通道告警推送',
  adminBackup: '灾备还原中心',
  adminAudit: '审计操作日志',
  adminAuth: '管理员与密钥凭证',
  adminAbout: '系统版本与架构',
}

export const enMessages: Record<string, string> = {
  // Brand & Mode
  appName: 'R20 QUANTUM TRADER',
  appMode: 'INSTITUTIONAL DESK',
  appModeMobile: 'MOBILE DESK',
  systemStatus: 'OPERATIONAL',
  hardDefense: 'FAIL-CLOSED DEFENSE',

  // 5 Top Tabs
  tabTrading: 'MASTER DESK',
  tabTradingSub: 'Live Chart & Order Execution',
  tabAiBrain: 'DECISION BRAIN',
  tabAiBrainSub: 'Council Debate & Calculus Dynamics',
  tabMarket: 'MARKET INTEL',
  tabMarketSub: 'Multi-Venue News & Flows',
  tabLabs: 'QUANTUM LABS',
  tabLabsSub: 'Cross-Venue Ledger & Stat-Arb',
  tabLedger: 'AUDIT LEDGER',
  tabLedgerSub: 'Closed Trades & Policy Snapshot',

  // 4 Core Financial Metric Cards
  totalEquity: 'MASTER TOTAL EQUITY',
  availMargin: 'Available Margin',
  benchReturn: 'BENCHMARK CUMULATIVE',
  benchBase: 'Initial Baseline',
  todayPnl: 'SESSION REALIZED P&L',
  todayTrades: 'Session Trades',
  winRate: 'Win Rate',
  unrealizedPnl: 'UNREALIZED FLOATING P&L',
  positionCount: 'Positions',
  ocoProtection: 'Cloud OCO Protection',

  // Master Trading Desk
  chartTitle: 'PROFESSIONAL MULTI-TIMEFRAME CHART',
  tradingViewEngine: 'Direct Exchange Chart Engine',
  collapse: 'Collapse Chart',
  expand: 'Expand Chart',
  activePositions: 'Active Positions',
  pendingOrders: 'Pending Orders',
  noPositions: 'No active exposure. Strict gateway conditions enforced.',
  noOrders: 'No pending orders. System on standby.',
  entryPrice: 'Entry Price',
  markPrice: 'Mark Price',
  liquidationPrice: 'Est. Liquidation',
  positionSize: 'Size',
  marginRatio: 'Margin Ratio',
  takeProfit: 'Take Profit',
  stopLoss: 'Stop Loss',
  actionBuy: 'BUY LONG',
  actionSell: 'SELL SHORT',
  actionWait: 'WAIT / STANDBY',

  // Decision Brain & Dynamics Radar
  councilHeader: 'Trading Desk Council Cross-Examination & Verdict',
  macroAssessment: 'Macro Multi-Timeframe Assessment Baseline',
  dynamicsRadar: 'Micro-Calculus Dynamics Matrix',
  velocity: 'Velocity v',
  acceleration: 'Accel a',
  jerk: 'Jerk j',
  trendStrength: 'ADX Trend',
  smartMoney: 'Smart Money Long',
  netInflow: 'Net Flow',
  confidence: 'Confidence Score',
  clickToInspect: 'Click card to inspect calculus derivation',

  // Market Intel
  newsTitle: 'Multi-Exchange Real-Time News & Intelligence Network',
  newsSubtitle: 'Official announcements, breaking headlines & on-chain alerts',
  filterAll: 'All Venues',
  filterOkx: 'OKX News',
  filterBinance: 'Binance Announcements',
  filterGate: 'Gate.io Alerts',
  noNews: 'No breaking news under selected filter.',
  originalLink: 'Source',
  circuitBreakerActive: 'Black Swan Circuit Breaker ACTIVE',
  circuitBreakerNormal: 'Normal Sentiment Monitoring',

  // Labs
  labTitle: 'Frontier Quantum Sandbox',
  labSubtitle: 'Isolated Sandbox · Mature models merged atomically into prod',
  labPillar1: 'Unified Cross-Exchange Ledger & Exposure',
  labPillar2: 'Cross-Exchange Funding & Spread Arbitrage',
  labPillar3: 'Sub-Millisecond PIL Candlestick Rendering',
  refreshLab: 'Recalculate Experiments',

  // Ledger
  ledgerTitle: 'Closed Trading Ledger & Performance Audit',
  ledgerSubtitle: 'Every trade cryptographically bound to policy hash snapshot and risk fingerprint',
  tradeTime: 'Executed Time',
  symbol: 'Asset',
  direction: 'Direction',
  closePnl: 'Realized Net P&L',
  policySnapshot: 'Policy Snapshot',

  // Common Operations & Admin
  adminConsole: 'ADMIN CONSOLE',
  docs: 'Documentation',
  refresh: 'Refresh',
  logout: 'Sign Out',
  themeLight: 'Light Mode',
  themeDark: 'Dark Mode',
  switchLang: 'Language',
  langName: '中文',
  menu: 'Navigation',
  saveSuccess: 'Configuration saved successfully',
  saveFailed: 'Failed to save configuration',

  // Admin 4 Groups
  adminNavGroup1: 'MONITORING',
  adminNavGroup2: 'STRATEGY & AI',
  adminNavGroup3: 'RISK & GATEWAY',
  adminNavGroup4: 'GOVERNANCE',
  adminOverview: 'System Overview',
  adminDecisions: 'Decision Stream',
  adminPrompt: 'Prompt Studio',
  adminLlm: 'LLM Providers',
  adminCouncil: 'Council Configuration',
  adminEvolution: 'Self-Evolution Engine',
  adminInterceptors: 'Physical Interceptors',
  adminVenues: 'Venues & Pairs',
  adminGateway: 'Execution Gateway',
  adminPolicy: 'Policy Snapshots',
  adminNotify: 'Alert Dispatcher',
  adminBackup: 'Disaster Recovery',
  adminAudit: 'Audit Records',
  adminAuth: 'Credentials & Auth',
  adminAbout: 'Version & Stack',
}

export const useI18nStore = defineStore('i18n', () => {
  const getInitLocale = (): Locale => {
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

  const locale = ref<Locale>(getInitLocale())

  const t = computed(() => {
    return locale.value === 'en' ? enMessages : zhMessages
  })

  function setLocale(newLocale: Locale) {
    locale.value = newLocale
    try {
      localStorage.setItem('r20_lang', newLocale)
      document.documentElement.lang = newLocale === 'zh' ? 'zh-CN' : 'en'
    } catch {
      // ignore
    }
  }

  function toggleLocale() {
    setLocale(locale.value === 'zh' ? 'en' : 'zh')
  }

  return {
    locale,
    t,
    setLocale,
    toggleLocale,
  }
})
