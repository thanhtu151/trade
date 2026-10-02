"""T116: stop-loss is enforced by the one-shot CI `trade` run, only inside valid sessions."""

import json
from datetime import datetime

import pandas as pd

from test_price_sanity_gate import write_json
from trading_safety import VIETNAM_TZ

IN_SESSION = datetime(2026, 9, 22, 10, 0, tzinfo=VIETNAM_TZ)   # Tue
OUT_OF_SESSION = datetime(2026, 9, 22, 15, 7, tzinfo=VIETNAM_TZ)
STOP = 19_500.0


def _env(monkeypatch, tmp_path, now=IN_SESSION, entry_date="2026-09-15 09:30:00", price=19_400.0,
         bar_date="2026-09-22"):
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
                        lambda *_a, **_k: pd.DataFrame({"time": [pd.Timestamp(bar_date)], "close": [price]}))
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


# --- T118 review fixes -------------------------------------------------------------------

def test_stale_last_bar_does_not_sell(monkeypatch, tmp_path):
    """Latest bar is the previous session's close -> fail closed, no automatic sell."""
    scheduler = _env(monkeypatch, tmp_path, bar_date="2026-09-21")
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" in portfolio["positions"] and not _sells(trades)


def test_blocked_close_does_not_alert_stop_loss(monkeypatch, tmp_path):
    """T+2 blocks the close: the alert must say it was NOT closed, never plain 'STOP LOSS'."""
    scheduler = _env(monkeypatch, tmp_path, entry_date="2026-09-21 09:30:00")
    alerts_file = tmp_path / "intraday_alerts.json"
    monkeypatch.setattr(scheduler, "INTRADAY_ALERTS_FILE", str(alerts_file))
    scheduler.task_intraday_monitor()
    messages = [a["message"] for a in json.loads(alerts_file.read_text())]
    assert any("KHÔNG đóng được" in m and "T+2" in m for m in messages)
    assert not any(m.startswith("🔴") for m in messages)


def test_successful_close_alerts_stop_loss(monkeypatch, tmp_path):
    scheduler = _env(monkeypatch, tmp_path)
    alerts_file = tmp_path / "intraday_alerts.json"
    monkeypatch.setattr(scheduler, "INTRADAY_ALERTS_FILE", str(alerts_file))
    scheduler.task_intraday_monitor()
    messages = [a["message"] for a in json.loads(alerts_file.read_text())]
    assert any(m.startswith("🔴") and "STOP LOSS" in m for m in messages)


def test_no_same_day_rebuy_after_stop_loss():
    from datetime import date
    from auto_trader import stop_loss_cooldown_reason

    trades = [{"type": "TRADE", "side": "SELL", "symbol": "PVD", "reason": "stop_loss_intraday",
               "time": "2026-09-22 09:20:00"}]
    assert stop_loss_cooldown_reason("PVD", trades, date(2026, 9, 22))
    assert stop_loss_cooldown_reason("PVD", trades, date(2026, 9, 23)) is None   # next day ok
    assert stop_loss_cooldown_reason("HPG", trades, date(2026, 9, 22)) is None   # other ticker ok
    target = [dict(trades[0], reason="target_intraday")]
    assert stop_loss_cooldown_reason("PVD", target, date(2026, 9, 22)) is None   # only stop-loss


def test_execute_paper_trade_skips_rebuy_after_stop(monkeypatch, tmp_path):
    import auto_trader
    scheduler = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(auto_trader, "now_text", lambda: "2026-09-22 10:05:00")
    scheduler.task_intraday_monitor()                       # stops out PVD at 10:00 on 2026-09-22
    result = auto_trader.execute_paper_trade("PVD", "BUY", 19_400.0)
    assert result["status"] == "skipped" and "stop-loss" in result["detail"]


# ---- T129: corporate actions must not look like a stop-loss crash ----------

def _bars(rows):
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"]).assign(
        time=lambda d: pd.to_datetime(d["time"]))


def test_history_rebase_factor_detects_pvd_and_vpb():
    import scheduler

    # PVD: bought 33.2 on 2026-07-10; after the 07-14 ex-date the source shows 07-10 as 19.47-19.95.
    pvd = _bars([("2026-07-10", 19.95, 19.95, 19.47, 19.47), ("2026-07-15", 20.15, 20.70, 19.45, 20.45)])
    factor = scheduler._history_rebase_factor({"avg_price": 33.2, "entry_date": "2026-07-10 10:44:22"}, pvd)
    assert round(factor, 2) == 1.66
    # VPB: bought 28.0 on 2026-09-21; after the 09-24 ex-date 09-21 reads 21.90-22.57.
    vpb = _bars([("2026-09-21", 22.10, 22.57, 21.90, 22.57), ("2026-09-24", 22.25, 22.55, 22.10, 22.10)])
    factor = scheduler._history_rebase_factor({"avg_price": 28.0, "entry_date": "2026-09-21 14:32:48"}, vpb)
    assert round(factor, 2) == 1.24


