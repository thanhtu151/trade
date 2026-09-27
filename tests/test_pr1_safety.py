import json
import ast
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest


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
    monkeypatch.setattr(self_healing, "trading_is_allowed", lambda _base: True)
    monkeypatch.setattr(auto_trader, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(auto_trader, "load_trades", lambda: trades)
    monkeypatch.setattr(auto_trader, "current_price", lambda _ticker: 100.0)
    monkeypatch.setattr(
        auto_trader,
        "get_kelly_position_size",
        lambda *_args: {"value": 10_000_000, "kelly_fraction": 0.1, "pct_portfolio": 10},
    )
    monkeypatch.setattr(
        data_fetcher,
        "get_stock_data_cached",
        lambda *_args, **_kwargs: pd.DataFrame(
            {"high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20}
        ),
    )

    def save_portfolio(_value):
        writes["portfolio"] += 1

    def save_trades(_value):
        writes["trades"] += 1

    monkeypatch.setattr(auto_trader, "save_portfolio", save_portfolio)
    monkeypatch.setattr(auto_trader, "save_trades", save_trades)

    first = auto_trader.execute_paper_trade(
        "FPT", "BUY", price=100, signal_id="signal-123", run_id="run-a", trade_date="2026-09-28"
    )
    second = auto_trader.execute_paper_trade(
        "FPT", "BUY", price=100, signal_id="signal-123", run_id="run-b", trade_date="2026-09-28"
    )

    state = json.loads((tmp_path / "scheduler_state.json").read_text(encoding="utf-8"))
    record = state["trade_idempotency"]["2026-09-28:FPT:BUY:signal-123"]
    assert first[0] is True
    assert second[0] is False
    assert "duplicate idempotency key" in second[1]
    assert writes == {"portfolio": 1, "trades": 1}
    assert record["run_id"] == "run-a"


def test_run_now_keeps_daily_idempotency_state(monkeypatch, tmp_path):
    import scheduler

    state_file = tmp_path / "scheduler_state.json"
    state_file.write_text(json.dumps({"auto_trade": "2026-09-28"}), encoding="utf-8")
    monkeypatch.setattr(scheduler, "STATE_FILE", str(state_file))
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
    monkeypatch.setattr(scheduler, "_load_scan_watchlist", lambda: ["FPT"])
    monkeypatch.setattr(
        auto_trader,
        "two_stage_scan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("logic failure")),
    )

    with pytest.raises(RuntimeError, match="logic failure"):
        scheduler.task_market_analysis()
    assert not state_file.exists()
