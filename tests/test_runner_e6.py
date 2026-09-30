import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from broker import PaperBroker
from runner import buy_qty, default_state, is_month_end, run_session
from signals import E6Ma200Breadth, Target

ETF = "E1VFVN30"


def weekday_add(day, n):
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


class Stub:
    def __init__(self, name, weight, cadence="month_end", monitor_only=False):
        self.name, self.weight, self.cadence, self.monitor_only = name, weight, cadence, monitor_only

    def target(self, asof, data):
        return Target(self.weight, "stub", asof.date().isoformat())


def bars(open_=30000, ref=30000, close=30000):
    return {"ref": ref, "open": open_, "close": close}


def session(day, broker, state, primary, monitors=(), month_end=False, b=None, journal=None):
    return run_session(day, ETF, b or bars(), {}, primary, list(monitors), broker, state, month_end,
                       signal_journal=journal)


def test_is_month_end():
    td = lambda d: d.weekday() < 5
    assert is_month_end(date(2026, 4, 30), td) and is_month_end(date(2026, 5, 29), td)   # Fri 29/5, then weekend
    assert not is_month_end(date(2026, 5, 28), td)


def test_buy_qty_is_lot_sized_and_affordable():
    q = buy_qty(100_000_000, ETF, 30000, 30000)
    assert q % 100 == 0 and q * 30030 * 1.00125 <= 100_000_000 < (q + 100) * 30030 * 1.00125


def test_month_end_on_then_buys_next_open_and_mid_month_does_nothing():
    b, st = PaperBroker(100_000_000, add_trading_days=weekday_add), default_state()
    on = Stub("E1", 1.0)
    r = session(date(2026, 3, 30), b, st, on, month_end=False)
    assert r["orders"] == [] and st["pending"] is None                 # mid-month: no decision
    r = session(date(2026, 3, 31), b, st, on, month_end=True)
    assert r["orders"] == [] and st["pending"]["side"] == "BUY"        # decided at close, not filled yet
    r = session(date(2026, 4, 1), b, st, on, b=bars(open_=30100))
    assert r["orders"][0].status == "FILLED" and st["pending"] is None
    assert b.get_positions()["E1VFVN30"]["qty"] >= 3000
    assert r["nav"] < 100_000_000                                      # costs paid


def test_switch_to_cash_waits_for_t_plus_2_then_sells():
    b, st = PaperBroker(100_000_000, add_trading_days=weekday_add), default_state()
    session(date(2026, 3, 31), b, st, Stub("E1", 1.0), month_end=True)
    off = Stub("E1", 0.0)
    session(date(2026, 4, 1), b, st, off, month_end=True)              # bought Wed 1/4 open; OFF decided at close
    assert st["pending"]["side"] == "SELL"
    r = session(date(2026, 4, 2), b, st, off)                          # Thu: only T+1 -> not sellable
    assert r["orders"][0].status == "REJECTED" and "T+2" in r["orders"][0].reason
    assert st["pending"]["side"] == "SELL" and b.get_positions()
    r = session(date(2026, 4, 3), b, st, off)                          # Fri: settled (T+2)
    assert r["orders"][0].status == "FILLED" and b.get_positions() == {} and st["pending"] is None
    assert b.get_cash() > 99_000_000


def test_monitor_only_signal_never_orders_and_is_journaled(tmp_path):
    b, st, j = PaperBroker(100_000_000, add_trading_days=weekday_add), default_state(), tmp_path / "s.jsonl"
    r = session(date(2026, 3, 31), b, st, Stub("E1", 0.0), [Stub("E6", 1.0, "daily", True)], True, journal=j)
    assert st["pending"] is None and r["orders"] == [] and b.get_positions() == {}
    rows = [json.loads(x) for x in j.read_text().splitlines()]
    assert {x["signal"]: x["monitor_only"] for x in rows} == {"E1": False, "E6": True}


def test_same_day_rerun_is_skipped():
    b, st = PaperBroker(100_000_000, add_trading_days=weekday_add), default_state()
    session(date(2026, 3, 31), b, st, Stub("E1", 1.0), month_end=True)
    assert session(date(2026, 3, 31), b, st, Stub("E1", 0.0), month_end=True)["skipped"] == "already_processed"
    assert st["pending"]["side"] == "BUY"


# ---- E6 -------------------------------------------------------------------
def _e6_data(n_up, n_down, vn30_trend=1.0, days=260):
    idx = pd.bdate_range("2025-01-01", periods=days)
    vn30 = pd.Series(1000 + vn30_trend * np.arange(days), index=idx)
    close, value = {}, {}
    for i in range(n_up + n_down):
        slope = 1.0 if i < n_up else -0.5
        close[f"S{i}"] = pd.Series(100 + slope * np.arange(days) + 200, index=idx)
        value[f"S{i}"] = pd.Series(1e9 - i * 1e6, index=idx)            # earlier symbols are more liquid
    return {"VN30": vn30, "stocks_close": pd.DataFrame(close), "stocks_value": pd.DataFrame(value)}, idx[-1]


def test_e6_on_when_vn30_above_ma_and_breadth_at_least_60():
    data, asof = _e6_data(35, 15)
    t = E6Ma200Breadth().target(asof, data)
    assert t.weight == 1.0 and t.details["breadth_pct"] == 70.0 and t.details["universe"] == 50


def test_e6_off_when_breadth_low_or_vn30_below_ma_or_short_history():
    data, asof = _e6_data(20, 30)                                       # 40% breadth
    t = E6Ma200Breadth().target(asof, data)
    assert t.weight == 0.0 and t.details["breadth_pct"] == 40.0
    data, asof = _e6_data(35, 15, vn30_trend=-1.0)                      # VN30 below its MA200
    t = E6Ma200Breadth().target(asof, data)
    assert t.weight == 0.0 and t.details["vn30_above"] is False
    data, asof = _e6_data(35, 15, days=150)
    assert E6Ma200Breadth().target(asof, data).reason == "insufficient_history"


def test_e6_breadth_uses_only_top_liquid_and_exact_60_boundary():
    data, asof = _e6_data(30, 20)                                       # exactly 60% of top 50
    assert E6Ma200Breadth().target(asof, data).weight == 1.0
    data, asof = _e6_data(29, 21)
    assert E6Ma200Breadth().target(asof, data).weight == 0.0
    # 10 extra illiquid stocks above the MA must not change the top-50 breadth
    d, asof = _e6_data(29, 21)
    idx = d["stocks_close"].index
    for i in range(10):
        d["stocks_close"][f"X{i}"] = pd.Series(300 + np.arange(len(idx)), index=idx)
        d["stocks_value"][f"X{i}"] = pd.Series(1.0, index=idx)
    assert E6Ma200Breadth().target(asof, d).weight == 0.0
