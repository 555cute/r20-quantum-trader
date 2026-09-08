"""Image-only deployment safety: preserve operator state and recover unknown outcomes."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from deploy import update_docker as updater


class SimulatedDocker(updater.DockerDeployment):
    """Docker boundary simulator; the real updater owns the transaction and checks."""

    def __init__(self, root: Path):
        super().__init__(root, "r20-quantum-trader", [], 10, 5)
        self.old_id = "sha256:" + "a" * 64
        self.new_id = "sha256:" + "b" * 64
        self.images: dict[str, Any] = {key: {"Id": key, "RepoDigests": [], "Config": {"Labels": {}}}
                                       for key in (self.old_id, self.new_id)}
        self.current_container: dict[str, Any] = {
            "Id": "old-container", "Image": self.old_id, "Config": {"Image": self.old_id},
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Mounts": [{"Type": "bind", "Source": str(root / "data"), "Destination": "/app/data", "RW": True}],
            "HostConfig": {"Dns": ["8.8.8.8"], "PortBindings": {}, "NetworkMode": "fixture"},
            "NetworkSettings": {"Networks": {"fixture": {}}},
        }
        self.rendered: dict[str, Any] = {
            "services": {self.service: {
                "image": self.old_id, "dns": ["8.8.8.8"], "networks": {"default": None},
                "volumes": [{"type": "bind", "source": str(root / "data"), "target": "/app/data"}],
            }},
            "networks": {"default": {"name": "fixture"}},
        }
        self.outcome = "healthy"
        self.rollouts = 0

    def config(self, image=None):
        result = deepcopy(self.rendered)
        if image:
            result["services"][self.service]["image"] = image
        return result

    def container(self):
        return deepcopy(self.current_container)

    def inspect_image(self, reference):
        return self.images[reference]

    def run(self, args, *, image=None, timeout=60, label):
        if label != "Compose rollout":
            raise AssertionError(f"Unexpected Docker operation: {label}")
        self.rollouts += 1
        self.current_container["Image"] = image
        self.current_container["Config"]["Image"] = image
        self.current_container["Id"] = f"container-{self.rollouts}"
        if self.outcome == "timeout":
            self.outcome = "healthy"
            raise updater.CommandTimeout("Compose rollout timed out")
        if self.outcome == "unhealthy":
            self.outcome = "healthy"
            self.current_container["State"]["Health"]["Status"] = "unhealthy"
            raise updater.DeploymentError("Candidate health check failed")
        self.current_container["State"]["Health"]["Status"] = "healthy"
        return ""


class DockerDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.deploy = SimulatedDocker(self.root)
        self.env = self.root / ".env"
        self.env.write_text("OPERATOR_SETTING=retained\n", encoding="utf-8")
        lock_patch = patch.object(updater, "file_lock", side_effect=lambda *_: nullcontext())
        lock_patch.start()
        self.addCleanup(lock_patch.stop)

    def update(self):
        return self.deploy.apply(rollback=False, image=self.deploy.new_id, no_pull=True, dry_run=False, lock_timeout=0)

    def test_pin_preserves_unrelated_bytes_and_rejects_ambiguous_assignment(self):
        self.env.write_bytes(b'# operator\r\nAPI_TOKEN="not-a-real-secret"\r\nR20_IMAGE=old:tag\r\nDNS=8.8.8.8\r\n')
        updater.pin_image(self.env, self.deploy.new_id)
        self.assertEqual(self.env.read_bytes(), (
            '# operator\r\nAPI_TOKEN="not-a-real-secret"\r\n'
            f'R20_IMAGE={self.deploy.new_id}\r\nDNS=8.8.8.8\r\n'
        ).encode())
        ambiguous = b'R20_IMAGE=one:tag\nexport R20_IMAGE=two:tag\n'
        self.env.write_bytes(ambiguous)
        with self.assertRaises(updater.DeploymentError):
            updater.pin_image(self.env, self.deploy.new_id)
        self.assertEqual(self.env.read_bytes(), ambiguous)

    def test_mount_drift_is_rejected_before_any_rollout_or_pin(self):
        self.deploy.rendered["services"][self.deploy.service]["volumes"][0]["source"] = str(self.root / "wrong-data")
        with self.assertRaises(updater.DeploymentError):
            self.update()
        self.assertEqual(self.deploy.current_container["Id"], "old-container")
        self.assertEqual(self.env.read_text(), "OPERATOR_SETTING=retained\n")
        self.assertFalse(self.deploy.state_path.exists())

    def test_failed_health_restores_previous_image_and_keeps_operator_data(self):
        data = self.root / "data" / "settings"
        data.write_text("operator-data", encoding="utf-8")
        self.deploy.outcome = "unhealthy"
        with self.assertRaises(updater.DeploymentError):
            self.update()
        self.assertEqual(self.deploy.current_container["Image"], self.deploy.old_id)
        self.assertEqual(self.deploy.current_container["State"]["Health"]["Status"], "healthy")
        state = json.loads(self.deploy.state_path.read_text())
        self.assertIsNone(state["pending"])
        self.assertEqual(state["current"]["id"], self.deploy.old_id)
        self.assertEqual(data.read_text(), "operator-data")
        self.assertEqual(self.env.read_text(), f"OPERATOR_SETTING=retained\nR20_IMAGE={self.deploy.old_id}\n")

    def test_timeout_records_recovery_without_racing_then_explicit_rollback_recovers(self):
        self.deploy.outcome = "timeout"
        with self.assertRaises(updater.CommandTimeout):
            self.update()
        self.assertEqual(self.deploy.rollouts, 1)
        state = json.loads(self.deploy.state_path.read_text())
        self.assertEqual(state["pending"]["id"], self.deploy.new_id)
        self.assertEqual(state["current"]["id"], self.deploy.old_id)
        self.assertEqual(self.env.read_text(), "OPERATOR_SETTING=retained\n")
        with self.assertRaises(updater.DeploymentError):
            self.update()
        result = self.deploy.apply(rollback=True, image=None, no_pull=False, dry_run=False, lock_timeout=0)
        self.assertEqual(result["current"]["id"], self.deploy.old_id)
        self.assertEqual(self.deploy.current_container["Image"], self.deploy.old_id)
        self.assertIsNone(json.loads(self.deploy.state_path.read_text())["pending"])


if __name__ == "__main__":
    unittest.main()
