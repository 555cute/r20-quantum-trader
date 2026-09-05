"""Universal Execution Router for Multi-Exchange Trading (OKX, Binance, Gate.io).
Takes the clean, standardized decision dictionary output by LLM / Council:
{
    "action": "BUY_LONG" | "SELL_SHORT" | "WAIT",
    "entry_price": float,
    "stop_loss_price": float,
    "take_profit_price": float,
    "margin_usdt": float,
    "leverage": int,
    "confidence": float
}
Validates geometry & R:R via order_risk.py, then routes the order to the selected exchange adapter.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from r20_backend.exchanges import BaseExchangeAdapter, get_exchange_adapter
from scripts.order_risk import validate_quote_geometry_and_rr

logger = logging.getLogger(__name__)


class ExecutionRouter:
    """Dispatches trade decisions to designated crypto exchanges with fail-closed safeguards."""

    @staticmethod
    def get_configured_adapter(exchange_id: Optional[str] = None) -> BaseExchangeAdapter:
        target_ex = (exchange_id or os.getenv("R20_PRIMARY_EXCHANGE", "okx")).lower()
        if target_ex not in ("okx", "binance", "gate"):
            target_ex = "okx"

        # Load secrets/credentials for the exchange
        api_key = os.getenv(f"{target_ex.upper()}_API_KEY", "")
        api_secret = os.getenv(f"{target_ex.upper()}_SECRET_KEY", "")
        passphrase = os.getenv(f"{target_ex.upper()}_PASSPHRASE", "")
        testnet = os.getenv(f"{target_ex.upper()}_TESTNET", "1") == "1"

        return get_exchange_adapter(target_ex, api_key=api_key, api_secret=api_secret, passphrase=passphrase, testnet=testnet)

    @classmethod
    def execute_decision(
        cls,
        base_asset: str,
        decision_item: Dict[str, Any],
        exchange_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Validates and routes an LLM decision to target exchange.
        
        Returns:
            (success: bool, message_or_order_id: str, meta: dict)
        """
        action = str(decision_item.get("action", "WAIT")).upper()
        if action not in ("BUY_LONG", "SELL_SHORT"):
            return False, f"Action is {action}, no execution needed", {}

        entry = float(decision_item.get("entry_price") or 0.0)
        tp = float(decision_item.get("take_profit_price") or 0.0)
        sl = float(decision_item.get("stop_loss_price") or 0.0)
        margin = float(decision_item.get("margin_usdt") or 150.0)
        leverage = int(decision_item.get("leverage") or 3)

        # 1. Physical Risk Gatekeeper (order_risk.py)
        is_valid, reason, rr = validate_quote_geometry_and_rr(action, entry, tp, sl)
        if not is_valid:
            logger.warning("ExecutionRouter: Order rejected by physical risk check: %s", reason)
            return False, f"物理级风控拦截: {reason}", {"rr": rr}

        # 2. Get Target Exchange Adapter
        adapter = cls.get_configured_adapter(exchange_id)

        # 3. Dispatch to Target Exchange
        try:
            success, ord_id, details = adapter.place_limit_order_with_sl_tp(
                base_asset=base_asset,
                action=action,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                margin_usdt=margin,
                leverage=leverage,
            )
            return success, ord_id, {
                "exchange": adapter.exchange_id,
                "rr": rr,
                "details": details,
            }
        except Exception as exc:
            logger.error("ExecutionRouter: Order submission failed: %s", exc)
            return False, f"交易所下单异常: {exc}", {"exchange": adapter.exchange_id}
