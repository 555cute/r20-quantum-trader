#!/usr/bin/env python3
"""Update one existing R20 Compose service without replacing operator configuration.

Standalone, standard-library-only command for a Linux Docker host. No application
imports, trading requests, volume deletion, image pruning, or automatic polling.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

STATE_NAME = ".r20-deploy-state.json"
LOCK_NAME = ".r20-deploy.lock"
IMAGE_KEY = re.compile(r"^\s*(?:export\s+)?R20_IMAGE\s*=")
IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")


class DeploymentError(RuntimeError):
    pass


class CommandTimeout(DeploymentError):
    """The Docker daemon may still be working; never blindly retry a mutation."""


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise DeploymentError(f"Refusing to replace a symlink: {path.name}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_image(image: str) -> str:
    if not IMAGE_REF.fullmatch(image) or ".." in image.split("/"):
        raise DeploymentError("Invalid image reference; use an image tag or sha256 digest")
    return image


def pin_image(env_path: Path, image: str) -> None:
    """Change only the bootstrap image selector, never the persisted app config."""
    validate_image(image)
    if env_path.is_symlink():
        raise DeploymentError("The bootstrap .env is a symlink; image pinning needs a regular file")
    existing = env_path.read_bytes().decode("utf-8") if env_path.exists() else ""
    lines = existing.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if IMAGE_KEY.match(line)]
    if len(matches) > 1:
        raise DeploymentError("Duplicate R20_IMAGE assignments in .env; resolve them before updating")
    newline = "\r\n" if "\r\n" in existing else "\n"
    replacement = f"R20_IMAGE={image}{newline}"
    if matches:
        lines[matches[0]] = replacement
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(replacement)
    mode = stat.S_IMODE(env_path.stat().st_mode) if env_path.exists() else 0o600
    atomic_write(env_path, "".join(lines), mode)


@contextmanager
def file_lock(path: Path, timeout: float):
    import fcntl

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DeploymentError(f"Busy lock: {path.name}; no container was changed")
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def image_repository(reference: str) -> str:
    reference = reference.split("@", 1)[0]
    parent, separator, leaf = reference.rpartition("/")
    leaf = leaf.split(":", 1)[0]
    return parent + separator + leaf


def mount_signature(container: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    return {
        entry["Destination"]: (entry["Type"], entry["Source"], entry.get("RW", True))
        for entry in container.get("Mounts", [])
    }


class DockerDeployment:
    def __init__(self, root: Path, service: str, files: list[str], wait_timeout: int, stop_timeout: int):
        self.root = root.resolve()
        self.service = service
        self.wait_timeout = wait_timeout
        self.stop_timeout = stop_timeout
        self.state_path = self.root / STATE_NAME
        self.compose = ["docker", "compose", "--project-directory", str(self.root)]
        for file in files:
            self.compose.extend(["--file", str((self.root / file).resolve())])

    def run(self, args: list[str], *, image: str | None = None, timeout: int = 60, label: str) -> str:
        environment = dict(os.environ)
        if image is not None:
            environment["R20_IMAGE"] = image
            environment["R20_PULL_POLICY"] = "never"
        try:
            result = subprocess.run(
                args, cwd=self.root, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(f"{label} timed out; outcome may be unknown. Inspect status before retrying") from exc
        if result.returncode:
            # Compose errors can quote credential-bearing configuration. Do not
            # forward arbitrary stderr into terminal transcripts or CI logs.
            raise DeploymentError(f"{label} failed (exit {result.returncode}); Docker output withheld to protect configuration")
        return result.stdout

    def ensure_local_host(self) -> None:
        if sys.platform != "linux":
            raise DeploymentError("Run this command on the Linux Docker host so the trading lock shares the container's filesystem")
        # DOCKER_CONTEXT overrides DOCKER_HOST in the Docker CLI.
        context = os.environ.get("DOCKER_CONTEXT")
        endpoint = None if context else os.environ.get("DOCKER_HOST")
        if not endpoint:
            command = ["docker", "context", "inspect"] + ([context] if context else [])
            contexts = json.loads(self.run(command, label="Inspect Docker context"))
            endpoint = contexts[0].get("Endpoints", {}).get("docker", {}).get("Host", "")
        if not endpoint.startswith("unix://"):
            raise DeploymentError("A local Unix Docker socket is required; remote Docker contexts cannot safely share the trading lock")

    def config(self, image: str | None = None) -> dict[str, Any]:
        config = json.loads(self.run(self.compose + ["config", "--format", "json"], image=image, label="Render Compose configuration"))
        if self.service not in config.get("services", {}):
            raise DeploymentError(f"Compose service not found: {self.service}")
        selected = config["services"][self.service]
        if image is not None and selected.get("image") != image:
            raise DeploymentError("The service image must use ${R20_IMAGE:-...}; refusing to rewrite operator Compose files")
        return config

    def container(self) -> dict[str, Any]:
        identifiers = self.run(self.compose + ["ps", "--all", "--quiet", self.service], label="Locate Compose service").split()
        if len(identifiers) != 1:
            raise DeploymentError("Expected one existing service container; first installation and scaled services are not update operations")
        return json.loads(self.run(["docker", "inspect", identifiers[0]], label="Inspect service container"))[0]

    def inspect_image(self, reference: str) -> dict[str, Any]:
        return json.loads(self.run(["docker", "image", "inspect", validate_image(reference)], label="Inspect image"))[0]

    def image_record(self, image: dict[str, Any], reference: str) -> dict[str, Any]:
        repository = image_repository(reference)
        digests = image.get("RepoDigests") or []
        pinned = next((item for item in digests if item.split("@", 1)[0] == repository), None)
        if not pinned and "@sha256:" in reference:
            if reference not in digests:
                raise DeploymentError("Requested digest does not match the local image")
            pinned = reference
        labels = image.get("Config", {}).get("Labels") or {}
        return {"image": pinned or image["Id"], "id": image["Id"], "revision": labels.get("org.opencontainers.image.revision", "")}

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        if self.state_path.is_symlink():
            raise DeploymentError("Deployment state must not be a symlink")
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DeploymentError("Deployment state is unreadable; refusing to discard rollback history") from exc
        if not isinstance(state, dict) or state.get("version") != 1 or state.get("service") != self.service:
            raise DeploymentError("Deployment state belongs to a different service or format")
        for field in ("current", "previous", "pending"):
            record = state.get(field)
            if record is not None and (not isinstance(record, dict) or not record.get("id") or not record.get("image")):
                raise DeploymentError(f"Invalid {field} deployment record")
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        atomic_write(self.state_path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    def live_record(self, container: dict[str, Any]) -> dict[str, Any]:
        return self.image_record(self.inspect_image(container["Image"]), container["Config"]["Image"])

    def status(self) -> dict[str, Any]:
        container = self.container()
        state = self.load_state()
        return {
            "service": self.service,
            "container_id": container["Id"],
            "current": self.live_record(container),
            "health": container["State"].get("Health", {}).get("Status", container["State"]["Status"]),
            "previous": state.get("previous"),
            "pending": state.get("pending"),
            "channel": state.get("channel"),
        }

    def trading_lock_path(self, container: dict[str, Any]) -> Path:
        data = next((entry for entry in container.get("Mounts", []) if entry["Destination"] == "/app/data"), None)
        if data is None or data["Type"] != "bind" or not data.get("RW"):
            raise DeploymentError("A writable /app/data bind mount is required to share R20's trading-cycle lock")
        path = Path(data["Source"])
        if not path.is_dir():
            raise DeploymentError("The data bind source is not accessible on this Docker host")
        return path / ".ai_factor_trader.lock"

    def check_pin_file(self) -> None:
        path = self.root / ".env"
        if path.is_symlink():
            raise DeploymentError("Bootstrap .env is a symlink; refusing image pin changes")
        if path.exists() and sum(bool(IMAGE_KEY.match(line)) for line in path.read_text(encoding="utf-8").splitlines()) > 1:
            raise DeploymentError("Duplicate R20_IMAGE assignments in .env")

    def check_runtime_configuration(self, configuration: dict[str, Any], before: dict[str, Any]) -> None:
        """Reject mount/network drift before Compose can create a wrong replacement."""
        service = configuration["services"][self.service]
        expected_mounts = {}
        for entry in service.get("volumes", []):
            source = entry.get("source", "")
            if entry["type"] == "volume":
                source = configuration.get("volumes", {}).get(source, {}).get("name", source)
            expected_mounts[entry["target"]] = (entry["type"], source, not entry.get("read_only", False))
        if expected_mounts != mount_signature(before):
            raise DeploymentError("Compose mounts differ from the running service; reconcile them explicitly before an image-only update")
        if (service.get("dns") or []) != (before["HostConfig"].get("Dns") or []):
            raise DeploymentError("Compose DNS differs from the running service")
        expected_ports = sorted(
            (f"{port['target']}/{port.get('protocol', 'tcp')}", str(port.get("published", "")), port.get("host_ip") or "")
            for port in service.get("ports", [])
        )
        actual_ports = sorted(
            (target, binding["HostPort"], binding.get("HostIp") or "")
            for target, bindings in (before["HostConfig"].get("PortBindings") or {}).items()
            for binding in bindings or []
        )
        if expected_ports != actual_ports:
            raise DeploymentError("Compose published ports differ from the running service")
        if service.get("network_mode"):
            if service["network_mode"] != before["HostConfig"].get("NetworkMode"):
                raise DeploymentError("Compose network mode differs from the running service")
        else:
            expected_networks = {
                configuration["networks"][name]["name"] for name in service.get("networks", {"default": None})
            }
            if expected_networks != set(before.get("NetworkSettings", {}).get("Networks", {})):
                raise DeploymentError("Compose networks differ from the running service")

    def switch(self, target: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
        self.check_runtime_configuration(self.config(target["image"]), before)
        self.run(
            self.compose + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--timeout", str(self.stop_timeout),
                            "--wait", "--wait-timeout", str(self.wait_timeout), self.service],
            image=target["image"], timeout=self.stop_timeout + self.wait_timeout + 60, label="Compose rollout",
        )
        after = self.container()
        if after["Image"] != target["id"]:
            raise DeploymentError("The running image does not match the resolved candidate")
        if after["State"].get("Health", {}).get("Status") != "healthy":
            raise DeploymentError("The candidate did not pass an image/container health check")
        if mount_signature(after) != mount_signature(before):
            raise DeploymentError("Service data mounts changed during rollout")
        for key in ("Dns", "PortBindings", "NetworkMode"):
            if after["HostConfig"].get(key) != before["HostConfig"].get(key):
                raise DeploymentError(f"Service {key} changed during rollout")
        return after

    def apply(self, *, rollback: bool, image: str | None, no_pull: bool, dry_run: bool, lock_timeout: float) -> dict[str, Any]:
        self.check_pin_file()
        configuration = self.config()
        state = self.load_state()
        before = self.container()
        old = self.live_record(before)
        lock_path = self.trading_lock_path(before)
        rollback_target = None
        if rollback:
            rollback_target = state.get("current") if state.get("pending") else state.get("previous")
            if not rollback_target:
                raise DeploymentError("No recorded image is available for rollback")
            if not state.get("pending") and state.get("current", {}).get("id") != old["id"]:
                raise DeploymentError("The running image changed outside this updater; refusing an ambiguous rollback")
            channel = state.get("channel") or configuration["services"][self.service]["image"]
            requested = rollback_target["image"]
        else:
            if state.get("pending"):
                raise DeploymentError("An interrupted deployment is recorded; inspect status and run rollback first")
            requested = image or os.environ.get("R20_IMAGE") or state.get("channel") or configuration["services"][self.service].get("image", "")
            channel = requested
        validate_image(requested)
        self.check_runtime_configuration(self.config(requested), before)
        if dry_run:
            return {"dry_run": True, "operation": "rollback" if rollback else "update", "current": old,
                    "requested": requested, "will_pull": not rollback and not no_pull, "trading_lock": str(lock_path),
                    "preserves": ["Compose files", "data/logs/backups", "DNS", "ports", "network", "application credentials/settings"]}

        with file_lock(self.root / LOCK_NAME, 0):
            if self.container()["Id"] != before["Id"] or self.load_state() != state:
                raise DeploymentError("Deployment changed during preflight; inspect status and retry")
            if not rollback and not no_pull:
                print("Resolving the candidate image before acquiring the trading lock...", flush=True)
                self.run(["docker", "pull", requested], timeout=600, label="Pull candidate image")
            resolved = self.inspect_image(requested)
            target = self.image_record(resolved, requested)
            if rollback_target is not None and target["id"] != rollback_target["id"]:
                raise DeploymentError("Rollback image identity no longer matches its record")
            self.check_runtime_configuration(self.config(target["image"]), before)
            print("Waiting for the R20 trading cycle to finish...", flush=True)
            with file_lock(lock_path, lock_timeout):
                if self.container()["Id"] != before["Id"]:
                    raise DeploymentError("The service container changed while waiting for its trading lock")
                transaction = {"version": 1, "service": self.service, "channel": channel,
                               "current": state["current"] if state.get("pending") else old,
                               "previous": state.get("previous"), "pending": target}
                self.save_state(transaction)
                try:
                    already_healthy = target["id"] == old["id"] and before["State"].get("Health", {}).get("Status") == "healthy"
                    after = before if already_healthy else self.switch(target, before)
                    pin_image(self.root / ".env", target["image"])
                    previous = old if target["id"] != old["id"] and not state.get("pending") else state.get("previous")
                    completed = {**transaction, "current": target, "previous": previous,
                                 "pending": None, "updated_at": datetime.now(timezone.utc).isoformat()}
                    self.save_state(completed)
                except CommandTimeout:
                    # A timed-out Docker CLI is not proof the daemon stopped.
                    # Leave durable recovery information; do not race a second up.
                    raise
                except Exception as original_error:
                    if state.get("pending"):
                        raise DeploymentError("Recovery rollback failed; original recovery state is retained. Inspect status before retrying") from original_error
                    print("Rollout failed; restoring the previous image without touching data...", flush=True)
                    try:
                        self.switch(old, before)
                        pin_image(self.root / ".env", old["image"])
                        restored = {**transaction, "current": old, "pending": None,
                                    "updated_at": datetime.now(timezone.utc).isoformat()}
                        self.save_state(restored)
                    except Exception as rollback_error:
                        raise DeploymentError("Rollout and rollback verification failed; recovery state is retained. Inspect status before taking further action") from rollback_error
                    raise DeploymentError(f"Rollout failed; previous image restored and healthy. {original_error}") from original_error
                return {"operation": "rollback" if rollback else "update", "health": "healthy", "container_id": after["Id"],
                        "current": target, "previous": completed.get("previous"), "image_pin": str(self.root / ".env"),
                        "configuration_preserved": True}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", nargs="?", choices=("update", "rollback", "status"), default="update")
    result.add_argument("--project-dir", type=Path, default=Path.cwd(), help="Existing Compose project directory (default: current directory)")
    result.add_argument("--service", default="r20-quantum-trader")
    result.add_argument("--file", action="append", default=[], help="Compose file, relative to the project; repeat to include operator overrides")
    result.add_argument("--image", help="Update channel/tag/digest; omitted uses the previously recorded channel")
    result.add_argument("--no-pull", action="store_true", help="Use an already available local image; useful for offline recovery")
    result.add_argument("--dry-run", action="store_true", help="Read-only plan: no pull, locks, files, or container changes")
    result.add_argument("--lock-timeout", type=float, default=300, help="Maximum wait for an active trading cycle, in seconds")
    result.add_argument("--wait-timeout", type=int, default=90, help="Maximum wait for container health")
    result.add_argument("--stop-timeout", type=int, default=30, help="Grace period for the old container")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.operation != "update" and args.image:
        raise DeploymentError("--image is only valid for update")
    if args.lock_timeout < 0 or not 1 <= args.wait_timeout <= 3600 or not 1 <= args.stop_timeout <= 3600:
        raise DeploymentError("Invalid lock/health/shutdown timeout")
    deployment = DockerDeployment(args.project_dir, args.service, args.file, args.wait_timeout, args.stop_timeout)
    deployment.ensure_local_host()
    result = deployment.status() if args.operation == "status" else deployment.apply(
        rollback=args.operation == "rollback", image=args.image, no_pull=args.no_pull, dry_run=args.dry_run, lock_timeout=args.lock_timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeploymentError, OSError, ValueError) as error:
        print(f"Update refused/failed: {error}", file=sys.stderr)
        raise SystemExit(1)
