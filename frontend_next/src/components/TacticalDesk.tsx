'use client'

import React, { useState } from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import { Layers, Clock, ShieldCheck, ArrowUpRight, ArrowDownRight, Trash2 } from 'lucide-react'

export function TacticalDesk() {
  const { positions, orders } = useDashboard()
  const { t } = useI18n()
  const [activeSubTab, setActiveSubTab] = useState<'positions' | 'orders'>('positions')

  return (
    <div className="rounded-lg border shadow-xs overflow-hidden bg-[#121317] border-[#242630]">
      {/* Sub Tabs */}
      <div className="px-3 py-2 border-b flex items-center justify-between bg-[#17181e] border-[#242630]">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setActiveSubTab('positions')}
            className={`px-3 py-1 rounded text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
              activeSubTab === 'positions'
                ? 'bg-[#f3f4f6] text-[#0a0b0e]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>{t.activePositions} ({positions.length})</span>
          </button>

          <button
            onClick={() => setActiveSubTab('orders')}
            className={`px-3 py-1 rounded text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
              activeSubTab === 'orders'
                ? 'bg-[#f3f4f6] text-[#0a0b0e]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>{t.pendingOrders} ({orders.length})</span>
          </button>
        </div>

        <div className="text-[10px] font-mono text-emerald-400 flex items-center gap-1">
          <ShieldCheck className="w-3 h-3 text-emerald-400" />
          <span>100% OCO CLOUD PROTECTION</span>
        </div>
      </div>

      {/* POSITIONS TABLE */}
      {activeSubTab === 'positions' && (
        <div className="overflow-x-auto min-h-[140px]">
          {positions.length === 0 ? (
            <div className="py-10 text-center text-xs font-mono text-slate-500">
              {t.noPositions}
            </div>
          ) : (
            <table className="w-full text-left text-xs font-mono whitespace-nowrap">
              <thead>
                <tr className="border-b text-[10px] uppercase text-slate-400 bg-[#0e0f13] border-[#242630]">
                  <th className="py-2 px-3">{t.instrument}</th>
                  <th className="py-2 px-2">{t.direction}</th>
                  <th className="py-2 px-2">{t.entryPrice}</th>
                  <th className="py-2 px-2">{t.markPrice}</th>
                  <th className="py-2 px-2">{t.margin}</th>
                  <th className="py-2 px-2">{t.unrealizedPnl}</th>
                  <th className="py-2 px-2">{t.tpPrice}</th>
                  <th className="py-2 px-2">{t.slPrice}</th>
                  <th className="py-2 px-3 text-right">{t.action}</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p: any) => {
                  const isLong = p.side === 'long'
                  const upl = Number(p.upl || p.unrealized_pnl || 0)
                  return (
                    <tr key={p.posId || p.instId} className="border-b last:border-b-0 hover:bg-[#1e2028] border-[#242630]">
                      <td className="py-2 px-3 font-bold text-slate-200">
                        {p.instId || p.symbol}
                      </td>
                      <td className="py-2 px-2">
                        <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold border ${
                          isLong
                            ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
                            : 'text-rose-400 bg-rose-500/10 border-rose-500/30'
                        }`}>
                          {isLong ? 'LONG 多' : 'SHORT 空'} {p.lever || p.leverage}x
                        </span>
                      </td>
                      <td className="py-2 px-2 tabular-nums text-slate-300">{p.avgPx || p.entry_price}</td>
                      <td className="py-2 px-2 tabular-nums text-slate-300">{p.markPx || p.mark_price || '--'}</td>
                      <td className="py-2 px-2 tabular-nums text-indigo-300">${p.margin || p.margin_usdt || '--'}</td>
                      <td className={`py-2 px-2 tabular-nums font-bold ${upl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {upl >= 0 ? '+' : ''}${upl.toFixed(2)}
                      </td>
                      <td className="py-2 px-2 tabular-nums text-emerald-400">{p.tpPx || p.tp_price || '--'}</td>
                      <td className="py-2 px-2 tabular-nums text-rose-400">{p.slPx || p.sl_price || '--'}</td>
                      <td className="py-2 px-3 text-right">
                        <button
                          onClick={() => alert('Fast Close Token Verification')}
                          className="px-2 py-0.5 rounded border text-[10px] text-rose-400 border-rose-500/30 bg-rose-500/10 hover:bg-rose-500/20 cursor-pointer"
                        >
                          {t.closePosition}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ORDERS TABLE */}
      {activeSubTab === 'orders' && (
        <div className="overflow-x-auto min-h-[140px]">
          {orders.length === 0 ? (
            <div className="py-10 text-center text-xs font-mono text-slate-500">
              {t.noOrders}
            </div>
          ) : (
            <table className="w-full text-left text-xs font-mono whitespace-nowrap">
              <thead>
                <tr className="border-b text-[10px] uppercase text-slate-400 bg-[#0e0f13] border-[#242630]">
                  <th className="py-2 px-3">{t.instrument}</th>
                  <th className="py-2 px-2">类型</th>
                  <th className="py-2 px-2">{t.direction}</th>
                  <th className="py-2 px-2">委托价</th>
                  <th className="py-2 px-2">数量</th>
                  <th className="py-2 px-2">触发条件</th>
                  <th className="py-2 px-3 text-right">{t.action}</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o: any) => (
                  <tr key={o.ordId} className="border-b last:border-b-0 hover:bg-[#1e2028] border-[#242630]">
                    <td className="py-2 px-3 font-bold text-slate-200">{o.instId}</td>
                    <td className="py-2 px-2 text-slate-400">{o.ordType || 'LIMIT'}</td>
                    <td className="py-2 px-2">
                      <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold ${
                        o.side === 'buy' ? 'text-emerald-400' : 'text-rose-400'
                      }`}>
                        {o.side?.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 px-2 tabular-nums text-slate-300">{o.px || '--'}</td>
                    <td className="py-2 px-2 tabular-nums text-slate-300">{o.sz || '--'}</td>
                    <td className="py-2 px-2 text-slate-400">{o.tpTriggerPx ? `TP: ${o.tpTriggerPx}` : (o.slTriggerPx ? `SL: ${o.slTriggerPx}` : '--')}</td>
                    <td className="py-2 px-3 text-right">
                      <button className="p-1 rounded text-rose-400 hover:opacity-80">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
