/**
 * QuantX PRO — 图表视觉证据自动采集（canvas 直出 + 面板/整页截图）
 *
 * 为什么需要它：图表是 canvas 绘制的，"是否像交易所、有没有遮挡、标记长什么样"
 * 只能靠**真实渲染结果**说话。本脚本连到 Docker 内运行的实例，采集：
 *   1. `chart.takeScreenshot()` 直出的**纯图表 canvas**（含成交图元与水印，不含任何 HTML 浮层）；
 *   2. 图表面板截图（含图内工具栏/图例，用于验证"浮层不遮挡蜡烛"）；
 *   3. 有成交标的的**矢量图标标记 + 十字线悬浮卡**；
 *   4. 浅色主题（验证主题切换后图表配色同源）；
 *   5. 全屏模式；
 *   6. 短视口（1600×900，实测最容易遮挡的尺寸）。
 *
 * 用法：
 *   QX_BASE=http://host.docker.internal:8090 R20_ADMIN_TOKEN=<管理员密码> \
 *   QX_SHOTS_DIR=/repo/docs/assets node scripts/chart_screenshots.mjs
 *
 * 输出：每个文件打印 `宽×高 字节数`，便于在文档里如实标注（不写"看起来没问题"）。
 */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const BASE = process.env.QX_BASE || 'http://127.0.0.1:8090'
const token = (process.env.R20_ADMIN_TOKEN || '').trim()
const OUT = process.env.QX_SHOTS_DIR || 'docs/assets'
const prefix = process.env.QX_SHOTS_PREFIX || 'chart'

mkdirSync(OUT, { recursive: true })

