"""提示词库路由：**预览要说清"这只是模板，不是运行时"**（第二百五十五刀，开新面 prompts.py）。

先打印实现（24-99）再动笔。

| 语义 | 口径 |
|---|---|
| ★★ **自曝预览性质** | 响应里带 `preview_mode="template_only_not_runtime"` —— 明确告诉前端：这里是**模板预览**，**不是**运行时真正生效的提示词（「UI 不说谎」）|
| ★ 四种模板 × 四种管线 | `base_templates` / `pipelines` / 每个档案的 `pipeline_views` 都是 `trading_system`、`trading_user`、`evolution_system`、`evolution_user` 四键；`effective_templates` 四个都经 `apply_module_layout` |
| ★ transport | `"python-direct"`（直调 Python，不再走子进程）|
| ★ 公开 URL 也要鉴权 | `/api/v1/prompt-library` 与 `/api/v1/admin/prompt-library` 是**同一个处理器**，且**都**要管理员头 |
| ★ 更新：超管 + 自定义层 | 四个字段 `.strip()` 后写进 `custom` 档案；**审计只记字数不记正文**（`custom_characters`）|
| ★ 诚实生效时点 | 返回的 `restart_note` 明说「**下一次** Python 交易主脑与自进化进程自动读取选中风格」|
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers.strategy import prompts as P
from r20_backend.schemas import PromptLibraryUpdate

REQUIRED_KEYS = sorted(["trading_system", "trading_user", "evolution_system",
                        "evolution_user"])


class PromptLibraryGetTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for name in ("refresh_settings", "require_admin_header"):
            p = mock.patch.object(P, name, mock.Mock(), create=True)
            p.start()
            self.addCleanup(p.stop)

        def _layout(template, profile, key, label, **kw):
            self.calls.append((key, label))
            return f"layout:{key}"

        self.layout = mock.patch.object(P, "apply_module_layout",
                                        side_effect=_layout, create=True)
        self.layout.start()
        self.addCleanup(self.layout.stop)

    def _run(self, **overrides):
        patches = {
            "load_library": mock.Mock(return_value={"active_style": "aggressive",
                                                    "active_profile_id": "p1"}),
            "active_profile": mock.Mock(return_value={"id": "p1"}),
            "all_profiles": mock.Mock(return_value=[{"id": "p1"}, {"id": "p2"}]),
            "pipeline_view": mock.Mock(return_value={"steps": []}),
            "rendered_snapshots": mock.Mock(return_value={"snap": 1}),
        }
        patches.update(overrides)
        for name, stub in patches.items():
            p = mock.patch.object(P, name, stub, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_preview_is_explicitly_not_the_runtime_prompt(self):
        """★★ 本刀最重要的一条：预览必须**自曝**它不是运行时。"""
        self._run()
        with mock.patch("scripts.ai_brain_trader.SYSTEM_PROMPT", "SYS", create=True), \
                mock.patch("scripts.self_improvement_engine.EVOLUTION_SYSTEM_PROMPT",
                           "EVO", create=True):
            out = P.prompt_library(x_r20_session="t")
        self.assertEqual(out["preview_mode"], "template_only_not_runtime")
        self.assertEqual(out["transport"], "python-direct")

    def test_all_four_pipelines_and_templates_are_present(self):
        self._run()
        with mock.patch("scripts.ai_brain_trader.SYSTEM_PROMPT", "SYS", create=True), \
                mock.patch("scripts.self_improvement_engine.EVOLUTION_SYSTEM_PROMPT",
                           "EVO", create=True):
            out = P.prompt_library(x_r20_session="t")
        self.assertEqual(sorted(out["base_templates"]), REQUIRED_KEYS)
        self.assertEqual(sorted(out["pipelines"]), REQUIRED_KEYS)
        self.assertEqual(sorted(out["effective_templates"]), REQUIRED_KEYS)
        for profile in out["profiles"]:
            self.assertEqual(sorted(profile["pipeline_views"]), REQUIRED_KEYS,
                             "每个档案都要有四种管线的视图")

    def test_effective_templates_are_labelled_per_pipeline(self):
        self._run()
        with mock.patch("scripts.ai_brain_trader.SYSTEM_PROMPT", "SYS", create=True), \
                mock.patch("scripts.self_improvement_engine.EVOLUTION_SYSTEM_PROMPT",
                           "EVO", create=True):
            P.prompt_library(x_r20_session="t")
        labels = dict(self.calls)
        self.assertEqual(labels["trading_system"], "交易 System")
        self.assertEqual(labels["trading_user"], "交易 User")
        self.assertEqual(labels["evolution_system"], "自进化 System")
        self.assertEqual(labels["evolution_user"], "自进化 User")

    def test_failure_is_500(self):
        self._run(load_library=mock.Mock(side_effect=RuntimeError("库坏了")))
        with self.assertRaises(HTTPException) as ctx:
            P.prompt_library(x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 500)


class PromptLibraryUpdateTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        p = mock.patch.object(P, "audit_record",
                              side_effect=lambda *a, **k: self.audits.append(a),
                              create=True)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(P, "refresh_settings", mock.Mock(), create=True)
        p.start()
        self.addCleanup(p.stop)
        self.superadmin = mock.Mock(return_value={"username": "root"})
        p = mock.patch.object(P, "require_superadmin", self.superadmin, create=True)
        p.start()
        self.addCleanup(p.stop)

    RAW = {"trading_system": "  甲  ", "trading_user": " 乙 ", "evolution_system": "丙",
           "evolution_user": " 丁  "}

    def _raw(self, key):
        return self.RAW[key]

    def _payload(self):
        return PromptLibraryUpdate(active_style="aggressive",
                                   trading_system="  甲  ", trading_user=" 乙 ",
                                   evolution_system="丙", evolution_user=" 丁  ")

    def _call(self, saver):
        with mock.patch.object(P, "load_library",
                               return_value={"active_style": "old"}, create=True), \
                mock.patch.object(P, "save_library", saver, create=True):
            return P.update_prompt_library(self._payload(), x_r20_session="t")

    def test_custom_layer_is_stripped_and_saved(self):
        saver = mock.Mock()
        out = self._call(saver)
        self.superadmin.assert_called_once_with("t")
        saved = saver.call_args.args[0]
        custom = saved["custom"]
        self.assertEqual(custom["trading_system"], "甲", "两侧空白要 strip")
        self.assertEqual(custom["trading_user"], "乙")
        self.assertEqual(custom["evolution_user"], "丁")
        self.assertEqual(saved["active_style"], "aggressive")
        self.assertIs(custom["editable"], True)
        self.assertEqual(out["saved"], True)

    def test_audit_records_size_not_content(self):
        """★ 审计只记**字数**，不记提示词正文（同拦截器"代码不入审计"的纪律）。"""
        self._call(mock.Mock())
        action, status, payload = self.audits[0]
        self.assertEqual((action, status), ("prompt.library.update", "success"))
        self.assertEqual(payload["actor"], "root")
        self.assertEqual(payload["active_style"], "aggressive")
        # ⚠️ 字数按**提交原文**算（未 strip）。逐字段长度**实测打印**得到 5/3/1/4（合计 13）——
        # 我先后手算成 4 和 14 都错了两次。**别数，打印**：（`len(" 丁  ")` 是 4，不是 5。）
        self.assertEqual(payload["custom_characters"], 13)
        self.assertEqual([len(self._raw(k)) for k in ("trading_system", "trading_user",
                                                      "evolution_system", "evolution_user")],
                         [5, 3, 1, 4], "这些长度是打印出来的，不是数出来的")
        for leaked in ("trading_system", "trading_user", "evolution_system",
                       "evolution_user"):
            self.assertNotIn(leaked, payload, "正文不入审计")

    def test_restart_note_is_honest_about_when_it_takes_effect(self):
        out = self._call(mock.Mock())
        self.assertIn("下一次", out["restart_note"], "改完不是立刻生效，文案要说清")
        self.assertEqual(out["active_style"], "aggressive")

    def test_value_error_is_400_and_others_are_500(self):
        for exc_type, expected in ((ValueError("风格名不对"), 400),
                                   (OSError("磁盘满"), 500)):
            with self.subTest(exc=type(exc_type).__name__):
                with self.assertRaises(HTTPException) as ctx:
                    self._call(mock.Mock(side_effect=exc_type))
                self.assertEqual(ctx.exception.status_code, expected)
                self.assertEqual(self.audits, [], "失败不写 success 审计")
                self.audits.clear()


class ActiveStyleValidatorTest(unittest.TestCase):
    """★ 模型层校验：`active_style` 只认 `stable` / `aggressive` / `custom`。

    （我第一版随手写了 "balanced"，被 pydantic 的 `pattern` 挡下 —— 又一次证明
    这些约束**内建在模型层**，不用等到运行期才发现。）
    """

    def test_unknown_style_is_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PromptLibraryUpdate(active_style="balanced")

    def test_the_three_known_styles_are_accepted(self):
        for style in ("stable", "aggressive", "custom"):
            with self.subTest(style=style):
                self.assertEqual(PromptLibraryUpdate(active_style=style).active_style, style)

    def test_prompt_bodies_are_length_capped(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PromptLibraryUpdate(active_style="stable", trading_system="x" * 12001)


if __name__ == "__main__":
    unittest.main()
