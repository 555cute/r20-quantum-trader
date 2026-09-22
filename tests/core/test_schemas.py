r"""请求模型：**同名 validator 的三份实现语义并不相同**（第二百八十八刀，开新面 schemas.py）。

先探针定位未命中的 11 行，全部落在三个 `validate_name` 上：

| 类 | 字段约束 | validator 签名 | 语义 |
|---|---|---|---|
| `PromptProfileCreateRequest` | `name: str` + `min_length=1` | `(cls, v: str) -> str` | 必填；`"\u3000"` 这类**过得了 min_length 但 strip 后为空**的值必须拒 |
| `PromptProfileUpdateRequest` | `name: str \| None = None` | `(cls, v) -> str \| None` | **PATCH 语义**：`None` 表示"不改这个字段"，必须**原样放行**（不能当空值拒）|
| `PolicyArchiveRequest` | `name: str` + `min_length=1` | `(cls, v: str) -> str` | 必填；同 Create |

★ 本刀的核心：`Optional` 版本与必填版本的**唯一差别**就是那个 `if v is not None` ——
漏掉它，PATCH 请求只要不带 `name` 就会 422，而"名字不能为空"的报错还会把运维引到错误的
方向上（他压根没提交 name）。三条路径各自单测钉死。

★ 另一个容易想当然的点：`name=""` **不会**走到 validator，而是被 `min_length=1` 拦在
更前面；要命中 `if not clean` 必须给**空白但不为空**的值（`" "` / 全角空格）。
"""
import unittest

from pydantic import ValidationError

from r20_backend import schemas as S


class PromptProfileCreateNameTests(unittest.TestCase):
    def test_a_normal_name_is_accepted(self):
        self.assertEqual(S.PromptProfileCreateRequest(name="稳定版").name, "稳定版")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(S.PromptProfileCreateRequest(name="  稳定版  ").name, "稳定版")

    def test_a_whitespace_only_name_is_rejected_by_the_validator(self):
        """`" "` 长度 1 过得了 `min_length=1`，必须由 `if not clean` 拦住。"""
        with self.assertRaises(ValidationError) as ctx:
            S.PromptProfileCreateRequest(name=" ")
        self.assertIn("方案名称不能为空", str(ctx.exception))

    def test_a_tab_only_name_is_rejected(self):
        with self.assertRaises(ValidationError):
            S.PromptProfileCreateRequest(name="\t\n ")

    def test_an_empty_name_is_rejected_by_the_length_constraint(self):
        """空串走的是 `min_length=1`，不是 validator —— 两条防线各自生效。"""
        with self.assertRaises(ValidationError) as ctx:
            S.PromptProfileCreateRequest(name="")
        self.assertNotIn("方案名称不能为空", str(ctx.exception))

    def test_the_optional_fields_have_defaults(self):
        req = S.PromptProfileCreateRequest(name="x")
        self.assertEqual(req.description, "")
        self.assertEqual(req.source_id, "stable")

    def test_the_name_length_cap(self):
        self.assertEqual(len(S.PromptProfileCreateRequest(name="x" * 60).name), 60)
        with self.assertRaises(ValidationError):
            S.PromptProfileCreateRequest(name="x" * 61)


class PromptProfileUpdateNameTests(unittest.TestCase):
    def test_an_absent_name_is_none(self):
        """★ PATCH 语义：不提交 `name` ⇒ `None` ⇒ **必须放行**（表示"不改这个字段"）。"""
        self.assertIsNone(S.PromptProfileUpdateRequest().name)

    def test_an_explicit_none_is_allowed(self):
        self.assertIsNone(S.PromptProfileUpdateRequest(name=None).name)

    def test_a_normal_name_is_stripped(self):
        self.assertEqual(S.PromptProfileUpdateRequest(name="  新版  ").name, "新版")

    def test_a_whitespace_only_name_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            S.PromptProfileUpdateRequest(name="   ")
        self.assertIn("方案名称不能为空", str(ctx.exception))

    def test_the_other_fields_default_to_none(self):
        req = S.PromptProfileUpdateRequest()
        for field in ("description", "enabled", "editor_mode", "simple_policy",
                      "pipelines", "trading_system", "evolution_user"):
            with self.subTest(field=field):
                self.assertIsNone(getattr(req, field))

    def test_the_note_defaults_to_the_admin_marker(self):
        self.assertEqual(S.PromptProfileUpdateRequest().note, "后台更新")

    def test_the_editor_mode_pattern_is_enforced(self):
        self.assertEqual(S.PromptProfileUpdateRequest(editor_mode="modules").editor_mode,
                         "modules")
        with self.assertRaises(ValidationError):
            S.PromptProfileUpdateRequest(editor_mode="whatever")

    def test_an_update_request_accepts_only_the_note(self):
        """只改备注是合法请求（其余字段全 None）。"""
        req = S.PromptProfileUpdateRequest(note="只改备注")
        self.assertEqual(req.note, "只改备注")
        self.assertIsNone(req.name)


