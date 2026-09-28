import json
from datetime import date, datetime
from zoneinfo import ZoneInfo


def test_running_task_records_deadline_and_reconciles_cancelled(tmp_path):
    from system_status import load_status, update_task
    from watchdog import reconcile_finished_runs

    update_task(tmp_path, "rebacktest", "running", run_id="42", now="2026-09-28T05:00:00+00:00")
    status = load_status(tmp_path)
    assert status["tasks"]["rebacktest"]["deadline_at"] == "2026-09-28T06:25:00+00:00"
    changed = reconcile_finished_runs(status, [{"id": 42, "status": "completed", "conclusion": "cancelled"}],
                                      now="2026-09-28T06:30:00+00:00")
    assert changed == ["rebacktest"]
    row = status["tasks"]["rebacktest"]
    assert row["state"] == "cancelled"
    assert row["consecutive_failures"] == 1
    assert "started_at" not in row


def test_watchdog_catchup_queue_is_dependency_ordered_and_reports_deferred():
    from watchdog import plan_catchup

    plan = plan_catchup({"missed": ["rebacktest", "learning", "eod"]}, [])
    assert plan == {"dispatch": "eod", "deferred": ["learning", "rebacktest"]}


def test_prior_snapshot_requires_previous_hose_session_and_same_epoch():
    from portfolio_snapshots import daily_change

    snapshots = [
        {"date": "2026-09-25", "equity": 80_000_000, "ledger_epoch": 2},
        {"date": "2026-09-28", "equity": 81_000_000, "ledger_epoch": 2},
    ]
    value, reason = daily_change(81_000_000, snapshots, date(2026, 9, 28), 2)
    assert value == 1.25
    assert reason is None
    value, reason = daily_change(81_000_000, snapshots, date(2026, 9, 29), 3)
    assert value is None
    assert "ledger epoch" in reason


def test_eod_summary_rejects_stale_snapshot(tmp_path, monkeypatch):
    import notify

    (tmp_path / "paper_portfolio.json").write_text(json.dumps({"cash": 100, "ledger_epoch": 1, "positions": {}}))
    (tmp_path / "portfolio_snapshots.json").write_text(json.dumps([
        {"date": "2026-07-15", "equity": 200, "ledger_epoch": 1}
    ]))
    monkeypatch.setattr(notify, "ict_today", lambda: date(2026, 9, 28))
    text = notify.build_eod_summary(tmp_path)
    assert "Daily change: N/A" in text
    assert "previous HOSE session" in text


def test_eod_persists_snapshot_even_when_prices_do_not_change(monkeypatch, tmp_path):
    import pandas as pd
    import scheduler
    import data_fetcher

    portfolio = {"cash": 100, "ledger_epoch": 3, "positions": {"FPT": {"qty": 1, "avg_price": 100,
                 "current_price": 100, "market_value": 100, "hold_days": 0}}}
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "already_ran_today", lambda _name: False)
    monkeypatch.setattr(scheduler, "mark_ran_today", lambda _name: None)
    monkeypatch.setattr(scheduler, "load_portfolio_direct", lambda: portfolio)
    monkeypatch.setattr(scheduler, "_save_portfolio_direct", lambda _p: None)
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: pd.DataFrame({"close": [100, 100]}))
    scheduler.task_eod_update()
    rows = json.loads((tmp_path / "portfolio_snapshots.json").read_text())
    assert rows[-1]["ledger_epoch"] == 3
    assert rows[-1]["equity"] == 200


def test_kill_switch_is_inhibitor_not_critical(monkeypatch, tmp_path):
    import self_healing

    (tmp_path / "paper_portfolio.json").write_text(json.dumps({"initial_cash": 100, "cash": 100, "positions": {}}))
    (tmp_path / "paper_trades.json").write_text("[]")
    monkeypatch.setattr("trading_safety.kill_switch_reason", lambda _base: "TRADING_ENABLED disables trading")
    report = self_healing.run_self_healing(tmp_path)
    assert report["critical"] == []
    assert report["inhibitors"] == ["trading kill switch: TRADING_ENABLED disables trading"]
    assert report["status"] == "operationally_blocked"
    assert report["trading_allowed"] is False

    monkeypatch.setattr(self_healing, "run_self_healing", lambda *_a, **_k: report)
    import scheduler
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(self_healing, "snapshot_state", lambda *_a, **_k: None)
    result = scheduler.run_now("heal")
    assert result["status"] == "operationally_blocked"
    from system_status import load_status
    assert load_status(tmp_path)["tasks"]["heal"]["state"] == "success"


def test_rebacktest_chunks_checkpoints_and_resumes(monkeypatch, tmp_path):
    import scheduler
    import backtester_pro

    symbols = [f"S{i}" for i in range(7)]
    (tmp_path / "training_watchlist.json").write_text(json.dumps(symbols))
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "STATE_FILE", str(tmp_path / "scheduler_state.json"))
    monkeypatch.setattr(scheduler, "REBACKTEST_CHUNK_SIZE", 5)
    monkeypatch.setattr(scheduler, "ict_today", lambda: date(2026, 9, 28))
    calls = []
    monkeypatch.setattr(backtester_pro, "run_portfolio_backtest_pro",
                        lambda tickers, **_kwargs: calls.append(list(tickers)) or {s: {"trades": 2} for s in tickers})
    first = scheduler.task_weekly_rebacktest(force=True)
    assert first["status"] == "deferred"
    assert calls == [symbols[:5]]
    assert (tmp_path / "rebacktest_checkpoint.json").exists()
    second = scheduler.task_weekly_rebacktest(force=True)
    assert second is None
    assert calls == [symbols[:5], symbols[5:]]
    assert not (tmp_path / "rebacktest_checkpoint.json").exists()


def test_actions_api_parser_rejects_tabular_gh_output():
    import pytest
    from watchdog import load_actions_runs

    with pytest.raises(ValueError, match="not JSON"):
        load_actions_runs("TRADING_ENABLED\tfalse\t2026-09-27T11:46:38Z")


def test_workflow_persists_rebacktest_outputs():
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "scheduler.yml").read_text()
    assert "rebacktest_checkpoint.json" in workflow
    assert "backtest_config.json" in workflow