let session = ''
if (token) {
  const r = await fetch(`${BASE}/api/v1/admin/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: token }),
  })
  const d = await r.json().catch(() => ({}))
  if (r.ok && d.session_token) session = d.session_token
}

/** PNG 头部解析：把"是不是真图、多大"写进输出（不做像素级断言，但拒绝空文件）。 */
function pngInfo(buf) {
  if (buf.length < 24 || buf[0] !== 0x89 || buf.toString('latin1', 1, 4) !== 'PNG') return null
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20), bytes: buf.length }
}

const saved = []
function save(name, buf) {
  const file = join(OUT, `${prefix}-${name}.png`)
  writeFileSync(file, buf)
  const info = pngInfo(buf)
  saved.push({ name, file, ...(info ?? { note: '非 PNG?' }) })
  console.log(`  ${info ? `${info.width}×${info.height}` : '??'}  ${String(info?.bytes ?? buf.length).padStart(8)}B  ${file}`)
}

/** 手动轮询等图表稳定（CSP 禁字符串求值，不能用 page.waitForFunction）。 */
async function waitChartStable(page, attempts = 30) {
  for (let i = 0; i < attempts; i += 1) {
    const st = await page.evaluate(() => {
      try {
        const legend = document.querySelector('[data-testid="qx-chart-legend"]')
        const series = window.__qxSeries
        const chart = window.__qxChart
        if (!legend || !series || !chart) return 'missing'
        const rows = series.data()
        if (!rows.length) return 'no-rows'
        const mid = rows[Math.floor(rows.length / 2)]
        if (mid?.time === undefined) return 'mid-no-time'
        return chart.timeScale().timeToCoordinate(mid.time) === null ? 'x-null' : 'ready'
      } catch {
        return 'throw'
      }
    })
    if (st === 'ready') return true
    await page.waitForTimeout(700)
  }
  return false
}

const browser = await chromium.launch({ args: ['--no-sandbox'] })

// ── ① 深色（默认主题）：纯 canvas + 面板 + 全屏 ──────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1100 }, deviceScaleFactor: 2 })
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('qx_debug_chart', '1')
      localStorage.setItem('r20_theme', 'dark')
    } catch {}
  })
  if (session) await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v) } catch {} }, ['r20_admin_session', session])
  const page = await ctx.newPage()
  await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
  if (!(await waitChartStable(page))) console.log('  ! 图表未就绪（继续采集，可能采到空图）')
  await page.waitForTimeout(1500)

  // 纯 canvas（库的 takeScreenshot：含标记/轨迹，不含 HTML 浮层）
  const canvasB64 = await page.evaluate(() => {
    const chart = window.__qxChart
    if (!chart) return null
    const c = chart.takeScreenshot(true, false)
    return c.toDataURL('image/png').split(',')[1] ?? null
  })
  if (canvasB64) save('canvas-dark-1h', Buffer.from(canvasB64, 'base64'))
  else console.log('  ! 取不到 canvas 截图')

  // 图表面板（含图内工具栏 + 图例，验证遮挡）
  await page.locator('[data-testid="qx-chart-shell"]').screenshot({ path: undefined }).then((buf) => save('panel-dark-1h', buf)).catch(() => {})

  // 全屏
  await page.locator('button[aria-label="Fullscreen"], button[aria-label="全屏"]').first().click().catch(() => {})
  await page.waitForTimeout(1200)
  await page.screenshot().then((buf) => save('fullscreen-dark', buf)).catch(() => {})
  await page.keyboard.press('Escape')
  await page.waitForTimeout(800)
  await ctx.close()
}

// ── ② 有成交的标的：矢量图标标记 + 十字线悬浮卡 ───────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1100 }, deviceScaleFactor: 2 })
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('qx_debug_chart', '1')
      localStorage.setItem('r20_theme', 'dark')
    } catch {}
  })
  if (session) await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v) } catch {} }, ['r20_admin_session', session])
  const page = await ctx.newPage()
  await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
  await waitChartStable(page)
  // 切到台账里有成交的标的（ADA），并悬停到有成交那根
  await page.locator('button[aria-label="Instruments"], button[aria-label="标的"]').first().click().catch(() => {})
  await page.waitForTimeout(500)
  await page.locator('[role="option"]').filter({ hasText: /ADA/ }).first().click().catch(() => {})
  await page.waitForTimeout(8000)
  const markers = await page.evaluate(() => window.__qxMarkerPlugin?.markers?.() ?? [])
  console.log(`  （该标的标记 ${markers.length} 枚：${markers.slice(0, 3).map((m) => `${m.kind}:${m.text}`).join(', ')}）`)
  const box = await page.locator('[data-testid="qx-chart-canvas"]').boundingBox()
  if (markers.length && box) {
    const xs = await page.evaluate(
      (times) => times.map((t) => window.__qxChart?.timeScale().timeToCoordinate(t) ?? null),
      markers.map((m) => m.time / 1000),
    )
    for (const x of xs.filter((v) => v !== null).flatMap((v) => [v - 6, v, v + 6])) {
      await page.mouse.move(box.x + Math.min(Math.max(x, 30), box.width - 40), box.y + box.height * 0.45)
      await page.waitForTimeout(400)
      const hasCard = await page.evaluate(() => [...document.querySelectorAll('div')].some((d) => d.className.includes('w-[16rem]')))
      if (hasCard) break
    }
  }
  await page.locator('[data-testid="qx-chart-shell"]').screenshot().then((buf) => save('markers-ada-hover', buf)).catch(() => {})
  const canvasB64 = await page.evaluate(() => {
    const c = window.__qxChart?.takeScreenshot(true, false)
    return c ? c.toDataURL('image/png').split(',')[1] ?? null : null
  })
  if (canvasB64) save('canvas-markers-ada', Buffer.from(canvasB64, 'base64'))
  await ctx.close()
}

// ── ③ 短视口（1600×900）：最容易发生遮挡的尺寸 ───────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 })
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('qx_debug_chart', '1')
      localStorage.setItem('r20_theme', 'dark')
    } catch {}
  })
  if (session) await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v) } catch {} }, ['r20_admin_session', session])
  const page = await ctx.newPage()
  await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
  await waitChartStable(page)
  await page.waitForTimeout(1200)
  await page.locator('[data-testid="qx-chart-shell"]').screenshot().then((buf) => save('panel-1600x900', buf)).catch(() => {})
  // 采集时顺带记录"浮层底边 / 蜡烛最高点"，避免文档只写"看起来没遮挡"
  const occ = await page.evaluate(() => {
    const host = document.querySelector('[data-testid="qx-chart-canvas"]')
    const legend = document.querySelector('[data-testid="qx-chart-legend"]')
    const series = window.__qxSeries
    const chart = window.__qxChart
    const hostRect = host.getBoundingClientRect()
    const lr = legend ? legend.getBoundingClientRect() : null
    const lb = lr ? lr.bottom - hostRect.top : null
    let offenders = 0
    let top = Number.POSITIVE_INFINITY
    for (const d of series.data()) {
      if (d.high === undefined) continue
      const x = chart.timeScale().timeToCoordinate(d.time)
      if (x === null) continue
      const y = series.priceToCoordinate(d.high)
      if (y === null) continue
      top = Math.min(top, y)
      if (lb !== null && x >= lr.left - hostRect.left - 6 && x <= lr.right - hostRect.left + 6 && y < lb - 1) offenders += 1
    }
    return { legendBottom: lb === null ? null : Math.round(lb), candleTop: Math.round(top), offenders, paneH: chart.panes()[0].getHeight() }
  })
  console.log(`  1600×900 遮挡实测：图例下沿=${occ.legendBottom}px 蜡烛最高点=${occ.candleTop}px 重叠=${occ.offenders} 根 主图pane=${occ.paneH}px`)
  await ctx.close()
}

// ── ④ 浅色主题（验证图表配色与界面令牌同源）──────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1100 }, deviceScaleFactor: 2 })
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('qx_debug_chart', '1')
      localStorage.setItem('r20_theme', 'light')
    } catch {}
  })
  if (session) await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v) } catch {} }, ['r20_admin_session', session])
  const page = await ctx.newPage()
  await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
  await waitChartStable(page)
  await page.waitForTimeout(1200)
  await page.locator('[data-testid="qx-chart-shell"]').screenshot().then((buf) => save('panel-light-1h', buf)).catch(() => {})
  const applied = await page.evaluate(() => ({
    theme: document.documentElement.getAttribute('data-theme'),
    grid: window.__qxChart?.options?.().grid?.horzLines?.color ?? null,
  }))
  console.log('  浅色主题实测：', JSON.stringify(applied))
  await ctx.close()
}

await browser.close()
console.log(`\n共采集 ${saved.length} 张 → ${OUT}`)
const bad = saved.filter((s) => !s.width || s.bytes < 5000)
console.log(bad.length ? `可疑（尺寸缺失或过小）：${JSON.stringify(bad)}` : '全部为有效 PNG（含尺寸与字节数）')
process.exit(bad.length === 0 ? 0 : 1)
