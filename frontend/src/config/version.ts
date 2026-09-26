/**
 * Central Source of Truth for AstraQuant
 * 统一版本与品牌定义配置中心
 * 以后发版只需在此处修改版本号，全站组件与页面自动全局同步！
 */

export const APP_VERSION_RAW = '8.3.1'
export const APP_VERSION = `v${APP_VERSION_RAW}`
export const APP_NAME = 'AstraQuant'
export const APP_NAME_EN = 'AstraQuant'
// —— 官方出处与发版标记：每次发版请同步更新 BRAND_REVISION（发版仪式清单之一）——
export const BRAND_REVISION = '2026.09.27-v831'
export const OFFICIAL_REPO = 'https://github.com/555cute/astra-quant-agent'
/** 官网（自有域名，**带 www**：与 DNS 实际解析一致，canonical/og:url 同源） */
export const OFFICIAL_SITE = 'https://www.astraquant.tech'
export const OFFICIAL_NOTICE = `AstraQuant 官方仓库：${OFFICIAL_REPO}`
