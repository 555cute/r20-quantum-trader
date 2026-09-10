"""US-001 · 组合风险预算原子预留层 risk_reservation。

设计语义参照 plan_local/THREE_VENUE_COORDINATION_DESIGN.md：
- §5「合看风险，不混资金」：同环境（AccountKey 维度）一份风险预算，
  每所账户用自身可用资金约束，组合层再合看 gross exposure；
- §6「未知状态显式化」：pending/partial/unknown 全额占用，未知结果
  绝不释放预算；只有确认终态（rejected / closed）才释放。

持久化对齐 scripts/db_manager.py 既有模式：sqlite3 直连、幂等建表、
逐操作短连接、commit 即落盘。进程重启后 recovery() 从库重建占用视图；
孤儿预留（无对应开放意图）只标记 pending_cleanup，不自动释放——
「不能假定本地追踪器消失 = 交易所仓位消失」。

线程安全：模块级 threading.Lock 串行化所有写路径 + BEGIN IMMEDIATE
事务保证「查总额→插入」原子；不引入任何新依赖。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Iterable, Optional

#: 状态词表（验收标准 1）
STATE_PENDING = "pending"
STATE_PARTIAL = "partial"
STATE_UNKNOWN = "unknown"
STATE_CONFIRMED = "confirmed"
STATE_REJECTED = "rejected"
STATE_CLOSED = "closed"

#: 占用中的状态：unknown / confirmed 均不释放（确认成交后到平仓前仍占预算）
OCCUPYING_STATES = frozenset({STATE_PENDING, STATE_PARTIAL, STATE_UNKNOWN,
                              STATE_CONFIRMED})
#: 确认终态：到达才释放
TERMINAL_STATES = frozenset({STATE_REJECTED, STATE_CLOSED})
#: 合法状态全集
VALID_STATES = OCCUPYING_STATES | TERMINAL_STATES

#: 孤儿预留标记（recovery 时无对应开放意图；仍占用，不自动释放）
STATE_PENDING_CLEANUP = "pending_cleanup"


class ReservationError(Exception):
    """预留层基础异常。"""


class ReservationExceeded(ReservationError):
    """组合 total_usdt 上限越界：拒绝新预留，绝不部分占用。"""


def _normalize_account_key(account_key) -> tuple:
    """AccountKey / (venue, environment, fingerprint) / "v:e:f" 串 →
    (key_str, venue, environment)。key_str 为稳定唯一键。"""
    if hasattr(account_key, "venue") and hasattr(account_key, "environment"):
        venue = str(account_key.venue)
        environment = str(account_key.environment)
        fingerprint = str(getattr(account_key, "fingerprint", "") or "")
        return f"{venue}:{environment}:{fingerprint}", venue, environment
    if isinstance(account_key, (tuple, list)) and len(account_key) == 3:
        venue, environment, fingerprint = (str(x) for x in account_key)
        return f"{venue}:{environment}:{fingerprint}", venue, environment
    s = str(account_key)
    parts = s.split(":")
    if len(parts) == 3:
        return s, parts[0], parts[1]
    # 退化字符串：无法拆出 venue/environment，诚实记空，不冒充
    return s, "", ""


class RiskReservationManager:
    """单进程内共享的组合风险预留台账（SQLite 持久化 + 线程锁）。"""

    _DDL = """CREATE TABLE IF NOT EXISTS risk_reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_key TEXT NOT NULL,
        venue TEXT NOT NULL DEFAULT '',
        environment TEXT NOT NULL DEFAULT '',
        intent_id TEXT NOT NULL,
        amount_usdt REAL NOT NULL,
        state TEXT NOT NULL,
        released INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_key, intent_id)
    );"""

    def __init__(self, db_path: str, total_limit_usdt: Optional[float] = None):
        self.db_path = str(db_path)
        self.total_limit_usdt = total_limit_usdt
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(self._DDL)
            conn.commit()
        finally:
            conn.close()

    # ---- 连接管理（对齐 db_manager.get_db 风格：短连接 + Row factory）----
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 内部读取（调用方须已持锁或只读快照场景）----
    def _active_total(self, conn, key_str: str) -> float:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usdt), 0) FROM risk_reservations "
            "WHERE account_key = ? AND released = 0", (key_str,)).fetchone()
        return float(row[0] or 0.0)

    # ---- 核心原子预留 ----
    def reserve(self, account_key, intent_id: str, amount_usdt: float,
                state: str, total_limit_usdt: Optional[float] = None) -> dict:
        """原子预留/状态推进。同 (account_key, intent_id) 幂等：
        - 新意图：state 须为占用态（pending/partial/unknown），预算足够则插入，
          越界抛 ReservationExceeded；
        - 已存在且占用中：推进状态（confirmed 仍占用；rejected/closed 释放），
          amount_usdt 变化时按差额重查上限；
        - 已释放（终态）的意图不可复活：后续调用原样返回当前记录（终态幂等）。
        返回该预留的当前快照 dict。
        """
        state = str(state).strip().lower()
        if state not in VALID_STATES:
            raise ReservationError(f"非法预留状态: {state!r}，允许 {sorted(VALID_STATES)}")
        amount_usdt = float(amount_usdt)
        if amount_usdt < 0:
            raise ReservationError("amount_usdt 不可为负")
        key_str, venue, environment = _normalize_account_key(account_key)
        intent_id = str(intent_id)
        limit = self.total_limit_usdt if total_limit_usdt is None else total_limit_usdt

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM risk_reservations "
                    "WHERE account_key = ? AND intent_id = ?",
                    (key_str, intent_id)).fetchone()
                if row is None:
                    if state not in OCCUPYING_STATES:
                        raise ReservationError(
                            f"新预留 {intent_id} 初始状态须为占用态，收到 {state!r}")
                    if limit is not None:
                        cur_total = self._active_total(conn, key_str)
                        if cur_total + amount_usdt > float(limit) + 1e-9:
                            conn.rollback()
                            raise ReservationExceeded(
                                f"预算越界: 已占 {cur_total} + 新增 {amount_usdt} "
                                f"> 上限 {limit} (account_key={key_str})")
                    conn.execute(
                        "INSERT INTO risk_reservations "
                        "(account_key, venue, environment, intent_id, amount_usdt, state, released)"
                        " VALUES (?, ?, ?, ?, ?, ?, 0)",
                        (key_str, venue, environment, intent_id, amount_usdt, state))
                else:
                    released = int(row["released"])
                    if released or row["state"] in TERMINAL_STATES:
                        # 终态幂等：不可复活、不改写，原样返回
                        conn.rollback()
                        return self._snapshot(row)
                    new_amount = amount_usdt if amount_usdt > 0 else float(row["amount_usdt"])
                    if limit is not None:
                        others = self._active_total(conn, key_str) - float(row["amount_usdt"])
                        if others + new_amount > float(limit) + 1e-9:
                            conn.rollback()
                            raise ReservationExceeded(
                                f"预算越界: 其他占用 {others} + 调整后 {new_amount} "
                                f"> 上限 {limit} (account_key={key_str})")
                    if state in TERMINAL_STATES:
                        conn.execute(
                            "UPDATE risk_reservations SET amount_usdt = ?, state = ?, "
                            "released = 1, updated_at = CURRENT_TIMESTAMP "
                            "WHERE id = ?", (new_amount, state, row["id"]))
                    else:
                        conn.execute(
                            "UPDATE risk_reservations SET amount_usdt = ?, state = ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (new_amount, state, row["id"]))
                conn.commit()
                row2 = conn.execute(
                    "SELECT * FROM risk_reservations "
                    "WHERE account_key = ? AND intent_id = ?",
                    (key_str, intent_id)).fetchone()
                return self._snapshot(row2)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def release(self, account_key, intent_id: str,
                state: str = STATE_CLOSED) -> dict:
        """显式释放（等价 reserve(..., state=终态) 的语义糖）。"""
        if state not in TERMINAL_STATES:
            raise ReservationError(f"release 只接受终态 {sorted(TERMINAL_STATES)}")
        return self.reserve(account_key, intent_id, 0.0, state)

    # ---- 组合查询 ----
    def total_reserved(self, account_key) -> float:
        """单 AccountKey 维度的当前占用合计（released=0，含 pending_cleanup）。"""
        key_str, _, _ = _normalize_account_key(account_key)
        conn = self._connect()
        try:
            return self._active_total(conn, key_str)
        finally:
            conn.close()

    def total_reserved_by_venue(self, environment: str) -> dict:
        """同环境按所聚合的占用视图（§5 合看风险，不混资金）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT venue, SUM(amount_usdt) AS total FROM risk_reservations "
                "WHERE environment = ? AND released = 0 GROUP BY venue",
                (str(environment),)).fetchall()
            return {r["venue"]: float(r["total"] or 0) for r in rows}
        finally:
            conn.close()

    def gross_exposure(self, environment: str) -> float:
        """同环境跨所聚合总敞口（含每所明细见 total_reserved_by_venue）。"""
        return round(sum(self.total_reserved_by_venue(environment).values()), 10)

    def reservations(self, account_key=None) -> list:
        """预留快照列表（诊断/测试用）。"""
        conn = self._connect()
        try:
            if account_key is None:
                rows = conn.execute(
                    "SELECT * FROM risk_reservations ORDER BY id").fetchall()
            else:
                key_str, _, _ = _normalize_account_key(account_key)
                rows = conn.execute(
                    "SELECT * FROM risk_reservations WHERE account_key = ? ORDER BY id",
                    (key_str,)).fetchall()
            return [self._snapshot(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _snapshot(row) -> dict:
        return {
            "account_key": row["account_key"],
            "venue": row["venue"],
            "environment": row["environment"],
            "intent_id": row["intent_id"],
            "amount_usdt": float(row["amount_usdt"]),
            "state": row["state"],
            "released": bool(row["released"]),
        }

    # ---- 重启恢复 ----
    def recovery(self, known_open_intents: Optional[Iterable[str]] = None) -> dict:
        """从持久化库重建占用视图（进程重启后调用一次）。

        - known_open_intents=None：保守恢复——所有未释放预留继续全额占用
          （§6：不能假定本地追踪器消失 = 交易所仓位消失）；
        - 传入已知开放意图集合：占用中且不在集合内的标记 pending_cleanup，
          **仍计入占用、不自动释放**，等人工/对账确认后显式 release。
        """
        with self._lock:
            conn = self._connect()
            try:
                open_set = ({str(i) for i in known_open_intents}
                            if known_open_intents is not None else None)
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT id, intent_id, state FROM risk_reservations "
                    "WHERE released = 0").fetchall()
                orphan_ids = []
                for r in rows:
                    if open_set is not None and r["intent_id"] not in open_set \
                            and r["state"] != STATE_PENDING_CLEANUP:
                        orphan_ids.append((r["id"], r["intent_id"]))
                for rid, _iid in orphan_ids:
                    conn.execute(
                        "UPDATE risk_reservations SET state = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (STATE_PENDING_CLEANUP, rid))
                conn.commit()
                return {"recovered_active": len(rows) - len(orphan_ids),
                        "orphans_marked": len(orphan_ids),
                        "orphan_intent_ids": [i for _, i in orphan_ids]}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()


# ---- 模块级默认实例（对齐 db_manager 的模块级 DB_PATH 用法）----
#: 默认落点跟随 data/（生产）；测试一律自建 manager 传临时路径，绝不写 data/**
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "risk_reservation.db")

_default_manager: Optional[RiskReservationManager] = None
_default_manager_lock = threading.Lock()


def get_manager(db_path: Optional[str] = None,
                total_limit_usdt: Optional[float] = None) -> RiskReservationManager:
    """取默认管理器（db_path 缺省 DEFAULT_DB_PATH）；传 db_path 则返回独立实例。"""
    global _default_manager
    if db_path is not None:
        return RiskReservationManager(db_path, total_limit_usdt)
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = RiskReservationManager(DEFAULT_DB_PATH,
                                                      total_limit_usdt)
        return _default_manager
