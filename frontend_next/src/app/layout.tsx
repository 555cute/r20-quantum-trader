import type { Metadata } from 'next'
import './globals.css'
import { I18nProvider } from '@/i18n/context'
import { DashboardProvider } from '@/context/DashboardContext'

export const metadata: Metadata = {
  title: 'R20 Quantum Master Terminal | 机构级多所量化对冲交易终端',
  description: '全自动高频波段量化终端，基于 Next.js 16 与多模型委员会架构。',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="bg-[#0a0b0e] text-[#f3f4f6] antialiased">
        <I18nProvider>
          <DashboardProvider>
            {children}
          </DashboardProvider>
        </I18nProvider>
      </body>
    </html>
  )
}
