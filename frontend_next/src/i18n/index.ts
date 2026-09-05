// Global i18n Dictionary Engine for R20 Quantum Trader
export type Locale = 'zh' | 'en'

export const translations = {
  zh: {
    // Header & Meta
    brand: 'R20 QUANTUM',
    desktopTag: '机构桌面端',
    failClosed: 'FAIL-CLOSED 硬防线',
    utc: 'UTC',
    bjt: '北京时间',
    admin: '管理控制台',
    
    // Venues
    venueOkx: 'OKX 生产主节点',
    venueBinance: 'BINANCE 行情',
    venueGate: 'GATE 深度',

    // Navigation Tabs
    tabMasterTerminal: 'MASTER TERMINAL',
    tabMasterTerminalSub: '主交易台',
    tabRadarDynamics: 'RADAR & DYNAMICS',
    tabRadarDynamicsSub: '微积分动能',
    tabNewsFlows: 'NEWS & FLOWS',
    tabNewsFlowsSub: '全网舆情',
    tabQuantumLabs: 'QUANTUM LABS',
    tabQuantumLabsSub: '前沿试验田',
    tabTradingLedger: 'TRADING LEDGER',
    tabTradingLedgerSub: '交易台账',

    // HUD Ribbon
    masterEquity: '官方主账户总权益',
    availBalance: '可用保证金',
    marginUsage: '保证金占用',
    benchmarkRoi: '基准累计净盈亏',
    initialCapital: '基准本金',
    sessionPerf: '今日已结净收益',
    winRate: '胜率',
    todayTrades: '今日成交',
    tradesCount: '笔',
    unrealizedUpl: '持仓浮动盈亏',
    longShortRatio: '多空分布',
    positionsCount: '笔持仓',
    cloudOcoProtected: '100% 交易所云端 OCO 挂单保护',

    // TradingView & Workstation
    chartTitle: '实盘专业 K 线视窗',
    chartSubtitle: '全要素微积分与多模态形态联动',
    tradingViewBadge: 'TradingView 原生引擎',
    refreshChart: '刷新图表',
    collapseChart: '折叠图表',
    expandChart: '展开图表',

    // Tactical Desk
    tacticalTitle: '实时持仓与挂单管理',
    activePositions: '当前持仓',
    pendingOrders: '在途限价与条件挂单',
    noPositions: '当前无持仓敞口，系统严守 2.0R 盈亏比门禁',
    noOrders: '当前无在途挂单',
    instrument: '标的',
    direction: '方向',
    entryPrice: '入场价',
    markPrice: '当前标记价',
    margin: '占用保证金',
    leverage: '杠杆',
    unrealizedPnl: '未结盈亏',
    tpPrice: '云端止盈',
    slPrice: '云端止损',
    action: '操作',
    closePosition: '平仓',

    // Quantum Cockpit (Right Wing)
    cockpitTitle: 'AI 投资委员会 & 微积分动力学',
    councilVerdicts: '投委会辩论雷达',
    calculusMatrix: '6 大核心标的微积分矩阵',
    traderA: 'Trader A (激进趋势)',
    traderB: 'Trader B (稳健波段)',
    traderC: 'Trader C (均值回归)',
    cioVerdict: 'CIO 首席投资官终审裁决',
    calculusVelocity: '微积分速度 v',
    calculusAccel: '加速度 a',
    calculusJerk: '跃度变化率 j',
    calculusImpulse: '做功冲量 I',
    calculusRegime: '动力学相态',
    energyIntegral: '能量定积分',
    var95: '95% 风险价值 (VaR)',

    // Labs & Multi-Exchange
    labsTitle: '量子前沿实验室 (v8.0 OmniMatrix 试验田)',
    labsSubtitle: '数据与算法在此全真验证，成熟后原子化迁入实盘引擎，绝不干扰现行系统。',
    pillar1: '多所合并资产与持仓台账',
    pillar2: '跨所费率与基差套利监控',
    pillar3: '多模态视觉 K 线图生成',
    unifiedExposure: '跨所合并净头寸敞口',
    aprEstimate: '预估无风险套利年化 (APR)',

    // Common
    loading: '加载中...',
    connected: '已连接',
    disconnected: '连接中断',
    stale: '数据陈旧',
  },
  en: {
    // Header & Meta
    brand: 'R20 QUANTUM',
    desktopTag: 'INSTITUTIONAL DESKTOP',
    failClosed: 'FAIL-CLOSED DEFENSE',
    utc: 'UTC',
    bjt: 'BJT (UTC+8)',
    admin: 'ADMIN CONSOLE',

    // Venues
    venueOkx: 'OKX PROD',
    venueBinance: 'BINANCE FEED',
    venueGate: 'GATE DEPTH',

    // Navigation Tabs
    tabMasterTerminal: 'MASTER TERMINAL',
    tabMasterTerminalSub: 'Trading Desk',
    tabRadarDynamics: 'RADAR & DYNAMICS',
    tabRadarDynamicsSub: 'Micro-Calculus',
    tabNewsFlows: 'NEWS & FLOWS',
    tabNewsFlowsSub: 'Sentiment',
    tabQuantumLabs: 'QUANTUM LABS',
    tabQuantumLabsSub: 'Sandbox',
    tabTradingLedger: 'TRADING LEDGER',
    tabTradingLedgerSub: 'Audit Trail',

    // HUD Ribbon
    masterEquity: 'MASTER TOTAL EQUITY',
    availBalance: 'Avail Margin',
    marginUsage: 'Margin Usage',
    benchmarkRoi: 'BENCHMARK CUMULATIVE',
    initialCapital: 'Initial Baseline',
    sessionPerf: 'SESSION REALIZED P&L',
    winRate: 'Win Rate',
    todayTrades: 'Daily Volume',
    tradesCount: 'trades',
    unrealizedUpl: 'UNREALIZED FLOATING P&L',
    longShortRatio: 'Long/Short',
    positionsCount: 'positions',
    cloudOcoProtected: '100% Exchange Cloud OCO Protected',

    // TradingView & Workstation
    chartTitle: 'Institutional Chart Workstation',
    chartSubtitle: 'Full-Factor Calculus & Multimodal Vision Integration',
    tradingViewBadge: 'TradingView Native Engine',
    refreshChart: 'Refresh Chart',
    collapseChart: 'Collapse',
    expandChart: 'Expand',

    // Tactical Desk
    tacticalTitle: 'Tactical Positions & Active Orders',
    activePositions: 'Active Positions',
    pendingOrders: 'Pending Limit & OCO Orders',
    noPositions: 'No active exposure. Strict R:R >= 2.0 gateway enforced.',
    noOrders: 'No pending orders in-flight.',
    instrument: 'Instrument',
    direction: 'Side',
    entryPrice: 'Entry Price',
    markPrice: 'Mark Price',
    margin: 'Margin',
    leverage: 'Lev',
    unrealizedPnl: 'Unrealized PnL',
    tpPrice: 'Cloud TP',
    slPrice: 'Cloud SL',
    action: 'Action',
    closePosition: 'Close',

    // Quantum Cockpit (Right Wing)
    cockpitTitle: 'AI Council & Micro-Calculus Cockpit',
    councilVerdicts: 'Council Deliberations',
    calculusMatrix: '6-Asset Calculus Dynamics',
    traderA: 'Trader A (Momentum)',
    traderB: 'Trader B (Swing)',
    traderC: 'Trader C (Mean-Reverting)',
    cioVerdict: 'CIO Executive Verdict',
    calculusVelocity: 'Velocity v',
    calculusAccel: 'Acceleration a',
    calculusJerk: 'Jerk j',
    calculusImpulse: 'Impulse I',
    calculusRegime: 'Kinematic Regime',
    energyIntegral: 'Energy Integral',
    var95: '95% VaR Risk',

    // Labs & Multi-Exchange
    labsTitle: 'Quantum Labs Sandbox (v8.0 OmniMatrix Preview)',
    labsSubtitle: 'Pre-flight proving ground: fully isolated from production execution engines.',
    pillar1: 'Multi-Venue Unified Ledger',
    pillar2: 'Cross-Venue Stat-Arb & Funding Rate',
    pillar3: 'Multimodal Local Chart Vision',
    unifiedExposure: 'Unified Net Exposure',
    aprEstimate: 'Estimated Delta-Neutral APR',

    // Common
    loading: 'Loading...',
    connected: 'CONNECTED',
    disconnected: 'DISCONNECTED',
    stale: 'STALE',
  },
} as const
