"""T9: price-sanity gate, whole-share qty in VND, paper fees, one stop-loss source, ATC exits."""

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def _buy_environment(monkeypatch, tmp_path, live_price=22_300.0, reference=None, files=False):
    import auto_trader
    import data_fetcher
    import self_healing

    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (True, "ok"))
    monkeypatch.setattr(auto_trader, "current_price", lambda _ticker: live_price)
    monkeypatch.setattr(
        auto_trader, "get_kelly_position_size",
        lambda *_args: {"value": 10_000_000, "kelly_fraction": 0.1, "pct_portfolio": 10},
    )
    monkeypatch.setattr(
        data_fetcher, "get_stock_data_cached",
        lambda *_a, **_k: pd.DataFrame({"high": [22_570.0] * 20, "low": [21_900.0] * 20, "close": [22_300.0] * 20}),
    )
    reference = reference or {"prev_close": 22_300.0, "day_low": 21_900.0, "day_high": 22_570.0}
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: dict(reference))
    write_json(tmp_path / "paper_portfolio.json",
               {"initial_cash": 100_000_000.0, "cash": 100_000_000.0, "positions": {}, "ledger_epoch": 1})
    write_json(tmp_path / "paper_trades.json", [{
        "type": "RESET", "time": "2026-09-01 09:00:00", "cash_after": 100_000_000.0,
        "positions_after": {}, "reason": "test", "actor": "pytest", "epoch_id": 1,
    }])
    return auto_trader


# ---- 2. price-sanity gate -------------------------------------------------

def test_gate_blocks_fill_more_than_7pct_from_previous_close():
    from trading_safety import price_sanity_reason

    # VPB 2026-09-21: bot bought 28.0 while the market traded 21.90-22.57.
    reason = price_sanity_reason(28_000, prev_close=22_300, day_low=21_900, day_high=22_570)
    assert reason and "previous close" in reason and "+25.6%" in reason
    assert price_sanity_reason(22_400, prev_close=22_300, day_low=21_900, day_high=22_570) is None
    assert price_sanity_reason(23_861, prev_close=22_300) is None  # +7.0%, still inside the band


def test_gate_blocks_fill_outside_todays_range_and_without_reference():
    from trading_safety import price_sanity_reason

    reason = price_sanity_reason(22_700, prev_close=22_300, day_low=21_900, day_high=22_570)
    assert reason and "outside today's range" in reason
    assert "no previous close" in price_sanity_reason(22_300, prev_close=None)
    assert price_sanity_reason(0, prev_close=22_300) is not None


def test_market_reference_splits_previous_close_from_today():
    import auto_trader

    df = pd.DataFrame({
        "time": ["2026-09-18", "2026-09-19", "2026-09-21"],
        "open": [22.0, 22.2, 22.3], "high": [22.4, 22.5, 22.57],
        "low": [21.8, 22.0, 21.9], "close": [22.2, 22.3, 22.4],
    })
    ref = auto_trader.market_reference("VPB", df=df, today=date(2026, 9, 21))
    assert ref == {"prev_close": 22_300.0, "day_low": 21_900.0, "day_high": 22_570.0}
    ref = auto_trader.market_reference("VPB", df=df, today=date(2026, 9, 22))
    assert ref == {"prev_close": 22_400.0, "day_low": None, "day_high": None}


def test_buy_is_blocked_by_price_gate_without_touching_state(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=28_000.0)
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=28.0, signal_id="vpb", trade_date="2026-09-21")
    assert outcome["status"] == "blocked"
    assert "PRICE GATE" in outcome["detail"] and "previous close" in outcome["detail"]
    assert json.loads((tmp_path / "paper_portfolio.json").read_text())["cash"] == 100_000_000.0
    assert len(json.loads((tmp_path / "paper_trades.json").read_text())) == 1
    state = json.loads((tmp_path / "scheduler_state.json").read_text())
    assert state["trade_idempotency"] == {}


def test_sell_is_blocked_by_price_gate(monkeypatch, tmp_path):
    import auto_trader
    import self_healing

    write_json(tmp_path / "paper_portfolio.json", {"initial_cash": 100_000_000.0, "cash": 80_000_000.0,
               "positions": {"PVD": {"qty": 1000, "avg_price": 19_800.0}}})
    write_json(tmp_path / "paper_trades.json", [])
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (True, "ok"))
    monkeypatch.setattr(auto_trader, "current_price", lambda _s: 33_200.0)
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 19_700.0, "day_low": 19_470.0, "day_high": 19_950.0})
    ok, message = auto_trader.sell_position("PVD")
    assert ok is False and "PRICE GATE" in message
    assert json.loads((tmp_path / "paper_portfolio.json").read_text())["positions"]["PVD"]["qty"] == 1000


