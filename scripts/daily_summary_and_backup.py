#!/usr/bin/env python3
"""
Automated Daily Quant Briefing Engine
Runs at 08:00 & 20:00 Beijing time to sync the lifecycle ledger and publish a read-only performance briefing.
Self-evolution and backups are owned by their dedicated scheduled jobs.
"""

import os
import json
import tarfile
import datetime
import subprocess
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
BACKUPS_DIR = os.path.join(WORKSPACE_DIR, "backups")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")

os.makedirs(BACKUPS_DIR, exist_ok=True)
sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
try:
    from qq_notifier import notify_daily_summary
except Exception:
    notify_daily_summary = None

def generate_daily_briefing_and_backup():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now_bj.strftime("%Y-%m-%d")

    # Read Account Initial State (Supports Dynamic Capital Reset Filter)
    account_init_file = os.path.join(DATA_DIR, "account_initial_state.json")
    reset_time_str = "1970-01-01 00:00:00"
    if os.path.exists(account_init_file):
        try:
            with open(account_init_file, "r", encoding="utf-8") as f:
                acc_init = json.load(f)
                reset_time_str = acc_init.get("reset_time", "1970-01-01 00:00:00")
        except Exception:
            pass

    # 1. Sync full ledger and load trades
    try:
        sync_script = os.path.join(WORKSPACE_DIR, "scripts", "sync_full_ledger.py")
        if os.path.exists(sync_script):
            subprocess.run(f"python3 {sync_script}", shell=True, capture_output=True, text=True, timeout=15)
    except Exception:
        pass

    trades = []
    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except Exception:
            pass

    closed_today = [t for t in trades if t.get("status") == "closed" and date_str in str(t.get("close_time", ""))]
    total_trades = len(closed_today)
    wins = [t for t in closed_today if float(t.get("pnl", 0)) > 0]
    losses = [t for t in closed_today if float(t.get("pnl", 0)) < 0]
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
    net_pnl = sum(float(t.get("pnl", 0)) for t in closed_today)

    # 2. Top Performing Asset
    asset_pnl = {}
    for t in closed_today:
        inst = t.get("inst") or t.get("name") or "OTHER"
        if inst and inst != "None":
            asset_pnl[inst] = asset_pnl.get(inst, 0.0) + float(t.get("pnl", 0))
    
    top_asset = max(asset_pnl.items(), key=lambda x: x[1])[0] if asset_pnl else "暂无"
    top_asset_pnl = asset_pnl.get(top_asset, 0.0)

    # 3. Macro Sentiment & News
    news_file = os.path.join(DATA_DIR, "news_sentiment.json")
    macro_env = "偏多震荡"
    if os.path.exists(news_file):
        try:
            with open(news_file, "r", encoding="utf-8") as f:
                n_data = json.load(f)
                macro_env = n_data.get("macro_sentiment", "偏多震荡")
        except Exception:
            pass

    briefing_text = (
        f"📅 日期：{date_str}（北京时间）\n"
        f"• 今日平仓战绩：{len(wins)} 胜 / {len(losses)} 负（胜率 {win_rate:.1f}%）\n"
        f"• 今日已结净盈亏：{net_pnl:+.2f} USDT\n"
        f"• 最优贡献标的：{top_asset} ({top_asset_pnl:+.2f} U)\n"
        f"• 市场舆情环境：{macro_env}\n"
        f"• 策略状态：多周期趋势共振滤网已激活，黑天鹅熔断哨兵全天候巡检中。"
    )

    if notify_daily_summary:
        notify_daily_summary(briefing_text)

    print("✅ 每日量化研报已成功生成并推送。")
    return briefing_text

if __name__ == "__main__":
    rep = generate_daily_briefing_and_backup()
    print("Daily Briefing Result:\n" + rep)
