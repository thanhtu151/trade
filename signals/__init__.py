"""Pluggable ETF-core signals. Each module exposes a class with `name` and `target(asof, data)`."""

from signals.base import Target, Signal
from signals.e1_ma10m import E1Ma10Month
from signals.e6_ma200_breadth import E6Ma200Breadth

REGISTRY = {E1Ma10Month.name: E1Ma10Month, E6Ma200Breadth.name: E6Ma200Breadth}

__all__ = ["Target", "Signal", "E1Ma10Month", "E6Ma200Breadth", "REGISTRY"]
