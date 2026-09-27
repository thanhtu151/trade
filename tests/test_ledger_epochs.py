import json
import subprocess
from pathlib import Path

import pytest


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def portfolio(cash, epoch=1):
    return {"initial_cash": 100_000_000.0, "cash": float(cash), "positions": {}, "ledger_epoch": epoch}


def reset(epoch, cash=100_000_000.0, time="2026-07-09 10:50:56"):
    return {
        "type": "RESET",
        "time": time,
        "cash_after": float(cash),
        "positions_after": {},
        "reason": "test reset",
        "actor": "pytest",
        "epoch_id": epoch,
    }


def trade(time, side, value, epoch=1, symbol="FPT", price=10_000.0):
    return {
        "type": "TRADE",
        "time": time,
        "symbol": symbol,
        "side": side,
        "qty": int(value / price),
        "price": price,
        "value": float(value),
        "reason": "test",
        "epoch_id": epoch,
    }


def test_reset_then_multiple_trades_has_zero_drift(tmp_path):
    from self_healing import run_self_healing

    ledger = [
        trade("2026-01-01 09:20:00", "BUY", 80_000_000, epoch=0),
        reset(1),
        trade("2026-07-10 09:20:00", "BUY", 20_000_000),
        trade("2026-07-11 09:20:00", "SELL", 5_000_000),
        trade("2026-07-12 09:20:00", "BUY", 10_000_000),
    ]
    write_json(tmp_path / "paper_portfolio.json", portfolio(75_000_000))
    write_json(tmp_path / "paper_trades.json", ledger)
    report = run_self_healing(tmp_path)
    assert report["trading_allowed"] is True
    assert report["metrics"]["cash_drift"] == 0
    assert report["metrics"]["current_epoch"] == 1


def test_second_reset_starts_fresh_cash_invariant(tmp_path):
    from self_healing import run_self_healing

    ledger = [
        reset(1),
        trade("2026-07-10 09:20:00", "BUY", 30_000_000, epoch=1),
        reset(2, cash=50_000_000, time="2026-08-01 09:00:00"),
        trade("2026-08-01 09:20:00", "BUY", 12_000_000, epoch=2),
    ]
    write_json(tmp_path / "paper_portfolio.json", portfolio(38_000_000, epoch=2))
    write_json(tmp_path / "paper_trades.json", ledger)
    report = run_self_healing(tmp_path)
    assert report["trading_allowed"] is True
    assert report["metrics"]["cash_drift"] == 0
    assert report["metrics"]["current_epoch"] == 2


def test_journal_recovers_crash_between_portfolio_and_ledger(tmp_path):
    from ledger_store import commit_portfolio_and_ledger, recover_pending_transaction

    old_portfolio = portfolio(100_000_000)
    old_ledger = [reset(1)]
    write_json(tmp_path / "paper_portfolio.json", old_portfolio)
    write_json(tmp_path / "paper_trades.json", old_ledger)
    new_portfolio = portfolio(90_000_000)
    new_ledger = old_ledger + [trade("2026-07-10 09:20:00", "BUY", 10_000_000)]

    with pytest.raises(RuntimeError, match="simulated crash"):
        commit_portfolio_and_ledger(tmp_path, new_portfolio, new_ledger, failpoint="after_portfolio")
    assert json.loads((tmp_path / "paper_portfolio.json").read_text()) == new_portfolio
    assert json.loads((tmp_path / "paper_trades.json").read_text()) == old_ledger

    assert recover_pending_transaction(tmp_path)
    assert json.loads((tmp_path / "paper_portfolio.json").read_text()) == new_portfolio
    assert json.loads((tmp_path / "paper_trades.json").read_text()) == new_ledger
    assert not (tmp_path / "paper_ledger_transaction.json").exists()


def test_manual_cash_edit_after_reset_blocks(tmp_path):
    from self_healing import run_self_healing

    write_json(tmp_path / "paper_portfolio.json", portfolio(79_000_000))
    write_json(tmp_path / "paper_trades.json", [reset(1), trade("2026-07-10 09:20:00", "BUY", 20_000_000)])
    report = run_self_healing(tmp_path)
    assert report["trading_allowed"] is False
    assert report["metrics"]["cash_drift"] == 1_000_000


def test_investigated_migration_inserts_audited_reset_and_zeroes_drift(tmp_path):
    from self_healing import MIGRATION_CONFIRMATION, migrate_reconstructed_reset

    before = trade("2026-07-09 10:50:55", "SELL", 10_000_000, epoch=0, symbol="PVD")
    after = trade("2026-07-10 10:44:22", "BUY", 25_000_000, epoch=0, symbol="PVD")
    write_json(tmp_path / "paper_portfolio.json", portfolio(75_000_000))
    write_json(tmp_path / "paper_trades.json", [before, after])
    result = migrate_reconstructed_reset(tmp_path, MIGRATION_CONFIRMATION, operator="github-user")
    migrated = json.loads((tmp_path / "paper_trades.json").read_text())
    assert [row["epoch_id"] for row in migrated] == [0, 1, 1]
    assert migrated[1]["type"] == "RESET"
    assert migrated[1]["actor"] == "github-user"
    assert result["report"]["metrics"]["cash_drift"] == 0
    audit = json.loads((tmp_path / "self_healing_audit.json").read_text())
    assert audit[-1]["action"] == "migrate_ledger_epoch"


