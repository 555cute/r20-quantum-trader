'use client'

import React, { createContext, useContext, useEffect, useState } from 'react'

interface DashboardState {
  data: any
  account: any
  todayStats: any
  positions: any[]
  orders: any[]
  signals: any[]
  isConnected: boolean
  isStale: boolean
  lastUpdate: number
  activeTab: 'trading' | 'factors' | 'news' | 'lab' | 'history'
  setActiveTab: (tab: 'trading' | 'factors' | 'news' | 'lab' | 'history') => void
  refreshNow: () => Promise<void>
}

const DashboardContext = createContext<DashboardState | null>(null)

export function DashboardProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<any>(null)
  const [activeTab, setActiveTab] = useState<'trading' | 'factors' | 'news' | 'lab' | 'history'>('trading')
  const [isConnected, setIsConnected] = useState(true)
  const [isStale, setIsStale] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(Date.now())

  async function fetchDashboard() {
    try {
      const resp = await fetch('/api/all', { cache: 'no-store' })
      if (!resp.ok) {
        setIsConnected(false)
        return
      }
      const json = await resp.json()
      setData(json)
      setIsConnected(true)
      setIsStale(!!json.stale)
      setLastUpdate(Date.now())
    } catch {
      setIsConnected(false)
    }
  }

  useEffect(() => {
    fetchDashboard()
    const timer = setInterval(fetchDashboard, 3000)
    return () => clearInterval(timer)
  }, [])

  const account = data?.account || {}
  const todayStats = data?.today_stats || {}
  const positions = data?.positions || []
  const orders = data?.orders || []
  const signals = data?.signals || []

  return (
    <DashboardContext.Provider
      value={{
        data,
        account,
        todayStats,
        positions,
        orders,
        signals,
        isConnected,
        isStale,
        lastUpdate,
        activeTab,
        setActiveTab,
        refreshNow: fetchDashboard,
      }}
    >
      {children}
    </DashboardContext.Provider>
  )
}

export function useDashboard() {
  const ctx = useContext(DashboardContext)
  if (!ctx) {
    throw new Error('useDashboard must be used within DashboardProvider')
  }
  return ctx
}
