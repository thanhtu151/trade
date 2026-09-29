import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import etf_core  # noqa: E402

ICT = timezone(timedelta(hours=7))


def weekday(day):
    return day.weekday() < 5


def bars(closes, start="2025-01-01", opens=None):
    dates = pd.bdate_range(start, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"time": dates, "open": closes if opens is None else opens, "high": closes,
                         "low": closes, "close": closes, "volume": 1e6})


def at(day, hour=15, minute=30):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ICT)


def run(state, etf, sig, now):
    return etf_core.process(state, etf, sig, now, weekday)


def rising(n=320):
    return np.linspace(1000, 2000, n)


def test_first_run_starts_at_latest_session_without_backfill():
    sig = bars(rising())
    etf = bars(np.full(320, 30_000.0))
    state = etf_core.default_state()
    last_day = sig["time"].iloc[-1]
    run(state, etf, sig, at(last_day))
    assert state["last_session"] == last_day.date().isoformat()
    assert len(state["equity"]) == 1 and state["trades"] == []


def test_month_end_signal_fills_next_open_with_costs_and_lots():
    sig = bars(rising())
    etf_open = np.full(320, 30_000.0)
    etf = bars(np.full(320, 30_000.0), opens=etf_open)
    dates = pd.bdate_range("2025-01-01", periods=320)
    month_end = max(d for d in dates if d.month == 12 and d.year == 2025)
    state = etf_core.default_state()
    state["last_session"] = (month_end - pd.Timedelta(days=1)).date().isoformat()
    events = run(state, etf, sig, at(dates[-1]))
    signal = [e for e in events if e["type"] == "signal"][0]
    fill = [e for e in events if e["type"] == "fill"][0]
    assert signal["session"] == month_end.date().isoformat() and signal["signal"] == "ON"
    assert fill["session"] == dates[dates.get_loc(month_end) + 1].date().isoformat()
    assert fill["units"] % 100 == 0 and fill["price"] == pytest.approx(30_000 * 1.0015)
    assert state["cash"] >= 0 and state["cash"] < 30_000 * 1.0015 * 100 * 1.01


def test_replay_is_idempotent():
    sig = bars(rising())
    etf = bars(np.full(320, 30_000.0))
    dates = pd.bdate_range("2025-01-01", periods=320)
    state = etf_core.default_state()
    state["last_session"] = dates[200].date().isoformat()
    run(state, etf, sig, at(dates[-1]))
    snapshot = (state["cash"], state["units"], len(state["trades"]), len(state["equity"]))
    assert run(state, etf, sig, at(dates[-1])) == []
    assert (state["cash"], state["units"], len(state["trades"]), len(state["equity"])) == snapshot


def test_partial_bar_before_close_is_ignored():
    sig = bars(rising())
    etf = bars(np.full(320, 30_000.0))
    today = sig["time"].iloc[-1]
    state = etf_core.default_state()
    state["last_session"] = sig["time"].iloc[-3].date().isoformat()
    run(state, etf, sig, at(today, 11, 0))
    assert state["last_session"] == sig["time"].iloc[-2].date().isoformat()


def test_signal_turns_off_and_sells_everything():
    closes = np.r_[np.linspace(1000, 2000, 260), np.linspace(2000, 900, 200)]
    sig = bars(closes)
    etf = bars(np.full(len(closes), 30_000.0))
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    state = etf_core.default_state()
    state["last_session"] = dates[230].date().isoformat()
    run(state, etf, sig, at(dates[-1]))
    sides = [t["side"] for t in state["trades"]]
    assert sides[0] == "BUY" and sides[-1] == "SELL" and state["units"] == 0
    assert state["signal"] == "OFF"


def test_month_end_uses_calendar_for_latest_session():
    session = pd.Timestamp("2026-09-30")
    assert etf_core.is_month_end(session, [session], weekday)
    assert not etf_core.is_month_end(pd.Timestamp("2026-09-28"), [pd.Timestamp("2026-09-28")], weekday)


def test_trend_rule_matches_backtest_definition():
    closes = pd.Series(np.linspace(1000, 2000, 320), index=pd.bdate_range("2025-01-01", periods=320))
    on, month_close, average = etf_core.trend_on(closes, closes.index[-1])
    month_ends = closes.groupby([closes.index.year, closes.index.month]).tail(1).iloc[-10:]
    assert on and month_close == month_ends.iloc[-1] and average == pytest.approx(month_ends.mean())


def test_run_daily_persists_state(tmp_path):
    sig = bars(rising())
    etf = bars(np.full(320, 30_000.0))
    fetch = lambda symbol, years: etf if symbol == etf_core.ETF else sig  # noqa: E731
    info = etf_core.run_daily(base_dir=tmp_path, now=at(sig["time"].iloc[-1]), fetch=fetch,
                              is_trading_day=weekday, notify=False)
    assert info["equity"] == etf_core.INITIAL_CASH
    assert etf_core.load_state(tmp_path)["last_session"] == sig["time"].iloc[-1].date().isoformat()
