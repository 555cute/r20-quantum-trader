#!/usr/bin/env python3
"""
Disk Space & Log Cleanup Utility
Features:
- Monitor available disk storage on the server
- Automatically rotate and trim log files (max 10MB per log, keep last 3 archives)
- Clean up old temporary files and caches
- Alert if free disk space falls below safe threshold (< 3GB)
"""

import os
import shutil
import glob
import subprocess
import datetime

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")
MAX_LOG_SIZE_MB = 10
BACKUP_COUNT = 3
MIN_SAFE_DISK_GB = 3.0

def get_disk_status():
    total, used, free = shutil.disk_usage("/")
    total_gb = total / (1024 ** 3)
    used_gb = used / (1024 ** 3)
    free_gb = free / (1024 ** 3)
    percent_used = (used / total) * 100
    return {
        "total_gb": round(total_gb, 2),
        "used_gb": round(used_gb, 2),
        "free_gb": round(free_gb, 2),
        "percent_used": round(percent_used, 1),
        "is_low": free_gb < MIN_SAFE_DISK_GB
    }

def clean_logs():
    cleaned_files = []
    if not os.path.exists(LOGS_DIR):
        return cleaned_files

    for file_path in glob.glob(os.path.join(LOGS_DIR, "*.log")):
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > MAX_LOG_SIZE_MB:
                # Rotate
                for i in range(BACKUP_COUNT - 1, 0, -1):
                    sfn = f"{file_path}.{i}"
                    dfn = f"{file_path}.{i+1}"
                    if os.path.exists(sfn):
                        shutil.move(sfn, dfn)
                # move current to .1
                shutil.move(file_path, f"{file_path}.1")
                # create fresh empty log
                open(file_path, 'w').close()
                cleaned_files.append(f"Rotated {os.path.basename(file_path)} ({size_mb:.1f}MB)")
            
            # Remove any backup beyond BACKUP_COUNT
            for old_f in glob.glob(f"{file_path}.*"):
                try:
                    num = int(old_f.split(".")[-1])
                    if num > BACKUP_COUNT:
                        os.remove(old_f)
                        cleaned_files.append(f"Deleted excess backup {os.path.basename(old_f)}")
                except ValueError:
                    pass
        except Exception as e:
            pass

    return cleaned_files

def clean_system_caches():
    actions = []
    # 1. Clean npm cache
    try:
        subprocess.run("npm cache clean --force", shell=True, capture_output=True, timeout=10)
        actions.append("npm cache cleaned")
    except Exception:
        pass

    # 2. Clean temporary files in /tmp older than 2 days
    try:
        subprocess.run("find /tmp -type f -mtime +2 -delete 2>/dev/null", shell=True, capture_output=True, timeout=10)
        actions.append("old /tmp files cleared")
    except Exception:
        pass

    return actions

def run_cleanup_and_check():
    disk = get_disk_status()
    log_actions = clean_logs()
    
    # If disk is getting tight (< 5GB), trigger deeper cache purge
    cache_actions = []
    if disk["free_gb"] < 5.0:
        cache_actions = clean_system_caches()

    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "disk": disk,
        "log_rotations": log_actions,
        "cache_cleared": cache_actions
    }
    return report

if __name__ == "__main__":
    rep = run_cleanup_and_check()
    print(f"=== 存储与日志清理检查 [{rep['timestamp']}] ===")
    print(f"磁盘状态: 剩余 {rep['disk']['free_gb']} GB / 总计 {rep['disk']['total_gb']} GB (使用率: {rep['disk']['percent_used']}%)")
    if rep["log_rotations"]:
        print(f"日志轮转: {', '.join(rep['log_rotations'])}")
    else:
        print("日志状态: 正常 (文件大小均在 10MB 限制内)")
    if rep["disk"]["is_low"]:
        print("⚠️ 警告: 可用磁盘空间低于 3GB！")
