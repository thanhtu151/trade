"""PaperBroker: simulated fills with the same pre-trade checks a live broker must pass.

Costs follow the CEO's protocol (T58), all module constants:
  BUY  = 0.125% fee + 0.075% slippage
  SELL = 0.125% fee + 0.100% sell tax + 0.075% slippage      (round trip ~0.5%)
etf_core.py keeps its own older constants and is not wired to this broker.
"""

import json
import math
from datetime import date
from pathlib import Path
from typing import Optional

from broker.base import Broker, Fill, Order

LOT = 100
BAND = 0.07                       # HOSE daily price limit vs reference price
SETTLEMENT_DAYS = 2               # T+2
BUY_FEE = 0.00125
SELL_FEE = 0.00125
SELL_TAX = 0.001
SLIPPAGE_PER_SIDE = 0.00075

# Tick table: HOSE trading rules ("quy chế giao dịch HOSE"), confirmed by the CEO in T58.
# Stocks <10,000 VND -> 10; 10,000-49,950 -> 50; >=50,000 -> 100; ETFs -> 10 at any price.
ETF_SYMBOLS = {"E1VFVN30", "FUEVFVND"}


def tick_size(symbol, price):
    if symbol in ETF_SYMBOLS or price < 10_000:
        return 10
    return 50 if price < 50_000 else 100


def _on_tick(price, tick):
    return abs(price / tick - round(price / tick)) < 1e-9


def _default_add_trading_days(day, n):
    from trading_calendar import add_trading_days
    return add_trading_days(day, n)


class PaperBroker(Broker):
    def __init__(self, cash, journal_path=None, add_trading_days=None):
        self.cash = float(cash)
        self.lots = {}            # symbol -> list of [buy_date, qty]
        self.seen = {}            # client_id -> Fill (idempotency)
        self.journal_path = Path(journal_path) if journal_path else None
        self._add_days = add_trading_days or _default_add_trading_days

    def to_dict(self):
        return {"cash": self.cash, "seen": sorted(self.seen),
                "lots": {s: [[d.isoformat(), q] for d, q in lots] for s, lots in self.lots.items()}}

    def load_dict(self, data):
        self.cash = float(data["cash"])
        self.lots = {s: [[date.fromisoformat(d), q] for d, q in lots] for s, lots in data["lots"].items()}
        self.seen = {cid: None for cid in data.get("seen", [])}
        return self

    # -- Broker API ---------------------------------------------------------
    def get_cash(self):
        return self.cash

    def get_positions(self, today: Optional[date] = None):
        out = {}
        for sym, lots in self.lots.items():
            qty = sum(q for _, q in lots)
            if qty:
                sellable = qty if today is None else self._sellable(sym, today)
                out[sym] = {"qty": qty, "sellable_qty": sellable}
        return out

    def cancel(self, client_id):
        return False              # paper orders fill or reject immediately

    def place_order(self, o: Order) -> Fill:
        if o.client_id and o.client_id in self.seen:   # restored ids map to None
            return Fill(o.client_id, "DUPLICATE", o.symbol, o.side)
        fill = self._execute(o)
        if o.client_id:
            self.seen[o.client_id] = fill
        self._journal(o, fill)
        return fill

    # -- internals ----------------------------------------------------------
    def _sellable(self, sym, today):
        return sum(q for d, q in self.lots.get(sym, [])
                   if self._add_days(d, SETTLEMENT_DAYS) <= today)

    def _reject(self, o, reason):
        return Fill(o.client_id, "REJECTED", o.symbol, o.side, reason=reason)

    def _execute(self, o):
        side = o.side.upper()
        if side not in ("BUY", "SELL"):
            return self._reject(o, "bad_side")
        if o.qty <= 0 or o.qty % LOT:
            return self._reject(o, f"qty_not_multiple_of_{LOT}")
        if o.ref_price <= 0 or o.market_price <= 0:
            return self._reject(o, "bad_price")
        # ceiling/floor round inward to the tick grid
        tick = tick_size(o.symbol, o.ref_price)
        ceil = math.floor(o.ref_price * (1 + BAND) / tick + 1e-9) * tick
        floor = math.ceil(o.ref_price * (1 - BAND) / tick - 1e-9) * tick
        for label, px in (("market", o.market_price), ("limit", o.limit_price)):
            if px is None:
                continue
            if not (floor <= px <= ceil):
                return self._reject(o, f"{label}_price_outside_band[{floor:g},{ceil:g}]")
            if not _on_tick(px, tick):
                return self._reject(o, f"{label}_price_off_tick_{tick}")
        if side == "SELL":
            if self._sellable(o.symbol, o.trade_date) < o.qty:
                return self._reject(o, "insufficient_sellable_qty_T+2")
            px = math.floor(o.market_price * (1 - SLIPPAGE_PER_SIDE) / tick + 1e-9) * tick
            px = max(px, floor)
            if o.limit_price is not None and px < o.limit_price:
                return self._reject(o, "limit_not_reached")
        else:
            px = math.ceil(o.market_price * (1 + SLIPPAGE_PER_SIDE) / tick - 1e-9) * tick
            px = min(px, ceil)
            if o.limit_price is not None and px > o.limit_price:
                return self._reject(o, "limit_not_reached")
        value = px * o.qty
        fee = value * (BUY_FEE if side == "BUY" else SELL_FEE + SELL_TAX)   # sell fee includes tax
        if side == "BUY":
            if self.cash < value + fee:
                return self._reject(o, "insufficient_cash")
            self.cash -= value + fee
            self.lots.setdefault(o.symbol, []).append([o.trade_date, o.qty])
        else:
            self.cash += value - fee
            self._consume(o.symbol, o.qty, o.trade_date)
        slip = (px - o.ref_price) / o.ref_price * 100
        slip = slip if side == "BUY" else -slip
        return Fill(o.client_id, "FILLED", o.symbol, side, o.qty, px, round(fee, 2), round(slip, 4))

    def _consume(self, sym, qty, today):
        """Sell oldest settled lots first."""
        for lot in sorted(self.lots[sym], key=lambda x: x[0]):
            if qty <= 0:
                break
            if self._add_days(lot[0], SETTLEMENT_DAYS) > today:
                continue
            take = min(lot[1], qty)
            lot[1] -= take
            qty -= take
        self.lots[sym] = [l for l in self.lots[sym] if l[1] > 0]

    def _journal(self, o, f):
        if not self.journal_path:
            return
        rec = {"date": o.trade_date.isoformat(), "client_id": o.client_id, "symbol": o.symbol,
               "side": f.side, "qty": o.qty, "status": f.status, "ref_price": o.ref_price,
               "market_price": o.market_price, "limit_price": o.limit_price,
               "fill_price": f.price, "fee": f.fee, "slippage_pct": f.slippage_pct,
               "reason": f.reason, "broker": "paper"}
        with self.journal_path.open("a", encoding="utf-8") as h:
            h.write(json.dumps(rec, ensure_ascii=False) + "\n")
