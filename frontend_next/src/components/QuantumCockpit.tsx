'use client'

import React, { useState } from 'react'
import { useDashboard } from '@/context/DashboardContext'
import { useI18n } from '@/i18n/context'
import { Cpu, Users, Zap, Shield, TrendingUp, ChevronRight } from 'lucide-react'

export function QuantumCockpit() {
  const { data, signals } = useDashboard()
  const { t } = useI18n()
  const [subTab, setSubTab] = useState<'council' | 'calculus'>('council')

  const factors = data?.factors || {}
  const council = data?.ai_council || {}
  const decisionLog = data?.decision_log || []
  const latestVerdict = decisionLog[0] || {}

  const calculusList = Object.entries(factors).map(([coin, f]: [string, any]) => {
    const calc = f?.calculus_dynamics || {}
    const integrals = f?.definite_integrals || {}
    const prob = f?.probability_theory || {}
    return {
      coin,
      velocity: calc.velocity || 0,
      accel: calc.acceleration || 0,
      impulse: calc.impulse || 0,
      jerk: calc.jerk || 0,
      regime: calc.regime || 'RANGE_LOW_VELOCITY',
      energy: integrals.energy_integral || 0,
      var95: prob.var_95_pct || 1.5,
      continuationProb: prob.continuation_prob_pct || 50,
      score: f?.composite_alpha_score || 0,
    }
  })

  return (
    <div className="rounded-lg border shadow-xs overflow-hidden bg-[#121317] border-[#242630] flex flex-col h-full">
      {/* Cockpit Tabs Header */}
      <div className="px-3 py-2 border-b flex items-center justify-between bg-[#17181e] border-[#242630]">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setSubTab('council')}
            className={`px-3 py-1 rounded text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
              subTab === 'council'
                ? 'bg-[#f3f4f6] text-[#0a0b0e]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Users className="w-3.5 h-3.5" />
            <span>{t.councilVerdicts}</span>
          </button>

          <button
            onClick={() => setSubTab('calculus')}
            className={`px-3 py-1 rounded text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
              subTab === 'calculus'
                ? 'bg-[#f3f4f6] text-[#0a0b0e]'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>{t.calculusMatrix}</span>
          </button>
        </div>

        <span className="text-[10px] font-mono px-1.5 py-0.2 rounded border text-indigo-400 border-indigo-500/30 bg-indigo-500/10">
          COUNCIL PRO
        </span>
      </div>

      {/* 1. COUNCIL SUBTAB */}
      {subTab === 'council' && (
        <div className="p-3 space-y-3 flex-1 overflow-y-auto max-h-[640px] text-xs font-mono">
          {/* CIO Latest Decision Box */}
          <div className="p-3 rounded border space-y-1.5 bg-[#17181e] border-[#242630]">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold text-amber-400 flex items-center gap-1">
                <Shield className="w-3.5 h-3.5" />
                <span>{t.cioVerdict}</span>
              </span>
              <span className="text-[10px] text-slate-400">
                {latestVerdict?.timestamp || 'Latest Cycle'}
              </span>
            </div>
            <div className="text-sm font-black text-slate-100 flex items-center gap-2">
              <span className="text-emerald-400">{latestVerdict?.action || 'STANDBY 观望严守'}</span>
              <span className="text-[11px] text-slate-400 font-normal">
                置信度 {latestVerdict?.confidence || 85}%
              </span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              {latestVerdict?.reasoning || '多周期微积分能量平衡，未击穿 2.0R 盈亏比安全边际，维持既有低风险仓位不变。'}
            </p>
          </div>

          {/* Trader A/B/C Proposals */}
          <div className="space-y-2">
            <div className="p-2.5 rounded border bg-[#0e0f13] border-[#242630]">
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold text-slate-200">{t.traderA}</span>
                <span className="text-[10px] text-emerald-400 font-bold">BULLISH 顺势买点</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug">
                1H 与 4H 宏观多头通道延续，速度 v &gt; 0 且冲量正向积累，建议在回踩 1.5x ATR 支撑处顺势做多。
              </p>
            </div>

            <div className="p-2.5 rounded border bg-[#0e0f13] border-[#242630]">
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold text-slate-200">{t.traderB}</span>
                <span className="text-[10px] text-indigo-400 font-bold">NEUTRAL 宽止损</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug">
                建议浮盈达 1.0R 时自动平移止损至开仓保本价，严守大波段利润，拒绝机械震荡频跳。
              </p>
            </div>

            <div className="p-2.5 rounded border bg-[#0e0f13] border-[#242630]">
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold text-slate-200">{t.traderC}</span>
                <span className="text-[10px] text-amber-400 font-bold">RISK-GATE 极值质询</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug">
                质询盘口深度与大户多空比散度，若未能确凿证明 R:R &ge; 2.0 则坚决行使一票否决权。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 2. CALCULUS SUBTAB */}
      {subTab === 'calculus' && (
        <div className="p-2 space-y-2 flex-1 overflow-y-auto max-h-[640px] text-xs font-mono">
          {calculusList.length === 0 ? (
            <div className="py-12 text-center text-slate-500">
              {t.loading}
            </div>
          ) : (
            calculusList.map((item) => (
              <div key={item.coin} className="p-2.5 rounded border space-y-1.5 bg-[#17181e] border-[#242630]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-black text-sm text-slate-100">{item.coin}</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded border text-indigo-300 border-indigo-500/30 bg-indigo-500/10 font-bold">
                      {item.regime}
                    </span>
                  </div>
                  <span className={`font-bold tabular-nums ${item.score >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    评分: {item.score >= 0 ? '+' : ''}{item.score}
                  </span>
                </div>

                <div className="grid grid-cols-4 gap-1.5 text-center text-[10px] py-1 border-t border-b border-[#242630]">
                  <div>
                    <div className="text-slate-500">速度 v</div>
                    <div className="font-bold text-slate-200 tabular-nums">{item.velocity.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">加速度 a</div>
                    <div className="font-bold text-slate-200 tabular-nums">{item.accel.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">冲量 I</div>
                    <div className="font-bold text-slate-200 tabular-nums">{item.impulse.toFixed(2)}</div>
                  </div>
                  <div>
                    <div className="text-slate-500">做功 E</div>
                    <div className="font-bold text-emerald-400 tabular-nums">{item.energy.toFixed(2)}</div>
                  </div>
                </div>

                <div className="flex items-center justify-between text-[10px] text-slate-400">
                  <span>多头延续概率: <strong className="text-emerald-400">{item.continuationProb}%</strong></span>
                  <span>95% VaR: <strong className="text-rose-400">{item.var95}%</strong></span>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
