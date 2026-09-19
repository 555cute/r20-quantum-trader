/**
 * 图表（从 0 重写版）浏览器验收。
 *
 * 设计原则：
 *   1. **只断言用户可见行为**，且尽量读库层真实状态（`series.options()` / `priceScale().options()`）；
 *   2. 每个动作后把"能证明这件事的状态"打出来（失败时不用再猜）；
 *   3. 浮层一律走 portal（`[data-portal-layer="1"]`），脚本按 portal 定位，不依赖旧 DOM 结构。
 */
import { chromium } from 'playwright'

const BASE = process.env.QX_BASE || 'http://127.0.0.1:8090' || 'http://127.0.0.1:8090'
const token = (process.env.R20_ADMIN_TOKEN || '').trim()

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

const rows = []
const add = (name, ok, detail = '') => rows.push({ name, ok, detail })
const browser = await chromium.launch({ args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1300 }, acceptDownloads: true })
await ctx.addInitScript(() => {
  try {
    localStorage.setItem('qx_debug_chart', '1')
  } catch {}
})
if (session) {
  await ctx.addInitScript(([k, v]) => {
    try {
      localStorage.setItem(k, v)
    } catch {}
  }, ['r20_admin_session', session])
}
const page = await ctx.newPage()
const errs = []
page.on('pageerror', (e) => errs.push(e.message.slice(0, 140)))
page.on('console', (m) => {
  if (m.type() === 'error' && !/favicon|Failed to load resource|WebSocket/i.test(m.text())) errs.push(m.text().slice(0, 140))
})

// ── 进入带图表的页面：/trading 深链（实测可用）；回落 /（首页同图）──
await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
await page.waitForTimeout(9000)
if (!(await page.locator('[data-testid="qx-chart-canvas"]').count())) {
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(9000)
}

const host = page.locator('[data-testid="qx-chart-canvas"]')
if (!(await host.count())) {
  console.log(JSON.stringify({ pass: 0, total: 1, rows: [{ name: '图表存在', ok: false, detail: '未找到图表容器' }], errs }))
  await browser.close()
  process.exit(1)
}
add('图表存在', true, '')
const box = await host.boundingBox()

// ── 读取库层状态的统一入口 ──
const state = () =>
  page.evaluate(() => {
    const refs = window.__qxRefs || {}
    const vis = (s) => (s && s.options ? s.options().visible : 'null')
    const candle = window.__qxSeries
    const scale = candle?.priceScale?.().options?.() ?? {}
    return {
      ma: [vis(refs.ma21), vis(refs.ma55)],
      boll: (refs.boll || []).map(vis),
      rsi: vis(refs.rsi),
      macd: vis(refs.macd),
      indicators: window.__qxIndicators ? window.__qxIndicators().ma.enabled : null,
      upColor: candle?.options?.().upColor ?? null,
      scaleMode: scale.mode ?? null,
      invert: scale.invertScale ?? null,
      markers: (window.__qxMarkerPlugin?.markers?.() ?? []).length,
    markerSpecs: (window.__qxMarkerPlugin?.markers?.() ?? []).slice(0, 3),
      overlay: window.__qxOverlay ?? null,
      indicatorState: window.__qxIndicators ? window.__qxIndicators() : null,
    }
  })

const portal = () => page.locator('[data-portal-layer="1"]').last()
const openMenu = async (names) => {
  for (const n of names) {
    const btn = page.locator(`button[aria-label="${n}"]`).first()
    if (await btn.count()) {
      await btn.click({ timeout: 5000 }).catch(() => {})
      await page.waitForTimeout(400)
      return n
    }
  }
  return null
}
const closeMenus = async () => {
  await page.keyboard.press('Escape')
  await page.waitForTimeout(250)
}
// 在**最后打开的浮层**里按文本点击（portal 后菜单不在原地 DOM）
const clickInMenu = async (patterns) => {
  for (const re of patterns) {
    const target = portal().locator('label, button, [role="option"]').filter({ hasText: re }).first()
    if (!(await target.count())) continue
    try {
      await target.click({ timeout: 5000 })
      await page.waitForTimeout(450)
      return String(re)
    } catch (e) {
      await page.waitForTimeout(200)
      return `click-failed:${String(re)} (${String(e).slice(0, 60)})`
    }
  }
  return null
}
/** 诊断：当前打开的所有浮层里有什么可点 */
const portalDump = async () =>
  page.evaluate(() =>
    [...document.querySelectorAll('[data-portal-layer="1"]')].map((el) => ({
      text: (el.textContent || '').replace(/\s+/g, ' ').slice(0, 90),
      buttons: [...el.querySelectorAll('button')].map((b) => (b.textContent || '').trim().slice(0, 20)),
    })),
  )

