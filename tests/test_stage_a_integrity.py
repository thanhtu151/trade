"""Stage (a)-1: price gate on fills, T+2 settlement, holiday session gate, fail-closed portfolio read."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from test_price_sanity_gate import _buy_environment, write_json


# ---- 1. price gate: fill inside the day range, whole lots, fees + tax on every order ----

def test_buy_outside_day_range_is_blocked_and_inside_fill_is_lot_sized_with_fee(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=22_700.0)  # above day high 22,570
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=22.7, signal_id="hi", trade_date="2026-09-21")
    assert outcome["status"] == "blocked" and "outside today's range" in outcome["detail"]

    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=22_400.0)
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=22.4, signal_id="ok", trade_date="2026-09-21")
    assert outcome["status"] == "executed", outcome
    event = json.loads((tmp_path / "paper_trades.json").read_text())[-1]
    assert event["qty"] % 100 == 0 and 21_900 <= event["price"] <= 22_570
    assert event["fees"] == pytest.approx(event["qty"] * event["price"] * auto_trader.PAPER_FEE_RATE)


def test_sell_records_fee_and_tax(monkeypatch, tmp_path):
    import auto_trader
    import self_healing

    write_json(tmp_path / "paper_portfolio.json", {"initial_cash": 100_000_000.0, "cash": 80_000_000.0,
               "positions": {"PVD": {"qty": 1000, "avg_price": 19_800.0, "entry_date": "2026-09-01 10:00:00"}}})
    write_json(tmp_path / "paper_trades.json", [])
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (True, "ok"))
    monkeypatch.setattr(auto_trader, "current_price", lambda _s: 19_900.0)
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 19_700.0, "day_low": 19_470.0, "day_high": 19_950.0})
    ok, message = auto_trader.sell_position("PVD")
    assert ok is True, message
    event = json.loads((tmp_path / "paper_trades.json").read_text())[-1]
    proceeds = 1000 * 19_900.0
    assert event["fees"] == pytest.approx(proceeds * (auto_trader.PAPER_FEE_RATE + auto_trader.PAPER_SELL_TAX_RATE))


# ---- 2. T+2 ------------------------------------------------------------------

def test_add_trading_days_skips_weekends_and_holidays():
    from trading_calendar import add_trading_days

    assert add_trading_days(date(2026, 9, 21), 2) == date(2026, 9, 23)   # Mon -> Wed
    assert add_trading_days(date(2026, 9, 25), 2) == date(2026, 9, 29)   # Fri -> Tue
    assert add_trading_days(date(2026, 8, 28), 2) == date(2026, 9, 4)    # 31/8, 1/9, 2/9 are holidays


@pytest.mark.parametrize("today,blocked", [
    (date(2026, 9, 21), True),    # T
    (date(2026, 9, 22), True),    # T+1
    (date(2026, 9, 23), False),   # T+2
])
def test_settlement_block_reason(today, blocked):
    import auto_trader

    reason = auto_trader.settlement_block_reason({"entry_date": "2026-09-21 09:30:00"}, today=today)
    assert (reason is not None) is blocked
    if blocked:
        assert "T+2" in reason and "2026-09-23" in reason


def _sell_environment(monkeypatch, tmp_path, entry_date):
    import auto_trader
    import self_healing

    write_json(tmp_path / "paper_portfolio.json", {"initial_cash": 100_000_000.0, "cash": 80_000_000.0,
               "positions": {"PVD": {"qty": 1000, "avg_price": 19_800.0, "entry_date": entry_date}}})
    write_json(tmp_path / "paper_trades.json", [])
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (True, "ok"))
    monkeypatch.setattr(self_healing, "trading_is_allowed", lambda _base, **_kw: True)
    monkeypatch.setattr(auto_trader, "current_price", lambda _s: 19_900.0)
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 19_700.0, "day_low": 19_470.0, "day_high": 19_950.0})
    import trading_safety
    monkeypatch.setattr(trading_safety, "vietnam_now", lambda: datetime(2026, 9, 22, 10, 0))
    return auto_trader


def test_sell_position_refuses_unsettled_shares_and_keeps_state(monkeypatch, tmp_path):
    auto_trader = _sell_environment(monkeypatch, tmp_path, "2026-09-21 09:30:00")  # today T+1
    ok, message = auto_trader.sell_position("PVD")
    assert ok is False and "T+2" in message
    assert json.loads((tmp_path / "paper_portfolio.json").read_text())["positions"]["PVD"]["qty"] == 1000
    assert json.loads((tmp_path / "paper_trades.json").read_text()) == []


def test_sell_position_allows_settled_shares(monkeypatch, tmp_path):
    auto_trader = _sell_environment(monkeypatch, tmp_path, "2026-09-18 09:30:00")  # Fri -> T+2 Tue 22nd
    ok, message = auto_trader.sell_position("PVD")
    assert ok is True, message
    assert "PVD" not in json.loads((tmp_path / "paper_portfolio.json").read_text())["positions"]


def test_direct_close_refuses_unsettled_shares(monkeypatch, tmp_path):
    import scheduler

    _sell_environment(monkeypatch, tmp_path, "2026-09-21 09:30:00")
    portfolio = {"cash": 0.0, "positions": {"PVD": {"qty": 1000, "avg_price": 19_800.0,
                                                    "entry_date": "2026-09-21 09:30:00"}}}
    assert scheduler._close_position_direct(portfolio, "PVD", 19_900.0, "stop_loss") is False
    assert "PVD" in portfolio["positions"]


# ---- 3. holidays ---------------------------------------------------------------

def test_session_gate_blocks_exchange_holidays_but_not_normal_weekdays():
    from trading_safety import market_session_reason

    holiday = datetime(2026, 9, 2, 10, 0)   # Wed, National Day
    assert "holiday" in market_session_reason(holiday)
    assert "holiday" in market_session_reason(holiday, exit_order=True)
    assert market_session_reason(datetime(2026, 9, 3, 10, 0)) is None


def test_operational_gate_blocks_on_holiday(tmp_path, monkeypatch):
    from trading_safety import operational_gate

    monkeypatch.setenv("TRADING_ENABLED", "true")
    allowed, reason = operational_gate(tmp_path, now=datetime(2026, 4, 30, 10, 0))
    assert allowed is False and "holiday" in reason


# ---- 4. fail-closed portfolio read ---------------------------------------------

def test_load_portfolio_direct_raises_when_read_fails(monkeypatch):
    import auto_trader
    import scheduler

    def boom():
        raise OSError("disk error")

    monkeypatch.setattr(auto_trader, "_safe_read_portfolio", boom)
    with pytest.raises(scheduler.PortfolioUnavailable):
        scheduler.load_portfolio_direct()


def test_load_portfolio_direct_rejects_corrupt_default(monkeypatch):
    import auto_trader
    import scheduler

    monkeypatch.setattr(auto_trader, "_safe_read_portfolio",
                        lambda: {"cash": 0.0, "positions": {}, "updated_at": "unknown"})
    with pytest.raises(scheduler.PortfolioUnavailable):
        scheduler.load_portfolio_direct()


def test_load_portfolio_direct_returns_valid_portfolio(monkeypatch):
    import auto_trader
    import scheduler

    good = {"cash": 5.0, "positions": {"VCB": {"qty": 100}}, "updated_at": "2026-09-22 10:00:00"}
    monkeypatch.setattr(auto_trader, "_safe_read_portfolio", lambda: good)
    assert scheduler.load_portfolio_direct() == good


def test_intraday_monitor_does_not_trade_when_portfolio_unreadable(monkeypatch):
    import scheduler

    monkeypatch.setattr(scheduler, "is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "ict_now", lambda: datetime(2026, 9, 22, 10, 0))
    monkeypatch.setattr(scheduler, "load_portfolio_direct",
                        lambda: (_ for _ in ()).throw(scheduler.PortfolioUnavailable("corrupt")))
    closed = []
    monkeypatch.setattr(scheduler, "_close_position_direct", lambda *a, **k: closed.append(a) or True)
    scheduler.task_intraday_monitor()
    assert closed == []
