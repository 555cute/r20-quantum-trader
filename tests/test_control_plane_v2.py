from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import r20_backend.notifications as notifications
import r20_backend.okx_trade_service as okx
import scripts.prompt_library as prompts
from r20_gateway.events import GatewayEvent
from r20_gateway.store import GatewayStore
from scripts.okx_runtime import OKXEnvironment


class OKXV5Tests(unittest.TestCase):
    def test_demo_request_is_signed_and_uses_v5_header(self):
        env=OKXEnvironment("demo","AK","SK","PP")
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"code":"0","data":[]}'
        captured={}
        def open_(request,timeout=0):
            captured["request"]=request; return Response()
        with patch.object(okx.urllib.request,"urlopen",side_effect=open_):
            self.assertEqual(okx._request("GET","/api/v5/account/positions",{"instType":"SWAP"},env),[])
        req=captured["request"]
        headers={k.lower():v for k,v in req.header_items()}
        self.assertIn("/api/v5/account/positions?instType=SWAP",req.full_url)
        self.assertEqual(headers["x-simulated-trading"],"1")
        self.assertEqual(headers["ok-access-key"],"AK")
        self.assertTrue(headers["ok-access-sign"])

    def test_business_scode_fails_closed(self):
        env=OKXEnvironment("live","AK","SK","PP")
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"code":"0","data":[{"sCode":"51008","sMsg":"margin"}]}'
        with patch.object(okx.urllib.request,"urlopen",return_value=Response()):
            with self.assertRaises(RuntimeError): okx._request("POST","/api/v5/trade/close-position",{"instId":"BTC-USDT-SWAP"},env)


class ChannelBusinessCodeTests(unittest.TestCase):
    def test_wecom_http_200_error_is_failure(self):
        env={"R20_WECHAT_WEBHOOK":"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x"}
        with patch.object(notifications,"validate_outbound_url",return_value=env["R20_WECHAT_WEBHOOK"]), patch.object(notifications,"_post_json",return_value=(True,"HTTP 200",{"errcode":93000,"errmsg":"denied"})):
            self.assertFalse(notifications.send_channel("wechat","x",env)[0])

    def test_telegram_http_200_error_is_failure(self):
        env={"R20_TELEGRAM_BOT_TOKEN":"T","R20_TELEGRAM_CHAT_ID":"1"}
        with patch.object(notifications,"_post_json",return_value=(True,"HTTP 200",{"ok":False,"description":"denied"})):
            self.assertFalse(notifications.send_channel("telegram","x",env)[0])

    def test_qq_http_200_error_is_failure(self):
        env={"R20_QQ_APP_ID":"A","R20_QQ_CLIENT_SECRET":"S","R20_QQ_OPENID":"O"}
        responses=[(True,"HTTP 200",{"access_token":"T"}),(True,"HTTP 200",{"code":11248,"message":"denied"})]
        with patch.object(notifications,"_post_json",side_effect=responses): self.assertFalse(notifications.send_channel("qq","x",env)[0])

    def test_diagnose_never_sends(self):
        env={"R20_TELEGRAM_BOT_TOKEN":"T","R20_TELEGRAM_CHAT_ID":"1"}
        with patch.object(notifications,"_post_json") as post:
            self.assertEqual(notifications.diagnose_channel("telegram",env)["status"],"ready"); post.assert_not_called()


