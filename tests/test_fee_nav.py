"""T267: buy/sell fee and 0.1% sell tax must reach cash, and therefore NAV (portfolio_equity)."""

import json
from datetime import datetime, timedelta

import pytest

from test_price_sanity_gate import _buy_environment

INITIAL = 100_000_000.0


def _read(tmp_path, name):
    return json.loads((tmp_path / name).read_text())


def test_nav_after_buy_drops_by_buy_fee_only(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path)
    ok, message = auto_trader.buy_position("VPB")
    assert ok is True, message
    buy = _read(tmp_path, "paper_trades.json")[-1]
    assert buy["tax"] == 0 and buy["fees"] == pytest.approx(buy["value"] * auto_trader.PAPER_FEE_RATE)
    nav = auto_trader.portfolio_equity(auto_trader.load_portfolio())
    # price unchanged since the fill: NAV loss is exactly the buy fee, no sell tax yet
    assert nav == pytest.approx(INITIAL - buy["fees"], abs=0.01)


def test_nav_after_round_trip_is_price_pnl_minus_buy_fee_sell_fee_and_tax(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path)
    ok, message = auto_trader.buy_position("VPB")
    assert ok is True, message
    buy = _read(tmp_path, "paper_trades.json")[-1]
    qty, buy_price = buy["qty"], buy["price"]

    import trading_safety
    monkeypatch.setattr(trading_safety, "vietnam_now",
                        lambda: datetime.now(trading_safety.VIETNAM_TZ) + timedelta(days=10))
    sell_price = 22_500.0
    monkeypatch.setattr(auto_trader, "current_price", lambda _s: sell_price)
    ok, message = auto_trader.sell_position("VPB")
    assert ok is True, message
    sell = _read(tmp_path, "paper_trades.json")[-1]

    rate_fee, rate_tax = auto_trader.PAPER_FEE_RATE, auto_trader.PAPER_SELL_TAX_RATE
    assert rate_tax == 0.001
    assert sell["tax"] == pytest.approx(qty * sell_price * 0.001)
    assert sell["fee"] == pytest.approx(qty * sell_price * rate_fee)

    portfolio = auto_trader.load_portfolio()
    assert portfolio["positions"] == {}
    expected = (INITIAL + qty * (sell_price - buy_price)
                - qty * buy_price * rate_fee
                - qty * sell_price * (rate_fee + rate_tax))
    assert auto_trader.portfolio_equity(portfolio) == pytest.approx(expected, abs=0.05)
    # the logged realised pnl equals the NAV change (all costs included)
    assert sell["pnl"] == pytest.approx(auto_trader.portfolio_equity(portfolio) - INITIAL, abs=0.05)


def test_buy_never_spends_more_than_cash_including_fee(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path)
    portfolio = auto_trader.load_portfolio()
    portfolio["cash"] = 5_000_000.0
    auto_trader.save_portfolio(portfolio)
    ok, message = auto_trader.buy_position("VPB", target_value=50_000_000, max_position_pct=1.0)
    assert ok is True, message
    assert auto_trader.load_portfolio()["cash"] >= 0