def test_migration_requires_exact_confirmation(tmp_path):
    from self_healing import migrate_reconstructed_reset

    with pytest.raises(ValueError, match="confirmation must equal"):
        migrate_reconstructed_reset(tmp_path, "yes", operator="github-user")


def test_migration_can_run_with_kill_switch_closed_then_allows_when_open(monkeypatch, tmp_path):
    from self_healing import MIGRATION_CONFIRMATION, migrate_reconstructed_reset, run_self_healing

    before = trade("2026-07-09 10:50:55", "SELL", 10_000_000, epoch=0, symbol="PVD")
    after = trade("2026-07-10 10:44:22", "BUY", 25_000_000, epoch=0, symbol="PVD")
    write_json(tmp_path / "paper_portfolio.json", portfolio(75_000_000))
    write_json(tmp_path / "paper_trades.json", [before, after])
    monkeypatch.setenv("TRADING_ENABLED", "false")
    result = migrate_reconstructed_reset(tmp_path, MIGRATION_CONFIRMATION, operator="github-user")
    assert result["report"]["metrics"]["cash_drift"] == 0
    assert result["report"]["trading_allowed"] is False
    monkeypatch.setenv("TRADING_ENABLED", "true")
    assert run_self_healing(tmp_path)["trading_allowed"] is True


def test_workflow_exposes_rebaseline_and_confirmed_migration():
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
    assert "rebaseline" in workflow
    assert "migrate-ledger-epoch" in workflow
    assert '--reason "$DISPATCH_REASON"' in workflow
    assert "MIGRATE_LEDGER_EPOCH_2026_07_09" in workflow
    assert '--operator "$GITHUB_ACTOR"' in workflow
    decide = workflow.index("- name: Decide which task to run")
    load = workflow.index("- name: Load state from `state` branch")
    validation = workflow.index('if [ "$TASK" = "rebaseline" ]')
    assert decide < validation < load


def test_retry_uses_exponential_backoff_and_jitter():
    from runtime_reliability import retry_transient

    attempts = []
    sleeps = []

    def operation():
        attempts.append(True)
        if len(attempts) < 3:
            raise TimeoutError("temporary")
        return "ok"

    result = retry_transient(operation, base_delay=2, jitter_ratio=0.5, random_fn=lambda: 0.5, sleeper=sleeps.append)
    assert result == "ok"
    assert len(attempts) == 3
    assert sleeps == [2.5, 5.0]


def test_new_sub_thousand_price_is_blocked_but_not_reserved(monkeypatch, tmp_path):
    import auto_trader
    import self_healing

    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base: (True, "ok"))
    monkeypatch.setattr(auto_trader, "load_portfolio", lambda: portfolio(100_000_000))
    monkeypatch.setattr(auto_trader, "current_price", lambda _ticker: 76.8)
    outcome = auto_trader.execute_paper_trade(
        "STB", "BUY", signal_id="bad-unit", trade_date="2026-09-28"
    )
    assert outcome["status"] == "blocked"
    assert "expected >= 1,000" in outcome["detail"]
    state = json.loads((tmp_path / "scheduler_state.json").read_text())
    assert state["trade_idempotency"] == {}


