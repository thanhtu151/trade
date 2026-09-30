"""Broker interface shared by PaperBroker and (later) TCBSBroker."""

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str                       # "BUY" | "SELL"
    qty: int
    trade_date: date
    ref_price: float                # reference (previous close) in VND; band = ref +/- 7%
    market_price: float             # price the order would execute at (e.g. ATO open), VND
    limit_price: Optional[float] = None
    client_id: str = ""             # idempotency key


@dataclass(frozen=True)
class Fill:
    client_id: str
    status: str                     # "FILLED" | "REJECTED" | "DUPLICATE"
    symbol: str
    side: str
    qty: int = 0
    price: float = 0.0
    fee: float = 0.0
    slippage_pct: float = 0.0       # adverse move vs ref_price, in percent
    reason: str = ""


class Broker:
    def get_cash(self) -> float:
        raise NotImplementedError

    def get_positions(self, today: Optional[date] = None) -> dict:
        raise NotImplementedError

    def place_order(self, order: Order) -> Fill:
        raise NotImplementedError

    def cancel(self, client_id: str) -> bool:
        raise NotImplementedError
