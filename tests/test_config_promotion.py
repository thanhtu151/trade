import ast
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def valid_config():
    return {
        "backtest_universe": ["AAA", "BBB"],
        "positive_ev_tickers": ["AAA"],
        "negative_ev_tickers": ["BBB"],
        "ev_data": {
            "AAA": {"ev": 1.2, "win_rate": 0.6, "trades": 8, "status": "ok"},
            "BBB": {"ev": 0.0, "win_rate": 0.0, "trades": 0, "status": "insufficient_trades"},
        },
        "optimal_params_per_ticker": {
            "AAA": {"atr_stop": 1.0, "atr_target": 2.0, "confluence_min": 4},
            "BBB": {"atr_stop": 1.2, "atr_target": 2.5, "confluence_min": 3},
        },
        "last_updated": "2026-09-28",
    }


def test_only_config_store_python_source_names_active_config():
    offenders = []
    for path in ROOT.glob("*.py"):
        if path.name == "config_store.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        if any(isinstance(node, ast.Constant) and node.value == "backtest_config.json" for node in ast.walk(tree)):
            offenders.append(path.name)
    assert offenders == []


def test_candidate_write_never_changes_active_bytes(tmp_path):
    from config_store import load_candidate, write_candidate

    active = tmp_path / "backtest_config.json"
    active.write_bytes(b'{"sentinel":"active"}\n')
    before = active.read_bytes()
    result = write_candidate("scheduler", valid_config(), base_dir=tmp_path)
    assert active.read_bytes() == before
    assert result["status"] == "valid"
    assert load_candidate(tmp_path)["source"] == "scheduler"


def test_validation_rejects_nan_and_promotion_is_fail_closed(tmp_path):
    from config_store import PROMOTE_CONFIRMATION, promote, write_candidate

    payload = valid_config()
    payload["ev_data"]["AAA"]["ev"] = float("nan")
    candidate = write_candidate("dashboard_pro", payload, base_dir=tmp_path)
    assert candidate["status"] == "invalid"
    assert any("finite" in error for error in candidate["validation_errors"])
    with pytest.raises(ValueError, match="invalid"):
        promote(PROMOTE_CONFIRMATION, actor="tester", base_dir=tmp_path)


def test_validation_rejects_nonfinite_numbers_anywhere_and_inconsistent_universe():
    from config_store import validate

    payload = valid_config()
    payload["optimal_config"] = {"atr_stop_mult": float("inf"), "atr_target_mult": 2.0,
                                 "max_hold_days": 15, "min_confluence": 4}
    payload["positive_ev_tickers"] = ["BBB"]
    payload["negative_ev_tickers"] = []
    errors = validate(payload)
    assert any("must be finite" in error and "optimal_config" in error for error in errors)
    assert any("positive_ev_tickers inconsistent" in error for error in errors)
    assert any("negative_ev_tickers must cover" in error for error in errors)


