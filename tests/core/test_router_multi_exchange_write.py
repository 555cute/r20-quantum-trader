"""多交易所写接口：**执行开关必须配精确确认短语**（第二百二十四刀）。

（教训：`require_superadmin` 返回的是**审计主体字典**而非用户名字符串 —— 我第一版按字符串打桩，
8 条用例全红在 `actor["username"]` 上。又是夹具之错，代码无辜。）

| 语义 | 口径 |
|---|---|
| ★ 三所开关各自短语 | 改 `gate/binance/okx` 执行开关必须带对应确认短语（`OPEN xx EXECUTION`），**大小写与首尾空白归一但必须精确**；不符 ⇒ **400** |
| ★ 密钥只增不减 | `secret_values` 只收「**非空且去空白后非空**」的项 ⇒ 空/空白**不会覆盖**已有密钥（同时也**无法由此接口清空**密钥）|
| ★ 门面注入 | `save_secrets`/`update_env`/`refresh_settings`/`audit_record` 经 `app_attr` 由门面解析后注入（结构优化约定，便于打桩）|
| testnet 开关 | 转成 **`"1"`/`"0"`** 字符串写环境 |
| ⚠️ **非 demo 即 live** | `okx_environment` 只判「等于 demo」，**其它任何值（含拼错）都落 `live`** —— 写错一个字母就可能把系统指向实盘 |

## ⚠️ 两处实测边界（列待议）

1. **部分写入（非原子）**：确认短语校验**在密钥落盘之后** —— 短语写错时报 400，但**密钥已经写进去了**。
   调用方以为"整次失败"，实际是"**改了一半**"。
2. **`okx_environment` 的宽松回落**：见上表最后一行（拼错 ⇒ 实盘）。
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from r20_backend.routers import exchanges as R


def _payload(**over):
    fields = {k: None for k in (
        "binance_api_key", "binance_secret_key", "binance_live_api_key",
        "binance_live_secret_key", "binance_demo_api_key", "binance_demo_secret_key",
        "gate_api_key", "gate_secret_key", "gate_live_api_key", "gate_live_secret_key",
        "gate_demo_api_key", "gate_demo_secret_key", "okx_live_api_key",
        "okx_live_secret_key", "okx_live_passphrase", "okx_demo_api_key",
        "okx_demo_secret_key", "okx_demo_passphrase", "binance_testnet", "gate_testnet",
        "gate_execution", "binance_execution", "okx_execution", "okx_environment",
        "preferred_venue", "routing_mode")}
    fields["confirmation"] = ""
    fields.update(over)
    return types.SimpleNamespace(**fields)


class WritePathTest(unittest.TestCase):
    def setUp(self):
        self.fns = {name: MagicMock(name=name) for name in
                    ("save_secrets", "update_env", "refresh_settings", "audit_record")}
        p = patch.object(R, "app_attr",
                         side_effect=lambda name, default: self.fns[name])
        self.app_attr = p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(R, "require_superadmin", return_value={"username": "张三"})
        self.superadmin = p2.start()
        self.addCleanup(p2.stop)

    def _call(self, **over):
        payload = _payload(**over)
        try:
            out = R.admin_multi_exchange_update(payload, "sess")
            return out, None
        except HTTPException as exc:
            return None, exc

    def test_execution_switch_requires_its_exact_phrase(self):
        _out, exc = self._call(okx_execution=True, confirmation="随便写的")
        self.assertIsNotNone(exc, "短语不符必须拒绝")
        self.assertEqual(exc.status_code, 400)
        self.assertIn("OPEN OKX EXECUTION", exc.detail, "要告诉调用方精确短语")
        self.assertFalse(self.fns["update_env"].called, "拒绝时**不得写环境**")

    def test_the_phrase_is_case_and_whitespace_insensitive(self):
        _out, exc = self._call(okx_execution=True, confirmation="  open okx execution ")
        self.assertIsNone(exc, "大小写与首尾空白要归一")
        self.assertEqual(self.fns["update_env"].call_args.args[0],
                         {"R20_OKX_EXECUTION": "1"})

    def test_each_venue_has_its_own_phrase(self):
        for field, phrase, env_key in (("gate_execution", "OPEN GATE EXECUTION",
                                        "R20_GATE_EXECUTION"),
                                       ("binance_execution", "OPEN BINANCE EXECUTION",
                                        "R20_BINANCE_EXECUTION")):
            with self.subTest(field=field):
                for fn in self.fns.values():
                    fn.reset_mock()
                _out, exc = self._call(**{field: False, "confirmation": phrase})
                self.assertIsNone(exc)
                self.assertEqual(self.fns["update_env"].call_args.args[0],
                                 {env_key: "0"}, "关闭也走同一条确认路径")

    def test_blank_secrets_are_ignored_and_keys_are_stripped(self):
        """★ 只写非空密钥：空/空白不覆盖已有值；有值的两侧空白要去掉。"""
        _out, exc = self._call(okx_demo_api_key="  KEY  ", gate_api_key="", binance_api_key="   ")
        self.assertIsNone(exc)
        self.assertEqual(self.fns["save_secrets"].call_args.args[0],
                         {"OKX_DEMO_API_KEY": "KEY"}, "空白项不得进入写入集合")

    def test_nothing_is_saved_when_every_secret_is_blank(self):
        self._call()
        self.assertFalse(self.fns["save_secrets"].called, "全空 ⇒ 连保存都不调用")
        self.assertTrue(self.fns["refresh_settings"].called, "设置刷新无条件执行")

    def test_testnet_switches_are_written_as_string_flags(self):
        _out, exc = self._call(binance_testnet=True, gate_testnet=False)
        self.assertIsNone(exc)
        self.assertEqual(self.fns["update_env"].call_args.args[0],
                         {"R20_BINANCE_TESTNET": "1", "R20_GATE_TESTNET": "0"})

    def test_okx_environment_falls_back_to_live_for_anything_but_demo(self):
        """⚠️ **实测边界（列待议）**：只有「等于 demo」才算演示档，**拼错也落实盘**。"""
        _out, exc = self._call(okx_environment="demoo")
        self.assertIsNone(exc)
        self.assertEqual(self.fns["update_env"].call_args.args[0], {"R20_OKX_ENV": "live"},
                         "拼写错误的档位被当成 **live**（保守默认应反过来）")
        for fn in self.fns.values():
            fn.reset_mock()
        self._call(okx_environment="DEMO")
        self.assertEqual(self.fns["update_env"].call_args.args[0], {"R20_OKX_ENV": "demo"},
                         "大小写仍可识别")

    def test_a_rejected_confirmation_still_lands_the_secrets(self):
        """⚠️ **实测边界（列待议）**：确认短语校验**在密钥落盘之后** ⇒ 报 400 但**密钥已写入**。

        调用方以为整次失败，实际是"改了一半" —— **部分写入**（非原子）。
        """
        _out, exc = self._call(okx_live_api_key="LIVE-KEY", okx_execution=True,
                               confirmation="错的短语")
        self.assertIsNotNone(exc)
        self.assertEqual(exc.status_code, 400)
        self.assertTrue(self.fns["save_secrets"].called,
                        "密钥**已经落盘**：拒绝发生在密钥写入之后（现状，列待议）")
        self.assertFalse(self.fns["update_env"].called)

    def test_superadmin_gate_runs_first(self):
        self._call(okx_environment="demo")
        self.assertTrue(self.superadmin.called, "先过超管鉴权")


    def test_audit_records_secret_key_names_but_never_secret_values(self):
        """★ 安全红线：审计留痕只记**键名**，绝不写密钥值（否则凭证进了日志/数据库）。"""
        self._call(okx_demo_api_key="  SUPER-SECRET  ", binance_testnet=True)
        event, status, detail = self.fns["audit_record"].call_args.args
        self.assertEqual(event, "multi_exchange.update")
        self.assertEqual(status, "success")
        self.assertEqual(detail["secret_keys_saved"], ["OKX_DEMO_API_KEY"],
                         "只记键名")
        self.assertEqual(detail["env_updated"], ["R20_BINANCE_TESTNET"])
        self.assertEqual(detail["actor"], "张三", "记的是审计主体")
        self.assertNotIn("SUPER-SECRET", repr(detail),
                         "**任何字段都不得出现密钥值**（安全红线）")

    def test_illegal_routing_mode_is_rejected_with_the_allowed_list(self):
        _out, exc = self._call(routing_mode="乱写的模式")
        self.assertIsNotNone(exc)
        self.assertEqual(exc.status_code, 400)
        self.assertIn("允许", exc.detail, "要把允许的值告诉调用方")

if __name__ == "__main__":
    unittest.main()
