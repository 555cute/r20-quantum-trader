<script setup lang="ts">
/**
 * DocsView.vue · DeepSeek Harness 风格系统与技术开发文档中心
 * 包含：双轨工作台骨架、目录大纲索引树 (TOC) 与平滑滚动侦测、全功能架构透视、微积分数学与硬防线技术规范、实机大图预览
 */
import { ref, onMounted, onUnmounted, onBeforeUnmount, watch } from 'vue';
import { useModalFocus } from '../../composables/useModalFocus';
import { useRouter } from 'vue-router';
import {
  ShieldCheck,
  Cpu,
  FileText,
  ArrowLeft,
  ExternalLink,
  Terminal,
  Users,
  Brain,
  TrendingUp,
  Layers,
  Lock,
  ShieldAlert,
  ChevronRight,
  Menu,
  X,
  Server,
  BookOpen,
} from 'lucide-vue-next';
import { APP_VERSION, APP_NAME } from '../../config/version';
import { useI18n } from '../../composables/useI18n';

const router = useRouter();
const { t } = useI18n();

const activeSection = ref('overview');
const mobileMenuOpen = ref(false);
const zoomImage = ref<string | null>(null);
const zoomPanel = ref<HTMLElement | null>(null);
const { sync: syncZoomFocus, release: releaseZoomFocus } = useModalFocus(
  zoomPanel,
  () => { zoomImage.value = null; },
);
watch(() => Boolean(zoomImage.value), syncZoomFocus);
onBeforeUnmount(releaseZoomFocus);

const sections = [
  { id: 'overview', title: '1. 系统架构与量化哲学', icon: TrendingUp },
  { id: 'dashboard', title: '2. 双翼工作台与资产控制舱', icon: Terminal },
  { id: 'council', title: '3. 对冲基金投委会 (Trading Desk)', icon: Users },
  { id: 'policy_snapshot', title: '4. 策略版本快照控制台 (Policy Snapshot)', icon: Layers },
  { id: 'prompt_studio', title: '5. 提示词策略与语义变量插槽', icon: FileText },
  { id: 'interceptors', title: '6. Python 物理拦截插件 (Fail-Closed)', icon: ShieldCheck },
  { id: 'risk_control', title: '7. 执行层风控管理中心', icon: Lock },
  { id: 'llm_hub', title: '8. 模型连接与 API 协议支持', icon: Cpu },
  { id: 'self_evolution', title: '9. 自进化认知与长期记忆闭环', icon: Brain },
  { id: 'deployment', title: '10. 生产部署与多通道通知', icon: Server },
  { id: 'faq', title: '11. 常见问题解答与风控底线 (FAQ)', icon: ShieldAlert },
];

function scrollToSection(id: string) {
  activeSection.value = id;
  mobileMenuOpen.value = false;
  const el = document.getElementById(id);
  if (el) {
    // 批 68：base.css 的 prefers-reduced-motion 只能关掉 CSS 滚动，
    // 关不掉 JS 的 behavior:'smooth' —— 前庭敏感用户仍会被强制平滑滚动。
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  }
}

function onScroll() {
  const scrollPos = window.scrollY + 120;
  for (let i = sections.length - 1; i >= 0; i--) {
    const el = document.getElementById(sections[i].id);
    if (el && el.offsetTop <= scrollPos) {
      activeSection.value = sections[i].id;
      break;
    }
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && mobileMenuOpen.value) {
    mobileMenuOpen.value = false;
  }
}

watch(mobileMenuOpen, (menu) => {
  if (typeof document !== 'undefined') {
    if (menu) {
      document.body.style.overflow = 'hidden';
    } else if (!zoomImage.value) {
      document.body.style.overflow = '';
    }
  }
});

onMounted(() => {
  window.addEventListener('keydown', onKeydown);
  window.addEventListener('scroll', onScroll, { passive: true });
});

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll);
  window.removeEventListener('keydown', onKeydown);
  if (typeof document !== 'undefined') {
    document.body.style.overflow = '';
  }
});
</script>

