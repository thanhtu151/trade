import json
import ast
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest




def _in_session():
    from datetime import datetime
    from trading_safety import VIETNAM_TZ

    return datetime(2026, 9, 29, 10, 0, tzinfo=VIETNAM_TZ)

def test_kill_switch_and_market_session(monkeypatch, tmp_path):
    from trading_safety import operational_gate, VIETNAM_TZ

    in_session = datetime(2026, 9, 28, 9, 30, tzinfo=VIETNAM_TZ)
    monkeypatch.setenv("TRADING_ENABLED", "false")
    allowed, reason = operational_gate(tmp_path, in_session)
    assert allowed is False
    assert "TRADING_ENABLED" in reason

    monkeypatch.setenv("TRADING_ENABLED", "true")
    allowed, _ = operational_gate(tmp_path, in_session)
    assert allowed is True
    allowed, reason = operational_gate(
        tmp_path, datetime(2026, 9, 28, 14, 26, tzinfo=VIETNAM_TZ)
    )
    assert allowed is False
    assert "outside" in reason


def test_two_runs_same_signal_execute_only_one_order(monkeypatch, tmp_path):
    import auto_trader
    import data_fetcher
    import self_healing

    portfolio = {"initial_cash": 100_000_000.0, "cash": 100_000_000.0, "positions": {}}
    trades = []
    writes = {"portfolio": 0, "trades": 0}

    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (True, "ok"))
    monkeypatch.setattr(auto_trader, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(auto_trader, "load_trades", lambda: trades)
    monkeypatch.setattr(auto_trader, "current_price", lambda _ticker: 10_000.0)
    monkeypatch.setattr(
        auto_trader,
        "get_kelly_position_size",
        lambda *_args: {"value": 10_000_000, "kelly_fraction": 0.1, "pct_portfolio": 10},
    )
    monkeypatch.setattr(
        data_fetcher,
        "get_stock_data_cached",
        lambda *_args, **_kwargs: pd.DataFrame(
            {"high": [10_100.0] * 20, "low": [9_900.0] * 20, "close": [10_000.0] * 20}
        ),
    )

    def save_portfolio(_value):
        writes["portfolio"] += 1

    def save_trades(_value):
        writes["trades"] += 1

    monkeypatch.setattr(auto_trader, "save_portfolio", save_portfolio)
    monkeypatch.setattr(auto_trader, "save_trades", save_trades)
    monkeypatch.setattr(auto_trader, "save_portfolio_and_trades", lambda _p, _t, operation="trade": (save_portfolio(_p), save_trades(_t)))

    first = auto_trader.execute_paper_trade(
        "FPT", "BUY", price=10_000, signal_id="signal-123", run_id="run-a", trade_date="2026-09-28"
    )
    second = auto_trader.execute_paper_trade(
        "FPT", "BUY", price=10_000, signal_id="signal-123", run_id="run-b", trade_date="2026-09-28"
    )

    state = json.loads((tmp_path / "scheduler_state.json").read_text(encoding="utf-8"))
    record = state["trade_idempotency"]["2026-09-28:FPT:BUY:signal-123"]
    assert first["status"] == "executed"
    assert second["status"] == "duplicate"
    assert "duplicate idempotency key" in second["detail"]
    assert writes == {"portfolio": 1, "trades": 1}
    assert record["run_id"] == "run-a"


def _trade_test_environment(monkeypatch, tmp_path, portfolio=None, sizing_value=10_000_000):
    import auto_trader
    import data_fetcher
    import self_healing

    portfolio = portfolio or {"initial_cash": 100_000_000.0, "cash": 100_000_000.0, "positions": {}}
    trades = []
    writes = {"portfolio": 0, "trades": 0}
    monkeypatch.setattr(auto_trader, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (True, "ok"))
    monkeypatch.setattr(auto_trader, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(auto_trader, "load_trades", lambda: trades)
    monkeypatch.setattr(auto_trader, "current_price", lambda _ticker: 10_000.0)
    monkeypatch.setattr(
        auto_trader,
        "get_kelly_position_size",
        lambda *_args: {"value": sizing_value, "kelly_fraction": 0.1, "pct_portfolio": 10},
    )
    monkeypatch.setattr(
        data_fetcher,
        "get_stock_data_cached",
        lambda *_args, **_kwargs: pd.DataFrame(
            {"high": [10_100.0] * 20, "low": [9_900.0] * 20, "close": [10_000.0] * 20}
        ),
    )
    monkeypatch.setattr(auto_trader, "save_portfolio", lambda _value: writes.__setitem__("portfolio", writes["portfolio"] + 1))
    monkeypatch.setattr(auto_trader, "save_trades", lambda _value: writes.__setitem__("trades", writes["trades"] + 1))
    monkeypatch.setattr(
        auto_trader,
        "save_portfolio_and_trades",
        lambda _p, _t, operation="trade": (
            writes.__setitem__("portfolio", writes["portfolio"] + 1),
            writes.__setitem__("trades", writes["trades"] + 1),
        ),
    )
    return auto_trader, portfolio, writes


@pytest.mark.parametrize(
    ("case", "portfolio", "sizing_value"),
    [
        ("already", {"cash": 100_000_000, "positions": {"FPT": {"qty": 100, "avg_price": 100}}}, 10_000_000),
        ("cash", {"cash": 500_000, "positions": {}}, 10_000_000),
        ("max", {"cash": 100_000_000, "positions": {f"T{i}": {"qty": 100, "avg_price": 100} for i in range(5)}}, 10_000_000),
        ("small", {"cash": 100_000_000, "positions": {}}, 5_000),
    ],
)
def test_business_skip_completes_reservation_as_skipped(monkeypatch, tmp_path, case, portfolio, sizing_value):
    auto_trader, _portfolio, writes = _trade_test_environment(
        monkeypatch, tmp_path, portfolio=portfolio, sizing_value=sizing_value
    )
    outcome = auto_trader.execute_paper_trade(
        "FPT", "BUY", price=10_000, signal_id=f"skip-{case}", trade_date="2026-09-28"
    )
    state = json.loads((tmp_path / "scheduler_state.json").read_text(encoding="utf-8"))
    record = state["trade_idempotency"][f"2026-09-28:FPT:BUY:skip-{case}"]
    assert outcome["status"] == "skipped"
    assert record["status"] == "skipped"
    assert writes == {"portfolio": 0, "trades": 0}


def test_transient_price_failure_releases_then_retry_executes_once(monkeypatch, tmp_path):
    auto_trader, _portfolio, writes = _trade_test_environment(monkeypatch, tmp_path)
    calls = iter((RuntimeError("temporary quote outage"), 10_000.0))

    def current_price(_ticker):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(auto_trader, "current_price", current_price)
    first = auto_trader.execute_paper_trade(
        "FPT", "BUY", signal_id="retry-signal", run_id="run-a", trade_date="2026-09-28"
    )
    second = auto_trader.execute_paper_trade(
        "FPT", "BUY", signal_id="retry-signal", run_id="run-b", trade_date="2026-09-28"
    )
    assert first["status"] == "transient"
    assert second["status"] == "executed"
    assert writes == {"portfolio": 1, "trades": 1}


def test_persistence_failure_keeps_reservation_fail_closed(monkeypatch, tmp_path):
    auto_trader, _portfolio, _writes = _trade_test_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(auto_trader, "save_portfolio_and_trades", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    outcome = auto_trader.execute_paper_trade(
        "FPT", "BUY", price=10_000, signal_id="crash-signal", trade_date="2026-09-28"
    )
    state = json.loads((tmp_path / "scheduler_state.json").read_text(encoding="utf-8"))
    record = state["trade_idempotency"]["2026-09-28:FPT:BUY:crash-signal"]
    assert outcome["status"] == "failed"
    assert record["status"] == "reserved"


def test_structured_blocked_result_preserves_gate_reason(monkeypatch):
    import auto_trader
    import self_healing

    reason = "outside configured trading sessions (09:15-11:25, 13:00-14:25 ICT)"
    monkeypatch.setattr(self_healing, "trading_permission", lambda _base, **_kw: (False, reason))
    outcome = auto_trader.execute_paper_trade("FPT", "BUY", signal_id="blocked")
    assert outcome == {"status": "blocked", "detail": reason, "idempotency_key": None}


def test_trade_cron_maps_to_trade_and_is_inside_session():
    from trading_safety import VIETNAM_TZ, market_session_reason

    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
    scheduler_source = (root / "scheduler.py").read_text(encoding="utf-8")
    assert 'cron: "20 2 * * 1-5"' in workflow
    assert '"20 2 * * 1-5") TASK=trade ;;' in workflow
    assert 'schedule.every().day.at("09:20").do(task_auto_trade)' in scheduler_source
    assert market_session_reason(datetime(2026, 9, 28, 9, 20, tzinfo=VIETNAM_TZ)) is None


def _scheduler_trade_environment(monkeypatch, tmp_path, outcomes):
    import auto_trader
    import scheduler
    import self_healing
    import trading_safety

    analysis_file = tmp_path / "analysis_results.json"
    analysis_file.write_text(
        json.dumps({"date": _in_session().date().isoformat(), "tradeable": [{"ticker": "FPT", "price": 100}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler, "ANALYSIS_RESULTS_FILE", str(analysis_file))
    monkeypatch.setattr(scheduler, "STATE_FILE", str(tmp_path / "scheduler_state.json"))
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "ict_now", _in_session)
    monkeypatch.setattr(trading_safety, "operational_gate", lambda _base: (True, "ok"))
    monkeypatch.setattr(
        self_healing,
        "run_self_healing",
        lambda _base, repair=True: {"trading_allowed": True, "status": "healthy"},
    )
    calls = []
    sequence = iter(outcomes)

    def execute(**kwargs):
        calls.append(kwargs)
        return next(sequence)

    monkeypatch.setattr(auto_trader, "execute_paper_trade", execute)
    return scheduler, calls


def test_scheduler_retries_transient_then_succeeds(monkeypatch, tmp_path):
    scheduler, calls = _scheduler_trade_environment(
        monkeypatch,
        tmp_path,
        [
            {"status": "transient", "detail": "quote timeout"},
            {"status": "executed", "detail": "ok"},
        ],
    )
    sleeps = []
    monkeypatch.setattr(scheduler.time, "sleep", sleeps.append)
    scheduler.task_auto_trade()
    assert len(calls) == 2
    assert len(sleeps) == 1 and sleeps[0] >= 1.0
    assert scheduler.already_ran_today("auto_trade") is True


@pytest.mark.parametrize("status", ["skipped", "duplicate", "blocked"])
def test_scheduler_nonfatal_trade_outcomes_do_not_fail(monkeypatch, tmp_path, status):
    scheduler, calls = _scheduler_trade_environment(
        monkeypatch, tmp_path, [{"status": status, "detail": "expected nonfatal outcome"}]
    )
    scheduler.task_auto_trade()
    assert len(calls) == 1
    assert scheduler.already_ran_today("auto_trade") is True


@pytest.mark.parametrize("status", ["failed", "transient"])
def test_scheduler_exhausted_failures_raise(monkeypatch, tmp_path, status):
    outcomes = [{"status": status, "detail": "still broken"}] * (2 if status == "transient" else 1)
    scheduler, calls = _scheduler_trade_environment(monkeypatch, tmp_path, outcomes)
    monkeypatch.setattr(scheduler.time, "sleep", lambda _delay: None)
    with pytest.raises(RuntimeError, match="still broken"):
        scheduler.task_auto_trade()
    assert len(calls) == len(outcomes)
    assert scheduler.already_ran_today("auto_trade") is False


def test_run_now_keeps_daily_idempotency_state(monkeypatch, tmp_path):
    import scheduler

    state_file = tmp_path / "scheduler_state.json"
    state_file.write_text(json.dumps({"auto_trade": "2026-09-28"}), encoding="utf-8")
    monkeypatch.setattr(scheduler, "STATE_FILE", str(state_file))
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(scheduler, "task_auto_trade", lambda: called.append(True))

    # run_now builds its dispatch table at call time, but must never delete state.
    scheduler.run_now("trade")
    assert called == [True]
    assert json.loads(state_file.read_text(encoding="utf-8"))["auto_trade"] == "2026-09-28"


def test_only_adapter_imports_vendor_package():
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in list(root.glob("*.py")) + list((root / "tools").glob("*.py")):
        if path.name == "market_data_adapter.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in {"vnstock", "vnai"}:
                offenders.append(path.name)
            if isinstance(node, ast.Import) and any(alias.name.split(".")[0] in {"vnstock", "vnai"} for alias in node.names):
                offenders.append(path.name)
    assert offenders == []


def test_llm_router_has_no_public_key_scraper():
    source = (Path(__file__).resolve().parent.parent / "llm_router.py").read_text(encoding="utf-8-sig")
    assert "free-llm-api-keys" not in source
    assert "_auto_fetch_keys" not in source
    assert '"keys": keys' not in source


def test_vendor_install_is_pinned_hashed_and_not_extra_index():
    root = Path(__file__).resolve().parent.parent
    vendor = (root / "requirements-vnstock.txt").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
    assert "vnstock==" in vendor and "vnai==" in vendor
    assert vendor.count("--hash=sha256:") == 2
    assert "--index-url https://vnstocks.com/api/simple --no-deps --only-binary=:all: --require-hashes" in workflow
    assert "--extra-index-url" not in workflow


def test_analysis_failure_is_not_marked_success(monkeypatch, tmp_path):
    import auto_trader
    import scheduler

    state_file = tmp_path / "scheduler_state.json"
    monkeypatch.setattr(scheduler, "STATE_FILE", str(state_file))
    monkeypatch.setattr(scheduler, "ANALYSIS_RESULTS_FILE", str(tmp_path / "analysis_results.json"))
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "ict_now", _in_session)
    monkeypatch.setattr(scheduler, "_load_scan_watchlist", lambda: ["FPT"])
    monkeypatch.setattr(
        auto_trader,
        "two_stage_scan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("logic failure")),
    )

    with pytest.raises(RuntimeError, match="logic failure"):
        scheduler.task_market_analysis()
    assert not state_file.exists()


def test_auto_trade_never_runs_outside_the_session(monkeypatch, tmp_path):
    from datetime import datetime
    import scheduler
    from trading_safety import VIETNAM_TZ

    marked = []
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "already_ran_today", lambda _key: False)
    monkeypatch.setattr(scheduler, "mark_ran_today", marked.append)
    monkeypatch.setattr("trading_safety.operational_gate", lambda _base: pytest.fail("must not reach the gate"))
    # Lunch break: skipped but retried later (not marked).
    monkeypatch.setattr(scheduler, "ict_now", lambda: datetime(2026, 9, 29, 11, 50, tzinfo=VIETNAM_TZ))
    assert scheduler.task_auto_trade()["reason"] == "outside trading session (late run)" and marked == []
    # After the close (the delayed 15:42 cron run): skipped and closed for the day.
    monkeypatch.setattr(scheduler, "ict_now", lambda: datetime(2026, 9, 29, 15, 42, tzinfo=VIETNAM_TZ))
    assert scheduler.task_auto_trade()["status"] == "blocked" and marked == ["auto_trade"]
