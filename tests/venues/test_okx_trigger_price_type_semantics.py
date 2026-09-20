"""保护腿**触发价类型**的语义与能力盘点（第一百五十八刀）。

## 背景

OKX 的算法单触发价可以按三种价格类型触发：`last`（最新成交价）、`index`（指数价）、
`mark`（标记价）。三者在大波动/插针时会给出不同的触发时刻 —— 对止损腿来说，
这是"会不会被插针提前打掉"的差别。

## 本次盘点（先查后钉）

| 事实 | 证据 |
|---|---|
| **入场附着路径显式发送 `mark`**（第一百六十六刀用户拍板）| `place_order(attach_tp/attach_sl)` 写入 `tp/slTriggerPxType = attach_trigger_px_type`（默认 `mark`，可覆盖）|
| **修正路径支持并校验**该类型 | `amend_algo_sl` 透传 `new_tp/sl_trigger_px_type`；`_validate_payload` 只接受 `last/index/mark` |
| 全仓**没有任何**地方读写该字段的"当前生效值" | 面板/提示词均不显示 ⇒ 操作员无法分辨某条腿按什么价触发 |
| 仓内**未核实**交易所默认值 | 本环境 web 搜索不可用 ⇒ 不凭记忆写死（已登记 `docs/FAILURE_SEMANTICS.md` §6）|

## 本门钉什么

不钉"应该是哪一类"（那是**实盘行为变更**，已登记待人工拍板），只钉三条**可验证事实**：
1. 附着路径**不**悄悄加类型字段（payload 逐字不变）；
2. 调用方走 `attach_algo_ords`/`extra` 时**能**显式指定，且取值域被校验（非法值必须拒）；
3. 修正路径的类型参数**真的透传**到请求体（否则"支持"只是文档上的）。

这样将来无论谁决定把入场腿改成 `mark`（或显式钉 `last`），都必须**有意识地**改动本门与
payload 用例 —— 而不是某天悄悄变了触发语义。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import scripts.okx_rest as okx
from scripts.okx_runtime import freeze_environment, unfreeze_environment

DEMO_ENV = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "DEMO_AK", "OKX_DEMO_SECRET_KEY": "DEMO_SK",
    "OKX_DEMO_PASSPHRASE": "DEMO_PP",
}


def _response(code="0", data=None):
    body = json.dumps({"code": code, "msg": "", "data": data if data is not None else []}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _body(call_mock):
    """从 urlopen 调用里取出请求体 JSON（律①：交易所调用停在 HTTP 边界）。"""
    request = call_mock.call_args.args[0]
    data = request.data
    return json.loads(data.decode("utf-8")) if data else None


class TriggerPriceTypeSemanticsTest(unittest.TestCase):
    def setUp(self):
        unfreeze_environment()
        patcher = patch.object(okx, "urlopen")
        self.urlopen = patcher.start()
        self.urlopen.return_value = _response(data=[{"ordId": "1", "clOrdId": "c", "sCode": "0"}])
        freeze_environment(DEMO_ENV)
        self.addCleanup(unfreeze_environment)
        self.addCleanup(patcher.stop)

    def _body(self):
        return _body(self.urlopen)

    def test_attach_path_sends_mark_explicitly(self):
        """用户拍板：入场保护腿按**标记价**触发（不再依赖未核实的交易所默认）。"""
        okx.place_order("BTC-USDT-SWAP", "buy", "1", td_mode="cross", pos_side="long",
                        ord_type="limit", px="100", attach_tp="120", attach_sl="90")
        leg = self._body()["attachAlgoOrds"][0]
        self.assertEqual(leg["tpTriggerPxType"], "mark")
        self.assertEqual(leg["slTriggerPxType"], "mark")
        # 只给 SL 时不得凭空塞 TP 类型（类型只跟着各自的触发价走）
        okx.place_order("BTC-USDT-SWAP", "buy", "1", td_mode="cross", pos_side="long",
                        ord_type="limit", px="100", attach_sl="90")
        leg2 = self._body()["attachAlgoOrds"][0]
        self.assertEqual(set(leg2), {"slTriggerPx", "slOrdPx", "slTriggerPxType"})

    def test_type_can_be_overridden_by_caller(self):
        """可显式覆盖（例如 `last`），但必须是**有意**传参。"""
        okx.place_order("BTC-USDT-SWAP", "buy", "1", td_mode="cross", pos_side="long",
                        ord_type="limit", px="100", attach_sl="90",
                        attach_trigger_px_type="last")
        self.assertEqual(self._body()["attachAlgoOrds"][0]["slTriggerPxType"], "last")

    def test_caller_may_specify_the_type_and_domain_is_validated(self):
        leg = {"tpTriggerPx": "120", "tpOrdPx": "-1", "tpTriggerPxType": "mark",
               "slTriggerPx": "90", "slOrdPx": "-1", "slTriggerPxType": "mark"}
        okx.place_order("BTC-USDT-SWAP", "buy", "1", td_mode="cross", pos_side="long",
                        ord_type="limit", px="100", attach_algo_ords=[leg])
        self.assertEqual(self._body()["attachAlgoOrds"], [leg],
                         "逐字传参通道失效 ⇒ 调用方无法显式指定触发价类型")
        with self.assertRaises(ValueError):
            okx.place_order("BTC-USDT-SWAP", "buy", "1", td_mode="cross", pos_side="long",
                            ord_type="limit", px="100",
                            attach_algo_ords=[dict(leg, tpTriggerPxType="close")])

    def test_amend_path_plumbs_the_type_through(self):
        okx.amend_algo_sl("1", "95", inst_id="BTC-USDT-SWAP", new_sl_trigger_px_type="mark")
        body = self._body()
        self.assertEqual(body.get("newSlTriggerPx"), "95")
        self.assertEqual(body.get("newSlTriggerPxType"), "mark",
                         "修正路径没把类型透传出去 ⇒ '支持'只停留在文档上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
