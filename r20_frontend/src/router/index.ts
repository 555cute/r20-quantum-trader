import { createRouter, createWebHistory } from 'vue-router'
import TerminalLayout from '../layouts/TerminalLayout.vue'
import TradingView from '../views/TradingView.vue'
import AiView from '../views/AiView.vue'
import NewsView from '../views/NewsView.vue'
import EvolutionView from '../views/EvolutionView.vue'
import LedgerView from '../views/LedgerView.vue'
import AdminView from '../views/AdminView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/terminal/trading' },
    {
      path: '/terminal', component: TerminalLayout, redirect: '/terminal/trading', children: [
        { path: 'trading', name: 'trading', component: TradingView, meta: { title: '实盘矩阵' } },
        { path: 'ai', name: 'ai', component: AiView, meta: { title: 'AI 推演' } },
        { path: 'news', name: 'news', component: NewsView, meta: { title: '全网舆情' } },
        { path: 'evolution', name: 'evolution', component: EvolutionView, meta: { title: '自进化' } },
        { path: 'ledger', name: 'ledger', component: LedgerView, meta: { title: '交易台账' } },
      ]
    },
    { path: '/admin', name: 'admin', component: AdminView, meta: { title: '管理后台' } },
    { path: '/:pathMatch(.*)*', redirect: '/terminal/trading' },
  ],
  scrollBehavior: () => ({ top: 0 })
})

router.afterEach(to => { document.title = `${to.meta.title || 'Terminal'} | R20 Quantum Trader` })
export default router
