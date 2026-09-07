import datetime
import os
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from r20_backend.account_paths import classify_bill
from r20_exchange.runtime import get_exchange

bills = get_exchange().bills(limit=100) or []
orders_by_id = {}
for bill in reversed(bills):
    ts = int(bill.get("ts", 0) or 0) / 1000.0
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    if dt < "2026-08-29 01:11:20":
        continue
    if classify_bill(bill) != "realized":
        continue
    ord_id = bill.get("ordId") or bill.get("billId")
    inst = str(bill.get("instId", "")).replace("-USDT-SWAP", "")
    pnl = float(bill.get("pnl", 0) or 0)
    fee = float(bill.get("fee", 0) or 0)
    if ord_id not in orders_by_id:
        orders_by_id[ord_id] = {
            "ordId": ord_id,
            "time": dt,
            "inst": inst,
            "gross_pnl": 0.0,
            "fee": 0.0,
            "net_pnl": 0.0,
        }
    orders_by_id[ord_id]["gross_pnl"] += pnl
    orders_by_id[ord_id]["fee"] += fee
    orders_by_id[ord_id]["net_pnl"] += pnl + fee

print("=== AGGREGATED REALIZED CLOSES ===")
wins = 0
losses = 0
for ord_id, item in orders_by_id.items():
    net = item["net_pnl"]
    if net > 0:
        wins += 1
    elif net < 0:
        losses += 1
    print(f"[{item['time']}] {item['inst']:<5} | ordId={ord_id} | gross={item['gross_pnl']:+.4f} | fee={item['fee']:+.4f} | net={net:+.4f}")
print("----------------------------------------------------")
closed = wins + losses
rate = (wins / closed * 100) if closed else 0.0
print(f"closed: {wins} win / {losses} loss ({closed}) win_rate={rate:.1f}%")