class PromptModuleTests(unittest.TestCase):
    def test_layout_reorders_and_overrides_editable_base(self):
        base="【A】\none\n\n【B】\ntwo"
        view=prompts.pipeline_view(base,{"pipelines":{}},"trading_system")
        self.assertEqual([x["title"] for x in view],["A","B"])
        view[0]["content"]="【A】\nchanged"
        profile={"pipelines":{"trading_system":[view[1],view[0]]}}
        compiled=prompts.apply_module_layout(base,profile,"trading_system","x")
        self.assertLess(compiled.index("【B】"),compiled.index("【A】")); self.assertIn("changed",compiled)

    def test_unknown_live_base_section_is_preserved(self):
        old={"pipelines":{"trading_user":[{"id":"a","title":"旧模块","content":"old","enabled":True,"source":"base"}]}}
        compiled=prompts.apply_module_layout("【新实时行情】\nlive",old,"trading_user","x")
        self.assertIn("live",compiled)

    def test_trading_user_layout_preserves_runtime_values_inside_locked_slots(self):
        base=("======================= 【当前决策时间戳与市场时效】 =======================\n"
              "【推演基准时间】: 2026-09-02 14:15:06\n"
              "【当前账户可用资金】: 3858.73 USDT\n\n"
              "======================= 【账户当前持仓与风险敞口全景】 =======================\n"
              "【账户持仓概况】: 当前系统总持仓 1/6\n"
              "【当前活动在途持仓明细】:\n- SOL 多仓 4张\n\n"
              "======================= 【六币种原生行情、技术指标与筹码矩阵】 =======================\n"
              "【BTC (BTC-USDT-SWAP)】| 数据质量: valid\n- 现价: 77575\n\n"
              "【推演与决策任务】:\n完整严格 JSON Schema")
        layout=[
            {"id":"t","title":"当前决策时间戳与市场时效","content":"实时插槽：北京时间、账户可用资金。","enabled":True,"locked":True,"source":"base"},
            {"id":"p","title":"账户当前持仓与风险敞口全景","content":"实时插槽：持仓。","enabled":True,"locked":True,"source":"base"},
            {"id":"m","title":"六币种原生行情、技术指标与筹码矩阵","content":"实时插槽：行情。","enabled":True,"locked":True,"source":"base"},
            {"id":"d","title":"推演与决策任务","content":"【推演与决策任务】:\n完整严格 JSON Schema","enabled":True,"locked":False,"source":"base"},
        ]
        compiled=prompts.apply_module_layout(base,{"pipelines":{"trading_user":layout}},"trading_user","x")
        self.assertIn("2026-09-02 14:15:06",compiled)
        self.assertIn("3858.73 USDT",compiled)
        self.assertIn("当前系统总持仓 1/6",compiled)
        self.assertIn("BTC (BTC-USDT-SWAP)",compiled)
        self.assertLess(compiled.index("2026-09-02 14:15:06"),compiled.index("当前系统总持仓 1/6"))
        self.assertLess(compiled.index("当前系统总持仓 1/6"),compiled.index("BTC (BTC-USDT-SWAP)"))
        self.assertLess(compiled.index("BTC (BTC-USDT-SWAP)"),compiled.index("完整严格 JSON Schema"))

    def test_safety_trading_rules_are_live_locked_not_stale_profile_text(self):
        base="【三重滤网裁决协议】\n新规则：ADX 18~22 小仓参与\n\n【开仓与价格几何】\n目标 R:R ≥ 2.2"
        layout=[
            {"id":"a","title":"三重滤网裁决协议","content":"旧规则：ADX < 20 必须 WAIT","enabled":True,"locked":False,"source":"base"},
            {"id":"b","title":"开仓与价格几何","content":"旧规则：目标 R:R ≥ 2.5","enabled":True,"locked":False,"source":"base"},
        ]
        compiled=prompts.apply_module_layout(base,{"pipelines":{"trading_system":layout}},"trading_system","x")
        self.assertIn("ADX 18~22 小仓参与",compiled)
        self.assertIn("目标 R:R ≥ 2.2",compiled)
        self.assertNotIn("ADX < 20 必须 WAIT",compiled)

    def test_stable_preset_balances_participation_without_weakening_p0(self):
        preset=prompts.PRESETS["stable"]
        self.assertIn("不得把“稳健”解释为长期空仓",preset["trading_system"])
        self.assertIn("P0 硬约束保持不变",preset["trading_system"])
        self.assertIn("普通回抽优先作为限价入场定位",preset["trading_user"])

    def test_trading_decision_contract_cannot_be_replaced_by_editor_summary(self):
        base="【推演与决策任务】:\n必须输出严格 JSON，包含 position_management 与 decisions"
        layout=[{"id":"d","title":"推演与决策任务","content":"可编辑规则模块摘要","enabled":True,"locked":False,"source":"base"}]
        compiled=prompts.apply_module_layout(base,{"pipelines":{"trading_user":layout}},"trading_user","x")
        self.assertIn("必须输出严格 JSON",compiled)
        self.assertNotIn("可编辑规则模块摘要",compiled)


class GatewayFDTests(unittest.TestCase):
    def test_connections_are_closed(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            store = GatewayStore(Path(tmp) / "gateway.db")
            with store.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_failed_transaction_rolls_back_and_closes_connection(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            store = GatewayStore(Path(tmp) / "gateway.db")
            store.set_state("value", "before")
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with store.connect() as connection:
                    connection.execute("UPDATE runtime_state SET value='after' WHERE key='value'")
                    raise RuntimeError("interrupted")
            self.assertEqual(store.get_state("value"), "before")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")


if __name__ == "__main__": unittest.main()
