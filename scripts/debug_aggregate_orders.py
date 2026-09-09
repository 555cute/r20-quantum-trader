import sys
import os
import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import scripts.okx_rest as okx_rest
import scripts.okx_runtime as okx_runtime

# 2026-09-09 OKX CLI 移除：账单直读走 V5 直签 REST；未配置 Key 一律 NOT READY 拒绝。
if not okx_runtime.current_environment().configured:
    print("[NOT READY] OKX API Key 未配置 — 调试工具仅支持 V5 直签 REST（fail-closed，无 CLI 回退）")
    raise SystemExit(3)

bills = okx_rest.bills(limit=100)

orders_by_id = {}
for b in reversed(bills):
    ts = int(b.get("ts", 0))/1000.0
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    if dt >= "2026-08-29 01:11:20":
        sub_type = str(b.get("subType"))
        if sub_type in ["5", "6"]:
            ord_id = b.get("ordId") or b.get("billId")
            inst = b.get("instId", "").replace("-USDT-SWAP", "")
            pnl = float(b.get("pnl", 0) or 0)
            fee = float(b.get("fee", 0) or 0)
            
            if ord_id not in orders_by_id:
                orders_by_id[ord_id] = {
                    "ordId": ord_id,
                    "time": dt,
                    "inst": inst,
                    "gross_pnl": 0.0,
                    "fee": 0.0,
                    "net_pnl": 0.0
                }
            orders_by_id[ord_id]["gross_pnl"] += pnl
            orders_by_id[ord_id]["fee"] += fee
            orders_by_id[ord_id]["net_pnl"] += (pnl + fee)

print("=== AGGREGATED REAL ORDERS (SINCE 01:11:20 REBOOT) ===")
wins = 0
losses = 0
for ord_id, o in orders_by_id.items():
    p = o["net_pnl"]
    if p > 0: wins += 1
    elif p < 0: losses += 1
    print(f"[{o['time']}] {o['inst']:<5} | ordId={ord_id} | gross={o['gross_pnl']:+.4f} | fee={o['fee']:+.4f} | net={p:+.4f} | {'🟢 胜' if p>0 else '🔴 负'}")

print("----------------------------------------------------")
print(f"真实订单级平仓战绩: {wins} 胜 / {losses} 负 (总订单数: {len(orders_by_id)}) | 真实胜率 = {wins/(wins+losses)*100:.1f}%")
