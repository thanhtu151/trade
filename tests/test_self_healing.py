import json

from self_healing import run_self_healing


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def valid_portfolio():
    return {
        "initial_cash": 100_000_000.0,
        "cash": 80_000_000.0,
        "positions": {
            "FPT": {
                "qty": 100_000,
                "avg_price": 100.0,
                "current_price": 110.0,
                "market_value": 1,
                "unrealized_pnl": 0,
                "pnl_pct": 0,
            }
        },
    }


def test_repairs_calculated_fields_and_writes_audit(tmp_path):
    write_json(tmp_path / "paper_portfolio.json", valid_portfolio())
    write_json(tmp_path / "paper_trades.json", [])

    report = run_self_healing(tmp_path)
    repaired = json.loads((tmp_path / "paper_portfolio.json").read_text(encoding="utf-8"))

    assert report["status"] == "healed"
    assert report["trading_allowed"] is True
    assert repaired["positions"]["FPT"]["market_value"] == 11_000_000
    assert repaired["positions"]["FPT"]["unrealized_pnl"] == 1_000_000
    assert repaired["positions"]["FPT"]["pnl_pct"] == 10.0
    assert (tmp_path / "self_healing_state.json").exists()


def test_removes_only_near_identical_automated_duplicates(tmp_path):
    write_json(tmp_path / "paper_portfolio.json", valid_portfolio())
    base = {
        "symbol": "FPT", "side": "BUY", "qty": 100, "price": 100,
        "value": 10_000, "reason": "two_stage_scheduler: BUY",
    }
    write_json(tmp_path / "paper_trades.json", [
        {**base, "time": "2026-08-03 09:00:00"},
        {**base, "time": "2026-08-03 09:00:02"},
        {**base, "time": "2026-08-03 09:01:00"},
    ])

    report = run_self_healing(tmp_path)
    trades = json.loads((tmp_path / "paper_trades.json").read_text(encoding="utf-8"))

    assert report["metrics"]["duplicate_events_removed"] == 1
    assert len(trades) == 2


def test_blocks_negative_cash_without_guessing_repair(tmp_path):
    portfolio = valid_portfolio()
    portfolio["cash"] = -1
    write_json(tmp_path / "paper_portfolio.json", portfolio)
    write_json(tmp_path / "paper_trades.json", [])

    report = run_self_healing(tmp_path)

    assert report["status"] == "blocked"
    assert report["trading_allowed"] is False
    assert any("cash" in item for item in report["critical"])


def test_recovers_corrupt_portfolio_from_backup(tmp_path):
    (tmp_path / "paper_portfolio.json").write_text("{broken", encoding="utf-8")
    write_json(tmp_path / "paper_portfolio.json.bak", valid_portfolio())
    write_json(tmp_path / "paper_trades.json", [])

    report = run_self_healing(tmp_path)

    assert report["trading_allowed"] is True
    assert any("restored paper_portfolio.json" in item for item in report["actions"])


def test_blocks_malformed_trade_instead_of_deleting_it(tmp_path):
    write_json(tmp_path / "paper_portfolio.json", valid_portfolio())
    write_json(tmp_path / "paper_trades.json", [{"symbol": "FPT", "qty": "bad"}])

    report = run_self_healing(tmp_path)
    trades = json.loads((tmp_path / "paper_trades.json").read_text(encoding="utf-8"))

    assert report["trading_allowed"] is False
    assert report["metrics"]["malformed_events"] == 1
    assert len(trades) == 1


def test_checkpoint_blocks_cash_drift_from_new_trade(tmp_path):
    portfolio = valid_portfolio()
    write_json(tmp_path / "paper_portfolio.json", portfolio)
    write_json(tmp_path / "paper_trades.json", [])
    run_self_healing(tmp_path)

    write_json(tmp_path / "paper_trades.json", [{
        "time": "2026-08-03 09:00:00", "symbol": "FPT", "side": "BUY",
        "qty": 100, "price": 100, "value": 10_000, "reason": "scheduler",
    }])
    report = run_self_healing(tmp_path)

    assert report["trading_allowed"] is False
    assert any("checkpoint mismatch" in item for item in report["critical"])


def test_checkpoint_accepts_balanced_new_trade(tmp_path):
    portfolio = valid_portfolio()
    write_json(tmp_path / "paper_portfolio.json", portfolio)
    write_json(tmp_path / "paper_trades.json", [])
    run_self_healing(tmp_path)

    portfolio["cash"] -= 10_000
    write_json(tmp_path / "paper_portfolio.json", portfolio)
    write_json(tmp_path / "paper_trades.json", [{
        "time": "2026-08-03 09:00:00", "symbol": "FPT", "side": "BUY",
        "qty": 100, "price": 100, "value": 10_000, "reason": "scheduler",
    }])
    report = run_self_healing(tmp_path)

    assert report["trading_allowed"] is True
