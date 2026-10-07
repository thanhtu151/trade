"""T269: the 15:00 EOD task must be able to book stop/target exits at the official close."""

from datetime import date, datetime

import pandas as pd
import pytest

from test_price_sanity_gate import write_json

TODAY = date(2026, 9, 29)  # Tuesday, trading day


def _at(hh, mm):
    from trading_safety import VIETNAM_TZ
    return datetime(2026, 9, 29, hh, mm, tzinfo=VIETNAM_TZ)


@pytest.mark.parametrize(("hh", "mm", "plain_exit", "eod_close"), [
    (14, 45, True, True),
    (15, 0, False, True),     # the scheduled EOD time: previously blocked
    (16, 30, False, True),    # late CI start still ok
    (16, 31, False, False),
    (12, 0, False, False),    # lunch break never opens, even for EOD
    (8, 59, False, False),
])
def test_eod_close_window(hh, mm, plain_exit, eod_close):
    from trading_safety import market_session_reason

    assert (market_session_reason(_at(hh, mm), exit_order=True) is None) is plain_exit
    assert (market_session_reason(_at(hh, mm), exit_order=True, eod_close=True) is None) is eod_close


def test_eod_close_window_still_respects_weekend_and_kill_switch(monkeypatch, tmp_path):
    from trading_safety import VIETNAM_TZ, market_session_reason, operational_gate

    assert "weekend" in market_session_reason(datetime(2026, 9, 27, 15, 0, tzinfo=VIETNAM_TZ), True, True)
    monkeypatch.setenv("TRADING_ENABLED", "false")
    allowed, reason = operational_gate(tmp_path, _at(15, 0), exit_order=True, eod_close=True)
    assert allowed is False and "TRADING_ENABLED" in reason


def _setup(monkeypatch, tmp_path, now_hhmm):
    import auto_trader
    import scheduler
    import self_healing
    import trading_safety

    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    monkeypatch.setattr(trading_safety, "vietnam_now", lambda: _at(*now_hhmm))
    monkeypatch.setattr(self_healing, "run_self_healing", lambda *a, **k: {"trading_allowed": True})
    monkeypatch.setattr(scheduler, "ict_today", lambda: TODAY)
    monkeypatch.setattr(auto_trader, "fill_price_block_reason", lambda *a, **k: None)
    monkeypatch.setattr(auto_trader, "settlement_block_reason", lambda pos: None)
    write_json(tmp_path / "paper_trades.json", [])
    return scheduler


def _portfolio():
    return {"cash": 50_000_000.0, "positions": {"VPB": {"qty": 1000, "avg_price": 22_000.0, "entry_fees": 22_000.0}}}


def _df(day):
    return pd.DataFrame({"time": [pd.Timestamp(day)], "close": [20_000.0]})


def test_eod_close_after_atc_fills_at_official_close_with_all_costs(monkeypatch, tmp_path):
    scheduler = _setup(monkeypatch, tmp_path, (15, 0))
    portfolio = _portfolio()
    assert scheduler._close_position_direct(portfolio, "VPB", 20_000.0, "stop_loss",
                                            market_df=_df(TODAY), eod_close=True) is True
    assert portfolio["positions"] == {}
    assert portfolio["cash"] == pytest.approx(50_000_000 + 20_000_000 * (1 - 0.002))


def test_same_close_without_eod_flag_is_still_blocked_at_15(monkeypatch, tmp_path):
    scheduler = _setup(monkeypatch, tmp_path, (15, 0))
    portfolio = _portfolio()
    assert scheduler._close_position_direct(portfolio, "VPB", 20_000.0, "stop_loss", market_df=_df(TODAY)) is False
    assert "VPB" in portfolio["positions"]


def test_eod_close_refuses_stale_prior_day_bar(monkeypatch, tmp_path):
    scheduler = _setup(monkeypatch, tmp_path, (15, 0))
    portfolio = _portfolio()
    assert scheduler._close_position_direct(portfolio, "VPB", 20_000.0, "stop_loss",
                                            market_df=_df(date(2026, 9, 28)), eod_close=True) is False
    assert "VPB" in portfolio["positions"]
    assert scheduler._close_position_direct(portfolio, "VPB", 20_000.0, "stop_loss",
                                            market_df=None, eod_close=True) is False


def test_eod_task_passes_eod_close_to_every_exit():
    import inspect
    import scheduler

    src = inspect.getsource(scheduler.task_eod_update)
    assert src.count("_close_position_direct(") == 3 and src.count("eod_close=True") == 3
