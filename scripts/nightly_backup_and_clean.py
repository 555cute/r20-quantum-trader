#!/usr/bin/env python3
"""Run one or more configured R20 custom backup jobs."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path: sys.path.insert(0, str(ROOT / "scripts"))

from r20_backend.backup_store import get_job, list_jobs
from backup_runtime import clean_stale_staging, run_backup_job


def notify(result: dict) -> None:
    try:
        from qq_notifier import send_qq_message
        icon = "✅" if result.get("status") == "success" else "⚠️" if result.get("status") == "partial" else "❌"
        lines = [
            f"{icon} 【R20 自定义灾备】{result.get('job_name', '未知任务')}",
            f"状态：{result.get('status', 'unknown')}",
            f"时间：{result.get('started_at', '')} - {result.get('finished_at', '')}"
        ]
        if result.get("sha256"):
            lines.append(f"SHA256：{result['sha256'][:16]}...")
        lines.append(f"目标：{len(result.get('targets', []))}，SQLite：{len(result.get('sqlite', []))}")
        if result.get("errors"):
            lines.append("错误：" + "；".join(result["errors"])[:600])
        send_qq_message("\n".join(lines))
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", default="")
    parser.add_argument("--all-enabled", action="store_true")
    args = parser.parse_args()

    # Clean stale temporary archives before running
    try:
        clean_stale_staging()
    except Exception:
        pass

    try:
        if args.job_id:
            jobs = [get_job(args.job_id)]
        else:
            jobs = [x for x in list_jobs() if x.get("enabled")]
    except ValueError as exc:
        print(json.dumps({"status": "failed", "reason": f"灾备任务不存在: {exc}"}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": f"加载灾备任务失败: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2

    if not jobs:
        print(json.dumps({"status": "skipped", "reason": "no enabled backup jobs"}, ensure_ascii=False))
        return 0

    results = []
    for job in jobs:
        try:
            result = run_backup_job(job)
        except Exception as exc:
            result = {
                "job_id": job.get("id", "unknown"),
                "job_name": job.get("name", "unknown"),
                "status": "failed",
                "errors": [f"未捕获任务异常: {type(exc).__name__}: {exc}"],
                "targets": [],
                "sqlite": []
            }
        results.append(result)
        if (result.get("status") == "success" and job.get("notify_on_success")) or (result.get("status") != "success" and job.get("notify_on_failure")):
            notify(result)
        print(json.dumps(result, ensure_ascii=False))

    return 0 if all(x.get("status") == "success" for x in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
