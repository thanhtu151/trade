"""T116: stop-loss is enforced by the one-shot CI `trade` run, only inside valid sessions."""

import json
from datetime import datetime

import pandas as pd

from test_price_sanity_gate import write_json
from trading_safety import VIETNAM_TZ

IN_SESSION = datetime(2026, 9, 22, 10, 0, tzinfo=VIETNAM_TZ)   # Tue
OUT_OF_SESSION = datetime(2026, 9, 22, 15, 7, tzinfo=VIETNAM_TZ)
STOP = 19_500.0


def _env(monkeypatch, tmp_path, now=IN_SESSION, entry_date="2026-09-15 09:30:00", price=19_400.0):
    import auto_trader
    import scheduler
    import self_healing
    import trading_safety

    write_json(tmp_path / "paper_portfolio.json", {
        "initial_cash": 100_000_000.0, "cash": 80_000_000.0, "updated_at": "2026-09-21T15:00:00",
        "positions": {"PVD": {"qty": 1000, "avg_price": 19_800.0, "stop_loss": STOP, "entry_date": entry_date}}})
    write_json(tmp_path / "paper_trades.json", [])
    monkeypatch.setenv("TRADING_ENABLED", "true")
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "ict_now", lambda: now)
    monkeypatch.setattr(trading_safety, "vietnam_now", lambda: now)
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 19_700.0, "day_low": 19_000.0, "day_high": 19_950.0})
    # Gates under test (kill switch, session, T+2) stay real; only the ledger audit is stubbed.
    monkeypatch.setattr(self_healing, "run_self_healing", lambda *_a, **_k: {
        "trading_allowed": True, "status": "ok", "critical": [], "inhibitors": []})
    import data_fetcher
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached",
                        lambda *_a, **_k: pd.DataFrame({"close": [price]}))
    import notify
    monkeypatch.setattr(notify, "notify_trade", lambda *_a, **_k: None)
    return scheduler


def _state(tmp_path):
    portfolio = json.loads((tmp_path / "paper_portfolio.json").read_text())
    trades = json.loads((tmp_path / "paper_trades.json").read_text())
    return portfolio, trades


def _sells(trades):
    return [t for t in trades if t.get("type") == "TRADE" and t.get("side") == "SELL"]


def test_stop_breached_in_session_creates_paper_sell(monkeypatch, tmp_path):
    scheduler = _env(monkeypatch, tmp_path)
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" not in portfolio["positions"]
    assert len(_sells(trades)) == 1 and trades[-1]["reason"] == "stop_loss_intraday"


def test_outside_session_does_not_sell(monkeypatch, tmp_path):
    scheduler = _env(monkeypatch, tmp_path, now=OUT_OF_SESSION)
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" in portfolio["positions"] and not _sells(trades)


def test_kill_switch_blocks_sell(monkeypatch, tmp_path):
    scheduler = _env(monkeypatch, tmp_path)
    write_json(tmp_path / "trading_disabled.json", {"disabled": True, "reason": "pytest"})
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" in portfolio["positions"] and not _sells(trades)


def test_unsettled_position_is_not_sold(monkeypatch, tmp_path):
    scheduler = _env(monkeypatch, tmp_path, entry_date="2026-09-21 09:30:00")  # bought T+1
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" in portfolio["positions"] and not _sells(trades)


def test_second_run_does_not_sell_twice(monkeypatch, tmp_path):
    scheduler = _env(monkeypatch, tmp_path)
    scheduler.task_intraday_monitor()
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" not in portfolio["positions"] and len(_sells(trades)) == 1


def test_trade_task_runs_stop_check_before_buys(monkeypatch, tmp_path):
    """The CI `trade` entry point (task_auto_trade) must trigger the stop check."""
    scheduler = _env(monkeypatch, tmp_path)
    import watchdog
    monkeypatch.setattr(scheduler, "already_ran_today", lambda _k: False)
    monkeypatch.setattr(scheduler, "mark_ran_today", lambda _k: None)
    monkeypatch.setattr(watchdog, "trade_session_open", lambda _n: True)
    write_json(tmp_path / "analysis_results.json", {"date": "2026-09-22", "tradeable": []})
    monkeypatch.setattr(scheduler, "ANALYSIS_RESULTS_FILE", str(tmp_path / "analysis_results.json"))
    scheduler.task_auto_trade()
    portfolio, trades = _state(tmp_path)
    assert "PVD" not in portfolio["positions"] and len(_sells(trades)) == 1
