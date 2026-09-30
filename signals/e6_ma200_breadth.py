"""E6 (MONITOR ONLY): VN30 > 200-day MA and breadth >= 60%.

Breadth = share of the top-50 most liquid stocks (mean traded value over the last 60 sessions
as of `asof`) whose close is above their own 200-day MA. Same rule as research/t47 (`signals`),
except the universe is ranked at `asof` rather than at the prior month-end.

Monitor-only: the runner records this signal but never turns it into an order.

`data` keys: "VN30" (Series of closes), "stocks_close" and "stocks_value" (DataFrames,
date x symbol, value = close * volume).
"""

import pandas as pd

from signals.base import Target


class E6Ma200Breadth:
    name = "E6_ma200d_b60"
    cadence = "daily"
    monitor_only = True

    def __init__(self, ma=200, breadth_min=0.60, top=50, value_window=60):
        self.ma, self.breadth_min, self.top, self.value_window = ma, breadth_min, top, value_window

    def target(self, asof, data):
        asof = pd.Timestamp(asof).normalize()
        vn30 = data["VN30"]
        vn30 = vn30[vn30.index <= asof].dropna()
        if len(vn30) < self.ma:
            return Target(0.0, "insufficient_history", asof.date().isoformat(), {"vn30_sessions": len(vn30)})
        vn30_close, vn30_ma = float(vn30.iloc[-1]), float(vn30.iloc[-self.ma:].mean())
        breadth, n = self._breadth(asof, data["stocks_close"], data["stocks_value"])
        if breadth is None:
            return Target(0.0, "no_breadth_universe", asof.date().isoformat(),
                          {"vn30": vn30_close, "ma200": round(vn30_ma, 2), "universe": n})
        on = vn30_close > vn30_ma and breadth >= self.breadth_min
        return Target(1.0 if on else 0.0, "on" if on else "off", asof.date().isoformat(),
                      {"vn30": vn30_close, "ma200": round(vn30_ma, 2), "vn30_above": vn30_close > vn30_ma,
                       "breadth_pct": round(breadth * 100, 1), "universe": n})

    def _breadth(self, asof, close, value):
        close, value = close[close.index <= asof], value[value.index <= asof]
        eligible = [s for s in close.columns if close[s].count() >= self.ma and close[s].iloc[-20:].notna().any()]
        if not eligible:
            return None, 0
        tv = value[eligible].fillna(0).iloc[-self.value_window:].mean()
        chosen = tv[tv > 0].sort_values(ascending=False).index[: self.top]
        if len(chosen) == 0:
            return None, 0
        above = 0
        for s in chosen:
            c = close[s].dropna()
            above += bool(c.iloc[-1] > c.iloc[-self.ma:].mean())
        return above / len(chosen), len(chosen)
