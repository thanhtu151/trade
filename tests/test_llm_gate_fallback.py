"""LLM outage must not act as a buy veto; a real LLM GIỮ/BÁN still blocks (T284)."""

import json
import logging
import sys
import types

import pandas as pd
import pytest

GOOD_ENSEMBLE = {"direction": 1, "reliable": True, "signal": "tăng mạnh", "confidence": 70}
CANDIDATE = {"ticker": "SSI", "score": 3.0, "weighted_score": 3.0, "price": 30_000}

UNAVAILABLE = {"action": "GIỮ", "confidence": 30, "key_reason": "Không đủ thông tin để quyết định",
               "llm_unavailable": True}


# ---- buy_consensus -------------------------------------------------------------

@pytest.mark.parametrize("item,ensemble,llm,expected", [
    ({"score": 4}, GOOD_ENSEMBLE, UNAVAILABLE, True),                                # outage -> score+ML
    ({"score": 4}, GOOD_ENSEMBLE, {"llm_unavailable": True}, True),
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "GIỮ", "llm_unavailable": False}, False),  # real hold
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "BÁN"}, False),
    ({"score": 4}, GOOD_ENSEMBLE, {"action": "MUA"}, True),
    ({"score": 2}, GOOD_ENSEMBLE, UNAVAILABLE, False),                               # still needs score
    ({"score": 4}, {"direction": 0, "reliable": True}, UNAVAILABLE, False),          # still needs ML
    ({"score": 4}, {"direction": 1, "reliable": False}, UNAVAILABLE, False),
])
def test_buy_consensus_waives_llm_only_when_unavailable(item, ensemble, llm, expected):
    import auto_trader

    assert auto_trader.buy_consensus(item, ensemble, llm) is expected


# ---- debate judge placeholder is flagged ----------------------------------------

def test_portfolio_manager_flags_placeholder_when_llm_returns_nothing(monkeypatch, caplog):
    import debate_agents
    import llm_router

    monkeypatch.setattr(llm_router, "call_llm_json", lambda **_k: {})
    with caplog.at_level(logging.WARNING, logger="debate_agents"):
        decision = debate_agents.portfolio_manager("SSI", {}, {}, {})
    assert decision["action"] == "GIỮ" and decision["llm_unavailable"] is True
    assert "llm_unavailable" in caplog.text

    monkeypatch.setattr(llm_router, "call_llm_json", lambda **_k: {"action": "GIỮ", "confidence": 60})
    assert "llm_unavailable" not in debate_agents.portfolio_manager("SSI", {}, {}, {})


def test_call_llm_warns_when_openai_missing(monkeypatch, tmp_path, caplog):
    import llm_router

    monkeypatch.setattr(llm_router, "OpenAI", None)
    monkeypatch.setattr(llm_router, "USAGE_FILE", str(tmp_path / "usage.json"))
    with caplog.at_level(logging.WARNING, logger="llm_router"):
        result = llm_router.call_llm("hi")
    assert result["success"] is False
    assert "package 'openai' is not installed" in caplog.text


# ---- stage 2 end to end: outage -> fallback, MUA -> pass, GIỮ -> block -----------

def _stage2(monkeypatch, final_decision, market_block=None):
    import auto_trader
    import data_fetcher
    import debate_agents
    import learning_engine
    import risk_limits

    bars = pd.DataFrame({"close": [30_000.0] * 80})
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: bars)
    monkeypatch.setattr(data_fetcher, "get_weekly_trend", lambda *_a, **_k: {"trend": 1})
    monkeypatch.setattr(data_fetcher, "get_news_sentiment_fast", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(learning_engine, "build_llm_context", lambda *_a, **_k: "")
    monkeypatch.setattr(learning_engine, "log_prediction", lambda **_k: None)
    monkeypatch.setitem(sys.modules, "train_ensemble",
                        types.SimpleNamespace(ensemble_predict=lambda *_a, **_k: dict(GOOD_ENSEMBLE)))
    monkeypatch.setattr(risk_limits, "market_uptrend_reason", lambda *_a, **_k: market_block)
    monkeypatch.setattr(auto_trader, "load_ai_fund_config", lambda: {})
    monkeypatch.setattr(auto_trader, "_get_market_regime_simple", lambda: "UNKNOWN")
    monkeypatch.setattr(auto_trader, "load_cash", lambda: 100_000_000.0)
    monkeypatch.setattr(debate_agents, "run_debate", lambda *_a, **_k: {
        "bull_case": {}, "bear_case": {}, "final_decision": final_decision})
    return auto_trader.stage2_deep_analysis([dict(CANDIDATE)])[0]


def test_stage2_llm_outage_falls_back_to_score_and_ensemble(monkeypatch):
    row = _stage2(monkeypatch, dict(UNAVAILABLE))
    assert row["tradeable"] is True
    assert row["llm_unavailable"] is True and row["llm"]["llm_unavailable"] is True


def test_stage2_llm_buy_passes(monkeypatch):
    row = _stage2(monkeypatch, {"action": "MUA", "confidence": 70})
    assert row["tradeable"] is True and row["llm_unavailable"] is False


def test_stage2_llm_hold_blocks(monkeypatch):
    row = _stage2(monkeypatch, {"action": "GIỮ", "confidence": 60, "key_reason": "rủi ro cao"})
    assert row["tradeable"] is False and row["llm_unavailable"] is False


def test_stage2_market_gate_still_blocks_during_llm_outage(monkeypatch):
    row = _stage2(monkeypatch, dict(UNAVAILABLE), market_block="VN30 is below its 10-month average")
    assert row["tradeable"] is False and row["llm_unavailable"] is True


# ---- daily audit refreshes tasks.heal --------------------------------------------

def test_daily_audit_replaces_stale_heal_blocked(tmp_path):
    from self_healing import heal_task_state, record_heal_status

    (tmp_path / "system_status.json").write_text(json.dumps({"tasks": {"heal": {
        "state": "blocked", "last_update": "2026-09-27T12:50:51",
        "blocked_reason": "trading kill switch: TRADING_ENABLED disables trading"}}}), encoding="utf-8")

    record_heal_status({"status": "healthy", "trading_allowed": True, "critical": [], "inhibitors": []},
                       base_dir=tmp_path)
    heal = json.loads((tmp_path / "system_status.json").read_text(encoding="utf-8"))["tasks"]["heal"]
    assert heal["state"] == "success" and "blocked_reason" not in heal

    switch = ["trading kill switch: TRADING_ENABLED disables trading"]
    assert heal_task_state({"trading_allowed": False, "critical": switch, "inhibitors": []})[0] == "blocked"
    assert heal_task_state({"trading_allowed": False, "critical": [], "inhibitors": ["x"]})[0] == "success"
    assert heal_task_state({"trading_allowed": False, "critical": ["cash drift"], "inhibitors": []})[0] == "failed"
