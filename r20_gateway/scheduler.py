"""Gateway-owned scheduler running existing jobs in isolated subprocesses."""
from __future__ import annotations
from contextlib import nullcontext
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

from r20_backend.schedule_store import load_schedule
from r20_backend.backup_store import list_jobs as list_backup_jobs
from r20_gateway.store import GatewayStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BJ_TZ = timezone(timedelta(hours=8))
MAINTENANCE_INTERVAL_SECONDS = 5
MAINTENANCE_JOB_NAME = "order_maintenance"
MAINTENANCE_ERROR_REPEAT_SECONDS = 60



@dataclass(frozen=True)
class JobSpec:
    name: str
    script: str
    interval_seconds: int | None = None
    timeout_seconds: int = 600
    schedule_key: str = ""
    default_times: tuple[str, ...] = ()
    offset_seconds: int = 0


JOBS = (
    JobSpec("trader", "ai_factor_trader.py", 15 * 60, 840),
    JobSpec("factor_library", "factor_library.py", 60, 55),
    JobSpec("news", "news_sentiment_harvester.py", 10 * 60, 300, offset_seconds=180),
    JobSpec("daily_briefing", "daily_summary_and_backup.py", None, 600, "briefing_times", ("08:00", "20:00")),
    JobSpec("self_improvement", "self_improvement_engine.py", None, 1200, "self_improvement_times", ("02:00", "08:00", "14:00", "20:00")),
)


def backup_job_specs() -> tuple[JobSpec, ...]:
    specs: list[JobSpec] = []
    for index, job in enumerate(list_backup_jobs()):
        if not job.get("enabled"):
            continue
        name = "nightly_backup" if index == 0 or job.get("id") == "nightly-default" else f"backup:{job['id']}"
        specs.append(JobSpec(name, "nightly_backup_and_clean.py", None, 1800, f"backup_job:{job['id']}", tuple(job.get("schedule_times", ["02:00"]))))
    return tuple(specs)


def current_jobs() -> tuple[JobSpec, ...]:
    return (*JOBS, *backup_job_specs())


def scheduler_snapshot(store: GatewayStore) -> dict[str, Any]:
    schedule = load_schedule()
    now = datetime.now(BJ_TZ)
    jobs = []
    for spec in current_jobs():
        raw = store.get_state(f"job.last.{spec.name}")
        try:
            last = datetime.fromisoformat(raw) if raw else None
        except ValueError:
            last = None
        value = schedule.get(spec.schedule_key) if spec.schedule_key else None
        times = tuple(str(item) for item in value) if isinstance(value, list) else ((str(value),) if isinstance(value, str) else spec.default_times)
        schedule_text = f"每 {spec.interval_seconds // 60} 分钟 (错峰 +{spec.offset_seconds // 60}m)" if (spec.interval_seconds and spec.offset_seconds) else (f"每 {spec.interval_seconds // 60} 分钟" if spec.interval_seconds else "、".join(times))
        jobs.append({
            "name": spec.name,
            "script": spec.script,
            "last_scheduled_at": last.isoformat() if last else "",
            "schedule": schedule_text,
            "timezone": "Asia/Shanghai",
            "overdue": bool(spec.interval_seconds and last and (now - last).total_seconds() > spec.interval_seconds * 2),
            "offset_seconds": spec.offset_seconds,
        })
    return {"jobs": jobs, "recent_runs": store.job_runs(30)}


