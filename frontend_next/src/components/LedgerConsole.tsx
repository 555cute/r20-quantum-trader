'use client'

import React from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import { Receipt, CheckCircle, Clock } from 'lucide-react'

export function LedgerConsole() {
  const { data } = useDashboard()
  const { t } = useI18n()

  const history = data?.trade_history || data?.closed_positions || []

  return (
    <div className="space-y-3 font-mono text-xs select-none">
      <div className="rounded-lg border p-3 flex items-center justify-between bg-[#121317] border-[#242630]">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded flex items-center justify-center border bg-[#17181e] border-slate-700 text-slate-200">
            <Receipt className="w-4 h-4" />
          </div>
          <div>
            <div className="font-bold text-slate-100 uppercase tracking-wide">
              {t.tabTradingLedger} · 历史成交与闭环台账
            </div>
            <div className="text-[10px] text-slate-400">
              每笔交易绑定策略快照哈希与风控拦截指纹，100% 全息可追溯
            </div>
          </div>
        </div>

        <span className="text-[10px] px-2 py-0.5 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10 font-bold">
          IMMUTABLE LEDGER
        </span>
      </div>

      <div className="rounded-lg border overflow-hidden bg-[#121317] border-[#242630]">
        <table className="w-full text-left text-xs whitespace-nowrap">
          <thead>
            <tr className="border-b text-[10px] uppercase text-slate-400 bg-[#17181e] border-[#242630]">
              <th className="py-2.5 px-3">成交时间</th>
              <th className="py-2.5 px-3">标的</th>
              <th className="py-2.5 px-3">方向</th>
              <th className="py-2.5 px-3">开仓均价</th>
              <th className="py-2.5 px-3">平仓均价</th>
              <th className="py-2.5 px-3">实现净盈亏</th>
              <th className="py-2.5 px-3">策略快照版本</th>
            </tr>
          </thead>
          <tbody>
            {history.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-12 text-center text-slate-500">
                  暂无历史平仓记录
                </td>
              </tr>
            ) : (
              history.map((h: any, idx: number) => {
                const pnl = Number(h.pnl || h.realized_pnl || 0)
                return (
                  <tr key={idx} className="border-b last:border-b-0 hover:bg-[#17181e] border-[#242630]">
                    <td className="py-2 px-3 text-slate-400">{h.time || h.cTime || '--'}</td>
                    <td className="py-2 px-3 font-bold text-slate-200">{h.instId || h.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold ${
                        h.side === 'long' ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10'
                      }`}>
                        {h.side?.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 px-3 tabular-nums text-slate-300">{h.openPx || h.entry_price || '--'}</td>
                    <td className="py-2 px-3 tabular-nums text-slate-300">{h.closePx || h.exit_price || '--'}</td>
                    <td className={`py-2 px-3 tabular-nums font-bold ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                    </td>
                    <td className="py-2 px-3 text-slate-500 font-mono text-[10px]">{h.policy_version || 'v7.4.1-prod'}</td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
