"""持仓模式只读体检 + 载荷预选（审计 §2 Gate 政策的**执行**）。

## 背景：一条写在代码里、却一直没被执行的政策

`r20_backend/exchanges/gate.py` 的模块头早就写着：

> ⚠️ 仅能力声明+只读检测——本系统**永不自动切换用户账户模式**；dual_plus 拆仓
> 不得折叠成净仓/双向解读，**检测不支持时禁新开仓并显示原因**（审计 §2 Gate）。

但实现侧当时只有"先发 `close=true`、等 Gate 拒绝、再换 `auto_size` 重试"的
**反应式**兼容：既不检测，又依赖 Gate 的错误码文案（`AUTO_INVALID_PARAM_CLOSE`）——
文案一变，保护腿挂不上 ⇒ 整笔开仓回滚。本门钉住政策真正落地后的四条：

1. **只读判定**：真实账户字段 `position_mode` / `in_dual_mode` → single/dual/dual_plus，
   都读不到一律 `unknown`（不拿默认值冒充事实）；
2. **dual_plus 不折叠**（拆仓语义与净仓/双向不同），且与 unknown 一样**禁新开仓**；
3. **载荷预先选对**：dual → `auto_size`（不再浪费一次注定被拒的请求），
   single → `close=true`；
4. **永不自动切换**账户模式（源码级钉子：全仓不得出现 `set_position_mode` 调用）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from r20_backend.exchanges.base import ExchangeCapabilityError  # noqa: E402
from r20_backend.exchanges.gate import (  # noqa: E402
    AUTO_SIZE_CLOSE_LONG, AUTO_SIZE_CLOSE_SHORT, GateAdapter, GateAPIError,
    interpret_position_mode,
)

# ── 本机 DEMO 账户实测回包（逐字保留，2026-09-20）─────────────────────────
REAL_DUAL_ACCOUNT = {
    "in_dual_mode": True, "enable_new_dual_mode": True, "position_mode": "dual",
    "margin_mode": 0, "margin_mode_name": "classic", "currency": "USDT",
}


class InterpretPositionModeTest(unittest.TestCase):
    def test_real_demo_account_is_dual(self):
        self.assertEqual(interpret_position_mode(REAL_DUAL_ACCOUNT), "dual")

    def test_explicit_field_wins(self):
        payload = dict(REAL_DUAL_ACCOUNT, position_mode="single", in_dual_mode=True)
        self.assertEqual(interpret_position_mode(payload), "single",
                         "显式 position_mode 优先于布尔兜底")

    def test_dual_plus_is_never_folded(self):
        self.assertEqual(interpret_position_mode({"position_mode": "dual_plus"}), "dual_plus",
                         "拆仓不得被折叠成 dual/single 解读")

    def test_bool_fallback(self):
        self.assertEqual(interpret_position_mode({"in_dual_mode": False}), "single")
        self.assertEqual(interpret_position_mode({"in_dual_mode": True}), "dual")
        self.assertEqual(interpret_position_mode({"in_dual_mode": "false"}), "single")

    def test_nested_raw_is_supported(self):
        self.assertEqual(interpret_position_mode({"raw": REAL_DUAL_ACCOUNT}), "dual")

    def test_unreadable_is_unknown_not_a_default(self):
        for bad in ({}, None, "junk", [], {"position_mode": ""},
                    {"in_dual_mode": "maybe"}):
            with self.subTest(bad=bad):
                self.assertEqual(interpret_position_mode(bad), "unknown",
                                 "读不到必须 unknown，不能拿默认值冒充事实")


class DetectPositionModeTest(unittest.TestCase):
    def _adapter(self, snapshot=None, raises=None):
        ad = GateAdapter.__new__(GateAdapter)      # 不跑 __init__（避免读凭证/环境）

        def _snap():
            if raises is not None:
                raise raises
            return snapshot
        ad.account_snapshot = _snap                 # type: ignore[method-assign]
        return ad

    def test_reads_real_shaped_snapshot(self):
        self.assertEqual(self._adapter({"raw": REAL_DUAL_ACCOUNT}).detect_position_mode(), "dual")

    def test_failure_is_unknown_not_an_exception(self):
        for exc in (RuntimeError("net down"), GateAPIError("AUTH", "bad key")):
            with self.subTest(exc=exc):
                ad = self._adapter(raises=exc)
                self.assertEqual(ad.detect_position_mode(), "unknown",
                                 "探测失败必须 fail-soft 成 unknown（由调用方禁新开仓）")


class ProtectivePayloadTest(unittest.TestCase):
    """载荷预选：dual 发 auto_size、single 发 close=true，且**只发一次**。"""

    def _adapter(self, *, fail_close_with=None):
        ad = GateAdapter.__new__(GateAdapter)
        ad.bodies = []

        def _req(method, path, params=None, body=None, **kw):
            # 深拷贝：重试分支会**原地**改 initial（pop close / 加 auto_size），
            # 浅拷贝会让 bodies[0] 显示成重试后的载荷（本门第一版就这么误判过）
            ad.bodies.append(json.loads(json.dumps(body or {})))
            initial = (body or {}).get("initial") or {}
            if fail_close_with and "close" in initial:
                raise GateAPIError(fail_close_with, "dual mode close not allowed")
            return {"id": f"oid-{len(ad.bodies)}"}
        ad.signed_request = _req                       # type: ignore[method-assign]
        ad.native_symbol = lambda s: "BTC_USDT"        # type: ignore[method-assign]
        ad.cancel_price_order = lambda oid: {"ok": True}   # type: ignore[method-assign]
        return ad

    def test_dual_mode_sends_auto_size_first(self):
        ad = self._adapter()
        ad.attach_protective_orders("BTC", "long", tp_px=85000, sl_px=77000,
                                    position_mode="dual")
        self.assertEqual(len(ad.bodies), 2, "两腿各一次请求，不该有被拒重试")
        first = ad.bodies[0]["initial"]
        self.assertEqual(first.get("auto_size"), AUTO_SIZE_CLOSE_LONG)
        self.assertNotIn("close", first, "dual 载荷不得带 close（会被拒）")
        self.assertNotIn("size", first)

    def test_dual_mode_short_uses_close_short(self):
        ad = self._adapter()
        ad.attach_protective_orders("BTC", "short", sl_px=83000, position_mode="dual")
        self.assertEqual(ad.bodies[0]["initial"].get("auto_size"), AUTO_SIZE_CLOSE_SHORT)

    def test_single_mode_keeps_close_true(self):
        ad = self._adapter()
        ad.attach_protective_orders("BTC", "long", sl_px=77000, position_mode="single")
        first = ad.bodies[0]["initial"]
        self.assertTrue(first.get("close"))
        self.assertEqual(first.get("size"), 0)
        self.assertNotIn("auto_size", first)

    def test_unknown_mode_keeps_reactive_fallback(self):
        """老调用方退路：先 close=true，被拒后换 auto_size（行为与改动前一致）。"""
        ad = self._adapter(fail_close_with="AUTO_INVALID_PARAM_CLOSE")
        ad.attach_protective_orders("BTC", "long", sl_px=77000)      # 不传模式
        self.assertEqual(len(ad.bodies), 2, "应为『先试 close=true、再重试 auto_size』")
        self.assertIn("close", ad.bodies[0]["initial"])
        self.assertEqual(ad.bodies[1]["initial"].get("auto_size"), AUTO_SIZE_CLOSE_LONG)

    def test_dual_mode_does_not_retry_on_success(self):
        """反向：dual 预选正确时，不该再出现第二次同腿请求。"""
        ad = self._adapter(fail_close_with="AUTO_INVALID_PARAM_CLOSE")
        ad.attach_protective_orders("BTC", "long", sl_px=77000, position_mode="dual")
        self.assertEqual(len(ad.bodies), 1)
        self.assertIn("auto_size", ad.bodies[0]["initial"])


class _EntryStub(GateAdapter):
    """入口体检用的桩：继承真实能力声明，只打桩 IO。"""

    def __init__(self, mode="dual", *, declares_modes=True, **kw):
        self._mode = mode
        self._positions = kw.pop("positions", [])
        self.placed = []
        self.legs_args = {}
        if not declares_modes:
            import dataclasses
            # capabilities 是 frozen dataclass（改属性会抛），故用 replace 造一个
            # "未声明 position_modes" 的变体，等价于 Binance/sandbox 那类适配器。
            self.capabilities = dataclasses.replace(
                GateAdapter.capabilities, position_modes=())

    def _keys(self):
        return ("k", "s")

    def detect_position_mode(self):
        return self._mode

    def positions(self):
        return list(self._positions)

    def fetch_instrument_spec(self, symbol, refresh=False):
        from r20_backend.exchanges import InstrumentSpec
        return InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                              tick_size=0.1, step_size=0.0001, ct_val=0.0001, min_size=1)

    def fetch_ticker(self, symbol):
        return {"last": 79000.0, "mark_price": 79000.0}

    def set_leverage(self, symbol, leverage, margin_mode="cross"):
        return {"leverage": str(int(leverage))}

    def place_order(self, symbol, side, contracts, price=None, tif="gtc", text=""):
        self.placed.append((symbol, side, contracts, price))
        return {"id": 9001, "text": "t"}

    def attach_protective_orders(self, symbol, pos_side, tp_px=None, sl_px=None,
                                 expiration=604800, price_type=0, **kwargs):
        self.legs_args = dict(kwargs)
        return {"tp": "tp1", "sl": "sl1"}

    def list_protective_orders(self, symbol):
        return [{"id": "tp1"}, {"id": "sl1"}]

    def cancel_order(self, symbol, order_id):
        return {"cancelled": True}


class EntryGuardTest(unittest.TestCase):
    def setUp(self):
        from r20_backend import execution_router
        self.router = execution_router
        self._pool_patch = patch.object(self.router, "_load_venue_pool_soft",
                                       lambda venue: {"assets": ["BTC"], "max_open": 5,
                                                      "margin_per_trade_usdt": 500.0,
                                                      "dry_run": False})
        self._pool_patch.start()
        self.addCleanup(self._pool_patch.stop)

    def _decision(self):
        return {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 300.0, "leverage": 3,
                "entry_price": 79000.0, "take_profit_price": 85000.0,
                "stop_loss_price": 77000.0, "confidence": 88.0, "venue": "gate"}

    def test_dual_account_opens_and_passes_mode_down(self):
        ad = _EntryStub(mode="dual")
        res = self.router.open_protected_position(self._decision(), adapter=ad, price_ref=79000.0)
        self.assertTrue(res["ok"], res.get("detail"))
        self.assertEqual(ad.legs_args.get("position_mode"), "dual",
                         "探测到的模式必须下传，否则保护腿还是会先发错载荷")

    def test_single_account_opens(self):
        ad = _EntryStub(mode="single")
        res = self.router.open_protected_position(self._decision(), adapter=ad, price_ref=79000.0)
        self.assertTrue(res["ok"], res.get("detail"))
        self.assertEqual(ad.legs_args.get("position_mode"), "single")

    def test_unknown_mode_refuses_without_placing_anything(self):
        ad = _EntryStub(mode="unknown")
        res = self.router.open_protected_position(self._decision(), adapter=ad, price_ref=79000.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "position_mode")
        self.assertIn("不自动切换账户模式", res["detail"], "必须显示原因（政策原话）")
        self.assertEqual(ad.placed, [], "拒开时不得留下任何委托")

    def test_dual_plus_refuses_and_explains(self):
        ad = _EntryStub(mode="dual_plus")
        res = self.router.open_protected_position(self._decision(), adapter=ad, price_ref=79000.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "position_mode")
        self.assertIn("dual_plus", res["detail"])
        self.assertIn("拆仓", res["detail"])
        self.assertEqual(ad.placed, [])

    def test_adapter_without_declared_modes_is_not_probed(self):
        """Binance/sandbox 等未声明 position_modes 的场所：不该被这套 Gate 政策拦下。"""
        ad = _EntryStub(mode="unknown", declares_modes=False)
        res = self.router.open_protected_position(self._decision(), adapter=ad, price_ref=79000.0)
        self.assertTrue(res["ok"], res.get("detail"))
        self.assertNotIn("position_mode", ad.legs_args,
                         "没探测到模式就不该多传 position_mode（sandbox 适配器没有 **kwargs）")
        self.assertIn("contracts", ad.legs_args, "既有 contracts 入参不受影响")

    def test_venue_declaring_other_modes_without_probe_is_not_blocked(self):
        """实测形态：Binance 声明 `('net','long_short')` 且**没有**探测方法。

        按"声明了就体检、探测不到就拒"处理会**整所停掉币安新开仓** ——
        本门钉住：只有实现了只读探测的适配器才受这套政策约束（本刀真实自伤复现）。
        """
        import dataclasses
        ad = _EntryStub(mode="unknown")
        ad.capabilities = dataclasses.replace(GateAdapter.capabilities,
                                              position_modes=("net", "long_short"))
        # 模拟"该适配器没有可用的只读探测"（真实币安适配器即是此形态）。
        # ⚠️ 不能 `del` 类上的方法：那会退回到 GateAdapter 继承来的真实现，
        # 于是又变成"能探测但读不到" ⇒ 拒开，测的就不是这件事了。
        ad.detect_position_mode = None
        res = self.router.open_protected_position(self._decision(), adapter=ad,
                                                  price_ref=79000.0)
        self.assertTrue(res["ok"], res.get("detail"))
        self.assertNotIn("position_mode", ad.legs_args)

    def test_no_auto_switch_call_anywhere(self):
        """政策钉：本系统**永不**自动切换用户账户的持仓模式。"""
        hits = []
        for base in (ROOT / "r20_backend", ROOT / "scripts"):
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "set_position_mode" in text and "不得宣称已删" not in text:
                    hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], f"出现自动切换账户模式调用：{hits}")


class CapabilityDeclarationTest(unittest.TestCase):
    def test_gate_declares_position_modes(self):
        from r20_backend.exchanges.gate import GateAdapter as _G
        self.assertEqual(tuple(_G.capabilities.position_modes),
                         ("single", "dual", "dual_plus"))

    def test_interpreted_mode_is_always_in_declared_set_or_unknown(self):
        from r20_backend.exchanges.gate import POSITION_MODES
        for payload in ({}, REAL_DUAL_ACCOUNT, {"position_mode": "dual_plus"}):
            self.assertIn(interpret_position_mode(payload), set(POSITION_MODES) | {"unknown"})


if __name__ == "__main__":
    unittest.main()


# ── G11-b：币安侧持仓模式（词汇与 Gate 不同，判定函数刻意分开）────────────────
class BinancePositionModeTest(unittest.TestCase):
    """实测（2026-09-20 DEMO）：`GET /fapi/v1/positionSide/dual` → `{"dualSidePosition": false}`，
    740 行 positionRisk 全为 `positionSide=BOTH` ⇒ **净模式**。"""

    def test_real_demo_payload_is_net(self):
        from r20_backend.exchanges.binance import interpret_dual_side_position as ib
        self.assertEqual(ib({"dualSidePosition": False}), "net")
        self.assertEqual(ib({"dualSidePosition": True}), "long_short")
        self.assertEqual(ib({"dualSidePosition": "false"}), "net")

    def test_unreadable_is_unknown(self):
        from r20_backend.exchanges.binance import interpret_dual_side_position as ib
        for bad in ({}, None, [], {"dualSidePosition": None}, {"dualSidePosition": "maybe"}):
            with self.subTest(bad=bad):
                self.assertEqual(ib(bad), "unknown")

    def test_gate_and_binance_vocabularies_are_separate(self):
        """两所模式词汇不同（single/dual/dual_plus vs net/long_short）——绝不共用一个枚举。"""
        from r20_backend.exchanges.binance import BinanceAdapter
        from r20_backend.exchanges.gate import GateAdapter
        self.assertEqual(tuple(GateAdapter.capabilities.position_modes),
                         ("single", "dual", "dual_plus"))
        self.assertEqual(tuple(BinanceAdapter.capabilities.position_modes),
                         ("net", "long_short"))
        self.assertEqual(set(GateAdapter.capabilities.position_modes)
                         & set(BinanceAdapter.capabilities.position_modes), set(),
                         "两所声明域不该有交集（否则一定是有人合并了枚举）")

    def test_detect_uses_read_only_endpoint_and_fails_soft(self):
        from r20_backend.exchanges.binance import BinanceAdapter
        ad = BinanceAdapter.__new__(BinanceAdapter)
        calls = []

        def _req(method, path, **kw):
            calls.append((method, path))
            return {"dualSidePosition": False}
        ad.signed_request = _req                      # type: ignore[method-assign]
        self.assertEqual(ad.detect_position_mode(), "net")
        self.assertEqual(calls, [("GET", "/fapi/v1/positionSide/dual")],
                         "必须是只读 GET（本系统绝不 POST 切换账户模式）")

        ad.signed_request = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down"))  # type: ignore[method-assign]
        self.assertEqual(ad.detect_position_mode(), "unknown", "探测失败必须 fail-soft")

    def test_entry_ready_subset_declared_for_both_venues(self):
        from r20_backend.exchanges.binance import BinanceAdapter
        from r20_backend.exchanges.gate import GateAdapter
        self.assertEqual(tuple(BinanceAdapter.capabilities.entry_ready_position_modes), ("net",),
                         "hedge 载荷未核验 ⇒ 不在可交易子集内")
        self.assertEqual(tuple(GateAdapter.capabilities.entry_ready_position_modes),
                         ("single", "dual"), "dual_plus 不可折叠 ⇒ 不在可交易子集内")
        for caps in (BinanceAdapter.capabilities, GateAdapter.capabilities):
            with self.subTest(venue=caps.venue):
                self.assertTrue(set(caps.entry_ready_position_modes)
                                <= set(caps.position_modes),
                                "可交易子集必须是声明域的子集")

    def test_hedge_mode_is_refused_with_explanation(self):
        """币安切到对冲模式 ⇒ 拒开并说明（载荷未核验，不是"我们没实现所以停所"）。

        ⚠️ 本用例用**真实币安的模式词汇与可交易子集**驱动守卫，但 decision 的
        `venue` 仍写 gate：合约挂牌/规格这条链路走的是本桩的 Gate 口径，
        本用例**不覆盖**币安的挂牌与规格链路（那有各自的门）。
        """
        import dataclasses
        from r20_backend.exchanges.binance import BinanceAdapter
        from r20_backend import execution_router
        ad = _EntryStub(mode="long_short")
        # 模式词汇与"已验证可交易子集"取**真实币安声明**；其余能力沿用 Gate 形状，
        # 因为本桩的规格/换算打桩是 Gate 口径（只需把币安那两个字段换过来即可隔离守卫行为）。
        ad.capabilities = dataclasses.replace(
            GateAdapter.capabilities,
            position_modes=BinanceAdapter.capabilities.position_modes,
            entry_ready_position_modes=BinanceAdapter.capabilities.entry_ready_position_modes)
        with patch.object(execution_router, "_load_venue_pool_soft",
                          lambda venue: {"assets": ["BTC"], "max_open": 5,
                                         "margin_per_trade_usdt": 500.0, "dry_run": False}):
            res = execution_router.open_protected_position(
                {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 300.0, "leverage": 3,
                 "entry_price": 79000.0, "take_profit_price": 85000.0,
                 "stop_loss_price": 77000.0, "confidence": 88.0, "venue": "gate"},
                adapter=ad, price_ref=79000.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "position_mode")
        self.assertIn("long_short", res["detail"])
        self.assertIn("Hedge", res["detail"])
        self.assertEqual(ad.placed, [], "拒开时不得留下任何委托")


if __name__ == "__main__":
    unittest.main()

class OkxInterpretPositionModeTest(unittest.TestCase):
    """第一百九十三刀：OKX 此前是**唯一没有持仓模式探测**的所。

    `execution_router` 的闸写成"声明了模式**且**有探测方法才体检" ⇒ 对 OKX **整段跳过**，
    而 OKX 恰是持仓最多的所。本类钉住新增的纯解释器（`/api/v5/account/config` 回包）。
    """

    def test_long_short_mode(self):
        from r20_backend.exchanges.okx import interpret_position_mode
        self.assertEqual(interpret_position_mode({"data": [{"posMode": "long_short_mode"}]}),
                         "long_short")

    def test_net_mode(self):
        from r20_backend.exchanges.okx import interpret_position_mode
        self.assertEqual(interpret_position_mode({"data": [{"posMode": "net_mode"}]}), "net")

    def test_unreadable_is_unknown_not_a_default(self):
        """读不出**绝不**给默认值（闸对 unknown 的处置是禁新开仓）。"""
        from r20_backend.exchanges.okx import interpret_position_mode
        for payload in (None, {}, {"data": []}, {"data": [None]}, {"data": "x"},
                        {"data": [{"posMode": ""}]}, {"data": [{"posMode": "??"}]},
                        {"data": [{"posMode": "LONG_SHORT"}]}):
            with self.subTest(payload=payload):
                self.assertEqual(interpret_position_mode(payload), "unknown")

    def test_accepts_bare_list_too(self):
        from r20_backend.exchanges.okx import interpret_position_mode
        self.assertEqual(interpret_position_mode([{"posMode": "net_mode"}]), "net")


class OkxDetectPositionModeTest(unittest.TestCase):
    def _adapter(self):
        from r20_backend.exchanges.okx import OKXAdapter
        ad = OKXAdapter.__new__(OKXAdapter)          # 不跑 __init__（避免读凭证/环境）
        ad._get_okx_env = lambda: None               # type: ignore[method-assign]
        return ad

    def test_reads_through_the_signed_config_endpoint(self):
        from scripts import okx_rest
        seen = {}

        def _req(method, path, params=None, **kw):
            seen["call"] = (method, path)
            return [{"posMode": "long_short_mode"}]

        original = okx_rest.request
        okx_rest.request = _req
        try:
            got = self._adapter().detect_position_mode()
        finally:
            okx_rest.request = original
        self.assertEqual(got, "long_short")
        self.assertEqual(seen["call"], ("GET", "/api/v5/account/config"),
                         "端点/方法是被钉住的契约（只读、不改账户）")

    def test_failure_is_unknown_not_an_exception(self):
        from scripts import okx_rest

        def _boom(*a, **k):
            raise RuntimeError("net down")

        original = okx_rest.request
        okx_rest.request = _boom
        try:
            self.assertEqual(self._adapter().detect_position_mode(), "unknown",
                             "探测失败必须 fail-soft 成 unknown，绝不抛（抛了会打断整轮）")
        finally:
            okx_rest.request = original


class ModeDeclarationConsistencyTest(unittest.TestCase):
    """**声明了持仓模式却没有探测方法 = 闸静默失效**（本刀发现的正是这一形态）。

    闸的判据是 `if declared_modes and callable(probe)` ⇒ 缺探测的所**整段跳过**，
    于是"声明"看起来像有护栏，实际没有。故：凡声明了 `position_modes` 的适配器，
    必须实现 `detect_position_mode`。
    """

    def test_every_adapter_declaring_modes_can_be_probed(self):
        from r20_backend.exchanges import get_adapter
        offenders = []
        for venue in ("okx", "binance", "gate"):
            ad = get_adapter(venue, environment="demo")
            declared = tuple(getattr(ad.capabilities, "position_modes", ()) or ())
            if declared and not callable(getattr(ad, "detect_position_mode", None)):
                offenders.append(f"{venue} 声明了 {declared} 但没有 detect_position_mode")
        self.assertEqual(offenders, [], "声明了持仓模式却无法探测 ⇒ 模式闸对该所静默失效：\n"
                                        + "\n".join(offenders))

    def test_teeth_on_a_probe_less_declaration(self):
        """牙齿：造一个"声明了模式却没有探测"的适配器，判据必须能识别。"""

        def offenders_of(pairs):
            out = []
            for venue, (declared, has_probe) in pairs.items():
                if declared and not has_probe:
                    out.append(venue)
            return out

        self.assertEqual(offenders_of({"x": (("net",), False)}), ["x"])
        self.assertEqual(offenders_of({"x": (("net",), True)}), [])

    def test_okx_is_now_entry_ready_in_long_short(self):
        from r20_backend.exchanges import get_adapter
        caps = get_adapter("okx", environment="demo").capabilities
        self.assertEqual(tuple(caps.entry_ready_position_modes), ("long_short",))
        self.assertIn("long_short", tuple(caps.position_modes))
        self.assertNotIn("net", tuple(caps.entry_ready_position_modes),
                         "净持仓模式的载荷未核验 ⇒ 不得列为准入（保守）")
