'use client'

import React, { useState } from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import { Newspaper, Flame, ExternalLink, ShieldAlert } from 'lucide-react'

export function NewsConsole() {
  const { data } = useDashboard()
  const { t } = useI18n()
  const [selectedPlatform, setSelectedPlatform] = useState<'ALL' | 'OKX' | 'Binance' | 'Gate.io'>('ALL')

  const intel = data?.news_intelligence || {}
  const allNews = intel.latest_news || []
  const coinsSentiment = Object.entries(intel.coins_sentiment || {})
  const macro = intel.macro_sentiment || '--'
  const breakerActive = !!intel.circuit_breaker?.active

  const filteredNews = selectedPlatform === 'ALL'
    ? allNews
    : allNews.filter((item: any) => {
        const p = item.platform || (item.platforms && item.platforms[0]) || 'OKX'
        return p.toLowerCase() === selectedPlatform.toLowerCase()
      })

  function platformBadgeStyle(platform: string) {
    const p = (platform || '').toLowerCase()
    if (p.includes('binance')) return 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    if (p.includes('gate')) return 'bg-blue-500/15 text-blue-400 border-blue-500/30'
    return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
  }

  return (
    <div className="space-y-3 font-mono text-xs select-none">
      {/* Top Banner */}
      <div className="rounded-lg border p-3 flex flex-wrap items-center justify-between gap-2 bg-[#121317] border-[#242630]">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded flex items-center justify-center border bg-[#17181e] border-slate-700 text-slate-200">
            <Newspaper className="w-4 h-4" />
          </div>
          <div>
            <div className="font-bold text-slate-100 uppercase tracking-wide">
              {t.tabNewsFlows} · 多所全要素舆情网
            </div>
            <div className="text-[10px] text-slate-400">
              OKX 突发快讯 + 币安官方公告 + Gate.io 合约上线异动
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <span className={`px-2 py-0.5 rounded border text-[10px] font-bold ${
            breakerActive ? 'bg-rose-500/15 text-rose-400 border-rose-500/30' : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
          }`}>
            <ShieldAlert className="w-3 h-3 inline mr-1" />
            {breakerActive ? '黑天鹅熔断' : '常态监控'}
          </span>
          <span className="px-2 py-0.5 rounded border text-[10px] bg-[#17181e] border-[#242630] text-slate-400">
            宏观: <strong className="text-slate-200">{macro}</strong>
          </span>
        </div>
      </div>

      {/* Platform Filter Tabs */}
      <div className="flex items-center gap-1.5 p-1 rounded-lg border bg-[#121317] border-[#242630]">
        {(['ALL', 'OKX', 'Binance', 'Gate.io'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setSelectedPlatform(tab)}
            className={`px-3 py-1 rounded text-xs font-bold transition-all cursor-pointer ${
              selectedPlatform === tab
                ? 'bg-[#f3f4f6] text-[#0a0b0e]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab === 'ALL' ? '全平台全息' : tab}
          </button>
        ))}
      </div>

      {/* News Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
        {filteredNews.length === 0 ? (
          <div className="col-span-2 py-12 text-center text-slate-500 border border-dashed rounded-lg border-[#242630]">
            当前筛选下暂无突发新闻
          </div>
        ) : (
          filteredNews.map((item: any) => (
            <div
              key={item.id}
              className="p-3 rounded-lg border flex flex-col justify-between space-y-2 bg-[#121317] border-[#242630] hover:bg-[#17181e] transition-colors"
            >
              <div>
                <div className="flex items-start justify-between gap-2 mb-1">
                  <div className="flex items-start space-x-1.5 min-w-0">
                    <Flame className="w-3.5 h-3.5 shrink-0 mt-0.5 text-rose-400" />
                    <span className="font-bold text-xs text-slate-200 line-clamp-2">
                      {item.title}
                    </span>
                  </div>
                  <span className="text-[10px] text-slate-500 shrink-0">
                    {item.time}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">
                  {item.summary}
                </p>
              </div>

              <div className="pt-2 border-t flex items-center justify-between text-[10px] border-[#242630] text-slate-400">
                <div className="flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.2 rounded border font-bold ${platformBadgeStyle(item.platform)}`}>
                    {item.platform || 'OKX'}
                  </span>
                  <span>标的: <strong className="text-slate-300">{(item.coins || []).join(', ') || 'ALL'}</strong></span>
                </div>
                {item.url && (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center text-indigo-400 hover:underline"
                  >
                    原文<ExternalLink className="w-3 h-3 ml-0.5" />
                  </a>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