def test_history_rebase_factor_ignores_real_moves_and_unknown_history():
    import scheduler

    # TCB 2026-07: entry 32.0 inside the entry-day range; the later -9.4% slide is a real loss.
    tcb = _bars([("2026-07-15", 32.05, 32.20, 31.50, 31.50), ("2026-07-22", 29.90, 30.30, 29.00, 29.00)])
    pos = {"avg_price": 32.0, "entry_date": "2026-07-15 09:24:07"}
    assert scheduler._history_rebase_factor(pos, tcb) is None
    assert scheduler._history_rebase_factor(pos, tcb[tcb["time"] > "2026-07-20"]) is None  # entry bar not in window
    assert scheduler._history_rebase_factor(pos, pd.DataFrame({"close": [29.0]})) is None
    # The last mark wins over the entry: marked 31.45 on 07-17, history still agrees.
    marked = dict(pos, current_price=31.45, price_mark_date="2026-07-17")
    bars = _bars([("2026-07-17", 31.90, 31.90, 31.45, 31.45), ("2026-07-20", 31.40, 31.40, 29.95, 29.95)])
    assert scheduler._history_rebase_factor(marked, bars) is None


def test_rebased_history_suspends_stop_instead_of_selling(monkeypatch, tmp_path):
    import data_fetcher

    scheduler = _env(monkeypatch, tmp_path)  # PVD avg 19,800, stop 19,500, entry 2026-09-15
    # Ex-date today: the source now shows the entry day at 11,700-12,000 and today at 11,900.
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: _bars([
        ("2026-09-15", 11_900.0, 12_000.0, 11_700.0, 11_880.0),
        ("2026-09-22", 11_900.0, 11_950.0, 11_800.0, 11_900.0)]))
    monkeypatch.setattr(scheduler, "INTRADAY_ALERTS_FILE", str(tmp_path / "intraday_alerts.json"))
    scheduler.task_intraday_monitor()
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert _sells(trades) == []
    pos = portfolio["positions"]["PVD"]
    assert pos["corporate_action_suspected"]["factor"] == 1.65
    assert "current_price" not in pos and pos["stop_loss"] == STOP
    alerts = json.loads((tmp_path / "intraday_alerts.json").read_text(encoding="utf-8"))
    assert len(alerts) == 1 and "sự kiện quyền" in alerts[0]["message"]


def test_eod_does_not_stop_out_on_rebased_history(monkeypatch, tmp_path):
    import data_fetcher
    import scheduler

    # VPB 2026-09-24: marked 28.0 on 09-21, history rebased x1.26 on the ex-date.
    pos = {"qty": 600, "avg_price": 28_000.0, "atr": 730.0, "stop_loss": 27_300.0, "target_price": 29_500.0,
           "hold_days": 0, "entry_date": "2026-09-21 14:32:48"}
    portfolio = {"cash": 0.0, "ledger_epoch": 1, "positions": {"VPB": pos}}
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "already_ran_today", lambda _name: False)
    monkeypatch.setattr(scheduler, "mark_ran_today", lambda _name: None)
    monkeypatch.setattr(scheduler, "run_etf_core", lambda: None)
    monkeypatch.setattr(scheduler, "load_portfolio_direct", lambda: portfolio)
    monkeypatch.setattr(scheduler, "record_eod_equity_snapshot", lambda: None)
    monkeypatch.setattr(scheduler, "_save_portfolio_direct", lambda _p: None)
    closes = []
    monkeypatch.setattr(scheduler, "_close_position_direct", lambda *a, **_k: closes.append(a) or True)
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: _bars([
        ("2026-09-21", 22_100.0, 22_570.0, 21_900.0, 22_570.0),
        ("2026-09-24", 22_250.0, 22_550.0, 22_100.0, 22_100.0)]))
    scheduler.task_eod_update()
    assert closes == []
    assert pos["corporate_action_suspected"]["factor"] == 1.2406 and pos["hold_days"] == 0