def test_direct_close_is_blocked_by_price_gate(monkeypatch):
    import auto_trader
    import scheduler
    import self_healing

    monkeypatch.setattr(self_healing, "trading_is_allowed", lambda _base, **_kw: True)
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 68_000.0, "day_low": 68_000.0, "day_high": 70_400.0})
    portfolio = {"cash": 0.0, "positions": {"VHM": {"qty": 100, "avg_price": 138_900.0}}}
    assert scheduler._close_position_direct(portfolio, "VHM", 126_900.0, "stop_loss") is False
    assert "VHM" in portfolio["positions"]


# ---- 1. one unit: VND price, whole-share lots --------------------------------

def test_thousand_vnd_provided_price_is_normalized_and_qty_is_whole_lots(monkeypatch, tmp_path):
    # Live quote unavailable: the analysis price (thousand VND) used to size qty x1000.
    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=None)
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=22.3, signal_id="unit", trade_date="2026-09-21")
    assert outcome["status"] == "executed", outcome
    event = json.loads((tmp_path / "paper_trades.json").read_text())[-1]
    assert event["price"] == 22_300.0
    assert isinstance(event["qty"], int) and event["qty"] % auto_trader.LOT_SIZE == 0
    assert event["qty"] == 400  # 10,000,000 / 22,300 -> 448 -> 4 lots
    position = json.loads((tmp_path / "paper_portfolio.json").read_text())["positions"]["VPB"]
    assert position["qty"] == 400 and position["avg_price"] == 22_300.0


def test_buy_position_always_rounds_to_lot(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=76_900.0,
                                   reference={"prev_close": 76_800.0, "day_low": 76_000.0, "day_high": 77_500.0})
    ok, message = auto_trader.buy_position("STB")
    assert ok is True, message
    event = json.loads((tmp_path / "paper_trades.json").read_text())[-1]
    assert isinstance(event["qty"], int) and event["qty"] % 100 == 0 and event["qty"] > 0


def test_log_trade_refuses_thousand_vnd_price():
    import auto_trader

    with pytest.raises(ValueError, match="VND per share"):
        auto_trader.log_trade([], "VPB", "BUY", 660_300, 28.0, "test")


# ---- 3. fees and sell tax ---------------------------------------------------

def test_trade_costs_are_parameterized():
    import auto_trader

    assert auto_trader.PAPER_FEE_RATE == 0.001 and auto_trader.PAPER_SELL_TAX_RATE == 0.001
    assert auto_trader.trade_costs("BUY", 10_000_000) == {"fee": 10_000.0, "tax": 0.0, "fees": 10_000.0}
    assert auto_trader.trade_costs("SELL", 10_000_000) == {"fee": 10_000.0, "tax": 10_000.0, "fees": 20_000.0}


def test_round_trip_charges_fees_to_cash_and_reconciles(monkeypatch, tmp_path):
    from self_healing import run_self_healing

    auto_trader = _buy_environment(monkeypatch, tmp_path)
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=22_300, signal_id="fee", trade_date="2026-09-21")
    assert outcome["status"] == "executed", outcome
    buy = json.loads((tmp_path / "paper_trades.json").read_text())[-1]
    value = 400 * 22_300.0
    assert buy["value"] == value and buy["fees"] == pytest.approx(value * 0.001)
    cash_after_buy = json.loads((tmp_path / "paper_portfolio.json").read_text())["cash"]
    assert cash_after_buy == pytest.approx(100_000_000 - value * 1.001)

    monkeypatch.setattr(auto_trader, "current_price", lambda _s: 22_500.0)
    # T+2: the shares just bought are only sellable once they have settled.
    import trading_safety
    from datetime import timedelta
    monkeypatch.setattr(trading_safety, "vietnam_now",
                        lambda: datetime.now(trading_safety.VIETNAM_TZ) + timedelta(days=10))
    ok, message = auto_trader.sell_position("VPB")
    assert ok is True, message
    sell =json.loads((tmp_path / "paper_trades.json").read_text())[-1]
    proceeds = 400 * 22_500.0
    assert sell["fees"] == pytest.approx(proceeds * 0.002)
    assert sell["pnl"] == pytest.approx(400 * 200 - proceeds * 0.002 - value * 0.001, abs=0.02)
    saved = json.loads((tmp_path / "paper_portfolio.json").read_text())
    assert saved["cash"] == pytest.approx(cash_after_buy + proceeds * 0.998)

    report = run_self_healing(tmp_path)
    assert report["metrics"]["cash_drift"] == pytest.approx(0, abs=1)


