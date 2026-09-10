/** US-005 · 三所账户对称卡（环境优先；未知≠填0） */
export const zhVenueAccounts = {
  title: '三所账户',
  desc: '先选资金环境，再看三所真实账户；读不到的数据显式标「—」，不以 0 冒充；实盘与模拟永不加总',
  envLabel: '资金环境',
  envDemo: '模拟盘',
  envLive: '实盘',
  refresh: '刷新',
  loading: '同步中…',
  needsAuth: '登录后台后可见真实账户数据',
  captured: '拉取于',
  unknown: '—',
  venueNames: {
    okx: 'OKX 欧易',
    gate: 'Gate.io',
    binance: 'Binance 币安',
  },
  status: {
    ready: '已同步',
    unavailable: '未接入',
    not_implemented: '未实装',
    degraded: '读取异常',
    unknown: '未知',
  },
  fields: {
    equity: '权益 (USDT)',
    available: '可用保证金',
    positions: '持仓',
    openOrders: '挂单',
    lastSync: '最后同步',
    unitN: '{n} 笔',
  },
};
