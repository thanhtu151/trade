"""E1: hold the ETF while the VN30 month-end close is >= the mean of its last 10 month-end closes.

Same rule as etf_core.trend_on, packaged as a pluggable signal. Fail-safe: with fewer than
10 month-ends of history the target is cash.
"""

import pandas as pd

from signals.base import Target


class E1Ma10Month:
    name = "E1_ma10m"
    series = "VN30"      # key in `data`; may be pointed at the ETF's own closes instead
    months = 10

    def __init__(self, series=None, months=None):
        self.series = series or self.series
        self.months = months or self.months

    def target(self, asof, data):
        asof = pd.Timestamp(asof).normalize()
        closes = data[self.series]
        closes = closes[closes.index <= asof].dropna()
        month_ends = closes.groupby([closes.index.year, closes.index.month]).tail(1)
        if len(month_ends) < self.months:
            return Target(0.0, "insufficient_history", asof.date().isoformat(),
                          {"month_ends": len(month_ends)})
        window = month_ends.iloc[-self.months:]
        last, mean = float(window.iloc[-1]), float(window.mean())
        on = last >= mean
        return Target(1.0 if on else 0.0, "close>=ma10m" if on else "close<ma10m",
                      asof.date().isoformat(), {"close": last, "ma": round(mean, 2)})
