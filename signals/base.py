"""Signal interface: a signal only says how much of the ETF to hold, never how to trade it."""

from dataclasses import dataclass, field
from typing import Optional, Protocol

import pandas as pd


@dataclass(frozen=True)
class Target:
    weight: float                 # 1.0 = fully in the ETF, 0.0 = cash
    reason: str
    asof: Optional[str] = None
    details: dict = field(default_factory=dict)


class Signal(Protocol):
    name: str

    def target(self, asof: pd.Timestamp, data: dict) -> Target:
        """`data` maps series name -> pd.Series of daily closes indexed by date."""