// ① 图例跟随十字线
const legendText = async () => {
  const el = page.locator('[data-testid="qx-chart-legend"]')
  return (await el.count()) ? (await el.innerText()).replace(/\n+/g, ' | ') : ''
}
const legendLast = await legendText()
await page.mouse.move(box.x + box.width / 2, box.y + 60)
await page.waitForTimeout(400)
const legendMid = await legendText()
await page.mouse.move(box.x + box.width * 0.25, box.y + 80)
await page.waitForTimeout(400)
const legendLeft = await legendText()
await page.mouse.move(box.x + box.width / 2, box.y + box.height + 80)
await page.waitForTimeout(400)
const legendBack = await legendText()
// 比较**时间戳**而不是整串：最新一根的价/量会被 ticker 实时改写，整串必然不同
const tsOf = (text) => (text.match(/(\d{4}\/\d{2}\/\d{2} \d{2}:\d{2})/) || [])[1] ?? '(无时间)'
add(
  '图例跟随十字线（离开回落最新）',
  Boolean(legendLast) &&
    tsOf(legendMid) !== tsOf(legendLast) &&
    tsOf(legendLeft) !== tsOf(legendMid) &&
    tsOf(legendBack) === tsOf(legendLast),
  `最新=${tsOf(legendLast)} 中部=${tsOf(legendMid)} 左侧=${tsOf(legendLeft)} 回落=${tsOf(legendBack)}`,
)

// ② 指标开关（读库层 visible + React 状态）
const s0 = await state()
await openMenu(['Indicators', '指标'])
// MA 行 = label[checkbox + span + 周期输入框]：必须点 **checkbox 本体**
// （点 label 中心会落在周期输入框上，浏览器不会转发到 checkbox —— 这不是 bug，是预期）
const maCheckbox = portal().locator('label').filter({ hasText: /^MA/ }).locator('input[type=checkbox]').first()
let clicked = null
if (await maCheckbox.count()) {
  await maCheckbox.click({ timeout: 5000 }).then(() => (clicked = 'MA-checkbox')).catch((e) => (clicked = `click-failed:${String(e).slice(0, 50)}`))
  await page.waitForTimeout(450)
}
const s1 = await state()
await closeMenus()
add(
  '指标开关生效（库层 visible + 状态）',
  Boolean(clicked) && s1.ma[0] !== s0.ma[0] && s1.indicators === false,
  `点击=${clicked} MA21 ${s0.ma[0]}→${s1.ma[0]} state.ma.enabled=${s1.indicators}`,
)
// 复位
await openMenu(['Indicators', '指标'])
const maCheckbox2 = portal().locator('label').filter({ hasText: /^MA/ }).locator('input[type=checkbox]').first()
if (await maCheckbox2.count()) await maCheckbox2.click({ timeout: 5000 }).catch(() => {})
await page.waitForTimeout(450)
await closeMenus()
const s2 = await state()
add('指标开关可复位', s2.ma[0] === s0.ma[0], `MA21 复位=${s2.ma[0]}`)

// ③ 蜡烛形态（空心 = 实体透明）
await openMenu(['Style', '形态'])
const hollowClicked = await clickInMenu([/Hollow/, /空心/])
const s3 = await state()
await closeMenus()
await openMenu(['Style', '形态'])
await clickInMenu([/Solid/, /实心/])
await closeMenus()
const s4 = await state()
add(
  'K 线形态切换（空心=实体内透明）',
  Boolean(hollowClicked) && /rgba\(0,\s*0,\s*0,\s*0\)|transparent/.test(String(s3.upColor)) && s4.upColor !== s3.upColor,
  `点击=${hollowClicked} upColor ${s0.upColor} → ${s3.upColor} → 复位 ${s4.upColor}`,
)

// ④ 价格尺度（对数 + 反向）
await openMenu(['Scale', '坐标'])
const logClicked = await clickInMenu([/^Log$/, /^对数$/])
const s5 = await state()
await closeMenus()
await openMenu(['Scale', '坐标'])
const invClicked = await clickInMenu([/Invert/, /价格轴反向/])
const s6 = await state()
await closeMenus()
add('价格轴对数模式', Boolean(logClicked) && s5.scaleMode === 1, `点击=${logClicked} mode ${s0.scaleMode}→${s5.scaleMode}`)
add('价格轴反向', Boolean(invClicked) && s6.invert === true, `点击=${invClicked} invert ${s0.invert}→${s6.invert}`)
// 复位
await openMenu(['Scale', '坐标'])
await clickInMenu([/^Regular$/, /^常规$/])
await closeMenus()
await openMenu(['Scale', '坐标'])
await clickInMenu([/Invert/, /价格轴反向/])
await closeMenus()
const s7 = await state()
add('价格尺度可复位', s7.scaleMode === 0 && s7.invert === false, `复位 mode=${s7.scaleMode} invert=${s7.invert}`)

