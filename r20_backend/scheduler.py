"""Standalone process scheduler for R20 maintenance jobs.

It owns scheduling but deliberately invokes existing scripts as isolated processes,
which preserves each script's file lock and fail-closed behavior.
"""
from __future__ import annotations
import fcntl
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from r20_backend.schedule_store import load_schedule
except ModuleNotFoundError:
    from schedule_store import load_schedule

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
LOGS = ROOT / "logs"
_BJ = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


class BeijingFormatter(logging.Formatter):
    """Convert each record's creation epoch, never the formatting wall clock."""

    @staticmethod
    def converter(timestamp):
        return datetime.fromtimestamp(timestamp, _BJ).timetuple()


def configure_logging() -> None:
    # Only this scheduler's handler uses Beijing time; leave global logging alone.
    LOGS.mkdir(exist_ok=True)
    if not logger.handlers:
        handler = logging.FileHandler(LOGS / "r20_scheduler.log")
        handler.setFormatter(BeijingFormatter(
            "%(asctime)s +08:00 %(levelname)s %(message)s"
        ))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

JOBS = {
    "trader": ("ai_factor_trader.py", 15 * 60),
    "factor_library": ("factor_library.py", 60),
    "news": ("news_sentiment_harvester.py", 10 * 60),
    "daily_briefing": ("daily_summary_and_backup.py", None),
    "self_improvement": ("self_improvement_engine.py", None),
    "nightly_backup": ("nightly_backup_and_clean.py", None),
}


def run_script(name: str) -> None:
    script = SCRIPTS / JOBS[name][0]
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, text=True, capture_output=True, timeout=600)
    if result.returncode:
        logger.error("job=%s rc=%s stderr=%s", name, result.returncode, result.stderr[-1000:])
    else:
        logger.info("job=%s completed stdout=%s", name, result.stdout[-500:])


def due_daily(now: datetime, schedule_time: str, last_run: datetime | None) -> bool:
    try:
        hour, minute = [int(part) for part in schedule_time.split(":", 1)]
    except (TypeError, ValueError):
        return False
    if (now.hour, now.minute) != (hour, minute):
        return False
    return not last_run or last_run.date() != now.date() or (last_run.hour, last_run.minute) != (hour, minute)


def main() -> None:
    configure_logging()
    DATA.mkdir(exist_ok=True)
    lock_path = DATA / ".r20_scheduler.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("R20 standalone scheduler already running")

        tz = timezone(timedelta(hours=8))
        last: dict[str, datetime | None] = {key: None for key in JOBS}
        logger.info("R20 standalone scheduler v6.6.2 started")
        while True:
            now = datetime.now(tz).replace(second=0, microsecond=0)
            current = datetime.now(tz)
            if not last["trader"] or (current - last["trader"]).total_seconds() >= 15 * 60:
                run_script("trader")
                last["trader"] = datetime.now(tz)
            if not last["factor_library"] or (current - last["factor_library"]).total_seconds() >= 60:
                run_script("factor_library")
                last["factor_library"] = datetime.now(tz)
            if not last["news"] or (current - last["news"]).total_seconds() >= 10 * 60:
                run_script("news")
                last["news"] = datetime.now(tz)
            schedule = load_schedule()
            briefing_times = schedule.get("briefing_times", ["08:00", "20:00"])
            if any(due_daily(now, schedule_time, last["daily_briefing"]) for schedule_time in briefing_times):
                run_script("daily_briefing")
                last["daily_briefing"] = now
            if due_daily(now, schedule.get("self_improvement_time", "20:00"), last["self_improvement"]):
                run_script("self_improvement")
                last["self_improvement"] = now
            if due_daily(now, schedule.get("backup_time", "02:00"), last["nightly_backup"]):
                run_script("nightly_backup")
                last["nightly_backup"] = now
            time.sleep(5)


if __name__ == "__main__":
    main()
