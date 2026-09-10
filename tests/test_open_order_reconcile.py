# -*- coding: utf-8 -*-
"""US-006 行为级回归：重启接管存量挂单——周期级挂单对账。

封闭三律：
律① 一切交易所调用停在 HTTP 边界——patch `scripts.okx_rest.urlopen`
    （import 时绑定别名，patch 位置即生效位置），零真实网络、零真实凭证、零真实下单。
律② 凭证注入用真实 freeze_environment(假键)，测试受控环境对象。
律③ 不触碰 r20_backend/**、frontend/**、okx_runtime.py、registry、data/**、.env；
    本地意图文件路径 patch 到 tempfile，绝不写真实 data/**。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.ai_factor_trader as trader
import scripts.okx_rest as okx_rest
from scripts.okx_runtime import freeze_environment, unfreeze_environment

DEMO_ENV = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "DEMO_AK", "OKX_DEMO_SECRET_KEY": "DEMO_SK", "OKX_DEMO_PASSPHRASE": "DEMO_PP",
}


def _response(code="0", msg="", data=None):
    body = json.dumps({"code": code, "msg": msg, "data": data if data is not None else []}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _req_method_path(request):
    url = request.full_url
    return request.get_method(), url.split("okx.com", 1)[1]


class _Router:
    """按 (method, path 前缀) 分发的假 urlopen，记录全部请求。"""

    def __init__(self, handlers):
        self.handlers = handlers  # {(method, path_prefix): data | Exception}
        self.calls = []

    def __call__(self, request, *args, **kwargs):
        method, path = _req_method_path(request)
        self.calls.append((method, path))
        for (m, prefix), outcome in self.handlers.items():
            if m == method and path.startswith(prefix):
                if isinstance(outcome, Exception):
                    raise outcome
                return _response(data=outcome)
        return _response(code="50011", msg="unexpected route")


PENDING_PATH = "/api/v5/trade/orders-pending"
CANCEL_PATH = "/api/v5/trade/cancel-order"


class _EnvFreezeMixin:
    def setUp(self):
        freeze_environment(dict(DEMO_ENV))
        self.addCleanup(unfreeze_environment)
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        self.intent_file = tmp.name
        self._patcher = patch.object(trader, "OPEN_INTENT_FILE", self.intent_file)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        tmp_t = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp_t.close()
        with open(tmp_t.name, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self._tracker_patcher = patch.object(trader, "POSITION_TRACKER_FILE", tmp_t.name)
        self._tracker_patcher.start()
        self.addCleanup(self._tracker_patcher.stop)

    def _write_intents(self, intents):
        with open(self.intent_file, "w", encoding="utf-8") as f:
            json.dump(intents, f)


class ReconcileOpenOrdersTests(_EnvFreezeMixin, unittest.TestCase):
    """reconcile_pending_orders：接管 / 撤销 + reason 溯源 / fail-closed。"""

    def _run(self, handlers, trackers=None, now_ms=None):
        handlers = {("POST", CANCEL_PATH): [{"sCode": "0"}], **handlers}
        router = _Router(handlers)
        buf = io.StringIO()
        with patch.object(okx_rest, "urlopen", router), redirect_stdout(buf):
            ok, kept = trader.reconcile_pending_orders(trackers=trackers, now_ms=now_ms)
        return ok, kept, buf.getvalue(), router

    def test_1_intent_match_keeps_order_with_zero_cancel(self):
        """意图归属：同合约同方向未过期 → 接管保留，零撤单调用。"""
        self._write_intents([{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": 1000}])
        pending = [{"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}]
        ok, kept, out, router = self._run({("GET", PENDING_PATH): pending}, now_ms=2000)
        self.assertTrue(ok)
        self.assertEqual(kept, {"o1"})
        self.assertIn("接管挂单 instId=BTC-USDT-SWAP ordId=o1", out)
        self.assertTrue(all(m == "GET" for m, _ in router.calls), "意图归属不得触发撤单")

    def test_2_orphan_cancelled_with_structured_reason_log(self):
        """孤儿单：无本地意图 → 撤销 + 结构化 reason 溯源日志。"""
        pending = [{"instId": "LINK-USDT-SWAP", "ordId": "o9", "side": "sell", "state": "live"}]
        ok, kept, out, router = self._run({("GET", PENDING_PATH): pending})
        self.assertTrue(ok)
        self.assertEqual(kept, set())
        self.assertIn("[挂单对账] 撤销孤儿单 instId=LINK-USDT-SWAP ordId=o9 side=sell 原因=无对应意图", out)
        self.assertIn(("POST", CANCEL_PATH), router.calls)

    def test_3_read_failure_is_fail_closed_not_clearall(self):
        """读取失败（网络/签名）→ ok=False fail-closed 禁新下单，且不清库（零撤单）。"""
        router = _Router({("GET", PENDING_PATH): OSError("network down")})
        buf = io.StringIO()
        with patch.object(okx_rest, "urlopen", router), redirect_stdout(buf):
            ok, kept = trader.reconcile_pending_orders(trackers={})
        self.assertFalse(ok, "对账读取失败必须 fail-closed")
        self.assertEqual(kept, set())
        self.assertIn("fail-closed", buf.getvalue())
        self.assertIn("warn", buf.getvalue())
        self.assertEqual([(m, p.split("?")[0]) for m, p in router.calls], [("GET", PENDING_PATH)], "读取失败不得清库撤单")

    def test_4_empty_pending_is_normal_cycle(self):
        """空挂单：正常放行（ok=True），零撤单调用。"""
        ok, kept, out, router = self._run({("GET", PENDING_PATH): []})
        self.assertTrue(ok)
        self.assertEqual(kept, set())
        self.assertTrue(all(m == "GET" for m, _ in router.calls))

    def test_5_tracker_attribution_keeps_order(self):
        """追踪器归属：持仓追踪器同合约同方向 → 接管保留（无需意图文件）。"""
        trackers = {"ETH-USDT-SWAP_short": {"instId": "ETH-USDT-SWAP", "posSide": "short"}}
        pending = [{"instId": "ETH-USDT-SWAP", "ordId": "o7", "side": "sell", "state": "partially_filled"}]
        ok, kept, out, router = self._run({("GET", PENDING_PATH): pending}, trackers=trackers)
        self.assertTrue(ok)
        self.assertEqual(kept, {"o7"})
        self.assertIn("接管挂单 instId=ETH-USDT-SWAP ordId=o7 side=sell 原因=追踪器归属", out)
        self.assertTrue(all(m == "GET" for m, _ in router.calls))

    def test_6_stale_intent_order_cancelled_as_expired(self):
        """意图失效：意图存在但超过 TTL → 撤旧，原因=周期意图已失效。"""
        self._write_intents([{"instId": "ADA-USDT-SWAP", "side": "buy", "ts": 0}])
        pending = [{"instId": "ADA-USDT-SWAP", "ordId": "o5", "side": "buy", "state": "live"}]
        ok, kept, out, router = self._run({("GET", PENDING_PATH): pending}, now_ms=trader.OPEN_INTENT_TTL_MS + 10)
        self.assertTrue(ok)
        self.assertEqual(kept, set())
        self.assertIn("[挂单对账] 撤销孤儿单 instId=ADA-USDT-SWAP ordId=o5 side=buy 原因=周期意图已失效", out)
        self.assertIn(("POST", CANCEL_PATH), router.calls)

    def test_7_side_mismatch_cancelled_with_reason(self):
        """方向不一致：意图存在但方向相反 → 撤销，原因=方向不一致。"""
        self._write_intents([{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": 1000}])
        pending = [{"instId": "BTC-USDT-SWAP", "ordId": "o3", "side": "sell", "state": "live"}]
        ok, kept, out, router = self._run({("GET", PENDING_PATH): pending}, now_ms=2000)
        self.assertTrue(ok)
        self.assertIn("原因=方向不一致", out)
        self.assertIn(("POST", CANCEL_PATH), router.calls)

    def test_8_cancel_failure_is_fail_closed(self):
        """撤销孤儿单自身失败（网络/签名）→ ok=False fail-closed。"""
        pending = [{"instId": "LINK-USDT-SWAP", "ordId": "o2", "side": "sell", "state": "live"}]
        router = _Router({("GET", PENDING_PATH): pending, ("POST", CANCEL_PATH): OSError("sign error")})
        buf = io.StringIO()
        with patch.object(okx_rest, "urlopen", router), redirect_stdout(buf):
            ok, kept = trader.reconcile_pending_orders(trackers={})
        self.assertFalse(ok)
        self.assertIn("fail-closed", buf.getvalue())

    def test_9_record_intent_then_reconcile_roundtrip(self):
        """闭环：record_open_intent 落盘 → 重启后 reconcile 依意图接管。"""
        trader.record_open_intent("BTC-USDT-SWAP", "buy", ts_ms=5000)
        pending = [{"instId": "BTC-USDT-SWAP", "ordId": "o11", "side": "buy", "state": "live"}]
        ok, kept, out, router = self._run({("GET", PENDING_PATH): pending}, now_ms=6000)
        self.assertTrue(ok)
        self.assertEqual(kept, {"o11"})
        self.assertIn("原因=意图归属", out)
        # 落盘格式可回读
        with open(self.intent_file, "r", encoding="utf-8") as f:
            rows = json.load(f)
        self.assertEqual(rows[0]["instId"], "BTC-USDT-SWAP")

    def test_10_pending_read_hits_orders_pending_endpoint_signed(self):
        """对账读取必须走 GET /api/v5/trade/orders-pending 直签通道。"""
        ok, _, _, router = self._run({("GET", PENDING_PATH): []})
        self.assertTrue(ok)
        self.assertEqual(router.calls[0][0], "GET")
        self.assertTrue(router.calls[0][1].split("?")[0].endswith(PENDING_PATH))
        self.assertIn("instType=SWAP", router.calls[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
