/** 关于与更新页文案 */
export const zhAdminAbout = {
  intro: "确认版本状态，执行安全快进更新。",
  badge: "治理 · 3/3",
  loading: "正在加载组件与版本数据...",
  productArchitecture: "产品架构:",
  systemVersion: "系统版本:",
  controlPlane: "网关控制面:",
  runtime: "运行环境:",
  repoLink: "GitHub 官方代码仓库",
  componentsTitle: "组件版本",
  componentsSub: "生产运行栈",
  securityUpdate: "安全更新",
  currentBranch: "当前分支",
  localCommit: "本地提交 (HEAD)",
  remoteCommit: "远端提交 (origin)",
  pending: "待检查",
  syncGap: "待同步差额",
  behind: "落后 {n} 提交",
  upToDate: "已最新",
  ahead: "(领先 {n})",
  connecting: "正在连接远端...",
  checkUpdate: "检查远端更新",
  runUpdate: "执行安全更新",
  gitOutput: "Git 执行输出：",
  safetyNote: "安全保护机制：执行更新时仅允许 Fast-Forward 快进合并；如果工作区有未提交的追踪代码冲突、远端不可达或无法快进，后台将自动拒绝更新以保护系统稳定性。",
  confirmTitle: "确认更新 AstraQuant 系统",
  confirmSubtitle: "关于 AstraQuant：执行 fast-forward 拉取最新主分支代码",
  confirmPrefix: "为防止误操作，请在下方输入确认短语",
  confirmSuffix: "：",
  phrasePlaceholder: "请输入 UPDATE R20",
  cancel: "取消",
  updating: "正在更新中...",
  confirmNow: "立即确认更新",
  checkBehind: "发现远端有 {behind} 个新提交可更新 (远端 {remote})",
  checkUpToDate: "当前代码已是最新，与远端主分支保持同步。",
  updateSuccess: "系统更新成功！",
  updateNoop: "当前分支已是最新。",

  // ── 推倒重来新增（批 8）──
  productTitle: '产品信息',
  bandVersion: '系统版本',
  bandControlPlane: '网关控制面',
  bandRuntime: '运行环境',
  bandSyncGap: '待同步差额',  // ── 批 41：本地化写死文案（更新检查失败）──
  updateCheckFailed: '更新检查失败：{msg}（无法确认是否落后，安全补丁可能静默脱班）',

  // ── 注册/返佣通道（2026-09）──
  // 三条地址来自后端 `/api/v1/admin/about` 的 `channels`（可被 OKX_INVITE_URL /
  // GATE_INVITE_URL / BINANCE_INVITE_URL 覆盖）；OKX 经纪商 code 与**实发订单上的
  // tag 同源**。此处只放界面 chrome，链接与 code 一律来自接口，不在前端硬编码。
  channelsTitle: '注册通道',
  channelsSub: '开户与费率绑定入口',
  channelsLead: '经下列入口注册可绑定对应交易所的费率与返佣；老用户满足交易所的召回条件时同样可绑定。',
  channelOpen: '注册入口',
  channelUnset: '未配置（可用环境变量覆盖）',
};