// ⑤ 全屏 + Esc
const shell = () =>
  page.evaluate(() => {
    const el = document.querySelector('[data-testid="qx-chart-shell"]')
    if (!el) return null
    const cs = getComputedStyle(el)
    return {
      attr: el.dataset.fullscreen || '',
      position: cs.position,
      height: Math.round(el.getBoundingClientRect().height),
      chartH: window.__qxContainer ? window.__qxContainer.clientHeight : -1,
      paneSum: window.__qxChart ? window.__qxChart.panes().reduce((a, p) => a + p.getHeight(), 0) : -1,
      vh: window.innerHeight,
    }
  })
const beforeFs = await shell()
await openMenu(['Fullscreen', '全屏'])
await page.waitForTimeout(900)
const inFs = await shell()
add(
  '全屏：shell fixed 铺满窗口、图表随之变高',
  inFs?.attr === '1' && inFs?.position === 'fixed' && Math.abs(inFs.height - inFs.vh) < 40 && inFs.chartH > beforeFs.chartH,
  JSON.stringify({ beforeFs, inFs }),
)
await page.keyboard.press('Escape')
await page.waitForTimeout(800)
const outFs = await shell()
add('Esc 退出全屏', outFs?.attr !== '1' && outFs.position !== 'fixed', JSON.stringify(outFs))

// ⑥ 截图（真实下载事件）
let fileName = null
try {
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 9000 }),
    openMenu(['Screenshot', '截图']),
  ])
  fileName = download.suggestedFilename()
} catch {
  fileName = null
}
add('截图下载 PNG', Boolean(fileName) && fileName.endsWith('.png'), `文件名=${fileName}`)

// ⑦ 右键菜单 + 水平线 + 清除
const drawCount = () => page.evaluate(() => Number(document.querySelector('[data-testid="qx-chart-canvas"]')?.dataset.drawingCount ?? -1))
const dc0 = await drawCount()
await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.35, { button: 'right' })
await page.waitForTimeout(600)
const menuVisible = await page.locator('[data-testid="qx-chart-context-menu"]').count()
add('右键菜单出现', menuVisible > 0, `菜单节点=${menuVisible}`)
const lineClicked = await clickInMenu([/horizontal/i, /水平线/])
const dc1 = await drawCount()
add('右键→在此画水平线落笔', Boolean(lineClicked) && dc1 > dc0, `点击=${lineClicked} drawingCount ${dc0} → ${dc1}`)
// 换位置右键（避开刚画的线），清除
await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.8, { button: 'right' })
await page.waitForTimeout(600)
const clearClicked = await clickInMenu([/Clear all/i, /清除全部绘图/])
const dc2 = await drawCount()
add(
  '右键→清除全部绘图',
  Boolean(clearClicked) && !String(clearClicked).startsWith('click-failed') && dc2 < dc1,
  `点击=${clearClicked} drawingCount ${dc1} → ${dc2} 浮层=${JSON.stringify(await portalDump())}`,
)

// ⑧ 尺寸守恒（官方公式：sum(panes) + 分隔条 + 时间轴 == 容器高）
const sizing = await page.evaluate(() => {
  const chart = window.__qxChart
  const container = window.__qxContainer
  const panes = chart.panes()
  const sum = panes.reduce((a, p) => a + p.getHeight(), 0)
  const axis = chart.timeScale().height()
  return { paneCount: panes.length, sum, axis, separators: Math.max(0, panes.length - 1), container: container.clientHeight, panes: panes.map((p) => p.getHeight()) }
})
const diff = sizing.container - (sizing.sum + sizing.axis + sizing.separators)
add('尺寸守恒（panes+轴+分隔条==容器，±3px）', Math.abs(diff) <= 3, `容器=${sizing.container} 之和=${sizing.sum + sizing.axis + sizing.separators} panes=${JSON.stringify(sizing.panes)} 轴=${sizing.axis} 差=${diff}`)