class GatewayScheduler:
    def __init__(self, store: GatewayStore, max_workers: int = 3):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="r20-job")
        self.maintenance_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="r20-maint")
        # Let an already-running maintenance operation hand off to the trader
        # before its subprocess takes the cross-process, nonblocking cycle lock.
        self._trade_handoff = threading.Lock()
        self.running: dict[str, Future[None]] = {}
        self._maintenance_future: Future[dict[str, Any]] | None = None
        self._last_maintenance_at: datetime | None = None
        self._last_maintenance_error_detail = ""
        self._last_maintenance_error_at: datetime | None = None



    def _last_at(self, name: str) -> datetime | None:
        raw = self.store.get_state(f"job.last.{name}")
        try:
            return datetime.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def initialize_migration_baseline(self, now: datetime | None = None) -> None:
        now = now or datetime.now(BJ_TZ)
        for spec in current_jobs():
            if not self.store.get_state(f"job.last.{spec.name}"):
                self.store.set_state(f"job.last.{spec.name}", now.isoformat())

    def _scheduled_times(self, spec: JobSpec, schedule: dict[str, Any]) -> tuple[str, ...]:
        if spec.schedule_key.startswith("backup_job:"):
            return spec.default_times
        value = schedule.get(spec.schedule_key)
        # Also check fallback keys if list key not found
        if value is None and spec.schedule_key == "self_improvement_times":
            value = schedule.get("self_improvement_time")
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        if isinstance(value, str):
            return (value,)
        return spec.default_times

    def due(self, spec: JobSpec, now: datetime, schedule: dict[str, Any]) -> bool:
        last = self._last_at(spec.name)
        if spec.interval_seconds:
            if spec.name == "trader":
                slot = int(now.timestamp()) // spec.interval_seconds
                last_slot = int(last.timestamp()) // spec.interval_seconds if last else -1
                return slot > last_slot and int(now.timestamp()) % spec.interval_seconds < 10
            if spec.offset_seconds:
                # Staggered execution aligned to clock with offset to prevent resource collisions
                ts = int(now.timestamp())
                slot = (ts - spec.offset_seconds) // spec.interval_seconds
                last_slot = (int(last.timestamp()) - spec.offset_seconds) // spec.interval_seconds if last else -1
                sec_in_slot = (ts - spec.offset_seconds) % spec.interval_seconds
                return slot > last_slot and sec_in_slot < 30
            return not last or (now - last).total_seconds() >= spec.interval_seconds
        minute = now.strftime("%H:%M")
        if minute not in self._scheduled_times(spec, schedule):
            return False
        return not last or last.date() != now.date() or last.strftime("%H:%M") != minute

    def _execute(self, spec: JobSpec) -> None:
        with self._trade_handoff if spec.name == "trader" else nullcontext():
            run_id = self.store.begin_job(spec.name)
            try:
                command = [sys.executable, str(SCRIPTS / spec.script)]
                if spec.schedule_key.startswith("backup_job:"):
                    command.extend(["--job-id", spec.schedule_key.split(":", 1)[1]])
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=spec.timeout_seconds,
                )
                detail = (result.stderr if result.returncode else result.stdout)[-2000:]
                self.store.finish_job(run_id, result.returncode, detail)
            except subprocess.TimeoutExpired as exc:
                self.store.finish_job(run_id, 124, f"timeout after {spec.timeout_seconds}s: {exc}")
            except Exception as exc:
                self.store.finish_job(run_id, 1, f"{type(exc).__name__}: {exc}")

    def _record_maintenance_failure(self, detail: str, now: datetime) -> None:
        text = str(detail or "pending entry maintenance failed").strip()[-2000:]
        if (
            text
            and text == self._last_maintenance_error_detail
            and self._last_maintenance_error_at is not None
            and (now - self._last_maintenance_error_at).total_seconds() < MAINTENANCE_ERROR_REPEAT_SECONDS
        ):
            return
        self._last_maintenance_error_detail = text
        self._last_maintenance_error_at = now
        run_id = self.store.begin_job(MAINTENANCE_JOB_NAME)
        self.store.finish_job(run_id, 1, text)

    def _maintenance_busy(self) -> bool:
        return self._maintenance_future is not None and not self._maintenance_future.done()

    def _run_pending_entry_maintenance(self, now: datetime) -> dict[str, Any]:
        if not self._trade_handoff.acquire(blocking=False):
            return {"status": "busy", "reason": "scheduled trader running", "pending": 0, "blocked": False, "errors": []}
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            from scripts.binance_order_maintenance import run_pending_entry_maintenance
            result = run_pending_entry_maintenance()
        except Exception as exc:
            result = {
                "status": "error",
                "reason": f"{type(exc).__name__}: {exc}",
                "pending": 0,
                "blocked": True,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        finally:
            self._trade_handoff.release()
        if not isinstance(result, dict):
            result = {
                "status": "error",
                "reason": "maintenance returned a non-dict result",
                "pending": 0,
                "blocked": True,
                "errors": ["maintenance returned a non-dict result"],
            }
        for detail in result.get("terminal_events") or []:
            run_id = self.store.begin_job(MAINTENANCE_JOB_NAME)
            self.store.finish_job(run_id, 0, detail)
        if result.get("status") == "error" or result.get("errors"):
            self._record_maintenance_failure(str(result.get("reason") or result.get("errors")), now)
        return result

    def maintain_pending_entries(self, now: datetime | None = None) -> bool:
        """Submit at most one maintenance run on the 1-slot pool. Never waits."""
        now = now or datetime.now(BJ_TZ)
        if self._maintenance_busy():
            return False
        if (
            self._last_maintenance_at is not None
            and (now - self._last_maintenance_at).total_seconds() < MAINTENANCE_INTERVAL_SECONDS
        ):
            return False
        self._last_maintenance_at = now
        try:
            self._maintenance_future = self.maintenance_executor.submit(self._run_pending_entry_maintenance, now)
        except RuntimeError:
            return False
        return True

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(BJ_TZ)
        self.running = {name: future for name, future in self.running.items() if not future.done()}
        schedule = load_schedule()
        launched: list[str] = []
        for spec in current_jobs():
            if spec.name in self.running or not self.due(spec, now, schedule):
                continue
            self.store.set_state(f"job.last.{spec.name}", now.isoformat())
            self.running[spec.name] = self.executor.submit(self._execute, spec)
            launched.append(spec.name)
        if "trader" not in launched:
            self.maintain_pending_entries(now)
        return launched


    def status(self) -> dict[str, Any]:
        schedule = load_schedule()
        result = []
        now = datetime.now(BJ_TZ)
        for spec in current_jobs():
            last = self._last_at(spec.name)
            schedule_text = f"每 {spec.interval_seconds // 60} 分钟 (错峰 +{spec.offset_seconds // 60}m)" if (spec.interval_seconds and spec.offset_seconds) else (f"每 {spec.interval_seconds // 60} 分钟" if spec.interval_seconds else "、".join(self._scheduled_times(spec, schedule)))
            result.append({
                "name": spec.name,
                "script": spec.script,
                "running": spec.name in self.running and not self.running[spec.name].done(),
                "last_scheduled_at": last.isoformat() if last else "",
                "schedule": schedule_text,
                "timezone": "Asia/Shanghai",
                "overdue": bool(spec.interval_seconds and last and (now - last).total_seconds() > spec.interval_seconds * 2),
                "offset_seconds": spec.offset_seconds,
            })
        return {"jobs": result, "recent_runs": self.store.job_runs(30)}

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)
        self.maintenance_executor.shutdown(wait=False, cancel_futures=False)

