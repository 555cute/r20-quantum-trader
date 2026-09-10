"""Regression coverage for real temporary storage and bound path aliases."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.config_sandbox import isolate_config


class ConfigSandboxTests(unittest.TestCase):
    def test_bound_aliases_restore_and_temporary_files_are_removed(self):
        import r20_backend.llm_manager as lm
        original = lm.LLM_CONFIG_FILE
        consumer = types.ModuleType('scripts._sandbox_consumer')
        consumer.CONFIG_PATH = original
        consumer.CONFIG_STRING = str(original)
        owner = unittest.TestCase()
        with patch.dict(sys.modules, {consumer.__name__: consumer}):
            try:
                root = isolate_config(owner)
                self.assertNotEqual(lm.LLM_CONFIG_FILE, original)
                self.assertEqual(consumer.CONFIG_PATH, lm.LLM_CONFIG_FILE)
                self.assertEqual(consumer.CONFIG_STRING, str(lm.LLM_CONFIG_FILE))
                self.assertTrue(lm.LLM_CONFIG_FILE.is_relative_to(root))
                lm.LLM_CONFIG_FILE.write_text('{"sandbox": true}')
                self.assertTrue(lm.LLM_CONFIG_FILE.is_file())
            finally:
                owner.doCleanups()
            self.assertEqual(lm.LLM_CONFIG_FILE, original)
            self.assertEqual(consumer.CONFIG_PATH, original)
            self.assertFalse(root.exists())

    def test_encrypted_storage_uses_real_temporary_paths(self):
        import r20_gateway.secrets as secrets
        # The imported function alias still resolves its defining module's paths.
        from r20_gateway.secrets import save_secrets
        root = isolate_config(self)
        self.assertTrue(secrets.KEY_FILE.is_relative_to(root))
        self.assertTrue(secrets.STORE_FILE.is_relative_to(root))
        save_secrets({'LLM_API_KEY': 'fake-sandbox-only'})
        self.assertTrue(secrets.KEY_FILE.is_file())
        self.assertTrue(secrets.STORE_FILE.is_file())
        self.assertEqual(secrets.load_secrets()['LLM_API_KEY'], 'fake-sandbox-only')

    def test_nested_policy_paths_share_one_sandbox(self):
        import r20_backend.policy_snapshot as policy
        import r20_backend.council_manager as council
        import scripts.prompt_library as prompts
        root = isolate_config(self)
        for path in (policy.ARCHIVE_INDEX_FILE, council.COUNCIL_CONFIG_FILE,
                     prompts.LIBRARY_FILE):
            self.assertTrue(Path(path).is_relative_to(root))
            self.assertTrue(Path(path).parent.is_dir())
        self.assertEqual(policy.ARCHIVE_INDEX_FILE.parent, policy.ARCHIVE_DIR)