# ---- 4. one stop-loss source ----------------------------------------------

def test_stop_loss_setter_keeps_position_and_plan_in_sync():
    import auto_trader

    pos = {"avg_price": 24_800.0, "stop_loss": 24_200.0, "plan": {"stop_loss": 24_200.0}}
    auto_trader.set_position_stop_loss(pos, 24_800.0)
    assert pos["stop_loss"] == pos["plan"]["stop_loss"] == 24_800.0
    assert pos["plan"]["initial_stop_loss"] == 24_200.0
    assert auto_trader.position_stop_loss(pos) == 24_800.0
    # Positions opened via buy_position only carried plan.stop_loss.
    assert auto_trader.position_stop_loss({"plan": {"stop_loss": 24_200.0}}) == 24_200.0


def test_eod_trailing_stop_updates_position_and_plan(monkeypatch, tmp_path):
    import data_fetcher
    import scheduler

    # HCM: entry 24,800, ATR 620, initial stop 24,200. Close >= entry + ATR moves the
    # stop to break-even; before T9 only position.stop_loss moved (24,800 vs plan 24,200).
    pos = {"qty": 100, "avg_price": 24_800.0, "atr": 620.0, "stop_loss": 24_200.0,
           "target_price": 26_100.0, "hold_days": 0,
           "plan": {"atr": 620.0, "stop_loss": 24_200.0, "target_price": 26_100.0}}
    portfolio = {"cash": 0.0, "ledger_epoch": 1, "positions": {"HCM": pos}}
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "already_ran_today", lambda _name: False)
    monkeypatch.setattr(scheduler, "mark_ran_today", lambda _name: None)
    monkeypatch.setattr(scheduler, "load_portfolio_direct", lambda: portfolio)
    monkeypatch.setattr(scheduler, "record_eod_equity_snapshot", lambda: None)  # keep tests off the repo NAV file
    monkeypatch.setattr(scheduler, "_save_portfolio_direct", lambda _p: None)
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached",
                        lambda *_a, **_k: pd.DataFrame({"close": [25_000.0, 25_500.0]}))
    scheduler.task_eod_update()
    assert pos["stop_loss"] == pos["plan"]["stop_loss"] == 24_800.0
    assert pos["plan"]["initial_stop_loss"] == 24_200.0


# ---- 5. exits may use the ATC auction -------------------------------------

@pytest.mark.parametrize(("hh", "mm", "entry_ok", "exit_ok"), [
    (14, 20, True, True),
    (14, 26, False, True),
    (14, 45, False, True),
    (14, 46, False, False),
    (15, 0, False, False),
])
def test_exit_orders_allowed_through_atc(hh, mm, entry_ok, exit_ok):
    from trading_safety import VIETNAM_TZ, market_session_reason

    now = datetime(2026, 9, 29, hh, mm, tzinfo=VIETNAM_TZ)
    assert (market_session_reason(now) is None) is entry_ok
    assert (market_session_reason(now, exit_order=True) is None) is exit_ok


def test_kill_switch_still_blocks_exit_orders(monkeypatch, tmp_path):
    from trading_safety import VIETNAM_TZ, operational_gate

    monkeypatch.setenv("TRADING_ENABLED", "false")
    allowed, reason = operational_gate(tmp_path, datetime(2026, 9, 29, 14, 40, tzinfo=VIETNAM_TZ), exit_order=True)
    assert allowed is False and "TRADING_ENABLED" in reason


def test_sell_paths_request_exit_gate(monkeypatch, tmp_path):
    import auto_trader
    import scheduler
    import self_healing

    calls = []
    monkeypatch.setattr(self_healing, "trading_permission",
                        lambda _base, **kw: (calls.append(kw) or (False, "blocked")))
    monkeypatch.setattr(self_healing, "trading_is_allowed",
                        lambda _base, **kw: (calls.append(kw) or False))
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    auto_trader.sell_position("FPT")
    scheduler._close_position_direct({"positions": {}}, "FPT", 10_000.0, "stop_loss")
    auto_trader.execute_paper_trade("FPT", "SELL", signal_id="x", trade_date="2026-09-29")
    auto_trader.execute_paper_trade("FPT", "BUY", signal_id="y", trade_date="2026-09-29")
    assert calls == [{"exit_order": True}, {"exit_order": True}, {"exit_order": True}, {"exit_order": False}]