def test_reset_state_appends_epoch_and_commits_both_files(monkeypatch, tmp_path):
    import auto_trader

    old = [trade("2026-01-01 09:20:00", "BUY", 10_000_000, epoch=0)]
    write_json(tmp_path / "paper_portfolio.json", portfolio(90_000_000, epoch=0))
    write_json(tmp_path / "paper_trades.json", old)
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(auto_trader, "PORTFOLIO_FILE", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(auto_trader, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    event = auto_trader.reset_state(50_000_000, reason="approved reset", actor="tester")
    stored_portfolio = json.loads((tmp_path / "paper_portfolio.json").read_text())
    stored_ledger = json.loads((tmp_path / "paper_trades.json").read_text())
    assert stored_ledger[:-1] == old
    assert stored_ledger[-1] == event
    assert event["type"] == "RESET" and event["epoch_id"] == 1
    assert stored_portfolio["cash"] == 50_000_000
    assert stored_portfolio["ledger_epoch"] == 1


def test_provider_unavailable_reports_degraded_without_import(monkeypatch):
    import market_data_adapter

    monkeypatch.setattr(market_data_adapter.importlib.util, "find_spec", lambda _name: None)
    assert market_data_adapter.provider_availability() == {
        "available": False,
        "status": "degraded",
        "detail": "vnstock unavailable; cached/non-vendor data only",
    }


def test_market_data_frame_normalizes_prices_to_vnd():
    import pandas as pd
    from data_fetcher import _normalize_vn_equity_frame

    raw = pd.DataFrame({"open": [59.5], "high": [60.0], "low": [59.0], "close": [59.7], "volume": [100]})
    normalized = _normalize_vn_equity_frame(raw)
    assert normalized.loc[0, "close"] == 59_700
    assert normalized.loc[0, "volume"] == 100
    assert _normalize_vn_equity_frame(normalized).loc[0, "close"] == 59_700


def test_vendor_call_timeout_is_bounded():
    from data_fetcher import _call_with_timeout

    blocker = __import__("threading").Event()
    with pytest.raises(TimeoutError, match="exceeded"):
        _call_with_timeout(blocker.wait, timeout=0.01)


def test_vnstock_worker_timeout_is_reported_and_temp_file_removed(monkeypatch, tmp_path):
    import data_fetcher

    output_paths = []

    def fake_mkstemp(**_kwargs):
        path = tmp_path / "worker-output.json"
        path.touch()
        output_paths.append(path)
        return __import__("os").open(path, __import__("os").O_RDWR), str(path)

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="vnstock_fetch_worker", timeout=0.01)

    monkeypatch.setattr(data_fetcher.tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(data_fetcher.subprocess, "run", fake_run)
    with pytest.raises(TimeoutError, match="worker exceeded"):
        data_fetcher._history_via_worker("FPT", "VCI", "2026-01-01", "2026-01-02", timeout=0.01)
    assert output_paths and not output_paths[0].exists()


def test_learning_and_reflection_default_to_current_epoch(monkeypatch, tmp_path):
    import learning_engine
    import reflection_manager

    write_json(tmp_path / "paper_trades.json", [reset(1)])
    predictions = {
        "old1": {"ticker": "FPT", "date": "2026-01-01", "resolved": True, "correct": False, "pnl_pct": -5, "ledger_epoch": 0},
        "old2": {"ticker": "FPT", "date": "2026-01-02", "resolved": True, "correct": False, "pnl_pct": -4, "ledger_epoch": 0},
        "old3": {"ticker": "FPT", "date": "2026-01-03", "resolved": True, "correct": False, "pnl_pct": -3, "ledger_epoch": 0},
        "new1": {"ticker": "FPT", "date": "2026-08-01", "resolved": True, "correct": True, "pnl_pct": 1, "ledger_epoch": 1},
        "new2": {"ticker": "FPT", "date": "2026-08-02", "resolved": True, "correct": True, "pnl_pct": 2, "ledger_epoch": 1},
        "new3": {"ticker": "FPT", "date": "2026-08-03", "resolved": True, "correct": True, "pnl_pct": 3, "ledger_epoch": 1},
    }
    write_json(tmp_path / "prediction_log.json", predictions)
    history = [
        {"symbol": "FPT", "date": "2026-01-01", "correct": False, "ledger_epoch": 0},
        {"symbol": "FPT", "date": "2026-08-01", "correct": True, "ledger_epoch": 1},
    ]
    write_json(tmp_path / "prediction_history.json", history)
    monkeypatch.setattr(learning_engine, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(learning_engine, "PREDICTION_LOG", str(tmp_path / "prediction_log.json"))
    monkeypatch.setattr(learning_engine, "MEMORY_FILE", str(tmp_path / "learning_memory.json"))
    monkeypatch.setattr(reflection_manager, "BASE_DIR", str(tmp_path))

    stats = learning_engine.calculate_accuracy_stats()
    assert stats["FPT"]["total_predictions"] == 3
    assert stats["FPT"]["accuracy"] == 1.0
    perf = reflection_manager.ReflectionManager(tmp_path / "prediction_history.json").get_recent_performance("FPT")
    assert perf["count"] == 1
    assert perf["accuracy"] == 100.0


def test_circuit_breaker_opens_and_half_opens_after_cooldown():
    from runtime_reliability import CircuitBreaker

    now = [10.0]
    circuit = CircuitBreaker(failure_threshold=2, recovery_seconds=30, clock=lambda: now[0])
    circuit.failure("VCI")
    assert circuit.allow("VCI") is True
    circuit.failure("VCI")
    assert circuit.allow("VCI") is False
    now[0] += 31
    assert circuit.allow("VCI") is True


def test_exchange_calendar_has_verified_closures_and_unknown_year_fails_closed():
    from datetime import date
    from scheduler import is_trading_day

    assert is_trading_day(date(2026, 1, 2)) is False
    assert is_trading_day(date(2026, 2, 16)) is False
    assert is_trading_day(date(2026, 4, 27)) is False
    assert is_trading_day(date(2026, 8, 31)) is False
    assert is_trading_day(date(2026, 9, 28)) is True
    assert is_trading_day(date(2027, 9, 1)) is False