def test_promote_requires_confirmation_backs_up_and_audits_then_rollback(tmp_path):
    from config_store import (PROMOTE_CONFIRMATION, ROLLBACK_CONFIRMATION, promote, rollback,
                              write_candidate)

    old = {"active": "old"}
    (tmp_path / "backtest_config.json").write_text(json.dumps(old), encoding="utf-8")
    write_candidate("dashboard_legacy", valid_config(), base_dir=tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        promote("wrong", actor="tester", base_dir=tmp_path)
    promoted = promote(PROMOTE_CONFIRMATION, actor="tester", base_dir=tmp_path)
    assert promoted["action"] == "promote"
    assert json.loads((tmp_path / "backtest_config.backup.json").read_text()) == old
    assert json.loads((tmp_path / "backtest_config.json").read_text())["positive_ev_tickers"] == ["AAA"]
    with pytest.raises(ValueError, match="confirmation"):
        rollback("wrong", actor="tester", base_dir=tmp_path)
    rolled_back = rollback(ROLLBACK_CONFIRMATION, actor="tester", base_dir=tmp_path)
    assert rolled_back["action"] == "rollback"
    assert json.loads((tmp_path / "backtest_config.json").read_text()) == old
    audit = json.loads((tmp_path / "backtest_config_audit.json").read_text())
    assert [row["action"] for row in audit] == ["promote", "rollback"]
    assert all(row["actor"] == "tester" and row["old_hash"] != row["new_hash"] for row in audit)


def test_nonfinite_backtest_metrics_become_explicit_insufficient_status():
    from backtester_pro import finite_metric_record

    row = finite_metric_record({
        "expectancy_pct": float("nan"), "win_rate": float("nan"), "trades": 0,
        "sharpe": float("nan"), "profit_factor": float("inf"),
    })
    assert row == {
        "ev": 0.0, "win_rate": 0.0, "trades": 0, "sharpe": 0.0,
        "profit_factor": 0.0, "status": "insufficient_trades",
    }
    assert all(not isinstance(value, float) or math.isfinite(value) for value in row.values())


def test_zero_trade_library_stats_are_sanitized_at_result_boundary():
    from backtester_pro import _build_result_from_stats

    stats = {"Return [%]": 0.0, "Buy & Hold Return [%]": -4.0, "Win Rate [%]": float("nan"),
             "Profit Factor": float("nan"), "Sharpe Ratio": float("nan"),
             "Sortino Ratio": float("nan"), "Calmar Ratio": float("nan"),
             "Max. Drawdown [%]": 0.0, "# Trades": 0, "Expectancy [%]": float("nan"),
             "Kelly Criterion": float("nan")}
    result = _build_result_from_stats("DXG", "test", 2, 0.0015, stats)
    assert result["status"] == "insufficient_trades"
    assert result["trades"] == 0
    assert result["expectancy_pct"] == result["win_rate"] == result["sharpe"] == 0.0
    assert all(not isinstance(value, float) or math.isfinite(value) for value in result.values())


def test_candidate_comparison_includes_universe_sign_params_and_trade_counts():
    from config_store import compare_configs

    active = valid_config()
    candidate = valid_config()
    candidate["positive_ev_tickers"] = ["BBB"]
    candidate["negative_ev_tickers"] = ["AAA"]
    candidate["ev_data"]["AAA"].update(ev=-0.5, trades=12)
    candidate["ev_data"]["BBB"].update(ev=0.7, trades=6, status="ok")
    candidate["optimal_params_per_ticker"]["AAA"]["atr_target"] = 3.0
    report = compare_configs(active, candidate)
    assert report["universe_added"] == ["BBB"]
    assert report["universe_removed"] == ["AAA"]
    assert report["ev_sign_changes"] == 2
    assert report["parameter_changes"]["AAA"]["atr_target"] == {"active": 2.0, "candidate": 3.0}
    assert report["tickers"]["AAA"]["active_trades"] == 8
    assert report["tickers"]["AAA"]["candidate_trades"] == 12


def test_all_three_backtest_writers_only_create_candidates(monkeypatch, tmp_path):
    import backtester
    import backtester_pro

    active = tmp_path / "backtest_config.json"
    active.write_bytes(b'{"active":"unchanged"}')
    before = active.read_bytes()
    monkeypatch.setattr(backtester, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(backtester, "run_backtest", lambda *_a, **_k: {
        "without_ensemble": {"ev_per_trade_pct": 1.0, "win_rate": 0.5, "total_trades": 8,
                             "sharpe_ratio": 1.0, "profit_factor": 1.2}
    })
    backtester.run_portfolio_backtest(["AAA"], source="dashboard_legacy")
    assert active.read_bytes() == before
    from config_store import load_candidate
    assert load_candidate(tmp_path)["source"] == "dashboard_legacy"

    monkeypatch.setattr(backtester_pro, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(backtester_pro, "run_backtest_pro", lambda *_a, **_k: ({
        "expectancy_pct": 1.0, "win_rate": 0.5, "trades": 8, "sharpe": 1.0, "profit_factor": 1.2,
    }, None, None))
    backtester_pro.run_portfolio_backtest_pro(["AAA"], optimize=False, source="dashboard_pro")
    assert active.read_bytes() == before
    assert load_candidate(tmp_path)["source"] == "dashboard_pro"


def test_completed_scheduler_rebacktest_keeps_active_byte_identical(monkeypatch, tmp_path):
    import backtester_pro
    import scheduler
    from datetime import date

    active = tmp_path / "backtest_config.json"
    active.write_bytes(b'{"active":"scheduler-sentinel"}\n')
    before = active.read_bytes()
    (tmp_path / "training_watchlist.json").write_text(json.dumps(["AAA"]))
    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "STATE_FILE", str(tmp_path / "scheduler_state.json"))
    monkeypatch.setattr(scheduler, "ict_today", lambda: date(2026, 9, 28))
    monkeypatch.setattr(backtester_pro, "run_portfolio_backtest_pro", lambda *_a, **_k: {
        "AAA": {"expectancy_pct": 1.0, "win_rate": 0.5, "trades": 8, "sharpe": 1.0,
                "profit_factor": 1.2, "optimal_params": {"atr_stop": 1.0, "atr_target": 2.0,
                                                           "confluence_min": 4}}
    })
    scheduler.task_weekly_rebacktest(force=True)
    assert active.read_bytes() == before
    candidate = json.loads((tmp_path / "backtest_config.candidate.json").read_text())
    assert candidate["source"] == "scheduler"
    assert candidate["status"] == "valid"


def test_dashboard_discloses_candidate_and_has_no_promotion_control():
    source = (ROOT / "dashboard_vn.py").read_text(encoding="utf-8-sig")
    assert "Đã lưu ứng viên, chưa áp dụng" in source
    assert "load_candidate" in source
    assert "PROMOTE_BACKTEST_CONFIG" not in source


def test_workflow_exposes_confirmed_promote_and_rollback():
    workflow = (ROOT / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
    assert "promote-backtest-config" in workflow
    assert "rollback-backtest-config" in workflow
    assert "PROMOTE_BACKTEST_CONFIG" in workflow
    assert "ROLLBACK_BACKTEST_CONFIG" in workflow
