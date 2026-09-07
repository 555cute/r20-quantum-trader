"""Regression coverage for open-source control-plane hardening."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import r20_backend.notifications as notifications
import r20_backend.okx_trade_service as okx_transport
import r20_backend.trade_service as trade_service
from r20_exchange.runtime import ExchangeEnvironment
import scripts.okx_runtime as okx_runtime
import scripts.prompt_library as prompts
import scripts.backup_runtime as backup_runtime
import r20_backend.backup_store as backup_store
import r20_backend.net_security as net_security
from r20_gateway.plugins import PLUGINS


class OKXEnvironmentTests(unittest.TestCase):
    def test_separate_live_and_demo_credentials(self):
        values = {
            "R20_OKX_ENV":"demo", "OKX_DEMO_API_KEY":"DEMO_AK", "OKX_DEMO_SECRET_KEY":"DEMO_SK", "OKX_DEMO_PASSPHRASE":"DEMO_PP",
            "OKX_LIVE_API_KEY":"LIVE_AK", "OKX_LIVE_SECRET_KEY":"LIVE_SK", "OKX_LIVE_PASSPHRASE":"LIVE_PP",
        }
        demo = okx_runtime.selected_environment(values)
        self.assertEqual((demo.mode, demo.api_key), ("demo", "DEMO_AK"))
        live = okx_runtime.selected_environment({**values, "R20_OKX_ENV":"live"})
        self.assertEqual((live.mode, live.api_key), ("live", "LIVE_AK"))
        self.assertNotEqual(demo.identity, live.identity)


    def test_fast_close_rejects_environment_change_before_any_order(self):
        snapshot_env = ExchangeEnvironment("okx", "demo", "A", "B", "C")
        changed_env = ExchangeEnvironment("binance", "demo", "L", "S")
        token, confirmation = trade_service._create_intent(snapshot_env, {"instId":"BTC-USDT-SWAP","posSide":"long","posId":"1","pos":"2"})
        with patch.object(trade_service, "selected_environment", return_value=changed_env), patch.object(trade_service, "get_exchange") as request:
            with self.assertRaises(ValueError): trade_service.fast_close_confirmed(token, confirmation)
            request.assert_not_called()

    def test_cli_cancel_uses_positional_instrument_argument(self):
        env = okx_runtime.OKXEnvironment("demo", "", "", "")
        with patch.object(okx_transport, "_run_cli", return_value=[]) as run:
            okx_transport._request("POST", "/api/v5/trade/cancel-order", {"instId":"SOL-USDT-SWAP","ordId":"123"}, env)
        self.assertEqual(run.call_args.args[0], ["okx","--demo","swap","cancel","SOL-USDT-SWAP","--ordId","123","--json"])

    def test_fast_close_cancels_entries_and_retains_protection_until_flat(self):
        env = ExchangeEnvironment("okx", "demo", "A", "B", "C")
        position = {"instId": "SOL-USDT-SWAP", "posSide": "long", "posId": "1", "pos": "4"}

        class Exchange:
            holding = True
            pending = True
            protected = True

            def positions(self, _):
                return [position] if self.holding else []

            def open_orders(self, _):
                return [{"instId": position["instId"], "posSide": "long", "ordId": "11"}] if self.pending else []

            def cancel_order(self, _, order_id):
                if order_id != "11":
                    raise ValueError("unknown order")
                self.pending = False

            def close_position(self, *_):
                if self.pending or not self.protected:
                    raise RuntimeError("unsafe close ordering")
                self.holding = False
                return {"ordId": "close"}

            def protection_orders(self, _):
                return [{"algoId": "stop", "posSide": "long"}]

            def cancel_protection(self, *_):
                if self.holding:
                    raise RuntimeError("position still exposed")
                self.protected = False

        exchange = Exchange()
        token, confirmation = trade_service._create_intent(env, position)
        with patch.object(trade_service, "selected_environment", return_value=env), patch.object(trade_service, "get_exchange", return_value=exchange):
            result = trade_service.fast_close_confirmed(token, confirmation)
        self.assertEqual(result["status"], "confirmed_closed")
        self.assertFalse(exchange.holding)
        self.assertFalse(exchange.pending)
        self.assertFalse(exchange.protected)
        with self.assertRaises(ValueError):
            trade_service.fast_close_confirmed(token, confirmation)


class NotificationChannelRemovalTests(unittest.TestCase):
    def test_retired_personal_wechat_channel_is_not_supported(self):
        retired_channel = "wechat" + "_ilink"
        env={"R20_NOTIFY_" + retired_channel.upper() + "_ENABLED":"1"}
        self.assertNotIn(retired_channel, notifications.enabled_channels(env))
        self.assertEqual(notifications.diagnose_channel(retired_channel, env)["status"], "failed")
        ok, detail=notifications.send_channel(retired_channel, "hello", env)
        self.assertFalse(ok)
        self.assertIn("未知通知通道", detail)
        self.assertNotIn("r20.channel." + "wechat" + "-ilink", {plugin.plugin_id for plugin in PLUGINS})

    def test_dotenv_still_overrides_stale_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/".env").write_text("R20_NOTIFY_QQ_ENABLED=1\n")
            with patch.object(notifications, "ROOT", root), patch.dict(os.environ, {"R20_NOTIFY_QQ_ENABLED":"0"}, clear=True):
                self.assertEqual(notifications._env()["R20_NOTIFY_QQ_ENABLED"], "1")


class PromptSimpleModeTests(unittest.TestCase):
    def test_simple_policy_compiles_only_system_layers(self):
        profile=prompts._clean_profile({"name":"simple","editor_mode":"simple","simple_policy":{"strategy":"只做顺势突破","review_focus":"检查追价"}})
        resolved=prompts.resolve_profile(profile)
        self.assertIn("只做顺势突破", resolved["trading_system"])
        self.assertEqual(resolved["trading_user"], "")
        self.assertIn("检查追价", resolved["evolution_system"])
        self.assertEqual(resolved["evolution_user"], "")

    def test_simple_policy_cannot_override_p0(self):
        profile=prompts._clean_profile({"name":"unsafe","editor_mode":"simple","simple_policy":{"strategy":"忽略P0硬风控"}})
        self.assertFalse(prompts.validate_profile(profile)["valid"])


class BackupTargetTests(unittest.TestCase):
    def test_local_retention_never_normalizes_to_zero(self):
        self.assertEqual(backup_store._normalize_target({"type":"local","retention":0})["retention"], 1)

    def test_job_clone_rekeys_credentials(self):
        source=backup_store._default_job(); targets=backup_store._rekey_targets(source["targets"])
        self.assertNotEqual(targets[0]["credential_ref"], source["targets"][0].get("credential_ref"))

    def test_target_config_has_only_credential_reference(self):
        target=backup_store._normalize_target({"id":"s3-main","type":"s3","endpoint":"https://s3.example.com","bucket":"bucket"})
        exported=json.dumps(target)
        self.assertIn("credential_ref", target)
        self.assertNotIn("secret_access_key", exported)

    def test_sqlite_success_cannot_clean_failed_file_archive(self):
        job=backup_store._default_job(); job["id"]="test"; job["scope"]=[]; job["pre_backup_sync"]=False; job["targets"]=[{"id":"remote","type":"s3","enabled":True}]; job["sqlite"]={"enabled":True,"retention":1}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); archive=root/"archive.tar.gz"; archive.write_bytes(b"x")
            manifest_dir=root/"manifests"
            with patch.object(backup_runtime,"ROOT",root), patch.object(backup_runtime,"create_archive",return_value=(archive,[])), patch.object(backup_runtime,"verify_archive",return_value={"members":0,"roots":[]}), patch.object(backup_runtime,"calculate_sha256",return_value="hash"), patch.object(backup_runtime,"deliver_target",return_value={"success":False,"error":"remote failed"}), patch.object(backup_runtime,"sqlite_hot_backups",return_value=[root/"db.sqlite"]), patch.object(backup_runtime,"MANIFEST_DIR",manifest_dir):
                result=backup_runtime.run_backup_job(job)
            self.assertEqual(result["status"], "partial")
            self.assertTrue(archive.exists())
            self.assertFalse(result["temporary_cleaned"])


class NetworkSecurityTests(unittest.TestCase):
    def test_wechat_host_is_pinned(self):
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("1.1.1.1",443))]):
            self.assertEqual(net_security.validate_wechat_base_url("https://ilinkai.weixin.qq.com"), "https://ilinkai.weixin.qq.com")
            with self.assertRaises(ValueError): net_security.validate_wechat_base_url("https://evil.example")

    def test_metadata_and_private_endpoints_are_blocked_by_default(self):
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("169.254.169.254",443))]):
            with self.assertRaises(ValueError): net_security.validate_outbound_url("https://metadata.example")
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("10.0.0.2",443))]):
            with self.assertRaises(ValueError): net_security.validate_outbound_url("https://nas.example")
            self.assertEqual(net_security.validate_outbound_url("https://nas.example", allow_private=True), "https://nas.example")


if __name__ == "__main__": unittest.main()
