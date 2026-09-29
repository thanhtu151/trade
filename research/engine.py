"""Daily event-driven portfolio simulator with Vietnamese market frictions.

Conventions
- Prices are vnstock adjusted prices in thousand VND; volume is shares.
- A strategy returns target weights at the close of a signal day. Orders fill
  at the next session's open (ATO), never at the signal close.
- Costs: brokerage + exchange fee on both sides, 0.1% personal income tax on
  sells, plus slippage on the open price.
- Board lots of 100 shares. Order size is capped at a fraction of the 20-day
  average volume; the unfilled rest carries over to the next session.
- T+2: shares bought at the open of session b become sellable at the open of
  session b+3 (they arrive around 13:00 on b+2, after the ATO).
- Limit locks: no buying when the session opens at an up-limit, no selling when
  it opens at a down-limit. Adjusted prices cannot reproduce exact limit prices,
  so a lock is a session that opens >= 6.5% beyond the previous close at the
  session extreme. Exchange-agnostic and conservative for HNX/UPCoM bands.
- Catastrophe stop: checked on the close, sold at the next open (gap risk).
- A held symbol that stops trading for `delist_after` sessions is written off
  at its last close less `delist_haircut`.
- Cash dividends are inside the adjusted prices (the 5% dividend tax is not
  modelled). Proceeds of a matched sell may fund same-day buys, as VN brokers
  allow; the T+2 cash delay does not bind for a fully invested long-only book.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Costs:
    brokerage: float = 0.0015
    exchange_fee: float = 0.0003
    sell_tax: float = 0.0010
    slippage: float = 0.0015

    def buy_price(self, open_price):
        return open_price * (1 + self.slippage)

    def sell_price(self, open_price):
        return open_price * (1 - self.slippage)

    def buy_fee(self, value):
        return value * (self.brokerage + self.exchange_fee)

    def sell_fee(self, value):
        return value * (self.brokerage + self.exchange_fee + self.sell_tax)


@dataclass
class Config:
    initial_cash: float = 1_000_000_000.0  # VND
    costs: Costs = field(default_factory=Costs)
    lot: int = 100
    max_adv_fraction: float = 0.05
    settle_sessions: int = 3
    stop_loss: float | None = 0.20
    delist_after: int = 20
    delist_haircut: float = 0.30
    order_expiry: int = 10  # sessions an unfilled rebalance order stays live


class Panel:
    """Wide OHLCV arrays aligned on one trading calendar."""

    def __init__(self, prices):
        wide = prices.pivot_table(index="time", columns="symbol", values=["open", "high", "low", "close", "volume"])
        wide = wide.sort_index()
        self.dates = wide.index
        self.symbols = list(wide["close"].columns)
        self.col = {s: i for i, s in enumerate(self.symbols)}
        self.open = wide["open"].to_numpy(float)
        self.high = wide["high"].to_numpy(float)
        self.low = wide["low"].to_numpy(float)
        self.close = wide["close"].to_numpy(float)
        self.volume = wide["volume"].fillna(0).to_numpy(float)
        traded = np.isfinite(self.close) & (self.volume > 0)
        self.traded = traded
        self.last_close = pd.DataFrame(self.close).ffill().to_numpy()
        self.adv = pd.DataFrame(np.where(traded, self.volume, 0.0)).rolling(20, min_periods=5).mean().shift(1).to_numpy()
        prev = np.vstack([np.full(len(self.symbols), np.nan), self.last_close[:-1]])
        with np.errstate(divide="ignore", invalid="ignore"):
            gap = self.open / prev - 1
        self.locked_up = traded & (gap >= 0.065) & (self.open >= self.high)
        self.locked_down = traded & (gap <= -0.065) & (self.open <= self.low)

    def frame(self, field_name):
        return pd.DataFrame(getattr(self, field_name), index=self.dates, columns=self.symbols)


def simulate(panel, strategy, config=None, start=None, end=None):
    """Run strategy over panel.

    strategy(panel, day_index, holdings) returns None (no rebalance today) or a
    {symbol: weight} map of the target book. A weight of None keeps an existing
    position as it is; held symbols missing from the map are sold.
    """
    cfg = config or Config()
    costs = cfg.costs
    dates = panel.dates
    first = 0 if start is None else int(dates.searchsorted(pd.Timestamp(start)))
    last = len(dates) - 1 if end is None else int(dates.searchsorted(pd.Timestamp(end), side="right")) - 1

    cash = cfg.initial_cash
    shares = {}      # symbol -> total shares
    lots = {}        # symbol -> list of [shares, buy_session]
    entry = {}       # symbol -> average cost per share (VND)
    stale = {}       # symbol -> sessions without a trade
    orders = {}      # symbol -> [target_shares, sessions_left]
    stop_sells = set()
    equity_curve, trades = [], []
    cost_paid = 0.0

    def price_vnd(i, j, arr):
        return arr[i, j] * 1000.0

    def sellable(sym, i):
        return sum(q for q, b in lots.get(sym, []) if i - b >= cfg.settle_sessions)

    def remove_shares(sym, qty):
        remaining = qty
        new_lots = []
        for q, b in sorted(lots[sym], key=lambda x: x[1]):
            take = min(q, remaining)
            remaining -= take
            if q - take > 0:
                new_lots.append([q - take, b])
        lots[sym] = new_lots
        shares[sym] -= qty
        if shares[sym] <= 0:
            for d in (shares, lots, entry, stale):
                d.pop(sym, None)

    for i in range(first, last + 1):
        # 1) execute pending orders at the open: sells first, then buys
        for sell_pass in (True, False):
            for sym in list(orders):
                target, left = orders[sym]
                j = panel.col[sym]
                held = shares.get(sym, 0)
                delta = target - held
                if (delta < 0) != sell_pass or delta == 0:
                    continue
                if not panel.traded[i, j] or not np.isfinite(panel.open[i, j]):
                    continue
                cap = panel.adv[i, j] * cfg.max_adv_fraction if np.isfinite(panel.adv[i, j]) else 0.0
                if sell_pass:
                    if panel.locked_down[i, j]:
                        continue
                    qty = min(-delta, sellable(sym, i), cap)
                    qty = int(qty // cfg.lot * cfg.lot) if qty < held else int(qty)
                    if qty <= 0:
                        continue
                    px = costs.sell_price(price_vnd(i, j, panel.open))
                    value = qty * px
                    fee = costs.sell_fee(value)
                    cash += value - fee
                    cost_paid += fee + qty * price_vnd(i, j, panel.open) * costs.slippage
                    pnl = (px - entry.get(sym, px)) * qty - fee
                    trades.append({"date": dates[i], "symbol": sym, "side": "SELL", "qty": qty, "price": px, "fee": fee,
                                   "pnl": pnl, "reason": "stop" if sym in stop_sells else "rebalance"})
                    remove_shares(sym, qty)
                else:
                    if panel.locked_up[i, j]:
                        continue
                    px = costs.buy_price(price_vnd(i, j, panel.open))
                    affordable = cash / (px * (1 + costs.brokerage + costs.exchange_fee))
                    qty = int(min(delta, cap, affordable) // cfg.lot * cfg.lot)
                    if qty <= 0:
                        continue
                    value = qty * px
                    fee = costs.buy_fee(value)
                    cash -= value + fee
                    cost_paid += fee + qty * price_vnd(i, j, panel.open) * costs.slippage
                    prev_qty = shares.get(sym, 0)
                    entry[sym] = (entry.get(sym, 0.0) * prev_qty + value + fee) / (prev_qty + qty)
                    shares[sym] = prev_qty + qty
                    lots.setdefault(sym, []).append([qty, i])
                    trades.append({"date": dates[i], "symbol": sym, "side": "BUY", "qty": qty, "price": px, "fee": fee,
                                   "pnl": 0.0, "reason": "rebalance"})
                if shares.get(sym, 0) == target:
                    orders.pop(sym, None)
                    stop_sells.discard(sym)
        for sym in list(orders):
            orders[sym][1] -= 1
            if orders[sym][1] <= 0 and sym not in stop_sells:
                orders.pop(sym)

        # 2) delisting write-offs
        for sym in list(shares):
            j = panel.col[sym]
            stale[sym] = 0 if panel.traded[i, j] else stale.get(sym, 0) + 1
            if stale[sym] >= cfg.delist_after:
                qty = shares[sym]
                value = qty * price_vnd(i, j, panel.last_close) * (1 - cfg.delist_haircut)
                cash += value
                trades.append({"date": dates[i], "symbol": sym, "side": "SELL", "qty": qty,
                               "price": value / qty, "fee": 0.0, "pnl": value - entry[sym] * qty, "reason": "delisted"})
                for d in (shares, lots, entry, stale):
                    d.pop(sym, None)
                orders.pop(sym, None)

        # 3) mark to market at the close
        holdings_value = sum(q * price_vnd(i, panel.col[s], panel.last_close) for s, q in shares.items())
        equity = cash + holdings_value
        equity_curve.append((dates[i], equity, cash))

        # 4) catastrophe stops, filled from the next open
        if cfg.stop_loss:
            for sym, qty in shares.items():
                j = panel.col[sym]
                if panel.traded[i, j] and panel.close[i, j] * 1000.0 <= entry[sym] * (1 - cfg.stop_loss):
                    orders[sym] = [0, cfg.order_expiry]
                    stop_sells.add(sym)

        # 5) strategy signal at the close -> orders for the next open
        weights = strategy(panel, i, dict(shares))
        if weights is not None:
            # Strategy order first (its ranking decides who gets cash), then sells of
            # dropped holdings; never iterate a set, whose order varies per process.
            for sym in list(weights) + sorted(s for s in shares if s not in weights):
                if sym in stop_sells:
                    continue
                w = weights.get(sym, 0.0)
                if w is None:  # keep the current position untouched
                    orders.pop(sym, None)
                    continue
                j = panel.col[sym]
                px = panel.last_close[i, j] * 1000.0
                if not np.isfinite(px) or px <= 0:
                    continue
                target = int(w * equity / px // cfg.lot * cfg.lot) if w > 0 else 0
                if target != shares.get(sym, 0):
                    orders[sym] = [target, cfg.order_expiry]
                else:
                    orders.pop(sym, None)

    curve = pd.DataFrame(equity_curve, columns=["date", "equity", "cash"]).set_index("date")
    curve["return"] = curve["equity"].pct_change().fillna(0.0)
    return {"curve": curve, "trades": pd.DataFrame(trades), "cost_paid": cost_paid}