// ⑨ 叠加层口径：只画本标的（默认 BTC 无成交 → 0 标记；旧实现会把别的币画上来）
const overlayBefore = await state()
add(
  '叠加层只画本标的成交（BTC 上无其它币成交）',
  Boolean(overlayBefore.overlay) && overlayBefore.overlay.pairs === 0 && overlayBefore.overlay.events === 0 && overlayBefore.overlay.trades > 0,
  `台账 ${overlayBefore.overlay?.trades ?? '?'} 行 / pairs=${overlayBefore.overlay?.pairs ?? '?'} events=${overlayBefore.overlay?.events ?? '?'}`,
)

// ⑩ 切到有成交的标的（ADA 12 笔）→ 轨迹与标记出现
let switched = false
const symBtn = page.locator('button[aria-label="Instruments"], button[aria-label="标的"]').first()
if (await symBtn.count()) {
  await symBtn.click({ timeout: 5000 }).catch(() => {})
  await page.waitForTimeout(500)
  const ada = page.locator('[role="option"]').filter({ hasText: /ADA/ }).first()
  if (await ada.count()) {
    await ada.click({ timeout: 5000 }).catch(() => {})
    switched = true
  }
}
await page.waitForTimeout(8000)
const overlayAfter = await state()
// 标记规格在"有成交的标的"上采样（末尾会切回 BTC，那时读到的必为 0）
const adaMarkerSpecs = await page.evaluate(() => window.__qxMarkerPlugin?.markers?.() ?? null)
add(
  '切到有成交的标的 → 轨迹/标记出现',
  switched && ((overlayAfter.overlay?.pairs ?? 0) + (overlayAfter.overlay?.events ?? 0) > 0 || overlayAfter.markers > 0),
  `切换到=${overlayAfter.overlay?.filter?.instId} pairs=${overlayAfter.overlay?.pairs} events=${overlayAfter.overlay?.events} markers=${overlayAfter.markers}`,
)
// 回到 BTC，避免影响后续（若有）
if (switched && (await symBtn.count())) {
  await symBtn.click({ timeout: 5000 }).catch(() => {})
  await page.waitForTimeout(400)
  const btc = page.locator('[role="option"]').filter({ hasText: /BTC/ }).first()
  if (await btc.count()) await btc.click({ timeout: 5000 }).catch(() => {})
  await page.waitForTimeout(3000)
}

// ⑪ 分页：滚动到最左只触发一次（不重复拉）
const requests = []
const allCandles = []
page.on('request', (req) => {
  if (!req.url().includes('/candles')) return
  allCandles.push(req.url().replace(/^.*\/api/, '/api'))
  if (req.url().includes('after=')) requests.push(req.url())
})
await page.evaluate(() => {
  const chart = window.__qxChart
  if (!chart) return
  chart.timeScale().setVisibleLogicalRange({ from: -50, to: 20 })
})
await page.waitForTimeout(6000)
const afterReqs = requests.length
const totalAfter = await page.evaluate(() => window.__qxContainer ? window.__qxChart?.timeScale().getVisibleLogicalRange() : null)
add(
  '历史分页：左滚触发加载（且不重复猛拉）',
  afterReqs >= 1 && afterReqs <= 3,
  `after= 请求 ${afterReqs} 次，全部 K 线请求 ${allCandles.length} 条：${JSON.stringify(allCandles.slice(-4))} 视窗=${JSON.stringify(totalAfter)} 分页态=${JSON.stringify(await page.evaluate(() => window.__qxPaging))} range 回调=${await page.evaluate(() => window.__qxRangeEvents ?? -1)}`,
)

// ⑫ TradingView 图内归属标识必须不存在（用户要求）
const tvLogo = await page.evaluate(() => document.querySelectorAll('#tv-attr-logo, [id*="tv-attr"], a[title*="Charting by"]').length)
add('已移除 TradingView 图内标识（#tv-attr-logo）', tvLogo === 0, `匹配节点=${tvLogo}`)

