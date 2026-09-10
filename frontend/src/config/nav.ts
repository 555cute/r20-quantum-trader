/**
 * 导航元数据单一来源：前台 tab / 后台侧栏分组 / 命令面板共用。
 * path 与后端钉扎路由严格一致（/ /trading /factors /news /lab /history /docs /admin/*）。
 */
import type { Component } from 'vue';
import {
  LayoutDashboard,
  BrainCircuit,
  Newspaper,
  Dna,
  ReceiptText,
  Gauge,
  ScrollText,
  Workflow,
  Landmark,
  Braces,
  ShieldCheck,
  Crosshair,
  Puzzle,
  KeyRound,
  Cpu,
  BellRing,
  Bot,
  DatabaseBackup,
  ClipboardList,
  UserCog,
  Info,
  BookOpen,
} from 'lucide-vue-next';

export interface NavItem {
  /** 内部 tab/路由标识（不随文案变化） */
  key: string;
  labelKey: string;
  path: string;
  icon: Component;
  /** 命令面板搜索别名（品牌词/旧称/英文） */
  alias?: string;
}

export const publicTabs: NavItem[] = [
  { key: 'trading', labelKey: 'nav.tabs.matrix', path: '/', icon: LayoutDashboard, alias: 'matrix trading 实盘' },
  { key: 'factors', labelKey: 'nav.tabs.radar', path: '/factors', icon: BrainCircuit, alias: 'radar factors ai 推演' },
  { key: 'news', labelKey: 'nav.tabs.news', path: '/news', icon: Newspaper, alias: 'news sentiment 舆情' },
  { key: 'lab', labelKey: 'nav.tabs.evolution', path: '/lab', icon: Dna, alias: 'lab evolution 进化' },
  { key: 'history', labelKey: 'nav.tabs.ledger', path: '/history', icon: ReceiptText, alias: 'ledger history 台账' },
];

export const adminGroups: { key: string; labelKey: string; items: NavItem[] }[] = [
  {
    key: 'observe',
    labelKey: 'nav.groups.observe',
    items: [
      { key: 'admin-overview', labelKey: 'nav.admin.overview', path: '/admin/overview', icon: Gauge, alias: 'overview 总览' },
      { key: 'admin-decisions', labelKey: 'nav.admin.decisions', path: '/admin/decisions', icon: ScrollText, alias: 'decisions 决策' },
      { key: 'admin-gateway', labelKey: 'nav.admin.gateway', path: '/admin/gateway', icon: Workflow, alias: 'gateway scheduler 调度' },
    ],
  },
  {
    key: 'strategy',
    labelKey: 'nav.groups.strategy',
    items: [
      { key: 'admin-council', labelKey: 'nav.admin.council', path: '/admin/council', icon: Landmark, alias: 'council 委员会' },
      { key: 'admin-promptlib', labelKey: 'nav.admin.prompts', path: '/admin/promptlib', icon: Braces, alias: 'prompt studio 提示词' },
      { key: 'admin-evolution', labelKey: 'nav.admin.evolution', path: '/admin/evolution', icon: Dna, alias: 'evolution memory 心法' },
      { key: 'admin-policy', labelKey: 'nav.admin.policy', path: '/admin/policy', icon: BookOpen, alias: 'policy snapshot 快照' },
    ],
  },
  {
    key: 'riskExec',
    labelKey: 'nav.groups.riskExec',
    items: [
      { key: 'admin-risk', labelKey: 'nav.admin.risk', path: '/admin/risk', icon: ShieldCheck, alias: 'risk 风控' },
      { key: 'admin-interceptors', labelKey: 'nav.admin.interceptors', path: '/admin/interceptors', icon: Crosshair, alias: 'interceptor fail-closed 拦截' },
      { key: 'admin-plugins', labelKey: 'nav.admin.plugins', path: '/admin/plugins', icon: Puzzle, alias: 'plugin 插件' },
      { key: 'admin-security', labelKey: 'nav.admin.security', path: '/admin/security', icon: KeyRound, alias: 'account symbols 标的池 账户 接入' },
    ],
  },
  {
    key: 'platform',
    labelKey: 'nav.groups.platform',
    items: [
      { key: 'admin-llm', labelKey: 'nav.admin.llm', path: '/admin/llm', icon: Cpu, alias: 'llm provider model 模型 供应商' },
      { key: 'admin-notify', labelKey: 'nav.admin.notify', path: '/admin/notify', icon: BellRing, alias: 'notify qq telegram 通知' },
      { key: 'admin-agents', labelKey: 'nav.admin.agents', path: '/admin/agents', icon: Bot, alias: 'agent worker 执行' },
    ],
  },
  {
    key: 'system',
    labelKey: 'nav.groups.system',
    items: [
      { key: 'admin-backup', labelKey: 'nav.admin.backup', path: '/admin/backup', icon: DatabaseBackup, alias: 'backup restore 备份' },
      { key: 'admin-audit', labelKey: 'nav.admin.audit', path: '/admin/audit', icon: ClipboardList, alias: 'audit 审计' },
      { key: 'admin-adminsys', labelKey: 'nav.admin.adminsys', path: '/admin/adminsys', icon: UserCog, alias: 'admin password 管理员' },
      { key: 'admin-about', labelKey: 'nav.admin.about', path: '/admin/about', icon: Info, alias: 'about version 关于' },
    ],
  },
];

export const allAdminItems = adminGroups.flatMap((g) => g.items);
