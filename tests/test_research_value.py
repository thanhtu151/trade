import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.engine import Panel  # noqa: E402
from research.fundamentals import earnings_yield_panel  # noqa: E402
from research.strategies import UniverseRule, Value, month_end_sessions  # noqa: E402


def flat_panel(symbols=("AAA",), start="2019-01-01", periods=400, price=10.0):
    dates = pd.bdate_range(start, periods=periods)
    rows = [{"time": d, "symbol": s, "open": price, "high": price, "low": price, "close": price,
             "volume": 100_000} for s in symbols for d in dates]
    return Panel(pd.DataFrame(rows))


def quarters(symbol, net_incomes, public_lag_days=45, first="2018-03-31"):
    ends = pd.date_range(first, periods=len(net_incomes), freq="QE")
    return pd.DataFrame({"symbol": symbol, "period_end": ends,
                         "public_date": ends + pd.Timedelta(days=public_lag_days),
                         "net_income": net_incomes})


def test_ttm_earnings_only_after_public_date():
    panel = flat_panel(start="2018-01-01", periods=600)
    earnings = quarters("AAA", [100.0] * 8)
    caps = pd.DataFrame({"symbol": "AAA", "period_end": pd.date_range("2018-03-31", periods=8, freq="QE"),
                         "market_cap": 4000.0, "shares": 400.0})
    ep, shares = earnings_yield_panel(panel, earnings, caps)
    fourth_public = earnings["public_date"].iloc[3]  # 2018-Q4 published 2019-02-14
    assert np.isnan(ep["AAA"].loc[:fourth_public - pd.Timedelta(days=1)]).all()
    assert ep["AAA"].loc[fourth_public] == pytest.approx(400 / 4000)
    assert shares["AAA"].iloc[-1] == 400


def test_ttm_requires_consecutive_quarters():
    panel = flat_panel(start="2018-01-01", periods=600)
    earnings = quarters("AAA", [100.0] * 8).drop(index=4)  # 2019-Q1 missing
    caps = pd.DataFrame({"symbol": "AAA", "period_end": pd.date_range("2018-03-31", periods=8, freq="QE"),
                         "market_cap": 4000.0, "shares": 400.0})
    ep, _ = earnings_yield_panel(panel, earnings, caps)
    # 2018-Q3..2019-Q2 is not consecutive, so no new TTM appears: the 2018-Q4 TTM
    # stays in use until it is 200 days old, then E/P is missing.
    assert ep["AAA"].loc["2019-06-03"] == pytest.approx(400 / 4000)
    q2_public = earnings[earnings["period_end"] == pd.Timestamp("2019-06-30")]["public_date"].iloc[0]
    assert np.isnan(ep["AAA"].loc[q2_public])


def test_market_cap_follows_price_between_quarters():
    dates = pd.bdate_range("2019-01-01", periods=300)
    close = np.where(dates < pd.Timestamp("2019-06-03"), 10.0, 20.0)
    prices = pd.DataFrame({"time": dates, "symbol": "AAA", "open": close, "high": close, "low": close,
                           "close": close, "volume": 100_000})
    panel = Panel(prices)
    earnings = quarters("AAA", [100.0] * 6)
    caps = pd.DataFrame({"symbol": "AAA", "period_end": pd.date_range("2018-03-31", periods=6, freq="QE"),
                         "market_cap": 4000.0, "shares": 400.0})
    ep, _ = earnings_yield_panel(panel, earnings, caps)
    # 2019-Q1 cap 4000 at price 10; price doubles in June before the Q2 cap -> cap 8000.
    assert ep["AAA"].loc["2019-06-10"] == pytest.approx(400 / 8000)


def test_value_ranks_cheapest_profitable_names():
    symbols = ("CHEAP", "MID", "RICH", "LOSS")
    panel = flat_panel(symbols, periods=600)
    ep = pd.DataFrame({"CHEAP": 0.20, "MID": 0.10, "RICH": 0.05, "LOSS": -0.10}, index=panel.dates)
    shares = pd.DataFrame(1_000_000.0, index=panel.dates, columns=list(symbols))
    strat = Value(panel, ep, shares, mode="ep", hold=2, keep_rank=3,
                  rule=UniverseRule(top_n=10, min_history=100, price_quantile_floor=0))
    i = max(month_end_sessions(panel.dates))
    assert strat.ranked(i)[:3] == ["CHEAP", "MID", "RICH"]
    assert "LOSS" not in strat.eligible(i)
    assert set(strat(panel, i, {})) == {"CHEAP", "MID"}


def test_stale_earnings_expire_and_zero_caps_are_missing():
    panel = flat_panel(start="2018-01-01", periods=700)
    earnings = quarters("AAA", [100.0] * 4)  # reporting stops after 2018-Q4
    caps = pd.DataFrame({"symbol": "AAA", "period_end": pd.date_range("2018-03-31", periods=8, freq="QE"),
                         "market_cap": [4000.0] * 7 + [0.0], "shares": 400.0})
    ep, _ = earnings_yield_panel(panel, earnings, caps, max_age_days=200)
    assert ep["AAA"].loc["2019-03-01"] == pytest.approx(0.1)
    assert np.isnan(ep["AAA"].loc["2019-08-01"])  # 2018-Q4 ended > 200 days earlier
    assert np.isfinite(ep["AAA"].dropna()).all()
