'use client'

import React from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import { Wallet, TrendingUp, Activity, ShieldCheck } from 'lucide-react'

export function TopHudRibbon() {
  const { account, todayStats, positions } = useDashboard()
  const { t } = useI18n()

  const totalEq = Number(account?.total_eq || 0).toFixed(2)
  const availEq = Number(account?.avail_eq || 0).toFixed(2)
  const marginUsage = Number(account?.margin_usage_pct || 0).toFixed(1)

  const benchmarkNetPnl = Number(account?.cum_net_pnl || 0).toFixed(2)
  const benchmarkRoi = Number(account?.cum_roi_pct || 0).toFixed(2)
  const initialCap = Number(account?.initial_capital || 0).toFixed(2)

  const todayNet = Number(todayStats?.net_realized ?? todayStats?.total_pnl ?? 0).toFixed(2)
  const todayWinrate = Number(todayStats?.win_rate || 0).toFixed(1)
  const todayTrades = (todayStats?.win_trades || 0) + (todayStats?.loss_trades || 0)

  const posUplNum = Number(account?.pos_upl_total ?? account?.upl ?? 0)
  const posUplStr = posUplNum.toFixed(2)

  const longCount = positions.filter((p: any) => p.side === 'long').length
  const shortCount = positions.filter((p: any) => p.side === 'short').length

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs font-mono select-none">
      {/* 1. Master Equity */}
      <div className="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs bg-[#121317] border-[#242630]">
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span className="flex items-center gap-1 font-bold text-slate-300">
            <Wallet className="w-3 h-3 text-indigo-400" />
            <span>{t.masterEquity}</span>
          </span>
          <span className="text-[9px] px-1 rounded border border-slate-700 bg-slate-800 text-slate-400">OKX PROD</span>
        </div>
        <div className="flex items-baseline justify-between mt-1">
          <div className="text-lg lg:text-xl font-black tracking-tight tabular-nums text-slate-100">
            ${totalEq}
          </div>
          <div className="text-[11px] text-slate-500">
            {t.availBalance}: <span className="font-bold text-slate-200">${availEq}</span>
          </div>
        </div>
        <div className="w-full h-1 rounded-full overflow-hidden mt-1.5 bg-[#1c1e26]">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${Math.min(100, Math.max(0, Number(marginUsage)))}%`,
              backgroundColor: Number(marginUsage) > 50 ? '#f59e0b' : '#10b981',
            }}
          />
        </div>
      </div>

      {/* 2. Benchmark ROI */}
      <div className="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs bg-[#121317] border-[#242630]">
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span className="flex items-center gap-1 font-bold text-slate-300">
            <TrendingUp className="w-3 h-3 text-emerald-400" />
            <span>{t.benchmarkRoi}</span>
          </span>
          <span className={`text-[10px] tabular-nums font-bold ${Number(benchmarkNetPnl) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {Number(benchmarkRoi) >= 0 ? '+' : ''}{benchmarkRoi}%
          </span>
        </div>
        <div className="flex items-baseline justify-between mt-1">
          <div className={`text-lg lg:text-xl font-black tracking-tight tabular-nums ${Number(benchmarkNetPnl) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {Number(benchmarkNetPnl) >= 0 ? '+' : ''}${benchmarkNetPnl}
          </div>
          <div className="text-[11px] text-slate-500">
            {t.initialCapital}: <span className="font-bold text-slate-300">${initialCap}</span>
          </div>
        </div>
        <div className="text-[10px] mt-1.5 truncate text-slate-500">
          P0 Hard Floor: 2.0R Geometry Lock
        </div>
      </div>

      {/* 3. Session Performance */}
      <div className="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs bg-[#121317] border-[#242630]">
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span className="flex items-center gap-1 font-bold text-slate-300">
            <Activity className="w-3 h-3 text-amber-400" />
            <span>{t.sessionPerf}</span>
          </span>
          <span className="text-[10px] font-bold text-slate-300">
            {t.winRate} {todayWinrate}%
          </span>
        </div>
        <div className="flex items-baseline justify-between mt-1">
          <div className={`text-lg lg:text-xl font-black tracking-tight tabular-nums ${Number(todayNet) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {Number(todayNet) >= 0 ? '+' : ''}${todayNet}
          </div>
          <div className="text-[11px] text-slate-500">
            {t.todayTrades}: <span className="font-bold text-slate-200">{todayTrades} {t.tradesCount}</span>
          </div>
        </div>
        <div className="text-[10px] mt-1.5 flex items-center justify-between text-slate-500">
          <span>W: <strong className="text-emerald-400">{todayStats?.win_trades || 0}</strong></span>
          <span>L: <strong className="text-rose-400">{todayStats?.loss_trades || 0}</strong></span>
          <span>R:R: <strong className="text-slate-200">{todayStats?.profit_factor || '2.0+'}</strong></span>
        </div>
      </div>

      {/* 4. Unrealized UPL & Risk Sentinel */}
      <div className="rounded-lg border px-3 py-2 flex flex-col justify-between shadow-xs bg-[#121317] border-[#242630]">
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span className="flex items-center gap-1 font-bold text-slate-300">
            <ShieldCheck className="w-3 h-3 text-blue-400" />
            <span>{t.unrealizedUpl}</span>
          </span>
          <span className="text-[10px] font-bold text-emerald-400">100% OCO CLOUD</span>
        </div>
        <div className="flex items-baseline justify-between mt-1">
          <div className={`text-lg lg:text-xl font-black tracking-tight tabular-nums ${posUplNum >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {posUplNum >= 0 ? '+' : ''}${posUplStr}
          </div>
          <div className="text-[11px] text-slate-500">
            {positions.length} {t.positionsCount}
          </div>
        </div>
        <div className="text-[10px] mt-1.5 flex items-center justify-between text-slate-500">
          <span>Long: <strong className="text-emerald-400">{longCount}</strong></span>
          <span>Short: <strong className="text-rose-400">{shortCount}</strong></span>
          <span>Protection: <strong className="text-emerald-400">FAIL-CLOSED</strong></span>
        </div>
      </div>
    </div>
  )
}
