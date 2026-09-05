'use client'

import React from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import { HeaderBar } from '@/components/HeaderBar'
import { TopHudRibbon } from '@/components/TopHudRibbon'
import { LiveTradingViewChart } from '@/components/LiveTradingViewChart'
import { TacticalDesk } from '@/components/TacticalDesk'
import { QuantumCockpit } from '@/components/QuantumCockpit'
import { NewsConsole } from '@/components/NewsConsole'
import { QuantumLabsConsole } from '@/components/QuantumLabsConsole'
import { LedgerConsole } from '@/components/LedgerConsole'

export function MasterTerminal() {
  const { activeTab } = useDashboard()
  const { t } = useI18n()

  return (
    <div className="min-h-screen flex flex-col select-none bg-[#0a0b0e] text-[#f3f4f6]">
      {/* Bloomberg Style Header Command Ribbon */}
      <HeaderBar />

      {/* Spacer */}
      <div className="h-[48px] shrink-0" />

      {/* Main Container */}
      <main className="flex-1 w-full px-2.5 lg:px-4 pt-2.5 pb-10 space-y-2.5">
        {/* Top HUD Metrics Ribbon (Single compact uniform row) */}
        <TopHudRibbon />

        {/* 1. MASTER TERMINAL TAB */}
        {activeTab === 'trading' && (
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-2.5 items-start">
            {/* Left Wing (70%: 8 of 12 cols): Chart + Orders & Positions */}
            <div className="xl:col-span-8 space-y-2.5">
              <LiveTradingViewChart />
              <TacticalDesk />
            </div>

            {/* Right Wing (30%: 4 of 12 cols): Council Debate + Micro Calculus */}
            <div className="xl:col-span-4 space-y-2.5">
              <QuantumCockpit />
            </div>
          </div>
        )}

        {/* 2. RADAR & DYNAMICS TAB */}
        {activeTab === 'factors' && (
          <div className="space-y-2.5">
            <QuantumCockpit />
          </div>
        )}

        {/* 3. NEWS & FLOWS TAB */}
        {activeTab === 'news' && (
          <div className="space-y-2.5">
            <NewsConsole />
          </div>
        )}

        {/* 4. QUANTUM LABS TAB */}
        {activeTab === 'lab' && (
          <div className="space-y-2.5">
            <QuantumLabsConsole />
          </div>
        )}

        {/* 5. TRADING LEDGER TAB */}
        {activeTab === 'history' && (
          <div className="space-y-2.5">
            <LedgerConsole />
          </div>
        )}
      </main>
    </div>
  )
}
