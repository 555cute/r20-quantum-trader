'use client'

import React, { useEffect, useState } from 'react'
import { useI18n } from '@/i18n/context'
import { Sparkles, Layers, DollarSign, Eye, RefreshCw, TrendingUp } from 'lucide-react'

export function QuantumLabsConsole() {
  const { t } = useI18n()
  const [activePillar, setActivePillar] = useState<'unified_ledger' | 'stat_arb' | 'chart_vision'>('unified_ledger')
  const [ledger, setLedger] = useState<any>(null)
  const [arb, setArb] = useState<any>(null)
  const [vision, setVision] = useState<any>(null)
  const [visionSymbol, setVisionSymbol] = useState('BTC')
  const [visionInterval, setVisionInterval] = useState('15m')
  const [loading, setLoading] = useState(false)

  async function loadData() {
    setLoading(true)
    try {
      if (activePillar === 'unified_ledger') {
        const res = await fetch('/api/v1/lab/unified-ledger').then(r => r.json())
        setLedger(res)
      } else if (activePillar === 'stat_arb') {
        const res = await fetch('/api/v1/lab/stat-arb-matrix').then(r => r.json())
        setArb(res)
      } else if (activePillar === 'chart_vision') {
        const res = await fetch(`/api/v1/lab/chart-vision/${visionSymbol}?interval=${visionInterval}`).then(r => r.json())
        setVision(res)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [activePillar])

  return (
    <div className="space-y-3 font-mono text-xs select-none">
      {/* Top Banner */}
      <div className="rounded-lg border p-3 flex flex-wrap items-center justify-between gap-2 bg-[#121317] border-[#242630]">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded flex items-center justify-center border bg-indigo-500/10 border-indigo-500/30 text-indigo-400">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-slate-100 uppercase tracking-wide">
                {t.labsTitle}
              </span>
              <span className="text-[9px] px-1.5 py-0.2 rounded border border-indigo-500/30 bg-indigo-500/10 text-indigo-400">
                SANDBOX ISOLATED
              </span>
            </div>
            <div className="text-[10px] text-slate-400">
              {t.labsSubtitle}
            </div>
          </div>
        </div>

        <button
          onClick={loadData}
          disabled={loading}
          className="px-2.5 py-1 rounded border flex items-center gap-1.5 cursor-pointer bg-[#17181e] border-[#242630] text-slate-300 hover:bg-[#1e2028]"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          <span>刷新试验</span>
        </button>
      </div>

      {/* Pillars Switcher */}
      <div className="flex items-center gap-1.5 p-1 rounded-lg border bg-[#121317] border-[#242630]">
        <button
          onClick={() => setActivePillar('unified_ledger')}
          className={`px-3 py-1.5 rounded font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
            activePillar === 'unified_ledger'
              ? 'bg-[#f3f4f6] text-[#0a0b0e]'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Layers className="w-3.5 h-3.5" />
          <span>{t.pillar1}</span>
        </button>

        <button
          onClick={() => setActivePillar('stat_arb')}
          className={`px-3 py-1.5 rounded font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
            activePillar === 'stat_arb'
              ? 'bg-emerald-500 text-white'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <DollarSign className="w-3.5 h-3.5" />
          <span>{t.pillar2}</span>
        </button>

        <button
          onClick={() => setActivePillar('chart_vision')}
          className={`px-3 py-1.5 rounded font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
            activePillar === 'chart_vision'
              ? 'bg-indigo-600 text-white'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Eye className="w-3.5 h-3.5" />
          <span>{t.pillar3}</span>
        </button>
      </div>

      {/* PILLAR 1: UNIFIED LEDGER */}
      {activePillar === 'unified_ledger' && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="p-3 rounded-lg border bg-[#121317] border-[#242630]">
              <div className="text-[11px] text-slate-400">全球多所合并总权益</div>
              <div className="text-xl font-black text-emerald-400 mt-0.5 tabular-nums">
                ${ledger?.total_equity_usdt || '--'} USDT
              </div>
            </div>
            <div className="p-3 rounded-lg border bg-[#121317] border-[#242630]">
              <div className="text-[11px] text-slate-400">跨所可用购买力</div>
              <div className="text-xl font-black text-indigo-400 mt-0.5 tabular-nums">
                ${ledger?.total_available_usdt || '--'} USDT
              </div>
            </div>
          </div>

          <div className="rounded-lg border overflow-hidden bg-[#121317] border-[#242630]">
            <table className="w-full text-left text-xs whitespace-nowrap">
              <thead>
                <tr className="border-b text-[10px] uppercase text-slate-400 bg-[#17181e] border-[#242630]">
                  <th className="py-2 px-3">标的</th>
                  <th className="py-2 px-3">合并净头寸敞口</th>
                  <th className="py-2 px-3">方向</th>
                  <th className="py-2 px-3">分所分布</th>
                  <th className="py-2 px-3">未结盈亏</th>
                </tr>
              </thead>
              <tbody>
                {(ledger?.unified_positions || []).map((pos: any) => (
                  <tr key={pos.asset} className="border-b last:border-b-0 border-[#242630]">
                    <td className="py-2 px-3 font-bold text-amber-400">{pos.asset}</td>
                    <td className="py-2 px-3 font-bold text-slate-200">{pos.net_exposure}</td>
                    <td className="py-2 px-3">
                      <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold ${
                        pos.direction === 'LONG' ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10'
                      }`}>
                        {pos.direction}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-slate-400">{pos.venues}</td>
                    <td className={`py-2 px-3 font-bold ${pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl} ({pos.roe_pct}%)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* PILLAR 2: STAT ARB */}
      {activePillar === 'stat_arb' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(arb?.arbitrage_matrix || []).map((item: any) => (
            <div key={item.symbol} className="p-3 rounded-lg border space-y-2 bg-[#121317] border-[#242630]">
              <div className="flex items-center justify-between">
                <span className="font-bold text-sm text-amber-400">{item.symbol}</span>
                <span className={`px-1.5 py-0.2 rounded text-[10px] border ${
                  item.opportunity === 'HIGH_POTENTIAL' ? 'border-emerald-500/30 text-emerald-400 font-bold bg-emerald-500/10' : 'border-slate-700 text-slate-400'
                }`}>
                  {item.opportunity === 'HIGH_POTENTIAL' ? '✦ 存在套利机会' : '常态监控'}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-1.5 text-center text-[10px] py-1 border-t border-b border-[#242630]">
                <div>OKX: <strong className="text-slate-200">{item.prices.okx}</strong></div>
                <div>BN: <strong className="text-slate-200">{item.prices.binance}</strong></div>
                <div>Gate: <strong className="text-slate-200">{item.prices.gate}</strong></div>
              </div>
              <div className="text-[11px] text-slate-400 space-y-0.5">
                <div>价差散度: <strong className="text-slate-200">{item.spread_disparity_pct}%</strong></div>
                <div>预估对冲套利 APR: <strong className="text-emerald-400 font-bold">{item.estimated_arb_apr_pct}%</strong></div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* PILLAR 3: CHART VISION */}
      {activePillar === 'chart_vision' && (
        <div className="p-4 rounded-lg border flex flex-col items-center justify-center min-h-[380px] bg-[#121317] border-[#242630] space-y-3">
          <div className="flex items-center gap-2">
            <select
              value={visionSymbol}
              onChange={(e) => { setVisionSymbol(e.target.value); loadData(); }}
              className="rounded px-2 py-1 text-xs outline-none border bg-[#0e0f13] border-slate-700 text-slate-200"
            >
              <option value="BTC">BTC</option>
              <option value="ETH">ETH</option>
              <option value="SOL">SOL</option>
            </select>
            <select
              value={visionInterval}
              onChange={(e) => { setVisionInterval(e.target.value); loadData(); }}
              className="rounded px-2 py-1 text-xs outline-none border bg-[#0e0f13] border-slate-700 text-slate-200"
            >
              <option value="15m">15m</option>
              <option value="1h">1H</option>
              <option value="4h">4H</option>
            </select>
            <button
              onClick={loadData}
              className="px-3 py-1 rounded text-xs font-bold bg-indigo-600 text-white cursor-pointer hover:bg-indigo-500"
            >
              绘制视觉帧
            </button>
          </div>

          {vision?.image_data_base64 ? (
            <div className="space-y-2 text-center">
              <img src={vision.image_data_base64} alt="Chart Vision Frame" className="rounded border border-[#242630] max-w-full" />
              <div className="text-[10px] text-slate-500">
                本地 PIL 毫秒级直绘 · 蜡烛根数: {vision.candles_rendered} · 最新收盘: {vision.latest_close}
              </div>
            </div>
          ) : (
            <div className="text-slate-500 text-xs">正在渲染视觉帧...</div>
          )}
        </div>
      )}
    </div>
  )
}
