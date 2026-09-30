"""Stage (a)-3: consensus buy gate, VN30 market gate, daily-loss/drawdown halt, fail-closed portfolio read."""

from datetime import date

import pandas as pd
import pytest

from test_price_sanity_gate import _buy_environment

GOOD_ENSEMBLE = {"direction": 1, "reliable": True}


# ---- 1. consensus gate ----------------------------------------------------------

@pytest.mark.parametrize("item,ensemble,llm,expected", [
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "MUA"}, True),
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "mua"}, True),
    ({"score": 4}, GOOD_ENSEMBLE, None, False),                                  # LLM failed / skipped
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "GIU"}, False),                     # LLM says hold
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "BAN"}, False),
    ({"score": 4}, {"direction": 0, "reliable": True}, {"action": "MUA"}, False),  # ensemble neutral
    ({"score": 4}, {"direction": -1, "reliable": True}, {"action": "MUA"}, False),
    ({"score": 4}, {"direction": 1, "reliable": False}, {"action": "MUA"}, False),  # not reliable
    ({"score": 4}, {"direction": 1}, {"action": "MUA"}, False),
    ({"score": 2}, GOOD_ENSEMBLE, {"action": "MUA"}, False),                     # weak confluence score
])
def test_buy_consensus(item, ensemble, llm, expected):
    import auto_trader

    assert auto_trader.buy_consensus(item, ensemble, llm) is expected


def _month_end_bars(closes):
    days = pd.date_range("2025-01-31", periods=len(closes), freq="ME")
    return pd.DataFrame({"time": days, "close": closes})


def test_market_gate_uptrend_downtrend_and_fail_closed():
    from risk_limits import market_uptrend_reason

    assert market_uptrend_reason(_month_end_bars([100 + i for i in range(12)])) is None     # last >= mean
    down = market_uptrend_reason(_month_end_bars([100 - i for i in range(12)]))
    assert down and "below its 10-month average" in down
    assert "fewer than" in market_uptrend_reason(_month_end_bars([100.0] * 5))
    assert "fails closed" in market_uptrend_reason(pd.DataFrame({"time": [], "close": []}))


def test_market_gate_fails_closed_when_fetch_raises(monkeypatch):
    import data_fetcher
    from risk_limits import market_uptrend_reason

    def boom(*_a, **_k):
        raise OSError("network")

    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", boom)
    assert "fails closed" in market_uptrend_reason()


# ---- 2. daily loss / drawdown halt -----------------------------------------------

TODAY = date(2026, 9, 22)
HISTORY = [{"time": "2026-09-18 15:00:00", "equity": 100_000_000.0},
           {"time": "2026-09-21 15:00:00", "equity": 101_000_000.0},
           {"time": "2026-09-22 09:30:00", "equity": 99_000_000.0}]   # same-day row must not be the baseline


def test_entry_halt_daily_loss_uses_previous_session_nav():
    from risk_limits import entry_halt_reason

    assert entry_halt_reason(99_000_000.0, HISTORY, 100_000_000.0, TODAY) is None          # -1.98%
    reason = entry_halt_reason(98_900_000.0, HISTORY, 100_000_000.0, TODAY)                # -2.08%
    assert reason and reason.startswith("daily loss")


def test_entry_halt_drawdown_from_peak():
    from risk_limits import entry_halt_reason

    history = [{"time": "2026-09-01 15:00:00", "equity": 120_000_000.0},
               {"time": "2026-09-21 15:00:00", "equity": 108_500_000.0}]
    assert entry_halt_reason(108_500_000.0, history, 100_000_000.0, TODAY) is None         # -9.58%
    reason = entry_halt_reason(108_000_000.0, history, 100_000_000.0, TODAY)               # -10.0%
    assert reason and reason.startswith("drawdown")


def test_entry_halt_thresholds_come_from_config():
    from risk_limits import entry_halt_reason, limits_from_config

    assert limits_from_config({}) == {"max_daily_loss_pct": 2.0, "max_drawdown_pct": 10.0,
                                      "require_market_uptrend": True}
    loose = limits_from_config({"max_daily_loss_pct": 5, "max_drawdown_pct": "bad", "require_market_uptrend": False})
    assert loose == {"max_daily_loss_pct": 5.0, "max_drawdown_pct": 10.0, "require_market_uptrend": False}
    assert entry_halt_reason(98_900_000.0, HISTORY, 100_000_000.0, TODAY, loose) is None
    assert "max_daily_loss_pct" in __import__("auto_trader").default_ai_fund_config()


def test_buy_is_blocked_and_alerted_when_drawdown_limit_hit(monkeypatch, tmp_path):
    import risk_limits

    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=22_400.0)
    monkeypatch.setattr(auto_trader, "load_equity_history",
                        lambda: [{"time": "2026-08-01 15:00:00", "equity": 130_000_000.0}])
    alerts = []
    monkeypatch.setattr(risk_limits, "alert_entry_halt", lambda reason, base_dir=None: alerts.append(reason))
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=22.4, signal_id="halt", trade_date="2026-09-21")
    assert outcome["status"] == "blocked" and "entry halt" in outcome["detail"]
    assert len(alerts) == 1 and alerts[0].startswith("drawdown")
    import json
    assert [e for e in json.loads((tmp_path / "paper_trades.json").read_text()) if e.get("type", "TRADE") != "RESET"] == []


def test_halt_does_not_block_sells(monkeypatch, tmp_path):
    """The halt only stops new entries; the SELL path never consults it."""
    import inspect

    import auto_trader

    source = inspect.getsource(auto_trader.execute_paper_trade)
    assert source.index("entry_halt_reason") > source.index('if action == "BUY":')


# ---- 4. fail-closed portfolio read -------------------------------------------------

@pytest.fixture
def portfolio_file(monkeypatch, tmp_path):
    import auto_trader

    path = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(path))
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader.time, "sleep", lambda *_a: None)
    return path


@pytest.mark.parametrize("content", ["{not json", "", "[1, 2]"])
def test_load_portfolio_raises_on_corrupt_file(portfolio_file, content):
    import auto_trader

    portfolio_file.write_text(content, encoding="utf-8")
    with pytest.raises(auto_trader.PortfolioReadError):
        auto_trader.load_portfolio()
    with pytest.raises(auto_trader.PortfolioReadError):
        auto_trader._safe_read_portfolio(strict=True)


def test_display_read_stays_lenient_and_marks_placeholder(portfolio_file):
    import auto_trader

    portfolio_file.write_text("{not json", encoding="utf-8")
    assert auto_trader._safe_read_portfolio()["updated_at"] == "unknown"


def test_load_portfolio_reads_valid_and_missing_files(portfolio_file):
    import auto_trader

    portfolio_file.write_text('{"cash": 5.0, "positions": {}, "initial_cash": 10.0}', encoding="utf-8")
    assert auto_trader.load_portfolio()["cash"] == 5.0
    portfolio_file.unlink()
    assert auto_trader.load_portfolio()["positions"] == {}     # first run: fresh default portfolio


def test_execute_paper_trade_reports_transient_on_corrupt_portfolio(monkeypatch, tmp_path):
    auto_trader = _buy_environment(monkeypatch, tmp_path, live_price=22_400.0)
    (tmp_path / "paper_portfolio.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(auto_trader.time, "sleep", lambda *_a: None)
    outcome = auto_trader.execute_paper_trade("VPB", "BUY", price=22.4, signal_id="corrupt", trade_date="2026-09-21")
    assert outcome["status"] == "transient" and "portfolio read failed" in outcome["detail"]
