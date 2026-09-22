"""三个 trader 小模块的**残余降级分支**收口 —— 第 315 刀。

一刀清三个模块的尾巴（各自 4–7 行未命中），都是"最后一道防线/形状校验/落盘兜底"这类
**只在异常路径上跑**的代码：

| 模块 | 未命中 | 性质 |
|---|---|---|
| `scripts/trader/gates.py` | 7 → 0 | 保证金闸门的常量注入回退 |
| `scripts/trader/data_shape.py` | 4 → 0 | 意图/追踪文件的形状校验 |
| `scripts/trader/ledger_writer.py` | 4 → 0 | 台账与意图落盘的兜底 |

三个模块此前都**没有专属行为测试文件**（引用者全是审计门与抽取门）。

## 本刀实测到两处"守卫写反了位置"

`gates.py` 的常量回退与 `ledger_writer.record_trade` 的类型守卫都是**看起来在防御、
实际拦不住真正的那类输入**。两处都只记录、未改（改它们属于改实盘行为）。
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.trader import data_shape, gates, ledger_writer  # noqa: E402


def _extract_func(module, name, extra_ns):
    """把顶层函数节点 AST 抽出后 exec 进**含注入项**的命名空间。

    这是本仓既有的隔离执行配方（见 `tests/llm/test_prompt_rendering_isolated.py`）：
    `gates.py` 的常量回退读的是 `globals()["MAX_*"]`，而**本模块自己没有这两个名字**
    （实测 `KeyError`）—— 只有抽出来放进一个**有**它们的命名空间才可达。
    """
    src = Path(module.__file__).read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    mod = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = dict(extra_ns)
    ns.setdefault("datetime", __import__("datetime"))
    exec(compile(mod, module.__file__, "exec"), ns)  # noqa: S102
    return ns[name]


class OrderMarginGateTests(unittest.TestCase):
    """多所下单保证金闸门 = min(AI 计划额, 张数隐含额, 绝对封顶, 权益×占比)。"""

    def _gate(self, planned, **over):
        # ⚠️ `size_implied` **永远**在候选 caps 里 ⇒ 默认要让它大到不构成约束，
        #    否则测的就不是"绝对封顶/占比封顶"了（本刀在此自伤过一次）
        kw = {"size": 10.0, "price": 100.0, "ct_val": 10.0, "leverage": 1.0,
              "usdt_available": 10000.0, "max_single_asset_margin": 600.0,
              "max_margin_equity_ratio": 0.2}
        kw.update(over)
        return gates.order_margin_gate(planned, **kw)

    def test_planned_margin_wins_when_it_is_the_smallest(self):
        self.assertEqual(self._gate(50.0), 50.0)

    def test_absolute_cap_wins_when_planned_is_larger(self):
        self.assertEqual(self._gate(5000.0), 600.0)

    def test_equity_ratio_cap_is_applied(self):
        self.assertEqual(self._gate(5000.0, usdt_available=1000.0,
                                    max_single_asset_margin=100000.0), 200.0)

    def test_size_implied_margin_is_a_cap(self):
        # size 2 × ct_val 3 × price 100 = 600 名义 / lev 2 = 300 隐含保证金
        self.assertEqual(self._gate(5000.0, size=2.0, ct_val=3.0, price=100.0,
                                    leverage=2.0, max_single_asset_margin=100000.0,
                                    usdt_available=0.0), 300.0)

    def test_zero_planned_falls_back_to_the_size_implied_value(self):
        small = {"size": 1.0, "ct_val": 1.0, "price": 100.0, "leverage": 1.0,
                 "max_single_asset_margin": 100000.0, "usdt_available": 0.0}
        self.assertEqual(self._gate(0.0, **small), 100.0)
        self.assertEqual(self._gate(-5.0, **small), 100.0)

    def test_unavailable_equity_does_not_invent_a_ratio_cap(self):
        # 权益不可得（0）⇒ 不臆造占比上限，但仍受绝对封顶约束
        self.assertEqual(self._gate(5000.0, usdt_available=0.0), 600.0)
        self.assertEqual(self._gate(5000.0, usdt_available="junk"), 600.0)

    def test_no_positive_caps_at_all_yields_zero(self):
        self.assertEqual(self._gate(0.0, size=0.0, max_single_asset_margin=0.0,
                                    usdt_available=0.0), 0.0)

    def test_leverage_is_floored_at_one(self):
        self.assertEqual(self._gate(5000.0, leverage=0.0, size=1.0, ct_val=1.0,
                                    price=100.0, max_single_asset_margin=100000.0,
                                    usdt_available=0.0), 100.0)

    def test_no_positive_caps_when_size_implied_is_also_zero(self):
        self.assertEqual(self._gate(0.0, size=0.0, ct_val=0.0, price=0.0,
                                    max_single_asset_margin=0.0, usdt_available=0.0), 0.0)

    def test_result_is_rounded_to_four_decimals(self):
        value = self._gate(5000.0, usdt_available=1000.003,
                           max_single_asset_margin=100000.0)
        self.assertEqual(value, round(1000.003 * 0.2, 4))

    def test_constant_fallback_via_isolated_namespace(self):
        # ★ 第 55–59 行：两个 caps 都是 None ⇒ 回退读 `globals()` 的同名常量
        fn = _extract_func(gates, "order_margin_gate",
                           {"MAX_SINGLE_ASSET_MARGIN": 123.0,
                            "MAX_MARGIN_EQUITY_RATIO": 0.5})
        # size_implied 取 0 ⇒ 不构成约束，测的就是常量回退本身
        self.assertEqual(fn(9999.0, size=0.0, price=0.0, ct_val=0.0, leverage=1.0,
                            usdt_available=0.0), 123.0)
        # ratio 回退到 0.5 ⇒ 1000×0.5=500 > 123 ⇒ 仍是绝对封顶
        self.assertEqual(fn(9999.0, size=0.0, price=0.0, ct_val=0.0, leverage=1.0,
                            usdt_available=1000.0), 123.0)
        self.assertEqual(fn(10.0, size=0.0, price=0.0, ct_val=0.0, leverage=1.0,
                            usdt_available=1000.0), 10.0)

    def test_only_one_of_the_two_fallbacks_can_fire_independently(self):
        fn = _extract_func(gates, "order_margin_gate",
                           {"MAX_SINGLE_ASSET_MARGIN": 50.0,
                            "MAX_MARGIN_EQUITY_RATIO": 0.9})
        # 只给 ratio ⇒ 绝对封顶回退到 50、占比用显式 0.1 ⇒ 100×0.1=10 ⇒ 取 10
        self.assertEqual(fn(9999.0, size=0.0, price=0.0, ct_val=0.0, leverage=1.0,
                            usdt_available=100.0, max_single_asset_margin=None,
                            max_margin_equity_ratio=0.1), 10.0)
        # 只给绝对封顶 ⇒ 占比回退到 0.9 ⇒ 100×0.9=90 ⇒ 取 90
        self.assertEqual(fn(9999.0, size=0.0, price=0.0, ct_val=0.0, leverage=1.0,
                            usdt_available=100.0, max_single_asset_margin=100.0,
                            max_margin_equity_ratio=None), 90.0)

    def test_the_fallback_is_unreachable_inside_the_module_itself(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：`gates.py` 自己**没有**
        #    `MAX_SINGLE_ASSET_MARGIN` / `MAX_MARGIN_EQUITY_RATIO` 两个全局
        #    （它们是门面里的名字），所以在本模块里**不传 caps** 会直接抛
        #    `KeyError` —— 那个"为兼容 AST 抽取的隔离调用"的回退，
        #    只有真的被抽出去放进门面命名空间时才成立。
        self.assertFalse(hasattr(gates, "MAX_SINGLE_ASSET_MARGIN"))
        self.assertFalse(hasattr(gates, "MAX_MARGIN_EQUITY_RATIO"))
        with self.assertRaises(KeyError):
            gates.order_margin_gate(100.0, size=1.0, price=100.0, ct_val=1.0,
                                    leverage=1.0, usdt_available=1000.0)


class EquityMarginCapTests(unittest.TestCase):
    def test_explicit_ratio_is_applied(self):
        self.assertEqual(gates.equity_margin_cap(1000.0, max_margin_equity_ratio=0.25), 250.0)

    def test_ratio_fallback_via_isolated_namespace(self):
        # ★ 第 83 行
        fn = _extract_func(gates, "equity_margin_cap",
                           {"MAX_MARGIN_EQUITY_RATIO": 0.3})
        self.assertEqual(fn(1000.0), 300.0)

    def test_unparsable_equity_returns_zero(self):
        # ★ 第 87 行 —— 返回 0.0 表示"不臆造上限"，不是"上限为零"
        self.assertEqual(gates.equity_margin_cap("junk", max_margin_equity_ratio=0.2), 0.0)

    def test_zero_or_negative_equity_returns_zero(self):
        self.assertEqual(gates.equity_margin_cap(0.0, max_margin_equity_ratio=0.2), 0.0)
        self.assertEqual(gates.equity_margin_cap(-100.0, max_margin_equity_ratio=0.2), 0.0)

    def test_none_equity_returns_zero(self):
        self.assertEqual(gates.equity_margin_cap(None, max_margin_equity_ratio=0.2), 0.0)

    def test_result_is_rounded(self):
        self.assertEqual(gates.equity_margin_cap(999.9999, max_margin_equity_ratio=0.3333),
                         round(999.9999 * 0.3333, 4))


class TradFiWindowTests(unittest.TestCase):
    def test_crypto_and_commodity_are_always_liquid(self):
        for asset in ("crypto", "commodity"):
            with self.subTest(asset=asset):
                self.assertTrue(gates.is_tradfi_market_liquid(asset))

    def test_other_assets_follow_the_us_session_window(self):
        # 只断言类型与"同一时刻两次调用一致"（时区无关的稳定性质）
        first = gates.is_tradfi_market_liquid("stock")
        self.assertIsInstance(first, bool)
        self.assertEqual(first, gates.is_tradfi_market_liquid("stock"))


class ValidateIntentsTests(unittest.TestCase):
    """意图文件形状校验：**每条违规都要说清后果**（否则运维不知道怎么修）。"""

    def test_non_list_top_level_reports_the_reason(self):
        bad, checked = data_shape.validate_intents({"a": 1})
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("顶层应为 list", bad[0])
        self.assertIn("fail-closed", bad[0])

    def test_non_dict_item_is_skipped_via_continue(self):
        # ★ 第 42 行 —— 非 dict 条目不参与后续字段检查（但记一条违规）
        bad, checked = data_shape.validate_intents(["junk", {"instId": "BTC", "side": "long",
                                                             "ts": 1}])
        self.assertEqual(checked, 2)
        self.assertTrue(any("不是 dict" in b for b in bad))
        self.assertEqual(len(bad), 1, "合法条目不许被牵连")

    def test_missing_inst_id_is_reported(self):
        bad, _ = data_shape.validate_intents([{"side": "long", "ts": 1}])
        self.assertTrue(any("instId 缺失" in b for b in bad))

    def test_missing_side_is_reported_with_its_consequence(self):
        bad, _ = data_shape.validate_intents([{"instId": "BTC", "ts": 1}])
        self.assertTrue(any("side 缺失" in b for b in bad))
        self.assertTrue(any("孤儿" in b for b in bad))

    def test_blank_strings_are_treated_as_missing(self):
        bad, _ = data_shape.validate_intents([{"instId": "  ", "side": "", "ts": 1}])
        self.assertEqual(len(bad), 2)

    def test_non_integer_ts_is_reported(self):
        bad, _ = data_shape.validate_intents([{"instId": "BTC", "side": "long", "ts": "1"}])
        self.assertTrue(any("毫秒整数" in b for b in bad))

    def test_boolean_ts_is_rejected_even_though_bool_is_an_int(self):
        bad, _ = data_shape.validate_intents([{"instId": "BTC", "side": "long", "ts": True}])
        self.assertTrue(any("毫秒整数" in b for b in bad))

    def test_future_timestamp_is_reported(self):
        future = int(time.time() * 1000) + 10 * 60 * 1000
        bad, _ = data_shape.validate_intents([{"instId": "BTC", "side": "long", "ts": future}])
        self.assertTrue(any("在未来" in b for b in bad))

    def test_slightly_future_timestamp_within_the_tolerance_passes(self):
        soon = int(time.time() * 1000) + 30_000
        bad, _ = data_shape.validate_intents([{"instId": "BTC", "side": "long", "ts": soon}])
        self.assertEqual(bad, [])

    def test_duplicate_inst_id_and_side_is_reported_with_both_indices(self):
        rows = [{"instId": "BTC", "side": "long", "ts": 1},
                {"instId": "BTC", "side": "long", "ts": 2}]
        bad, _ = data_shape.validate_intents(rows)
        self.assertTrue(any("[0]" in b and "[1]" in b and "重复" in b for b in bad))

    def test_same_inst_with_different_sides_is_not_a_duplicate(self):
        rows = [{"instId": "BTC", "side": "long", "ts": 1},
                {"instId": "BTC", "side": "short", "ts": 2}]
        self.assertEqual(data_shape.validate_intents(rows)[0], [])

    def test_empty_list_is_clean(self):
        self.assertEqual(data_shape.validate_intents([]), ([], 0))


class ValidateTrackersTests(unittest.TestCase):
    def test_non_dict_top_level_reports_the_reason(self):
        # ★ 第 65 行
        bad, checked = data_shape.validate_trackers([1, 2])
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("顶层应为 dict", bad[0])
        self.assertIn("水位丢失", bad[0])

    def test_bad_key_shape_is_reported(self):
        bad, _ = data_shape.validate_trackers({"BTC-USDT-SWAP": {}})
        self.assertTrue(any("不符合" in b for b in bad))

    def test_non_dict_value_is_reported_and_skipped(self):
        # ★ 第 72 行 `continue`
        bad, _ = data_shape.validate_trackers({"BTC-USDT-SWAP_long": "junk"})
        self.assertTrue(any("值应为 dict" in b for b in bad))

    def test_valid_entry_is_clean(self):
        bad, checked = data_shape.validate_trackers(
            {"BTC-USDT-SWAP_long": {"scale_count": 1}})
        self.assertEqual(bad, [])
        self.assertEqual(checked, 1)

    def test_negative_or_boolean_scale_count_is_reported(self):
        for value in (-1, True, "2", 1.5):
            with self.subTest(value=value):
                bad, _ = data_shape.validate_trackers(
                    {"BTC-USDT-SWAP_long": {"scale_count": value}})
                self.assertTrue(bad, value)

    def test_absent_scale_count_is_fine(self):
        self.assertEqual(data_shape.validate_trackers({"BTC-USDT-SWAP_long": {}})[0], [])


class DataShapeFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        Path(path).write_text(text, encoding="utf-8")
        return path

    def test_validate_trackers_file_returns_the_read_error(self):
        # ★ 第 116 行 —— 读不出来 ⇒ 一条说明性违规（**不是**空列表）
        path = self._write("trackers.json", "{ broken")
        result = data_shape.validate_trackers_file(path)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], str)

    def test_missing_file_yields_an_empty_list(self):
        result = data_shape.validate_trackers_file(os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(result, [])

    def test_valid_trackers_file_reports_its_violations(self):
        path = self._write("trackers.json", json.dumps({"BADKEY": {}}))
        result = data_shape.validate_trackers_file(path)
        self.assertEqual(len(result), 1)
        self.assertIn("不符合", result[0])

    def test_valid_intents_file_via_the_file_wrapper(self):
        path = self._write("intents.json", json.dumps([]))
        self.assertEqual(data_shape.validate_intents_file(path), [])

    def test_read_json_safe_returns_the_raw_and_an_empty_error(self):
        # 无误时 `err` 是**空串**（不是 None）—— 消费方用 `if err:` 判真假
        path = self._write("ok.json", json.dumps({"a": 1}))
        raw, err = data_shape.read_json_safe(path)
        self.assertEqual(raw, {"a": 1})
        self.assertEqual(err, "")
        self.assertFalse(err)

    def test_read_json_safe_reports_an_error_tuple(self):
        path = self._write("bad.json", "{ broken")
        raw, err = data_shape.read_json_safe(path)
        self.assertIsNone(raw)
        self.assertIsInstance(err, str)


class RecordOpenIntentTests(unittest.TestCase):
    """开仓意图落盘：**原子替换**（第一百三十五刀），坏文件从空重建。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "open_intents.json")
        self.written: list = []
        self.printed: list = []

    def _write(self, text):
        Path(self.path).write_text(text, encoding="utf-8")

    def _atomic(self, path, payload):
        self.written.append((path, payload))

    def _run(self, inst_id="BTC-USDT-SWAP", side="long", ts_ms=None, **over):
        kw = {"OPEN_INTENT_FILE": self.path, "OPEN_INTENT_TTL_MS": 3_600_000,
              "_atomic_write_json": self._atomic}
        kw.update(over)
        with patch.object(ledger_writer, "print",
                          lambda *a, **k: self.printed.append(" ".join(map(str, a)))):
            return ledger_writer.record_open_intent(inst_id, side, ts_ms, **kw)

    def test_intent_is_appended(self):
        self._write(json.dumps([{"instId": "ETH-USDT-SWAP", "side": "short",
                                 "ts": int(time.time() * 1000)}]))
        self._run()
        self.assertEqual(len(self.written), 1)
        payload = self.written[0][1]
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[-1]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(payload[-1]["side"], "long")

    def test_corrupt_file_is_rebuilt_from_empty(self):
        # ★ 第 45 行 —— 空文件/损坏文件从空重建，**不影响本单交易**
        self._write("{ broken")
        self._run()
        payload = self.written[0][1]
        self.assertEqual(len(payload), 1, "坏文件不许把本条意图也丢掉")
        self.assertEqual(payload[0]["instId"], "BTC-USDT-SWAP")

    def test_missing_file_starts_from_empty(self):
        self._run()
        self.assertEqual(len(self.written[0][1]), 1)

    def test_non_list_top_level_is_discarded(self):
        self._write(json.dumps({"a": 1}))
        self._run()
        self.assertEqual(len(self.written[0][1]), 1)

    def test_same_inst_and_side_replaces_the_old_intent(self):
        now = int(time.time() * 1000)
        self._write(json.dumps([{"instId": "BTC-USDT-SWAP", "side": "long", "ts": now}]))
        self._run()
        payload = self.written[0][1]
        self.assertEqual(len(payload), 1, "同标的同向只能留一条")

    def test_opposite_side_is_kept(self):
        now = int(time.time() * 1000)
        self._write(json.dumps([{"instId": "BTC-USDT-SWAP", "side": "short", "ts": now}]))
        self._run()
        self.assertEqual(len(self.written[0][1]), 2)

    def test_expired_intents_are_pruned(self):
        old = int(time.time() * 1000) - 10 * 3_600_000
        self._write(json.dumps([{"instId": "ETH-USDT-SWAP", "side": "long", "ts": old}]))
        self._run()
        payload = self.written[0][1]
        self.assertEqual([i["instId"] for i in payload], ["BTC-USDT-SWAP"])

    def test_non_dict_rows_are_pruned(self):
        self._write(json.dumps(["junk", 42, None]))
        self._run()
        self.assertEqual(len(self.written[0][1]), 1)

    def test_history_is_capped_at_two_hundred(self):
        now = int(time.time() * 1000)
        rows = [{"instId": f"C{i}-USDT-SWAP", "side": "long", "ts": now}
                for i in range(400)]
        self._write(json.dumps(rows))
        self._run()
        self.assertEqual(len(self.written[0][1]), 200)

    def test_explicit_timestamp_is_used(self):
        self._run(ts_ms=1234567890123)
        self.assertEqual(self.written[0][1][0]["ts"], 1234567890123)

    def test_unwritable_target_prints_instead_of_raising(self):
        def boom(path, payload):
            raise OSError("disk full")
        self._run(_atomic_write_json=boom)
        self.assertTrue(any("不影响本单交易" in line for line in self.printed))
        self.assertEqual(self.written, [])


