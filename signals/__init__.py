"""Pluggable ETF-core signals. Each module exposes a class with `name` and `target(asof, data)`."""

from signals.base import Target, Signal
from signals.e1_ma10m import E1Ma10Month

# E6 (MA200 daily + 60% breadth) will be registered here and run in monitor-only mode
# alongside E1; it is intentionally not implemented yet.
REGISTRY = {E1Ma10Month.name: E1Ma10Month}

__all__ = ["Target", "Signal", "E1Ma10Month", "REGISTRY"]
