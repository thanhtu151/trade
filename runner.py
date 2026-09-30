"""Runner: signal -> target -> order -> broker, one session at a time (paper or, later, live).

Timing mirrors etf_core: the target is decided on the close of session `day`; any resulting
order is stored as `pending` and sent as an ATO order on the next session (open price).
Monitor-only signals (E6) are logged to the signal journal and never produce orders.

etf_core.py is intentionally NOT rewired to this runner (T58): its fill costs/slippage differ
from the new protocol, so switching would change its published output and break its tests.
"""

import json
import math
from datetime import timedelta
from pathlib import Path

import pandas as pd

from broker.base import Order
from broker.paper import BAND, BUY_FEE, LOT, SLIPPAGE_PER_SIDE, tick_size


def default_state():
    return {"pending": None, "target_weight": 0.0, "last_day": None}


def is_month_end(day, is_trading_day):
    """True when no later trading day exists in the same month."""
    d = day + timedelta(days=1)
    while d.month == day.month:
        if is_trading_day(d):
            return False
        d += timedelta(days=1)
    return True


def buy_qty(cash, symbol, ref, open_price):
    """Largest board-lot quantity affordable at the worst expected fill price."""
    tick = tick_size(symbol, ref)
    ceil = math.floor(ref * (1 + BAND) / tick + 1e-9) * tick
    px = min(math.ceil(open_price * (1 + SLIPPAGE_PER_SIDE) / tick - 1e-9) * tick, ceil)
    return int(cash // (px * (1 + BUY_FEE)) // LOT * LOT)


def _append(path, record):
    if path:
        with Path(path).open("a", encoding="utf-8") as h:
            h.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run_session(day, etf, bars, data, primary, monitors, broker, state, month_end,
                signal_journal=None):
    """Advance one session.

    day: datetime.date; etf: symbol; bars: {"ref": prev close, "open": .., "close": ..} (VND).
    data: input for signal.target(); primary: order-driving signal; monitors: list of signals.
    month_end: True if `day` is the last session of its month.
    Returns {"day", "signals", "orders", "nav", "skipped"}; mutates `state`.
    """
    if state.get("last_day") and state["last_day"] >= day.isoformat():
        return {"day": day.isoformat(), "skipped": "already_processed", "signals": {}, "orders": [],
                "nav": _nav(broker, etf, bars["close"])}
    orders = []
    pending = state.get("pending")
    if pending:                                   # yesterday's decision, fills at today's open
        if pending["side"] == "BUY":
            qty = buy_qty(broker.get_cash(), etf, bars["ref"], bars["open"])
        else:
            qty = broker.get_positions().get(etf, {}).get("qty", 0)   # broker enforces T+2 and journals
        if qty > 0:
            fill = broker.place_order(Order(etf, pending["side"], qty, day, bars["ref"], bars["open"],
                                            client_id=f"{pending['signal_day']}-{pending['side']}-{etf}@{day}"))
            orders.append(fill)
        # a SELL rejected only because shares are not settled yet (T+2) is retried next session
        holding = broker.get_positions().get(etf, {}).get("qty", 0) > 0
        retry = holding and (qty == 0 or (orders and orders[-1].status == "REJECTED"
                                          and "T+2" in orders[-1].reason))
        if not (pending["side"] == "SELL" and retry):
            state["pending"] = None

    results = {}
    for sig in [primary] + list(monitors):
        results[sig.name] = sig.target(pd.Timestamp(day), data)
        _append(signal_journal, {"day": day.isoformat(), "signal": sig.name,
                                 "monitor_only": bool(getattr(sig, "monitor_only", False)),
                                 "weight": results[sig.name].weight, "reason": results[sig.name].reason,
                                 "details": results[sig.name].details})

    if getattr(primary, "cadence", "daily") == "daily" or month_end:
        state["target_weight"] = results[primary.name].weight
        held = broker.get_positions().get(etf, {}).get("qty", 0)
        side = "BUY" if state["target_weight"] > 0 and held == 0 else \
               "SELL" if state["target_weight"] == 0 and held > 0 else None
        if side:
            state["pending"] = {"side": side, "signal_day": day.isoformat()}
    return _finish(day, state, results, orders, broker, etf, bars)


def _nav(broker, etf, close):
    held = broker.get_positions().get(etf, {}).get("qty", 0)
    return round(broker.get_cash() + held * close, 0)


def _finish(day, state, results, orders, broker, etf, bars):
    state["last_day"] = day.isoformat()
    return {"day": day.isoformat(), "signals": results, "orders": orders,
            "nav": _nav(broker, etf, bars["close"]), "pending": state.get("pending")}
