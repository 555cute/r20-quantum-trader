"""策略快照路由：**整包标识**与「回滚已发生 ⇒ 审计必须落库」（第二百五十四刀）。

先打印整个文件（130 行）再动笔。两条审计记录都值得单独守卫：

| 事故 | 内容 |
|---|---|
| ★ **审计 P0-3** | 四单元 `policy_hash` **看不到风控/路由** ⇒ 只差风控设置的两个归档会同 hash ⇒「● 当前正在运行」会**同时点亮两个版本**（UI 谎报）。修复：另给一个**整包标识** `package_hash`，前端据此精确判定 |
| ★★ **审计 P0-4（2026-09-13）** | 还原端点原来读 `payload` 上一个**不存在的** hash 属性（pydantic 不产生该属性）⇒ `AttributeError` 发生在**6 个存储全部改完之后** ⇒ 被兜成 HTTP 500「恢复策略版本失败」：**管理员看到失败、系统实际已回滚、`policy.restore` 审计永不落库**。修复：读**真实字段** `policy_hash`，并按同族路由补 `actor` |

P0-4 是本项目「**成交已发生，副作用失败不该改变结果**」的活标本 —— 本刀用两条断言把它钉住：
① 传给还原函数的是**请求里的真字段**；② 成功路径**必须**写 `policy.restore` 审计。
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers.strategy import policy as P
from r20_backend.schemas import PolicyArchiveRequest, PolicyRestoreRequest


class PolicySnapshotTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        p = mock.patch.object(P, "audit_record",
                              side_effect=lambda *a, **k: self.audits.append(a))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(P, "require_admin_header", mock.Mock(), create=True)
        p.start()
        self.addCleanup(p.stop)
        self.superadmin = mock.Mock(return_value={"username": "root"})
        p = mock.patch.object(P, "require_superadmin", self.superadmin, create=True)
        p.start()
        self.addCleanup(p.stop)

    def _call(self, fn, *a, **k):
        try:
            return fn(*a, **k), None
        except HTTPException as exc:
            return None, exc

    # ── 当前快照（审计 P0-3）──────────────────────────────
    def test_current_snapshot_carries_a_package_identity(self):
        """★ P0-3：只有 `policy_hash` 不足以判定"哪一份在运行"，必须另有整包标识。"""
        with mock.patch("r20_backend.policy_snapshot.generate_policy_snapshot",
                        return_value={"policy_version": "v9", "policy_hash": "abc"}), \
                mock.patch("r20_backend.policy_snapshot.capture_full_strategy_package",
                           return_value={"package": {"risk": 1}}), \
                mock.patch("r20_backend.policy_snapshot.package_identity",
                           return_value="PKG-HASH") as ident:
            out, _exc = self._call(P.admin_get_policy_current_snapshot, x_r20_session="t")
        self.assertEqual(out["package_hash"], "PKG-HASH")
        self.assertEqual(out["policy_hash"], "abc")
        ident.assert_called_once_with({"risk": 1})

    def test_package_identity_failure_degrades_to_empty_not_to_a_lie(self):
        """整包标识算不出来 ⇒ 给**空串**（前端能看出"不可判定"），而不是编一个值。"""
        with mock.patch("r20_backend.policy_snapshot.generate_policy_snapshot",
                        return_value={"policy_hash": "abc"}), \
                mock.patch("r20_backend.policy_snapshot.capture_full_strategy_package",
                           side_effect=RuntimeError("读不到")):
            out, _exc = self._call(P.admin_get_policy_current_snapshot, x_r20_session="t")
        self.assertEqual(out["package_hash"], "")
        self.assertEqual(out["ok"], True)

    def test_snapshot_failure_is_500_with_a_readable_detail(self):
        with mock.patch("r20_backend.policy_snapshot.generate_policy_snapshot",
                        side_effect=RuntimeError("磁盘坏了")):
            _out, exc = self._call(P.admin_get_policy_current_snapshot, x_r20_session="t")
        self.assertEqual(exc.status_code, 500)
        self.assertIn("磁盘坏了", exc.detail)

    def test_archives_failure_is_500(self):
        with mock.patch("r20_backend.policy_snapshot.load_archive_index",
                        side_effect=RuntimeError("索引损坏")):
            _out, exc = self._call(P.admin_get_policy_archives, x_r20_session="t")
        self.assertEqual(exc.status_code, 500)

    # ── 还原（审计 P0-4 的回归守卫）───────────────────────
    def test_restore_uses_the_real_schema_field(self):
        """★ P0-4①：必须把**请求里的真字段**传下去（当年读的是不存在的属性）。"""
        with mock.patch("r20_backend.policy_snapshot.restore_archived_policy",
                        return_value={"target_policy_hash": "target"}) as restorer:
            out, _exc = self._call(P.admin_restore_policy,
                                   PolicyRestoreRequest(policy_hash="abc123"), x_r20_session="t")
        restorer.assert_called_once_with(policy_hash="abc123")
        self.assertEqual(out["target_policy_hash"], "target")
        self.assertEqual(out["ok"], True, "响应 = {ok: True, **manager 的结果}")

    def test_restore_audit_is_written_with_the_actor(self):
        """★★ P0-4②：回滚一旦发生，**审计必须落库** —— 当年它永不落库。"""
        with mock.patch("r20_backend.policy_snapshot.restore_archived_policy",
                        return_value={"target_policy_hash": "target"}):
            self._call(P.admin_restore_policy, PolicyRestoreRequest(policy_hash="abc123"),
                       x_r20_session="t")
        self.assertEqual(len(self.audits), 1, "成功还原必须留下 policy.restore")
        args = self.audits[0]
        self.assertEqual(args[0], "policy.restore")
        self.assertEqual(args[1], "success")
        self.assertEqual(args[2]["actor"], "root")
        self.assertEqual(args[2]["policy_hash"], "abc123")

    def test_restore_error_mapping(self):
        cases = [(FileNotFoundError("没这个版本"), 404),
                 (ValueError("哈希不对"), 400),
                 (RuntimeError("运行时拒绝"), 400),
                 (KeyError("别的东西"), 500)]
        for exc_type, expected in cases:
            with self.subTest(exc=type(exc_type).__name__):
                with mock.patch("r20_backend.policy_snapshot.restore_archived_policy",
                                side_effect=exc_type):
                    _out, http = self._call(P.admin_restore_policy,
                                            PolicyRestoreRequest(policy_hash="abc123"),
                                            x_r20_session="t")
                self.assertEqual(http.status_code, expected)
                self.assertEqual(self.audits, [], "失败不写 success 审计")
                self.audits.clear()

    # ── 归档 ─────────────────────────────────────────────
    def test_archive_requires_superadmin_and_audits_both_hashes(self):
        entry = {"policy_hash": "h1", "package_hash": "p1"}
        with mock.patch("r20_backend.policy_snapshot.archive_current_policy",
                        return_value=entry) as archiver:
            out, _exc = self._call(P.admin_archive_policy,
                                   PolicyArchiveRequest(name="发版前", description="说明"),
                                   x_r20_session="t")
        self.superadmin.assert_called_once_with("t")
        self.assertEqual(archiver.call_args.kwargs["name"], "发版前")
        self.assertEqual(archiver.call_args.kwargs["author"], "root")
        self.assertEqual(out["entry"], entry)
        self.assertEqual(self.audits[0][2]["policy_hash"], "h1")
        self.assertEqual(self.audits[0][2]["package_hash"], "p1", "P0-3 同源：审计也记整包标识")

    def test_archive_value_error_is_400_other_is_500(self):
        for exc_type, expected in ((ValueError("名字重复"), 400),
                                   (OSError("磁盘满"), 500)):
            with self.subTest(exc=type(exc_type).__name__):
                with mock.patch("r20_backend.policy_snapshot.archive_current_policy",
                                side_effect=exc_type):
                    _out, http = self._call(P.admin_archive_policy,
                                            PolicyArchiveRequest(name="n", description="d"),
                                            x_r20_session="t")
                self.assertEqual(http.status_code, expected)

    # ── 删除 ─────────────────────────────────────────────
    def test_delete_rejects_an_illegal_hash_before_touching_the_manager(self):
        for bad in ("", "../etc/passwd", "a b", "a/b"):
            with self.subTest(hash=bad):
                with mock.patch("r20_backend.policy_snapshot.delete_archived_policy") as deleter:
                    _out, exc = self._call(P.admin_delete_policy_archive, bad,
                                           x_r20_session="t")
                self.assertEqual(exc.status_code, 400)
                deleter.assert_not_called()
                self.assertEqual(self.audits, [])

    def test_delete_success_maps_errors_and_audits(self):
        with mock.patch("r20_backend.policy_snapshot.delete_archived_policy",
                        return_value={"deleted": True}):
            out, _exc = self._call(P.admin_delete_policy_archive, "abc-123", x_r20_session="t")
        self.assertEqual(out, {"ok": True, "deleted": True})
        self.assertEqual(self.audits[0][0], "policy.delete")

        for exc_type, expected in ((FileNotFoundError("没有"), 404),
                                   (ValueError("不允许"), 400),
                                   (OSError("io"), 500)):
            with self.subTest(exc=type(exc_type).__name__):
                self.audits.clear()
                with mock.patch("r20_backend.policy_snapshot.delete_archived_policy",
                                side_effect=exc_type):
                    _out, http = self._call(P.admin_delete_policy_archive, "abc",
                                            x_r20_session="t")
                self.assertEqual(http.status_code, expected)


class PolicyHashValidatorTest(unittest.TestCase):
    """★ 模型层校验：`policy_hash` 必须是 **6~64 位**的字母/数字/下划线/短横线。

    （我第一版直接在测试里用了 `"abc"`，被 pydantic 挡下 —— 这也说明约束**真的内建在模型层**，
    不用等到存储层才发现。）
    """

    def test_too_short_is_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PolicyRestoreRequest(policy_hash="abc")

    def test_too_long_is_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PolicyRestoreRequest(policy_hash="a" * 65)

    def test_illegal_characters_are_rejected(self):
        from pydantic import ValidationError
        for bad in ("abc/def", "abc def", "abc.def"):
            with self.subTest(hash=bad):
                with self.assertRaises(ValidationError):
                    PolicyRestoreRequest(policy_hash=bad)

    def test_a_six_char_hash_is_accepted(self):
        self.assertEqual(PolicyRestoreRequest(policy_hash="abc123").policy_hash, "abc123")


if __name__ == "__main__":
    unittest.main()

class ArchivesSuccessTest(unittest.TestCase):
    """★ `policy.py` 收口那一行：归档列表成功路径必须**原样包装**返回。

    （只有失败分支被测过 ⇒ 探针把 55 标了出来。）
    """

    def test_archives_are_wrapped_without_interpretation(self):
        entries = [{"policy_hash": "h1"}, {"policy_hash": "h2"}]
        with mock.patch.object(P, "require_admin_header", mock.Mock(), create=True), \
                mock.patch("r20_backend.policy_snapshot.load_archive_index",
                           return_value=entries):
            out = P.admin_get_policy_archives(x_r20_session="t")
        self.assertEqual(out, {"ok": True, "archives": entries},
                         "列表原样返回（路由不做二次解释）")
