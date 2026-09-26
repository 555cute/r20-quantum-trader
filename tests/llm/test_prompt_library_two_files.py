"""提示词方案库的**双文件**模型回归（2026-09）。

## 这个文件在防什么（实测事故）

改造前方案库只有**一个**文件 `data/prompt_library.json`，而它是**被 git 跟踪**的
（`.gitignore` 的 `data/*.json` 明明忽略它，文件是 2026-09-02 用 `git add -f`
强加进来的）。于是：

    用户在后台改一次提示词 → M data/prompt_library.json → 工作区变脏
    → 后台「更新」撞上 `routers/system.py` 的
      「工作区存在未提交修改 ⇒ 409 拒绝更新」
    → 用户为了更新只能 `git checkout --` 丢弃改动（丢弃的正是他的提示词）
    → `git pull` 再把仓库版本盖回来

表现出来就是「更新后预设提示词覆盖了用户的预设提示词」。

修法是把「出厂」与「用户」分成两个文件：

- `BASELINE_FILE` = `data/prompt_library.json`：出厂基线，**跟踪**，随发版更新，
  运行期**只读**；
- `LOCAL_FILE` = `data/prompt_library.local.json`：用户改动，**不跟踪**，
  运行期**唯一的写入目标**。

本文件钉住这套语义。**最关键的两条**是 `test_baseline_bytes_never_change_*`
（基线被写 = 工作区又会被弄脏 = 事故复发）与
`test_pristine_preset_is_not_persisted_locally`（纯净判定错了 = 要么用户编辑丢失，
要么预设永远收不到发版改进）。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import prompt_library as pl

REAL_BASELINE = Path(__file__).resolve().parents[2] / "data" / "prompt_library.json"

_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（见 `tests/__init__.py` 的守卫）。

    本文件把**线上基线**的副本当夹具，用来断言双文件语义在真实形状下成立
    （尤其是"基线优先于代码兜底"这条 —— 实测两者内容并不相同）。
    只读、不改；声明在此是为了把"依赖线上配置内容"从**静默**变成**可审计**。
    """
    global _READ_SCOPE
    from tests import allow_real_data_reads
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None


class TwoFileLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-prompt-twofile-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.baseline = self.root / "prompt_library.json"
        self.local = self.root / "prompt_library.local.json"
        # 基线用**线上那份的副本**（形状/内容真实）；本地一开始不存在 ⇒ 最接近"刚部署"。
        shutil.copy(REAL_BASELINE, self.baseline)
        self._p1 = patch.object(pl, "BASELINE_FILE", self.baseline)
        self._p2 = patch.object(pl, "LOCAL_FILE", self.local)
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)
        self.baseline_bytes = self.baseline.read_bytes()

    def _local_json(self):
        return json.loads(self.local.read_text(encoding="utf-8"))

    # ---- 读取：本地优先，本地不在就等于基线 ---------------------------------

    def test_missing_local_file_means_load_equals_baseline(self):
        """本地文件不在（用户没改过任何东西）⇒ 逐位等于基线，且**不创建**本地文件。"""
        lib = pl.load_library()
        self.assertEqual(sorted(lib["profiles"]), sorted(
            json.loads(self.baseline_bytes.decode("utf-8"))["profiles"]))
        self.assertFalse(self.local.exists(), "只读一次不应产生写入（无副作用）")

    def test_local_profile_wins_over_baseline(self):
        self.local.write_text(json.dumps({
            "version": 2, "active_profile_id": "stable",
            "profiles": {"stable": pl._clean_profile({"name": "本地版"}, "stable")},
            "revisions": [],
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(pl.load_library()["profiles"]["stable"]["name"], "本地版")

    def test_local_active_profile_wins(self):
        self.local.write_text(json.dumps({
            "version": 2, "active_profile_id": "wide_oscillation", "profiles": {}, "revisions": [],
        }), encoding="utf-8")
        self.assertEqual(pl.load_library()["active_profile_id"], "wide_oscillation")

    def test_baseline_missing_falls_back_to_code_presets(self):
        """基线缺失（新克隆且用户没改过）⇒ 退化为代码预设，不抛异常。"""
        self.baseline.unlink()
        lib = pl.load_library()
        self.assertEqual(lib["active_profile_id"], "stable")
        self.assertEqual([p["id"] for p in pl.all_profiles()][:2],
                         ["stable", "wide_oscillation"])

    # ---- 写入：只写本地、只写差异 -------------------------------------------

    def test_baseline_bytes_never_change_on_save(self):
        """⚠️ 核心不变量：**基线永远不被写**。

        基线一旦被写，git 工作区就又脏了 ⇒ 后台「更新」又会被 409 拦住 ⇒
        「更新后预设覆盖用户预设」的事故原样复发。
        """
        pl.update_profile("stable", {"name": "我的自定义方案"}, note="测试")
        pl.create_profile("我的新方案", source_id="stable")
        self.assertEqual(self.baseline.read_bytes(), self.baseline_bytes,
                         "基线被改写了 —— 工作区又会被弄脏，事故会复发")

    def test_edited_preset_is_persisted_locally(self):
        pl.update_profile("stable", {"name": "我的自定义方案"}, note="测试")
        self.assertIn("stable", self._local_json()["profiles"])
        self.assertEqual(pl.get_profile("stable")["name"], "我的自定义方案")

    def test_user_created_profile_is_persisted_locally(self):
        pl.create_profile("我的新方案", source_id="stable")
        pids = self._local_json()["profiles"]
        self.assertTrue([p for p in pids if p.startswith("custom-")], pids)

    def test_pristine_preset_is_not_persisted_locally(self):
        """与出厂**逐字相同**的预设不落本地 ⇒ 它才能继续跟随发版更新。

        判据必须忽略 `created_at`/`updated_at`（每次 `_clean_profile` 都刷新），
        否则"永远不相同"，本地会留一份副本把厂基线**永久钉死**在旧版本上。
        """
        lib = pl.load_library()
        lib["profiles"]["wide_oscillation"] = pl.get_profile("wide_oscillation")
        pl.save_library(lib)
        self.assertNotIn("wide_oscillation", self._local_json()["profiles"],
                         "纯净预设被落进本地 = 以后收不到发版改进")

    def test_untouched_baseline_profile_is_not_persisted_locally(self):
        """基线里那条 `stable` 原样存回 ⇒ 也不该落本地（它仍是"没改过"）。"""
        pl.save_library(pl.load_library())
        self.assertNotIn("stable", self._local_json()["profiles"])

    # ---- 修订历史：按 id 去重合并，不随 load 膨胀 ---------------------------

    def test_revisions_do_not_duplicate_across_repeated_saves(self):
        for i in range(3):
            pl.update_profile("stable", {"name": f"第{i}版"}, note=f"n{i}")
        first = len(pl.load_library()["revisions"])
        pl.save_library(pl.load_library())
        pl.save_library(pl.load_library())
        self.assertEqual(len(pl.load_library()["revisions"]), first,
                         "基线修订被反复前置 ⇒ 历史无限膨胀（去重要按 id）")

    def test_revision_history_survives_the_split(self):
        pl.update_profile("stable", {"name": "改一下"}, note="留下痕迹")
        self.assertTrue(pl.profile_history("stable"), "修订历史丢了")

    # ---- 指纹辅助函数的边界 -----------------------------------------------

    def test_fingerprint_ignores_timestamps_only(self):
        base = pl._clean_profile({"name": "A", "trading_system": "X"}, "p1")
        bumped = dict(base, created_at="1999-01-01 00:00:00", updated_at="1999-01-01 00:00:00")
        self.assertEqual(pl._profile_fingerprint(base), pl._profile_fingerprint(bumped))
        self.assertNotEqual(pl._profile_fingerprint(base),
                            pl._profile_fingerprint(dict(base, name="B")))

    def test_is_user_owned_semantics(self):
        shipped = pl._shipped_profiles(json.loads(self.baseline_bytes.decode("utf-8")))
        self.assertFalse(pl._is_user_owned("stable", shipped["stable"], shipped),
                         "与出厂逐字相同 ⇒ 不算用户所有")
        self.assertTrue(pl._is_user_owned("nope-1", pl._clean_profile({}, "nope-1"), shipped),
                        "基线里没有它 ⇒ 用户自建")
        self.assertTrue(pl._is_user_owned(
            "stable", pl._clean_profile(dict(shipped["stable"], name="改了"), "stable"), shipped))

    def test_shipped_profiles_prefers_baseline_over_code_presets(self):
        """基线在时**必须盖住**代码兜底：实测二者不同（evolution_system 1049 vs 92）。"""
        shipped = pl._shipped_profiles(json.loads(self.baseline_bytes.decode("utf-8")))
        self.assertGreater(len(shipped["stable"].get("evolution_system") or ""),
                           len(pl.PRESETS["stable"].get("evolution_system") or ""))


if __name__ == "__main__":
    unittest.main()