// 遮挡检查前先等图表稳定（同 CSP 原因不用 waitForFunction；手动轮询，见 chart-occlusion.mjs 说明）
const waitChartStable = async (attempts = 24) => {
  for (let i = 0; i < attempts; i += 1) {
    const st = await page.evaluate(() => {
      try {
        const w = window
        const legend = document.querySelector('[data-testid="qx-chart-legend"]')
        const series = w.__qxSeries
        const chart = w.__qxChart
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
await waitChartStable()

// ⑬ 遮挡专项：**逐根**判定"落在浮层横向范围内的蜡烛"是否顶到浮层下沿
//    （只比整图最高点会漏判——实测曾有 26 根蜡烛被图例压住而断言是绿的）
const occlusion = await page.evaluate(() => {
  const host = document.querySelector('[data-testid="qx-chart-canvas"]')
  const series = window.__qxSeries
  const chart = window.__qxChart
  if (!host || !series || !chart) return null
  const hostRect = host.getBoundingClientRect()
  // **只判定我们自己的常驻浮层**（按 data-testid 白名单）。
  // 不能用"所有绝对定位 div"：绘图库（lightweight-charts-drawing）会自建覆盖整个
  // 图区的图层（无 className），那会把它自己当成遮挡物，判定全错。
  const overlays = [
    ...host.parentElement.querySelectorAll('[data-testid="qx-chart-legend"], [data-testid="qx-chart-overlay-bar"]'),
  ].filter((el) => {
    const cs = getComputedStyle(el)
    if (cs.display === 'none' || cs.visibility === 'hidden') return false
    const r = el.getBoundingClientRect()
    return r.width > 2 && r.height > 2
  })
  const rects = overlays.map((el) => {
    const r = el.getBoundingClientRect()
    return {
      cls: String(el.className).slice(0, 30),
      top: r.top - hostRect.top,
      bottom: r.bottom - hostRect.top,
      left: r.left - hostRect.left,
      right: r.right - hostRect.left,
    }
  })
  const ts = chart.timeScale()
  const offenders = []
  let checked = 0
  for (const d of series.data()) {
    if (d.high === undefined) continue
    const x = ts.timeToCoordinate(d.time)
    if (x === null) continue
    const yHigh = series.priceToCoordinate(d.high)
    if (yHigh === null) continue
    checked += 1
    for (const r of rects) {
      if (x < r.left - 6 || x > r.right + 6) continue
      if (yHigh < r.bottom - 1) {
        offenders.push({ time: d.time, x: Math.round(x), y: Math.round(yHigh), overlay: r.cls, overlapPx: Math.round(r.bottom - yHigh) })
        break
      }
    }
  }
  return {
    hostHeight: Math.round(host.getBoundingClientRect().height),
    paneH: chart.panes()[0].getHeight(),
    scaleTop: chart.priceScale('right').options().scaleMargins.top,
    legendBottom: rects.find((r) => r.cls.includes('pointer-events-none'))?.bottom ?? null,
    checked,
    offenderCount: offenders.length,
    offenders: offenders.slice(0, 4),
  }
})
add(
  '浮层不遮挡蜡烛（逐根判定）',
  Boolean(occlusion) && occlusion.offenderCount === 0,
  occlusion
    ? `图区高 ${occlusion.hostHeight} 主图pane=${occlusion.paneH}px 留白=${(occlusion.scaleTop * 100).toFixed(1)}% 图例下沿=${Math.round(occlusion.legendBottom ?? -1)}px 检查 ${occlusion.checked} 根 → 重叠 ${occlusion.offenderCount} 根 ${occlusion.offenderCount ? JSON.stringify(occlusion.offenders) : ''}`
    : '无法测量',
)

// ⑭ 标记必须是**图标标记**（入场三角/出场胶囊），不是内置圆点
const markerSpecs = adaMarkerSpecs
if (markerSpecs === null) {
  add('买卖/盈亏标记为矢量图标（非圆点）', false, '__qxMarkerPlugin 未挂载（标记图元没建起来）')
} else if (markerSpecs.length === 0) {
  add('买卖/盈亏标记为矢量图标（非圆点）', false, '当前标的标记数为 0（ADA 有 7 笔成交，应当有标记）')
} else {
  const kinds = [...new Set(markerSpecs.map((m) => m.kind))]
  const hasText = markerSpecs.every((m) => typeof m.text === 'string' && m.text.length > 0)
  const hasPosition = markerSpecs.every((m) => m.position === 'aboveBar' || m.position === 'belowBar')
  add(
    '买卖/盈亏标记为矢量图标（非圆点）',
    hasText && hasPosition && kinds.includes('exit'),
    `标记 ${markerSpecs.length} 枚 · 类型=${kinds.join('/')} · 样例=${markerSpecs.slice(0, 3).map((m) => `${m.kind}:${m.text}@${m.position}`).join(' , ')}`,
  )
}

// ⑫ 无页面异常
const realErrs = errs.filter((e) => !/ResizeObserver/.test(e))
add('零页面异常', realErrs.length === 0, realErrs.slice(0, 3).join(' / '))

await browser.close()
const pass = rows.filter((r) => r.ok).length
console.log(JSON.stringify({ pass, total: rows.length, rows, errs }, null, 2))
process.exit(pass === rows.length ? 0 : 1)
