"""多所环境档与候选域探测（`r20_backend/exchanges/env_profiles.py`）残余分支收口测试 —— 第 347 刀。

本模块 208 行，是多所平权环境档（live|demo|testnet|sandbox）与多域网络探测核心：
- 场所可用环境枚举（`environments_of`）：查询场所支持的完整环境档；
- 候选域网络探测（`_probe_candidate`）：真实 urlopen 探测成功延迟、非 200 状态码丢弃与超时/网络异常容错；
- 持久化配置容错（`_load_persisted`）：坏 JSON 异常捕获并回退空字典；
- 原子持久化异常处理（`_persist`）：写盘异常捕获、临时文件清理（包括删除抛错安全忽略）并向外重抛。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from r20_backend.exchanges.env_profiles import (
    _load_persisted,
    _persist,
    _probe_candidate,
    environments_of,
)


class VenueEnvProfilesTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_env_profiles_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.test_profile_file = self.tmp_path / "pinned_venue_endpoints.json"

        # 严防测试向生产 data/pinned_venue_endpoints.json 写入
        p = patch("r20_backend.exchanges.env_profiles.PROFILE_FILE", self.test_profile_file)
        p.start()
        self.addCleanup(p.stop)

    # -------------------------------------------------------------------------
    # 1. 环境列表查询 (environments_of)
    # -------------------------------------------------------------------------
    def test_environments_of_returns_sorted_list(self):
        # 覆盖 line 107
        envs_gate = environments_of("gate")
        self.assertIn("live", envs_gate)
        self.assertIn("sandbox", envs_gate)
        self.assertEqual(envs_gate, sorted(envs_gate))

        # 未知场所返回空列表
        self.assertEqual(environments_of("unknown_venue"), [])

    # -------------------------------------------------------------------------
    # 2. 候选域网络探测 (_probe_candidate)
    # -------------------------------------------------------------------------
    @patch("r20_backend.exchanges.env_profiles.urlopen")
    def test_probe_candidate_success_200(self, mock_urlopen):
        # 状态码 200 返回毫秒延迟整数 (lines 115-125)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = mock_resp

        latency = _probe_candidate("https://api.gateio.ws")
        self.assertIsInstance(latency, int)
        self.assertGreaterEqual(latency, 1)

    @patch("r20_backend.exchanges.env_profiles.urlopen")
    def test_probe_candidate_non_200_returns_none(self, mock_urlopen):
        # 状态码非 200 返回 None (lines 122-123)
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = mock_resp

        self.assertIsNone(_probe_candidate("https://api.gateio.ws"))

    @patch("r20_backend.exchanges.env_profiles.urlopen")
    def test_probe_candidate_exception_returns_none(self, mock_urlopen):
        # 网络异常/超时返回 None (line 127)
        mock_urlopen.side_effect = TimeoutError("DNS probe timeout")
        self.assertIsNone(_probe_candidate("https://unreachable.domain.test"))

    # -------------------------------------------------------------------------
    # 3. 持久化配置加载与保存容错 (_load_persisted & _persist)
    # -------------------------------------------------------------------------
    def test_load_persisted_corrupt_file_returns_empty_dict(self):
        # 损坏文件时捕获异常并返回空字典 (line 136)
        self.test_profile_file.write_text("{ corrupt json", encoding="utf-8")
        self.assertEqual(_load_persisted(), {})

    def test_persist_disk_error_cleans_tmp_and_raises(self):
        # 1) 写盘失败时正常删除临时文件并重抛异常 (lines 154-159)
        with patch("os.replace", side_effect=OSError("disk read-only")):
            with self.assertRaises(OSError):
                _persist("gate", "sandbox", "https://api.gate.com", {"https://api.gate.com": 50})
            tmp_file = Path(str(self.test_profile_file) + ".tmp")
            self.assertFalse(tmp_file.exists())

        # 2) 临时文件删除本身也抛 OSError 时静默 pass 并重抛最外层异常 (line 158)
        with patch("os.replace", side_effect=OSError("disk read-only")):
            with patch.object(Path, "unlink", side_effect=OSError("unlink denied")):
                with self.assertRaises(OSError):
                    _persist("gate", "sandbox", "https://api.gate.com", {})


if __name__ == "__main__":
    unittest.main()