class PolicyArchiveNameTests(unittest.TestCase):
    def test_a_normal_name_is_accepted(self):
        self.assertEqual(S.PolicyArchiveRequest(name="归档").name, "归档")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(S.PolicyArchiveRequest(name="  归档  ").name, "归档")

    def test_a_whitespace_only_name_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            S.PolicyArchiveRequest(name=" ")
        self.assertIn("快照名称不能为空", str(ctx.exception))

    def test_an_empty_name_is_rejected_by_the_length_constraint(self):
        with self.assertRaises(ValidationError) as ctx:
            S.PolicyArchiveRequest(name="")
        self.assertNotIn("快照名称不能为空", str(ctx.exception))

    def test_tags_default_to_an_empty_list(self):
        self.assertEqual(S.PolicyArchiveRequest(name="x").tags, [])

    def test_tags_are_kept(self):
        self.assertEqual(S.PolicyArchiveRequest(name="x", tags=["a", "b"]).tags, ["a", "b"])

    def test_the_name_length_cap_is_80(self):
        self.assertEqual(len(S.PolicyArchiveRequest(name="x" * 80).name), 80)
        with self.assertRaises(ValidationError):
            S.PolicyArchiveRequest(name="x" * 81)


class PolicyRestoreHashTests(unittest.TestCase):
    """`unify_hash` 的 `model_validator(mode="before")`：`hash` 与 `policy_hash` 二选一。"""

    def test_policy_hash_is_accepted(self):
        self.assertEqual(S.PolicyRestoreRequest(policy_hash="abc123").policy_hash, "abc123")

    def test_the_legacy_hash_key_is_unified(self):
        self.assertEqual(S.PolicyRestoreRequest(hash="abc123").policy_hash, "abc123")

    def test_policy_hash_wins_over_the_legacy_key(self):
        req = S.PolicyRestoreRequest(policy_hash="winner1", hash="loser1")
        self.assertEqual(req.policy_hash, "winner1")

    def test_an_absent_hash_is_rejected_despite_the_documented_default(self):
        """⚠️ 实测：字段声明写的是 `min_length=0`，但 `unify_hash` 把缺省值 `""` 拿去
        匹配 `^[a-zA-Z0-9_-]{6,64}$` ⇒ **空 body 一律 422**。声明与校验口径不一致，
        按实际行为钉住（回滚接口必须显式给 hash）。"""
        with self.assertRaises(ValidationError) as ctx:
            S.PolicyRestoreRequest()
        self.assertIn("6~64 位", str(ctx.exception))

    def test_an_explicitly_empty_hash_is_also_rejected(self):
        with self.assertRaises(ValidationError):
            S.PolicyRestoreRequest(policy_hash="")

    def test_a_too_short_hash_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            S.PolicyRestoreRequest(policy_hash="abc")
        self.assertIn("6~64 位", str(ctx.exception))

    def test_an_illegal_character_is_rejected(self):
        with self.assertRaises(ValidationError):
            S.PolicyRestoreRequest(policy_hash="abc$$$123")

    def test_a_64_char_hash_is_accepted(self):
        self.assertEqual(len(S.PolicyRestoreRequest(policy_hash="a" * 64).policy_hash), 64)


class RiskConfigUpdateTests(unittest.TestCase):
    def test_an_empty_body_is_valid(self):
        """全字段可选 ⇒ 空 PATCH 合法（路由层自己决定要不要报"没东西可改"）。"""
        self.assertIsNotNone(S.RiskConfigUpdate())

    def test_unknown_fields_are_ignored(self):
        self.assertFalse(hasattr(S.RiskConfigUpdate(nonsense=1), "nonsense"))


if __name__ == "__main__":
    unittest.main()
