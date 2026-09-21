"""因子/持仓追踪文件的**本地读取路径**（第二百零五刀）。

| 语义 | 纪律 |
|---|---|
| tracker 装载 | 薄壳：把路径交给 `load_json_dict_disclosed(...)` 并取第一项（**披露式读取**统一入口）|
| 因子库读取 | 文件存在则解析；**文件损坏/读不动 ⇒ `{}`（当空，不抛）**；不存在同样 `{}` |

★ 「损坏当空」是**保守**取向：面板拿不到因子就显示空，而不是整页 500；
但它同时也是**可被观察到的降级**（`load_json_dict_disclosed` 那条路会把缺失记进
`data_health.errors`，而这里是纯本地文件读取，返回空字典即"这一项没有"）。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.dashboard_payload import factors


class TrackerLoaderShellTest(unittest.TestCase):
    def test_delegates_to_the_disclosing_reader_and_takes_the_first_item(self):
        with patch.object(factors, "load_json_dict_disclosed",
                          return_value=({"BTC-USDT-SWAP_long": {}}, "missing")) as m:
            out = factors.load_position_trackers("trackers.json")
        self.assertEqual(out, {"BTC-USDT-SWAP_long": {}})
        self.assertEqual(m.call_args.args, ("trackers.json",),
                         "薄壳只负责把路径透传给统一入口")

    def test_empty_first_item_is_returned_as_is(self):
        with patch.object(factors, "load_json_dict_disclosed", return_value=({}, None)):
            self.assertEqual(factors.load_position_trackers("trackers.json"), {})


class FactorLibraryLoaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in self.dir.iterdir()] and self.dir.rmdir())

    def _write(self, text, name="lib.json"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_file_is_an_empty_dict(self):
        self.assertEqual(factors._load_local_factor_library(self.dir / "nope.json"), {},
                         "文件不存在 ⇒ 空库（不抛）")

    def test_valid_json_is_parsed(self):
        p = self._write(json.dumps({"BTC-USDT-SWAP": {"score": 8}}))
        self.assertEqual(factors._load_local_factor_library(p),
                         {"BTC-USDT-SWAP": {"score": 8}})

    def test_broken_json_is_an_empty_dict_and_does_not_raise(self):
        """★ 损坏文件 ⇒ `{}`（**当空，不抛**）：面板显示空因子，而不是整页报错。"""
        p = self._write("{ this is not json")
        self.assertEqual(factors._load_local_factor_library(p), {})

    def test_unreadable_path_is_an_empty_dict(self):
        """目录路径（打不开）同样收敛成空，不冒泡 OSError。"""
        self.assertEqual(factors._load_local_factor_library(self.dir), {})


if __name__ == "__main__":
    unittest.main()
