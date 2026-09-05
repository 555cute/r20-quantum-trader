'use client'

import React, { useEffect, useRef, useState } from 'react'
import { LineChart, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react'
import { useI18n } from '@/i18n/context'

const symbols = [
  { id: 'BTC', label: 'BTC/USDT', tvSymbol: 'BINANCE:BTCUSDT' },
  { id: 'ETH', label: 'ETH/USDT', tvSymbol: 'BINANCE:ETHUSDT' },
  { id: 'SOL', label: 'SOL/USDT', tvSymbol: 'BINANCE:SOLUSDT' },
  { id: 'DOGE', label: 'DOGE/USDT', tvSymbol: 'BINANCE:DOGEUSDT' },
  { id: 'XRP', label: 'XRP/USDT', tvSymbol: 'BINANCE:XRPUSDT' },
  { id: 'PEPE', label: 'PEPE/USDT', tvSymbol: 'BINANCE:PEPEUSDT' },
]

const intervals = [
  { id: '15', label: '15m' },
  { id: '60', label: '1H' },
  { id: '240', label: '4H' },
  { id: 'D', label: '1D' },
]

export function LiveTradingViewChart() {
  const { t, locale } = useI18n()
  const [selectedSymbol, setSelectedSymbol] = useState(symbols[0])
  const [selectedInterval, setSelectedInterval] = useState(intervals[1])
  const [isCollapsed, setIsCollapsed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  function renderWidget() {
    if (!containerRef.value) return
    containerRef.value.innerHTML = ''

    const widgetDiv = document.createElement('div')
    widgetDiv.className = 'tradingview-widget-container__widget'
    widgetDiv.style.height = '100%'
    widgetDiv.style.width = '100%'
    containerRef.value.appendChild(widgetDiv)

    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.type = 'text/javascript'
    script.async = true
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: selectedSymbol.tvSymbol,
      interval: selectedInterval.id,
      timezone: 'Asia/Shanghai',
      theme: 'dark',
      style: '1',
      locale: locale === 'zh' ? 'zh_CN' : 'en',
      enable_publishing: false,
      hide_top_toolbar: false,
      hide_legend: false,
      save_image: false,
      backgroundColor: 'rgba(11, 15, 25, 1)',
      gridColor: 'rgba(255, 255, 255, 0.05)',
      allow_symbol_change: true,
      calendar: false,
      support_host: 'https://www.tradingview.com',
    })
    containerRef.value.appendChild(script)
  }

  useEffect(() => {
    if (!isCollapsed) {
      renderWidget()
    }
  }, [selectedSymbol, selectedInterval, locale, isCollapsed])

  return (
    <div className="rounded-lg border shadow-xs transition-colors overflow-hidden bg-[#121317] border-[#242630]">
      {/* Control Ribbon */}
      <div className="px-3 py-2 border-b flex flex-wrap items-center justify-between gap-2 bg-[#17181e] border-[#242630]">
        <div className="flex items-center space-x-2">
          <div className="w-6 h-6 rounded flex items-center justify-center border shadow-xs bg-slate-800 border-slate-700 text-slate-200">
            <LineChart className="w-3.5 h-3.5" />
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-bold font-mono tracking-wide text-slate-200">
              {t.chartTitle}
            </span>
            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10">
              {t.tradingViewBadge}
            </span>
          </div>
        </div>

        {/* Symbol & Interval Selector */}
        <div className="flex items-center gap-1.5">
          <div className="flex items-center rounded border p-0.5 text-xs font-mono bg-[#0e0f13] border-[#242630]">
            {symbols.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelectedSymbol(s)}
                className={`px-2 py-0.5 rounded text-[11px] font-bold cursor-pointer transition-all ${
                  selectedSymbol.id === s.id
                    ? 'bg-slate-700 text-slate-100 shadow-xs'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {s.id}
              </button>
            ))}
          </div>

          <div className="hidden sm:flex items-center rounded border p-0.5 text-xs font-mono bg-[#0e0f13] border-[#242630]">
            {intervals.map((i) => (
              <button
                key={i.id}
                onClick={() => setSelectedInterval(i)}
                className={`px-2 py-0.5 rounded text-[11px] font-bold cursor-pointer transition-all ${
                  selectedInterval.id === i.id
                    ? 'bg-[#f3f4f6] text-[#0a0b0e] shadow-xs'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {i.label}
              </button>
            ))}
          </div>

          <button
            onClick={renderWidget}
            className="p-1 rounded border cursor-pointer hover:bg-[#1e2028] bg-[#121317] border-[#242630] text-slate-400"
            title={t.refreshChart}
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="px-2 py-1 rounded border text-[11px] font-mono cursor-pointer hover:bg-[#1e2028] bg-[#121317] border-[#242630] text-slate-300 flex items-center gap-1"
          >
            {isCollapsed ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
            <span>{isCollapsed ? t.expandChart : t.collapseChart}</span>
          </button>
        </div>
      </div>

      {/* Chart Canvas */}
      {!isCollapsed && (
        <div className="w-full relative h-[440px] bg-[#0b0f19]">
          <div ref={containerRef} className="w-full h-full tradingview-widget-container" />
        </div>
      )}
    </div>
  )
}