class RecordTradeTests(unittest.TestCase):
    """台账落盘：JSON 与 SQLite **各自独立**，一个坏不许拖累另一个。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = os.path.join(self.tmp.name, "ledger.json")
        self.json_writes: list = []
        self.sqlite_rows: list = []
        self.printed: list = []

    def _atomic(self, path, payload):
        self.json_writes.append((path, payload))

    def _sqlite(self, row):
        self.sqlite_rows.append(row)

    def _run(self, trade, **over):
        kw = {"LEDGER_JSON_FILE": self.ledger, "_atomic_write_json": self._atomic,
              "record_trade_sqlite": self._sqlite,
              "current_environment": lambda: type("E", (), {"mode": "live"})(),
              "__version__": "9.9.9"}
        kw.update(over)
        with patch.object(ledger_writer, "print",
                          lambda *a, **k: self.printed.append(" ".join(map(str, a)))):
            return ledger_writer.record_trade(trade, **kw)

    def test_venue_is_defaulted_without_overwriting_an_explicit_value(self):
        self._run({"instId": "BTC"})
        self.assertEqual(self.json_writes[0][1][0]["venue"], "okx")
        self.json_writes.clear()
        self._run({"instId": "BTC", "venue": "gate"})
        self.assertEqual(self.json_writes[0][1][0]["venue"], "gate")

    def test_policy_version_is_filled_from_the_snapshot_helper(self):
        import types
        fake = types.ModuleType("policy_snapshot")
        fake.generate_policy_snapshot = lambda: {"policy_version": "v7.9.2"}
        with patch.dict(sys.modules, {"policy_snapshot": fake}):
            self._run({"instId": "BTC"})
        self.assertEqual(self.json_writes[0][1][0]["policy_version"], "v7.9.2")

    def test_policy_version_falls_back_when_the_helper_is_unavailable(self):
        # ★ 第 76 行
        with patch.dict(sys.modules, {"policy_snapshot": None}):
            self._run({"instId": "BTC"})
        self.assertEqual(self.json_writes[0][1][0]["policy_version"], "v9.9.9@unknown")

    def test_existing_policy_version_is_left_alone(self):
        import types
        fake = types.ModuleType("policy_snapshot")
        fake.generate_policy_snapshot = lambda: {"policy_version": "SHOULD-NOT-WIN"}
        with patch.dict(sys.modules, {"policy_snapshot": fake}):
            self._run({"instId": "BTC", "policy_version": "v1"})
        self.assertEqual(self.json_writes[0][1][0]["policy_version"], "v1")

    def test_trade_is_appended_to_the_existing_ledger(self):
        Path(self.ledger).write_text(json.dumps([{"instId": "OLD"}]), encoding="utf-8")
        self._run({"instId": "NEW"})
        payload = self.json_writes[0][1]
        self.assertEqual([t["instId"] for t in payload], ["OLD", "NEW"])

    def test_ledger_write_failure_does_not_block_sqlite(self):
        def boom(path, payload):
            raise OSError("disk full")
        self._run({"instId": "BTC"}, _atomic_write_json=boom)
        self.assertEqual(len(self.sqlite_rows), 1, "JSON 失败不许拖累 SQLite")
        self.assertTrue(any("Failed to record trade to JSON" in line
                            for line in self.printed))

    def test_sqlite_failure_does_not_raise(self):
        def boom(row):
            raise RuntimeError("db locked")
        self._run({"instId": "BTC"}, record_trade_sqlite=boom)
        self.assertEqual(len(self.json_writes), 1, "SQLite 失败不许拖累 JSON")
        self.assertTrue(any("Failed to record trade to SQLite" in line
                            for line in self.printed))

    def test_absent_sqlite_writer_is_skipped_silently(self):
        self._run({"instId": "BTC"}, record_trade_sqlite=None)
        self.assertEqual(self.sqlite_rows, [])

    def test_environment_axis_is_stamped_from_the_runtime(self):
        self._run({"instId": "BTC"})
        self.assertEqual(self.sqlite_rows[0]["environment"], "live")

    def test_explicit_environment_is_not_overwritten(self):
        self._run({"instId": "BTC", "environment": "demo"})
        self.assertEqual(self.sqlite_rows[0]["environment"], "demo")

    def test_environment_lookup_failure_is_swallowed(self):
        # ★ 第 97 行 `pass` —— 环境不可证明时交给 db_manager 兜底 unknown_legacy
        def boom():
            raise RuntimeError("runtime down")
        self._run({"instId": "BTC"}, current_environment=boom)
        self.assertNotIn("environment", self.sqlite_rows[0])
        self.assertEqual(len(self.sqlite_rows), 1, "整条仍要入库")

    def test_sqlite_row_is_a_copy_not_the_same_object(self):
        trade = {"instId": "BTC"}
        self._run(trade)
        self.assertIsNot(self.sqlite_rows[0], trade)

    def test_non_dict_trade_raises_before_the_type_guard_can_run(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 67 行的 `trade_data.setdefault(...)`
        #    在类型守卫 `if not isinstance(trade_data, dict): return` **之前**执行 ⇒
        #    真正的非 dict（字符串/列表/None）会在这里 `AttributeError`，
        #    那个 `return` 根本轮不到。已用 `assertRaises` 钉住现状。
        for bad in ("junk", ["a"], None, 42):
            with self.subTest(bad=bad):
                with self.assertRaises(AttributeError):
                    self._run(bad)

    def test_a_duck_typed_object_with_setdefault_does_reach_the_guard(self):
        # 反面：只要有 `.setdefault` 就不是 dict 也能走到那个 `return`（第 70 行）
        class _Duck:
            def setdefault(self, key, value):
                return value

        self._run(_Duck())
        self.assertEqual(self.json_writes, [], "被守卫挡下 ⇒ 什么都不写")
        self.assertEqual(self.sqlite_rows, [])


if __name__ == "__main__":
    unittest.main()
