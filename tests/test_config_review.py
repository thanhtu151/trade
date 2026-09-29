import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config_review  # noqa: E402
import config_store  # noqa: E402

ICT = timezone(timedelta(hours=7))


def make_config(strong=6, weak=4, strong_trades=60, weak_trades=15):
    universe = [f"S{i}" for i in range(strong)] + [f"W{i}" for i in range(weak)]
    ev_data, params = {}, {}
    for t in universe:
        is_strong = t.startswith("S")
        ev_data[t] = {"ev": 1.2 if is_strong else 0.8, "win_rate": 0.55 if is_strong else 0.5,
                      "trades": strong_trades if is_strong else weak_trades, "sharpe": 1.0,
                      "profit_factor": 1.6 if is_strong else 1.3, "status": "ok"}
        params[t] = {"atr_stop": 1.0, "atr_target": 2.0, "confluence_min": 4}
    positive = [t for t in universe if ev_data[t]["ev"] > 0 and ev_data[t]["trades"] >= 5]
    return {"backtest_universe": universe, "ev_data": ev_data, "positive_ev_tickers": positive,
            "negative_ev_tickers": sorted(set(universe) - set(positive)), "optimal_params_per_ticker": params}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_AUTO_PROMOTE", raising=False)
    (tmp_path / config_store.ACTIVE_NAME).write_text(json.dumps({"positive_ev_tickers": ["OLD"]}))
    return tmp_path


def candidate(base, config, created=None):
    envelope = config_store.write_candidate("scheduler", config, base_dir=base, notify=False)
    if created is not None:
        envelope["created_at"] = created.isoformat()
        (base / config_store.CANDIDATE_NAME).write_text(json.dumps(envelope))
    return envelope


def test_ticker_evidence_requires_trades_and_significance():
    ok, detail = config_review.ticker_evidence(
        {"ev": 1.0, "win_rate": 0.55, "trades": 60, "profit_factor": 1.6, "status": "ok"}, 1.0, 2.0)
    assert ok and detail["z"] >= 3 and detail["breakeven"] == pytest.approx(1 / 3 + 0.02, abs=1e-4)
    ok, detail = config_review.ticker_evidence(
        {"ev": 1.0, "win_rate": 0.55, "trades": 15, "profit_factor": 1.6, "status": "ok"}, 1.0, 2.0)
    assert not ok and not detail["C3_trades"] and not detail["C5_evidence"]
    ok, _ = config_review.ticker_evidence(
        {"ev": 1.0, "win_rate": 55, "trades": 60, "profit_factor": 1.1, "status": "ok"}, 1.0, 2.0)
    assert not ok  # profit factor below 1.2 (win rate given in percent is normalised)


def test_strong_candidate_is_filtered_then_promoted(base):
    candidate(base, make_config())
    row = config_review.review_pending(base, notify=False)
    assert row["decision"] == "promote" and row["checks"] == {"C1_valid": True, "C2_fresh": True, "C6_breadth": True}
    active = config_store.load_active(base)
    assert active["positive_ev_tickers"] == [f"S{i}" for i in range(6)]
    assert active["ev_data"]["W0"]["status"] == "not_significant"
    assert config_store.validate(active) == []
    audit = json.loads((base / config_store.AUDIT_NAME).read_text())
    assert audit[-1]["action"] == "promote" and audit[-1]["actor"] == "config_review"
    # Reviewed once only; our own reviewed candidate is not reviewed again.
    assert config_review.review_pending(base, notify=False) is None


def test_realistic_candidate_with_few_trades_is_rejected(base):
    # Like the real rebacktest output: 7-25 trades per ticker over two years.
    candidate(base, make_config(strong=0, weak=50, weak_trades=21))
    row = config_review.review_pending(base, notify=False)
    assert row["decision"] == "reject" and row["qualified"] == []
    assert config_store.load_active(base) == {"positive_ev_tickers": ["OLD"]}


def test_too_few_qualified_tickers_is_rejected(base):
    candidate(base, make_config(strong=3))
    row = config_review.review_pending(base, notify=False)
    assert row["decision"] == "reject" and not row["checks"]["C6_breadth"]


def test_stale_candidate_is_rejected(base):
    candidate(base, make_config(), created=datetime.now(ICT) - timedelta(days=10))
    row = config_review.review_pending(base, notify=False)
    assert row["decision"] == "reject" and not row["checks"]["C2_fresh"]


def test_advisory_mode_does_not_promote(base, monkeypatch):
    monkeypatch.setenv("CONFIG_AUTO_PROMOTE", "false")
    candidate(base, make_config())
    row = config_review.review_pending(base, notify=False)
    assert row["decision"] == "approved_not_promoted"
    assert config_store.load_active(base) == {"positive_ev_tickers": ["OLD"]}


def test_invalid_candidate_is_rejected(base):
    config = make_config()
    config["ev_data"]["S0"]["status"] = "error"  # a data failure during rebacktest
    candidate(base, config)
    row = config_review.review_pending(base, notify=False)
    assert row["decision"] == "reject" and not row["checks"]["C1_valid"]