<template>
  <div class="min-h-screen font-sans selection:bg-[var(--accent)] selection:text-white" style="background-color: var(--surface-0); color: var(--ink-1);">
    <!-- Top Header Navigation -->
    <header
      class="sticky top-0 z-[var(--z-header)] border-b px-3 sm:px-6 h-12 flex items-center justify-between backdrop-blur-xl"
      style="background-color: var(--surface-header); border-color: var(--line-1);"
    >
      <div class="flex items-center space-x-2 sm:space-x-3 min-w-0">
        <button type="button"
          @click="router.push('/')"
          class="btn btn-quiet h-7 px-2.5 text-xs font-medium cursor-pointer inline-flex items-center gap-1.5 rounded-full"
          :title="t('docs.backTerminal')"
        >
          <ArrowLeft class="w-3.5 h-3.5" />
          <span class="hidden sm:inline">{{ t('docs.backTerminalShort') }}</span>
        </button>
        <div class="h-4 w-px hidden sm:block shrink-0" style="background-color: var(--line-1);" />
        <div class="flex items-center space-x-2 min-w-0">
          <BookOpen class="h-4 w-4 text-[var(--accent)] shrink-0" />
          <!-- 批 45：本页此前**没有 h1**（首个标题是章节 h2）。顶栏品牌名即文档主标题，
               换成 h1，类名一字未改，观感不变。 -->
          <h1 class="font-bold text-xs sm:text-sm tracking-wide shrink-0 whitespace-nowrap text-[var(--ink-strong)]">
            {{ APP_NAME }}
          </h1>
          <span
            class="dsh-pill font-mono text-3xs"
          >
            {{ APP_VERSION }} 文档中心
          </span>
        </div>
      </div>

      <div class="flex items-center space-x-2 shrink-0">
        <button type="button"
          @click="mobileMenuOpen = !mobileMenuOpen"
          class="sm:hidden btn btn-quiet btn-icon h-7 w-7 cursor-pointer rounded-full"
          :title="t('docs.tocBtn')"
          :aria-label="t('docs.tocBtn')"
          :aria-expanded="mobileMenuOpen"
          :aria-controls="'docs-mobile-drawer'"
        >
          <Menu v-if="!mobileMenuOpen" class="w-3.5 h-3.5" />
          <X v-else class="w-3.5 h-3.5" />
        </button>

        <button type="button"
          @click="router.push('/admin')"
          class="hidden sm:inline-flex btn btn-ghost h-7 px-3 text-xs font-medium cursor-pointer items-center gap-1.5 rounded-full"
        >
          <Lock class="w-3.5 h-3.5 text-[var(--accent)]" />
          <span>控制台</span>
        </button>

        <a
          href="https://github.com/555cute/r20-quantum-trader"
          target="_blank"
          rel="noopener noreferrer"
          class="btn btn-primary h-7 px-3 text-xs font-medium inline-flex items-center gap-1.5 rounded-full"
        >
          <ExternalLink class="w-3.5 h-3.5" aria-hidden="true" />
          <span class="hidden sm:inline">GitHub</span>
          <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
        </a>
      </div>
    </header>

    <!-- Mobile TOC Backdrop Overlay -->
    <div
      v-if="mobileMenuOpen"
      class="fixed inset-0 bg-black/60 backdrop-blur-xs z-40 sm:hidden"
      @click="mobileMenuOpen = false"
    />

    <!-- Main Container -->
    <div class="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8 flex gap-8">
      <!-- Left Sticky Sidebar (TOC) -->
      <aside
        id="docs-mobile-drawer"
        class="w-64 shrink-0 fixed inset-y-12 left-0 z-50 sm:z-30 sm:bg-transparent p-4 sm:p-0 border-r sm:border-r-0 transition-transform duration-200 sm:translate-x-0 sm:sticky sm:top-16 sm:h-[calc(100vh-5rem)] overflow-y-auto"
        :class="mobileMenuOpen ? 'translate-x-0 bg-[var(--surface-1)] shadow-2xl' : '-translate-x-full sm:translate-x-0 invisible sm:visible'"
        style="border-color: var(--line-1);"
      >
        <div class="flex items-center justify-between mb-3 px-2">
          <div class="text-3xs font-bold uppercase tracking-wider text-[var(--ink-3)]">
            {{ t('docs.tocBtn') }} (TOC)
          </div>
          <button type="button"
            @click="mobileMenuOpen = false"
            class="sm:hidden btn btn-quiet btn-icon h-6 w-6"
            :title="t('docs.closeToc')"
            :aria-label="t('docs.closeToc')"
          >
            <X class="w-3.5 h-3.5" />
          </button>
        </div>
        <nav class="space-y-1">
          <button type="button"
            v-for="s in sections"
            :key="s.id"
            @click="scrollToSection(s.id)"
            :aria-current="activeSection === s.id ? 'location' : undefined"
            class="w-full text-left px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center justify-between group cursor-pointer border"
            :style="activeSection === s.id
              ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)', borderColor: 'var(--line-3)', fontWeight: 'bold' }
              : { backgroundColor: 'transparent', borderColor: 'transparent', color: 'var(--ink-2)' }"
          >
            <div class="flex items-center space-x-2.5 truncate" :title="s.title">
              <component :is="s.icon" class="w-3.5 h-3.5 shrink-0" />
              <span class="truncate">{{ s.title }}</span>
            </div>
            <ChevronRight class="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" :class="activeSection === s.id ? 'opacity-100' : ''" />
          </button>
        </nav>

        <div class="dsh-card-sub mt-6 p-3.5 text-xs space-y-2">
          <div class="text-3xs uppercase font-bold text-[var(--ink-3)]">社区交流与极客讨论</div>
          <div class="font-bold flex items-center justify-between text-[var(--ink-1)]">
            <span>QQ 官方群</span>
            <span class="dsh-pill font-mono font-bold text-3xs">655973677</span>
          </div>
          <p class="text-3xs text-[var(--ink-3)] leading-body">
            欢迎量化极客、提示词工程师、深度求索 R1 用户共同交流探索。
          </p>
        </div>
      </aside>

      <!-- Right Content Area -->
      <main class="min-w-0 flex-1 space-y-12 pb-24">
        <!-- 1. 系统概览与量化哲学 -->
        <section id="overview" class="space-y-4 pt-2 scroll-mt-14">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 01</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">系统架构与量化哲学</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            <strong>AstraQuant 量化交易系统</strong> 是一套专为高波动加密货币（Crypto）打造的<strong>机构级全自动波段量化决策与执行系统</strong>。系统通过 OKX / Binance / Gate.io REST API 直签执行私有账户与交易请求。系统运行在严格的北京时间（UTC+8）自然日财务基准之上，聚焦 1H~4H 大级别顺势波段，以<strong>“胜率第一、宁缺毋滥、三位一体 Fail-Closed 物理硬防线”</strong>为最高风控宗旨。
          </p>

          <!-- 4 Core Pillars Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
            <div class="dsh-card-sub p-4 space-y-2">
              <div class="flex items-center space-x-2 text-xs font-bold text-[var(--up)]">
                <ShieldCheck class="w-4 h-4" />
                <span>Fail-Closed 物理硬拦截</span>
              </div>
              <p class="text-xs text-[var(--ink-2)] leading-body">
                绝不将风控寄托于 LLM 提示词本身。在交易执行底层设立不可覆盖的 Python 物理拦截插件管线，4H 顺势门禁、80% 置信度、1H ADX 震荡过滤及真实 2.0R 盈亏比门禁物理硬切断。
              </p>
            </div>

            <div class="dsh-card-sub p-4 space-y-2">
              <div class="flex items-center space-x-2 text-xs font-bold text-[var(--accent)]">
                <Users class="w-4 h-4" />
                <span>多模型决策委员会 (Council Pro)</span>
              </div>
              <p class="text-xs text-[var(--ink-2)] leading-body">
                支持并发调度宏观分析师、盘口微结构官、舆情侦察官等多参谋席位展开深度思考辩论，落地一票否决、加权共识与动能突破三种裁决机制，由首席终审仲裁官收口输出严格契约。
              </p>
            </div>

            <div class="dsh-card-sub p-4 space-y-2">
              <div class="flex items-center space-x-2 text-xs font-bold text-[var(--ink-strong)]">
                <Layers class="w-4 h-4" />
                <span>语义数据插槽提示词系统</span>
              </div>
              <p class="text-xs text-[var(--ink-2)] leading-body">
                全网快讯、自进化心法、多标的数理矩阵等动态数据抽象为标准语义变量插槽（如 <code>&#123;&#123;news_intelligence&#125;&#125;</code>），支持模块自由解耦与策略方案一键导入导出。
              </p>
            </div>

            <div class="dsh-card-sub p-4 space-y-2">
              <div class="flex items-center space-x-2 text-xs font-bold text-[var(--warn)]">
                <Brain class="w-4 h-4" />
                <span>自进化认知复盘闭环</span>
              </div>
              <p class="text-xs text-[var(--ink-2)] leading-body">
                每 6 小时自动读取真实平仓台账流水进行自我反思与痛点归因，自动更新 <code>AI_TRADING_MEMORY.md</code> 长效实战心法，具备时效覆盖与动态经验淘汰机制。
              </p>
            </div>
          </div>
        </section>

        <!-- 2. 双翼工作台与资产控制舱 -->
        <section id="dashboard" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 02</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">量化工作台与资产控制舱</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            前台终端采用 R20 机构级量化工作台架构，首屏直接铺满 K 线图表工位与活跃持仓挂单：
          </p>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1 text-xs">
            <div class="dsh-card-sub p-3.5 space-y-1.5">
              <div class="font-bold text-xs text-[var(--ink-strong)]">主工位操盘中心</div>
              <p class="text-[var(--ink-2)] leading-body">
                • <strong>6 单元多所资产 HUD</strong>：多所总权益、走势折线、今日已结、持仓浮盈、多空敞口与云端防线解耦呈现。<br>
                • <strong>资金费与手续费明细透传</strong>：实时汇总跨周期永续合约资金费与手续费，消除浮盈与已结盈亏认知差。<br>
                • <strong>TradingView 官方原生 K 线操盘工作站</strong>：本地打包集成，0 外部依赖免 VPN 秒开；支持 150 根 K 线全屏铺满、MA/BOLL/VOL 多指标独立共存。
              </p>
            </div>
            <div class="dsh-card-sub p-3.5 space-y-1.5">
              <div class="font-bold text-xs text-[var(--ink-strong)]">多因子微积分动力学矩阵</div>
              <p class="text-[var(--ink-2)] leading-body">
                • <strong>微积分物理动能指标</strong>：实时计算一阶速度 $v$、二阶加速度 $a$ 与 ADX 趋势动量。<br>
                • <strong>聪明钱微结构</strong>：追踪大户多空比与净流入流出，点击行即刻呼出白盒透视抽屉。
              </p>
            </div>
          </div>
        </section>

        <!-- 3. 多模型决策委员会 -->
        <section id="council" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 03</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">多模型决策委员会 (Council Pro)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            为了彻底消除单一模型的幻觉与盲区，系统落地了<strong>多参谋并发辩论与博弈仲裁机制</strong>。在每一轮决策前，行情数理包将分发给各独立席位进行并发思考：
          </p>

          <div class="dsh-card-sub p-4 text-xs space-y-2.5">
            <div class="font-bold text-xs text-[var(--ink-strong)]">三种委员会共识机制：</div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 pt-1">
              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--down)]">1. 一票否决制 (Paranoid Veto)</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">只要任一参谋提出重大风险预警，仲裁官无条件强制降级为 WAIT。</div>
              </div>
              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--accent)]">2. 加权共识制 (Weighted Majority)</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">按各席位置信度加权投票，仅在同向权重绝对占优时准许发单。</div>
              </div>
              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--up)]">3. 动能突破优先 (Alpha Hunter)</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">当微积分加速度与冲击超阈值共振时，赋予技术突破参谋优先表决权。</div>
              </div>
            </div>
          </div>
        </section>

        <!-- 4. 策略版本快照控制台 -->
        <section id="policy_snapshot" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 04</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">策略版本快照控制台 (Policy Snapshot)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            实时聚合提示词、自进化心法、物理拦截器与投委会四大单元的不可变指纹，解决量化策略碎片化与复盘失真难题：
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <div class="dsh-card-sub p-3">
              <div class="font-bold" style="color: var(--info)">1. 不可变哈希指纹</div>
              <p class="text-3xs mt-1 text-[var(--ink-3)]">四大单元配置任何变动即时生成唯一版本哈希，不可伪造。</p>
            </div>
            <div class="dsh-card-sub p-3">
              <div class="font-bold" style="color: var(--info)">2. 具名版本归档与删除</div>
              <p class="text-3xs mt-1 text-[var(--ink-3)]">一键将跑得好的全盘配置持久化入库，可随时管理并支持回滚。</p>
            </div>
            <div class="dsh-card-sub p-3">
              <div class="font-bold" style="color: var(--up)">3. 一键秒级原子回滚</div>
              <p class="text-3xs mt-1 text-[var(--ink-3)]">调乱参数时，全盘原子恢复四大单元真实配置，下一周期立即可用。</p>
            </div>
          </div>
        </section>

        <!-- 5. 提示词策略与变量插槽 -->
        <section id="prompt_studio" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 05</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">提示词策略与语义变量插槽</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            提示词策略工作室彻底解除了所有预设锁定，支持对四大核心管线（交易 System、交易 User、自进化 System、自进化 User）进行可视化定制。
          </p>

          <!-- Variable Table -->
          <div class="dsh-card overflow-x-auto">
            <table class="table w-full text-xs" aria-label="语义变量插槽字段说明">
              <thead>
                <tr>
                  <th scope="col">变量占位符</th>
                  <th scope="col">数据分类</th>
                  <th scope="col">注入内容与实战用途</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td class="font-mono font-bold text-[var(--accent)]">&#123;&#123;account_balance&#125;&#125;</td>
                  <td>账户资产</td>
                  <td class="text-[var(--ink-2)]">交易所私有接口实时拉取的可用 USDT 现金余额（如 4393.08 USDT）</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--accent)]">&#123;&#123;risk_budget&#125;&#125;</td>
                  <td>风控预算</td>
                  <td class="text-[var(--ink-2)]">按可用资金与风控参数自适应推导的单笔保证金、单标的封顶、日亏熔断线与 R:R 底线</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--warn)]">&#123;&#123;account_positions&#125;&#125;</td>
                  <td>账户敞口</td>
                  <td class="text-[var(--ink-2)]">注入在途持仓方向、均价、未结浮盈 ROI、最高浮盈点（High Water Mark）及极值回撤百分比</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--warn)]">&#123;&#123;pending_orders&#125;&#125;</td>
                  <td>挂单池</td>
                  <td class="text-[var(--ink-2)]">注入在途未成交 Maker 限价挂单 ID、价位、数量及挂单时长，供模型执行 KEEP 或 CANCEL</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--ink-strong)]">&#123;&#123;market_matrix&#125;&#125;</td>
                  <td>数理行情</td>
                  <td class="text-[var(--ink-2)]">全标的实时价、微积分导数 (v/a/j/I)、定积分做功 (E/A)、延续/击穿概率 (P续/P破)、资金费率与盘口深度</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--accent)]">&#123;&#123;news_intelligence&#125;&#125;</td>
                  <td>全网快讯</td>
                  <td class="text-[var(--ink-2)]">注入全网最新重大突发要闻、美联储决策、黑天鹅预警与宏观情绪倾向标签</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--up)]">&#123;&#123;trading_memory&#125;&#125;</td>
                  <td>自进化心法</td>
                  <td class="text-[var(--ink-2)]">注入真实复盘提炼的核心心法、避坑铁律与长效实战教训（源自 structured_trading_memory.json）</td>
                </tr>
                <tr>
                  <td class="font-mono font-bold text-[var(--ink-3)]">&#123;&#123;decision_timestamp&#125;&#125;</td>
                  <td>系统环境</td>
                  <td class="text-[var(--ink-2)]">当前决策周期的精确北京时间戳与时效基准</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Prompt Guide Highlights -->
          <div class="dsh-card-sub p-4 space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold text-[var(--ink-strong)] flex items-center space-x-1.5">
                <BookOpen class="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>💡 高胜率提示词编写五大核心军规（破解“赢小输大”实战攻略）</span>
              </h3>
              <span class="dsh-pill text-3xs font-mono">PROMPT_GUIDE.md</span>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--up)]">1. 科学波段呼吸，严禁过早提损保本</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">
                  浮盈 &lt; 1.0R~1.2R 坚决给足 1.8x~2.2x ATR 宽止损呼吸空间；浮盈稳固达到 ≥ 1.5R 且波段确立后再移损保本，杜绝潜力波段刚启动就被 15M 杂波扫出。
                </div>
              </div>

              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--accent)]">2. 杜绝惊慌砸盘，让主升浪波段奔跑</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">
                  严禁在轻度浮盈后的正常日内健康回抽中恐慌市价平仓；走势未破坏前坚决 HOLD，目标盈亏比锚定 2.0R~2.8R 空间，用深厚单笔收益弥补试错成本。
                </div>
              </div>

              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--warn)]">3. 多资产敞口自律，防范系统性 Beta 踩踏</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">
                  全账户同向持仓达 2 笔以上时，提示词要求自律收紧新开仓门槛（提升至 85%+），严格避开强相关币种同向开单，防范单边跳水连环被扫。
                </div>
              </div>

              <div class="p-2.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);">
                <div class="font-bold text-[var(--down)]">4. 止损后坚决冷静，杜绝绞肉市连续接刀</div>
                <div class="text-3xs mt-1 text-[var(--ink-3)]">
                  标的一旦止损出局，提示词明确要求严格遵守冷静期，未出现大级别突破前严禁反手或在同一区间连续重复抄底摸顶（拯救 ARB 式绞肉）。
                </div>
              </div>
            </div>

            <p class="text-3xs text-[var(--ink-3)] leading-body">
              * 完整攻略与对冲基金投委会席位模板请查阅代码库根目录文档 <code>docs/PROMPT_GUIDE.md</code>。
            </p>
          </div>
        </section>

        <!-- 6. Python 物理拦截插件 -->
        <section id="interceptors" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 06</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">Python 物理拦截插件 (Fail-Closed)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            物理拦截插件体系是整个系统的安全底座。任何发往交易所的开平仓请求，必须严格穿透全部活跃拦截器的串行校验。
          </p>

          <div class="dsh-card-sub p-3.5 space-y-2">
            <h3 class="text-xs font-bold text-[var(--ink-strong)]">核心物理硬门禁原则：</h3>
            <ul class="list-disc list-inside text-xs text-[var(--ink-2)] space-y-1">
              <li><strong>4H 大级别顺势门禁</strong>：严禁逆 4H 大周期均线开反向单。</li>
              <li><strong>置信度硬阈值门禁</strong>：模型终审置信度未达到设定阈值一律强制压制为 WAIT。</li>
              <li><strong>ADX 动能过滤门禁</strong>：1H ADX 小于设定阈值判定为无序震荡，拒绝开仓。</li>
              <li><strong>真实 R 盈亏比门禁</strong>：止盈与止损空间比值低于设定值物理硬拦截。</li>
            </ul>
          </div>
        </section>

        <!-- 7. 执行层风控管理中心 -->
        <section id="risk_control" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 07</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">执行层风控管理中心</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            可视化配置单日最大亏损熔断、杠杆上限、单笔保证金比例与持仓集中度限制，支持一键切换保守、稳健、进取三套风控预设套件。
          </p>
        </section>

        <!-- 8. 模型连接与协议支持 -->
        <section id="llm_hub" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 08</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">模型连接与 API 协议支持</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            全协议支持 OpenAI Chat Completions、Responses API 与 Anthropic Claude Messages 协议，支持远端模型一键探活与 Failover 自动容灾故障转移。
          </p>
        </section>

        <!-- 9. 自进化认知与长期记忆 -->
        <section id="self_evolution" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 09</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">自进化认知与长期记忆闭环</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            每 6 小时自动读取平仓记录，进行原因剖析与数学验证，动态更新 AI 心法库，具备防过拟合与经验半衰期淘汰机制。
          </p>
        </section>

        <!-- 10. 生产部署与通知 -->
        <section id="deployment" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 10</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">生产部署与多通道通知</h2>
          </div>

          <p class="text-xs sm:text-sm leading-body font-sans text-[var(--ink-2)]">
            支持 Docker 容器化部署、Systemd 常驻守护，并集成 Telegram、飞书、企业微信、Discord、Webhook 多通道实时通知推送。
          </p>
        </section>

        <!-- 11. FAQ -->
        <section id="faq" class="space-y-4 pt-6 border-t scroll-mt-14" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="dsh-pill font-mono font-bold text-3xs">CHAPTER 11</span>
            <h2 class="text-lg sm:text-xl font-bold tracking-tight text-[var(--ink-strong)]">常见问题解答与风控底线 (FAQ)</h2>
          </div>

          <div class="space-y-3 text-xs">
            <div class="dsh-card-sub p-3 space-y-1">
              <h3 class="font-bold text-[var(--ink-strong)]">Q: 系统如何保证资金安全？</h3>
              <p class="text-[var(--ink-2)] leading-body">
                A: API Key 凭证本地 Fernet 加密存储；绝不开启提现权限；所有订单均有云端 OCO 止损物理保护；全盘具备单日亏损熔断保护。
              </p>
            </div>
            <div class="dsh-card-sub p-3 space-y-1">
              <h3 class="font-bold text-[var(--ink-strong)]">Q: 模型请求失败或超时会怎样？</h3>
              <p class="text-[var(--ink-2)] leading-body">
                A: 系统自动触发预设的 Failover 备用模型链；若全链不可用，系统自动保持 Fail-Closed 状态（不执行任何新开仓动作）。
              </p>
            </div>
          </div>
        </section>
      </main>
    </div>

    <!-- Zoom Image Modal -->
    <div
      v-if="zoomImage"
      ref="zoomPanel"
      role="dialog"
      aria-modal="true"
      :aria-label="t('docs.zoomModalAria')"
      tabindex="-1"
      class="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm cursor-zoom-out outline-none"
      @click="zoomImage = null"
    >
      <img
        :src="zoomImage"
        alt="放大的文档插图"
        class="max-w-full max-h-[90vh] rounded-lg shadow-2xl cursor-default"
        @click.stop
      />
      <button
        type="button"
        class="absolute top-4 right-4 btn btn-quiet btn-icon text-white hover:bg-white/20"
        :title="`${t('common.close')} (Esc)`"
        :aria-label="t('common.close')"
        @click="zoomImage = null"
      >
        <X class="w-5 h-5" />
      </button>
    </div>
  </div>
</template>
