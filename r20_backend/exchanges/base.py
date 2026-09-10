"""多交易所统一适配层（Phase 1 · 2026-09-09 立项）。

设计原则（源自 plan_local/R20_DEEP_ROADMAP_2026-09.md，对齐 freqtrade/nautilus 模式）：

1. **能力表驱动**：每个交易所子类 = 一张差异声明字典（ExchangeCapabilities），
   标的命名、数量语义、触发价默认、限频、附属 TP/SL、大陆 IP 政策全部显式声明。
2. **显式不支持，永不静默模拟**：未实装的私有切面（下单/账户/保护单）一律抛
   ``ExchangeCapabilityError`` fail-closed——绝不返回假数据骗过上层。
3. **对外统一币本位，进 venue 前换算**：上层决策契约只谈「保证金 USDT / 杠杆 /
   现价」，``quote_qty_to_native()`` 按场所规格折算张数或币数（含精度截断）。
4. **行情与执行分离**（nautilus 模式）：本阶段 Binance/Gate 仅实装公共只读行情；
   执行路由留到 Phase 3（单所 ≥100 笔样本门槛前不接第二所实盘）。

符号规范：全系统内部 canonical 资产名 = 裸币种（"BTC"）；venue 原生 instId 只在
适配器边界内存在（OKX "BTC-USDT-SWAP" / Binance "BTCUSDT" / Gate "BTC_USDT"）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, Optional

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class ExchangeCapabilityError(RuntimeError):
    """能力缺失显式拒绝（fail-closed）。上层不得捕获后静默降级。"""


@dataclass(frozen=True)
class ExchangeCapabilities:
    venue: str                       # "okx" | "binance" | "gate"
    display_name: str
    symbol_template: str             # e.g. "{base}-USDT-SWAP"
    quote: str = "USDT"
    # ---- 数量语义 ----
    quantity_unit: str = "contracts"  # "contracts"(张) | "base_asset"(币本位数量)
    signed_size: bool = False         # Gate: 正多负空带符号张数
    # ---- 条件单/OCO 语义（Phase 3 用，先声明后实现）----
    supports_attached_tp_sl: bool = False   # OKX attachAlgoOrds / Bybit tpslMode
    trigger_price_default: str = "last"     # 本系统内部语义标注（last/mark/index）
    # ⚠️ US-004 纠偏（审计 2026-09-10 §2 Binance，替代旧「默认 MARK_PRICE」误断言）：
    # Binance Algo API 的 workingType 官方默认是 CONTRACT_PRICE——实现必须显式传入
    # 触发价格类型，不依赖任何默认值；旧调研把默认写成 MARK_PRICE 已作废。
    # ---- US-004 真实语义字段（审计 §2 出处逐所声明，勿再挤在 supports_orders 一栏）----
    # 订单 ID 类型（审计 §2 Gate id_string 防 JS int64 精度损失 / Binance orderId int64）：
    #   "string"             — 原生即字符串（OKX ordId/algoId）
    #   "int64_id_string"    — int64 且响应带 id_string，读取一律用 id_string 字符串（Gate）
    #   "int64_precision_risk" — int64 无 id_string，跨 JSON Number 链路必须 str 归一（Binance）
    order_id_type: str = "string"
    native_amend: bool = False        # 原生改单端点（Gate price_orders/amend=True）
    decimal_amount: bool = False      # 十进制张数 amount 字符串（Gate 模型支持，账户实况未验）
    # 持仓模式族（仅声明支持域，永不自动切换用户账户；未支持档=禁新开仓并显示原因）
    # gate: ("single","dual","dual_plus")——dual_plus 拆仓不得折叠成净仓/双向（审计 §2）
    position_modes: tuple = ()
    conditional_family: str = "none"  # attached | independent_resource | algo_service
    protection_semantics: str = ""    # 保护生效条件的人读语义（见各所声明与 §0 设计纠正）
    # ---- 公共行情 ----
    max_candle_limit: int = 300
    bar_case: str = "upper"                 # OKX 混合大小写 vs binance/gate 全小写
    has_top_trader_ratio: bool = False
    has_taker_ratio: bool = False
    # ---- 私有面能力（本阶段三家除 OKX 现状外均为 False）----
    supports_account: bool = False
    supports_orders: bool = False
    # ---- G9 统一开闸判定（US-009 后收口）：适配器执行的 env 旗标单源声明 ----
    #: 空 = 经本适配器的执行面未实装/未启用（恒关闸，结构性，非风险开关）；
    #: 非空 = live 档开闸旗标名（sandbox 档自动映射 `<前缀>DEMO_EXECUTION` 变体），
    #: 判定单一入口 registry.execution_open（能力表 AND 环境双轴旗标）。
    adapter_execution_flag: str = ""
    # ---- 网络与合规现实（文档化，供选所/可用性矩阵使用）----
    mainland_ip_restricted: bool = False
    rate_limit_note: str = ""


@dataclass(frozen=True)
class InstrumentSpec:
    """合约规格——三家原生字段归一后的统一形态。"""
    venue: str
    inst_id: str
    base: str
    tick_size: float          # 最小价格变动
    step_size: float          # 最小数量增量（base_asset 语义用；contracts 用 ct_val 整数）
    ct_val: float             # 每张合约对应的币本位面值（base_asset 所恒为 1.0）
    min_size: float           # 原生单位最小下单量（张 或 币）
    max_leverage: float = 0.0
    status: str = "trading"
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


def canonical_base(symbol: str) -> str:
    """任意写法（BTC / btc / BTC-USDT-SWAP / BTCUSDT / BTC_USDT）→ 裸币种 "BTC"。"""
    s = str(symbol or "").strip().upper()
    for marker in ("-USDT-SWAP", "USDT", "_USDT", "-USDT"):
        if s.endswith(marker):
            s = s[: -len(marker)]
            break
    return s.replace("-", "").replace("_", "")


class BaseExchangeAdapter:
    """交易所适配器基类。子类必须声明 capabilities 并设置 base_url。

    端点档位：类属性 live_url/test_url 声明；实例化时若环境变量
    ``R20_{VENUE}_TESTNET``=1 且该所声明了 test_url，则 base_url 指向沙盒。
    """

    capabilities: ExchangeCapabilities
    base_url: str = ""
    live_url: str = ""
    test_url: str = ""        # 旧单沙盒域声明，仅作未配 profile 场所的兼容兜底
    environment: str = "live"  # US-001：资金/沙盒档位（live|demo|testnet|sandbox）

    def __init__(self, session: Optional[requests.Session] = None,
                 environment: Optional[str] = None) -> None:
        """端点解析（US-001 起）：(venue, environment) → env_profiles 单一入口。

        environment=None → 按旧 ``R20_{VENUE}_TESTNET`` 布尔兼容映射
        （binance→demo 逐字节同旧 URL；gate→sandbox 双候选择优探测；
        未声明档的场所维持旧 live/test_url 行为）。
        """
        from . import env_profiles
        venue_key = str(getattr(self.capabilities, "venue", "") or "").lower()
        env = (str(environment or "").strip().lower()
               or env_profiles.legacy_environment_for(venue_key))
        if env_profiles.has_env(venue_key, env):
            self.base_url = env_profiles.resolve_base_url(venue_key, env)
        elif env != "live" and self.test_url:
            self.base_url = self.test_url          # 未配 profile 场所的旧兜底
        elif self.live_url:
            self.base_url = self.live_url
        self.environment = env
        self._session = session
        self._specs_cache: Dict[str, InstrumentSpec] = {}

    # ------------------------------------------------------------------
    # 会话与公共 HTTP（读-only、fail-soft：失败返回 None，不抛不炸上层）
    # ------------------------------------------------------------------
    def get_session(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update(DEFAULT_HEADERS)
            self._session = s
        return self._session

    def _public_get(self, path: str, params: Optional[Dict[str, Any]] = None,
                    timeout: float = 4.0) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self.get_session().get(url, params=params, timeout=timeout)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 切面 1：标的命名双向转译
    # ------------------------------------------------------------------
    def native_symbol(self, symbol: str) -> str:
        """canonical 资产名 → venue 原生 instId。"""
        return self.capabilities.symbol_template.format(base=canonical_base(symbol))

    def canonical(self, inst_id: str) -> str:
        """venue 原生 instId → canonical 资产名。"""
        return canonical_base(inst_id)

    def to_bar(self, bar: str) -> str:
        """统一周期（内部 OKX 风格 15m/1H/4H）→ venue 字面量。"""
        b = str(bar or "15m").strip()
        if self.capabilities.bar_case == "lower":
            return b.lower()
        return b

    # ------------------------------------------------------------------
    # 切面 2：公共行情（子类实现；统一返回归一形态或 None）
    # ------------------------------------------------------------------
    def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def fetch_candles(self, symbol: str, bar: str = "15m",
                      limit: int = 100) -> Optional[list]:
        raise NotImplementedError

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        raise NotImplementedError

    def fetch_orderbook(self, symbol: str, depth: int = 20) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def fetch_top_trader_ratio(self, symbol: str) -> Optional[float]:
        """大户持仓多空比。能力缺失 → 显式 None + 上层按数据健康度处理，不伪造。"""
        if not self.capabilities.has_top_trader_ratio:
            return None
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 切面 3：合约规格
    # ------------------------------------------------------------------
    def fetch_instrument_spec(self, symbol: str, refresh: bool = False) -> Optional[InstrumentSpec]:
        inst = self.native_symbol(symbol)
        if not refresh and inst in self._specs_cache:
            return self._specs_cache[inst]
        spec = self._load_spec(inst)
        if spec:
            self._specs_cache[inst] = spec
        return spec

    def _load_spec(self, inst_id: str) -> Optional[InstrumentSpec]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 切面 4：数量语义换算层（对外币本位 → 原生单位，含精度截断）
    # ------------------------------------------------------------------
    def quote_qty_to_native(self, notional_usdt: float, price: float,
                            spec: InstrumentSpec) -> float:
        """名义价值 USDT + 现价 + 规格 → 该场所原生下单量（正数）。

        - base_asset 语义（Binance）：币数，向下截断到 step_size；
        - contracts 语义（OKX/Gate）：整数张，四舍五入后校验最小张数。
        换算失败/低于最小名义价值 → 0.0（调用方据此拒单，fail-closed）。

        US-004 注：本函数是「按名义额估整数张」的便利换算，不是数量类型禁令——
        Gate 等支持十进制 amount 的场所（capabilities.decimal_amount=True 且环境合约
        规格许可）可在下单入口显式传 Decimal/十进制字符串 amount 绕过本整数换算，
        不得再把「所有合约必须 int 张数」当硬编码事实（审计 §2 Gate decimal amount）。
        """
        if notional_usdt <= 0 or price <= 0:
            return 0.0
        cap = self.capabilities
        if cap.quantity_unit == "base_asset":
            qty = Decimal(str(notional_usdt / price))
            step = Decimal(str(spec.step_size or 1e-9))
            qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
            native = float(qty)
            if native < spec.min_size or native * price < 1e-9:
                return 0.0
            return native
        # contracts：每张名义价值 = price * ct_val
        per_contract = price * (spec.ct_val or 1.0)
        if per_contract <= 0:
            return 0.0
        contracts = int(round(notional_usdt / per_contract))
        if contracts < int(spec.min_size or 1) or contracts < 1:
            return 0.0
        return float(contracts)

    # ------------------------------------------------------------------
    # 切面 5/6：私有执行与账户 —— 本阶段全部显式拒绝
    # ------------------------------------------------------------------
    def _unsupported(self, facet: str) -> ExchangeCapabilityError:
        return ExchangeCapabilityError(
            f"{self.capabilities.display_name}: {facet} 未实装"
            f"（多所执行属 Phase 3 范围，单所 ≥100 笔 DEMO 样本门槛前显式拒绝）"
        )

    def account_snapshot(self) -> Dict[str, Any]:
        raise self._unsupported("账户快照")

    def positions(self) -> list:
        raise self._unsupported("持仓查询")

    def place_order(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise self._unsupported("下单")

    def attach_protective_orders(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise self._unsupported("云端保护单（TP/SL）")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise self._unsupported("撤单")
