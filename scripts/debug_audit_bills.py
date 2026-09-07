import datetime
import os
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from r20_backend.account_paths import classify_bill
from r20_exchange.runtime import get_exchange

bills = get_exchange().bills(limit=100) or []
print("=== BILLS (chronological) ===")
total_pnl = 0.0
total_fee = 0.0
total_funding = 0.0

for bill in reversed(bills):
    ts = int(bill.get("ts", 0) or 0) / 1000.0
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    if dt < "2026-08-29 01:11:20":
        continue
    inst = str(bill.get("instId", "")).replace("-USDT-SWAP", "")
    pnl = float(bill.get("pnl", 0) or 0)
    fee = float(bill.get("fee", 0) or 0)
    bal_chg = float(bill.get("balChg", 0) or 0)
    kind = classify_bill(bill)
    total_pnl += pnl
    total_fee += fee
    if kind == "funding":
        funding = bal_chg if bal_chg != 0 else pnl
        total_funding += funding
        print(f"[{dt}] funding | {inst:<5} | {funding:+.4f}")
    elif kind == "commission":
        print(f"[{dt}] commission | {inst:<5} | fee={fee:+.4f}")
    elif kind == "realized":
        print(f"[{dt}] realized | {inst:<5} | pnl={pnl:+.4f} | fee={fee:+.4f} | net={pnl + fee:+.4f}")

print("----------------------------------------------------")
print(f"sum pnl={total_pnl:+.2f} fee={total_fee:+.2f} funding={total_funding:+.2f} net={total_pnl + total_fee + total_funding:+.2f}")
