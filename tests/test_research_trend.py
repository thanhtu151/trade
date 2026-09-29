import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.engine import Panel  # noqa: E402
from research.run_trend import Hold, Trend, run  # noqa: E402


def index_panel(closes):
    dates = pd.bdate_range("2015-01-01", periods=len(closes))
    return Panel(pd.DataFrame({"time": dates, "symbol": "VN30", "open": closes, "high": closes,
                               "low": closes, "close": closes, "volume": 1e8}))


def test_daily_trend_exits_after_crossing_below_average():
    closes = np.r_[np.linspace(500, 1000, 400), np.linspace(1000, 500, 300)]
    panel = index_panel(closes)
    daily, switches, trades = run(panel, Trend(panel, rule="daily", window=200), None, None)
    sides = trades["side"].tolist()
    assert sides == ["BUY", "SELL"]
    exit_day = panel.dates.get_loc(trades["date"].iloc[1])
    assert 400 < exit_day < 520  # after the peak, once price falls through the 200-day mean
    hold, _, _ = run(panel, Hold(), None, None)
    assert (1 + daily).prod() > (1 + hold).prod()  # sidestepped most of the decline


def test_monthly_trend_only_acts_at_month_end():
    closes = np.r_[np.linspace(500, 1000, 400), np.linspace(1000, 500, 300)]
    panel = index_panel(closes)
    _, _, trades = run(panel, Trend(panel, rule="monthly", window=10), None, None)
    for d in trades["date"]:
        prev = panel.dates[panel.dates.get_loc(d) - 1]  # signal session
        nxt = panel.dates[panel.dates.get_loc(prev) + 1]
        assert prev.month != nxt.month or prev == panel.dates[-1]


def test_expense_ratio_charged_only_while_invested():
    panel = index_panel(np.full(300, 800.0))
    hold, _, _ = run(panel, Hold(), None, None)
    assert (1 + hold).prod() - 1 < -0.004  # ~0.65%/year * 1.2 years plus entry costs
