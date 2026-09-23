"""多所路由分配策略（`r20_backend/exchanges/routing_policy.py`）残余分支收口测试 —— 第 345 刀。

本模块 308 行，是多所平权资产池、路由模式与首选场所持久化配置核心：
- 资产白名单规范化（`_normalize_assets`）：空值防御与非字符串丢弃；
- 交易所资产池加载（`load_venue_pool` / `load_binance_pool` / `gate_pool_assets`）：坏 JSON 容错与非数值风控覆盖项容错；
- Gate 实盘执行就绪判定（`_gate_execution_ready`）：秘钥读取异常防御；
- 原始配置读取与原子落盘容错（`_read_raw_routing` / `save_routing_mode` / `save_preferred_venue`）：坏 JSON 自愈、磁盘写入异常捕获与临时文件清理。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.exchanges.routing_policy import (
    _gate_execution_ready,
    _normalize_assets,
    _read_raw_routing,
    gate_pool_assets,
    load_binance_pool,
    load_venue_pool,
    save_preferred_venue,
    save_routing_mode,
)


class VenueRoutingPolicyTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_routing_policy_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.test_routing_file = self.tmp_path / "venue_routing.json"

        # 严防测试向生产 data/venue_routing.json 写入
        p = patch("r20_backend.exchanges.routing_policy.ROUTING_FILE", self.test_routing_file)
        p.start()
        self.addCleanup(p.stop)

    # -------------------------------------------------------------------------
    # 1. 资产规范化 (_normalize_assets)
    # -------------------------------------------------------------------------
    def test_normalize_assets_empty_or_none_returns_empty_list(self):
        for empty_val in (None, ""):
            with self.subTest(empty_val=empty_val):
                res = _normalize_assets(empty_val, "gate")
                self.assertEqual(res, [])

    # -------------------------------------------------------------------------
    # 2. 交易所资产池加载容错 (load_venue_pool & shortcuts)
    # -------------------------------------------------------------------------
    def test_load_venue_pool_corrupt_json_handled(self):
        # 配置文件损坏（坏 JSON）时捕获异常并安全回退默认池 (line 135)
        self.test_routing_file.write_text("{ corrupt json", encoding="utf-8")
        pool = load_venue_pool("gate")
        self.assertIn("assets", pool)
        self.assertIn("margin_per_trade_usdt", pool)
        self.assertEqual(pool["assets"], [])

    def test_load_venue_pool_non_numeric_risk_fields_handled(self):
        # 覆盖配置中数值字段为非数值类型时安全捕获并保留 (line 143)
        self.test_routing_file.write_text(
            json.dumps({"gate": {"margin_per_trade_usdt": "not-a-number"}}),
            encoding="utf-8",
        )
        pool = load_venue_pool("gate")
        self.assertEqual(pool["margin_per_trade_usdt"], "not-a-number")

    def test_load_binance_pool_shortcut(self):
        # 验证 load_binance_pool 捷径调用 load_venue_pool("binance") (line 154)
        pool = load_binance_pool()
        self.assertIn("assets", pool)
        self.assertIn("dry_run", pool)
        self.assertFalse(pool["dry_run"])

    def test_gate_pool_assets_shortcut(self):
        # 验证 gate_pool_assets 捷径调用 list(load_gate_pool()["assets"]) (line 292)
        self.test_routing_file.write_text(
            json.dumps({"gate": {"assets": ["BTC", "ETH"]}}),
            encoding="utf-8",
        )
        assets = gate_pool_assets()
        self.assertEqual(assets, ["BTC", "ETH"])

    # -------------------------------------------------------------------------
    # 3. Gate 实盘就绪校验 (_gate_execution_ready)
    # -------------------------------------------------------------------------
    def test_gate_execution_ready_secrets_exception_returns_false(self):
        # 读取加密密钥抛出异常时捕获并安全返回 False (line 172)
        with patch("r20_backend.exchanges.routing_policy.execution_open", return_value=True):
            with patch("r20_gateway.secrets.load_secrets", side_effect=RuntimeError("decrypt error")):
                self.assertFalse(_gate_execution_ready())

    # -------------------------------------------------------------------------
    # 4. 原始配置读取与保存容错 (_read_raw_routing, save_routing_mode, save_preferred_venue)
    # -------------------------------------------------------------------------
    def test_read_raw_routing_corrupt_file_returns_empty_dict(self):
        # 坏 JSON 时捕获异常返回空字典 (line 183)
        self.test_routing_file.write_text("bad_content", encoding="utf-8")
        self.assertEqual(_read_raw_routing(), {})

    def test_save_routing_mode_disk_error_cleans_tmp_and_returns_false(self):
        # 原子落盘异常时删除临时文件并返回 False (lines 249-254)
        with patch("os.replace", side_effect=OSError("disk read-only")):
            res = save_routing_mode("balanced")
            self.assertFalse(res)
            tmp_file = self.test_routing_file.with_suffix(".json.tmp")
            self.assertFalse(tmp_file.exists())

        # 当删除临时文件也抛异常时静默忽略 pass (line 253)
        with patch("os.replace", side_effect=OSError("disk read-only")):
            with patch.object(Path, "unlink", side_effect=OSError("unlink error")):
                res_pass = save_routing_mode("balanced")
                self.assertFalse(res_pass)

    def test_save_preferred_venue_disk_error_cleans_tmp_and_returns_false(self):
        # 原子落盘异常时删除临时文件并返回 False (lines 278-283)
        with patch("os.replace", side_effect=OSError("disk read-only")):
            res = save_preferred_venue("okx")
            self.assertFalse(res)
            tmp_file = self.test_routing_file.with_suffix(".json.tmp")
            self.assertFalse(tmp_file.exists())

        # 当删除临时文件也抛异常时静默忽略 pass (line 282)
        with patch("os.replace", side_effect=OSError("disk read-only")):
            with patch.object(Path, "unlink", side_effect=OSError("unlink error")):
                res_pass = save_preferred_venue("okx")
                self.assertFalse(res_pass)


if __name__ == "__main__":
    unittest.main()
