'use client'

import React, { useEffect, useState } from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import {
  Terminal,
  Cpu,
  Newspaper,
  Sparkles,
  Receipt,
  Settings,
  ShieldAlert,
  Sun,
  Moon,
  Globe,
  Wifi,
} from 'lucide-react'

export function HeaderBar() {
  const { activeTab, setActiveTab, isConnected, isStale } = useDashboard()
  const { locale, setLocale, t } = useI18n()

  const [currentTimeUtc, setCurrentTimeUtc] = useState('')
  const [currentTimeLocal, setCurrentTimeLocal] = useState('')
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  useEffect(() => {
    function updateClock() {
      const d = new Date()
      setCurrentTimeUtc(d.toLocaleTimeString('en-GB', { timeZone: 'UTC', hour12: false }))
      setCurrentTimeLocal(d.toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }))
    }
    updateClock()
    const timer = setInterval(updateClock, 1000)
    return () => clearInterval(timer)
  }, [])

  function toggleLanguage() {
    setLocale(locale === 'zh' ? 'en' : 'zh')
  }

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', next)
      if (next === 'dark') {
        document.documentElement.classList.add('dark')
      } else {
        document.documentElement.classList.remove('dark')
      }
    }
  }

  const navItems = [
    { id: 'trading', label: t.tabMasterTerminal, icon: Terminal },
    { id: 'factors', label: t.tabRadarDynamics, icon: Cpu },
    { id: 'news', label: t.tabNewsFlows, icon: Newspaper },
    { id: 'lab', label: t.tabQuantumLabs, icon: Sparkles },
    { id: 'history', label: t.tabTradingLedger, icon: Receipt },
  ] as const

  return (
    <header className="fixed top-0 left-0 right-0 z-50 h-[48px] px-3 lg:px-4 flex items-center border-b select-none transition-colors bg-[#0e0f12]/95 backdrop-blur-md border-[#242630]">
      <div className="w-full flex items-center justify-between gap-3">
        {/* 1. Left: Brand & Exchange Venues Heartbeats */}
        <div className="flex items-center space-x-3 shrink-0">
          <div
            className="flex items-center space-x-2 cursor-pointer"
            onClick={() => setActiveTab('trading')}
          >
            <div className="w-6 h-6 rounded flex items-center justify-center font-mono font-black text-xs border border-[#334155] bg-gradient-to-br from-[#1e293b] to-[#0f172a] text-[#f8fafc]">
              Ω
            </div>
            <div className="flex items-center space-x-1.5">
              <span className="font-mono font-black text-xs tracking-wider uppercase text-slate-100">
                {t.brand}
              </span>
              <span className="text-[9px] font-mono px-1 py-0.2 rounded border border-slate-700 bg-slate-800 text-slate-300 font-semibold">
                {t.desktopTag}
              </span>
            </div>
          </div>

          {/* Three Venues Status */}
          <div className="hidden xl:flex items-center space-x-1.5 pl-2 border-l border-[#242630]">
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded border text-[10px] font-mono bg-[#121317] border-[#242630]">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
              <span className="text-slate-300 font-bold">OKX</span>
              <span className="text-[9px] text-emerald-400">PROD</span>
            </div>
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded border text-[10px] font-mono bg-[#121317] border-[#242630]">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
              <span className="text-slate-300 font-bold">BINANCE</span>
              <span className="text-[9px] text-amber-400">FEED</span>
            </div>
            <div className="flex items-center space-x-1 px-2 py-0.5 rounded border text-[10px] font-mono bg-[#121317] border-[#242630]">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
              <span className="text-slate-300 font-bold">GATE</span>
              <span className="text-[9px] text-blue-400">FEED</span>
            </div>
          </div>
        </div>

        {/* 2. Center: Tab Navigation Switcher */}
        <nav className="hidden md:flex items-center p-1 rounded-lg border shrink-0 bg-[#17181e] border-[#242630]">
          {navItems.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`h-7.5 flex items-center space-x-2 px-3 rounded-md text-xs font-mono font-bold transition-all cursor-pointer whitespace-nowrap ${
                  active
                    ? 'bg-[#f3f4f6] text-[#0a0b0e] shadow-xs'
                    : 'text-slate-400 hover:text-slate-100'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
              </button>
            )
          })}
        </nav>

        {/* 3. Right: Dual Clocks, i18n Switcher, Admin Link */}
        <div className="flex items-center space-x-2 text-xs font-mono">
          {/* Dual Clocks (UTC & BJT) */}
          <div className="hidden 2xl:flex items-center h-7 space-x-2 px-2.5 rounded border text-[11px] bg-[#121317] border-[#242630] text-slate-400">
            <span>
              UTC <strong className="text-slate-200 tabular-nums">{currentTimeUtc}</strong>
            </span>
            <span className="text-slate-600">|</span>
            <span>
              BJT <strong className="text-emerald-400 tabular-nums">{currentTimeLocal}</strong>
            </span>
          </div>

          {/* i18n Language Toggle Button */}
          <button
            onClick={toggleLanguage}
            className="h-7 px-2.5 rounded border flex items-center space-x-1.5 cursor-pointer bg-[#121317] border-[#242630] text-slate-200 hover:bg-[#1e2028] transition-colors"
            title="切换语言 / Switch Language"
          >
            <Globe className="w-3.5 h-3.5 text-indigo-400" />
            <span className="font-bold">{locale === 'zh' ? '中' : 'EN'}</span>
          </button>

          {/* Fail-Closed Risk Badge */}
          <div className="hidden sm:flex items-center h-7 px-2 rounded border text-[10px] font-mono font-bold text-emerald-400 border-emerald-500/30 bg-emerald-500/10">
            <ShieldAlert className="w-3 h-3 mr-1 text-emerald-400" />
            {t.failClosed}
          </div>

          {/* Admin Link */}
          <a
            href="/admin/"
            className="h-7 px-2.5 rounded border flex items-center space-x-1.5 cursor-pointer bg-[#121317] border-[#242630] text-slate-200 hover:bg-[#1e2028] transition-colors"
          >
            <Settings className="w-3.5 h-3.5 text-indigo-400" />
            <span className="hidden sm:inline font-bold">{t.admin}</span>
          </a>

          {/* Theme Toggle */}
          <button
            onClick={toggleTheme}
            className="w-7 h-7 rounded border flex items-center justify-center cursor-pointer bg-[#121317] border-[#242630] text-slate-400 hover:bg-[#1e2028] transition-colors"
          >
            {theme === 'dark' ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
    </header>
  )
}
