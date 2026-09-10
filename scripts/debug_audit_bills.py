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

print("=== 01:11:20 REBOOT AFTERMATH BILLS (Chronological) ===")
total_pnl = 0.0
total_fee = 0.0
total_funding = 0.0

for b in reversed(bills):
    ts = int(b.get("ts", 0))/1000.0
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    if dt >= "2026-08-29 01:11:20":
        b_type = str(b.get("type"))
        sub_type = str(b.get("subType"))
        inst = b.get("instId", "").replace("-USDT-SWAP", "")
        pnl = float(b.get("pnl", 0) or 0)
        fee = float(b.get("fee", 0) or 0)
        bal_chg = float(b.get("balChg", 0) or 0)
        bal = float(b.get("bal", 0) or 0)
        
        total_pnl += pnl
        total_fee += fee
        if b_type == "8" or sub_type in ["173", "174"]:
            total_funding += (bal_chg if bal_chg != 0 else pnl)
            print(f"[{dt}] 资金费 | {inst:<5} | 资金费扣除={bal_chg:+.4f} | 账户余额={bal:.2f}")
        elif sub_type in ["3", "4"]:
            print(f"[{dt}] 开仓扣费 | {inst:<5} | 手续费={fee:+.4f} | 账户余额={bal:.2f}")
        elif sub_type in ["5", "6"]:
            print(f"[{dt}] 平仓结算 | {inst:<5} | 毛盈亏={pnl:+.4f} | 手续费={fee:+.4f} | 净变动={bal_chg:+.4f} | 账户余额={bal:.2f}")

print("----------------------------------------------------")
print(f"汇总: 平仓毛盈亏总和={total_pnl:+.2f} U, 累计手续费={total_fee:+.2f} U, 资金费={total_funding:+.2f} U")
print(f"真实账户净变动 = {total_pnl + total_fee:+.2f} U")
