"""Buy-gate LLM prompts share one strict JSON contract; a malformed reply is llm_unavailable (T285)."""

import json
import re

import pytest

from llm_verdict import VERDICT_EXAMPLES, VERDICT_OUTPUT_FORMAT, parse_verdict

VALID = {"decision": "MUA", "confidence": 72, "reasons": ["Ensemble tăng mạnh"], "risks": ["RSI cao"]}
MARKET = {"price": 30_000, "rsi": 58, "macd_bull": True, "vol_ratio": 1.8, "score": 5,
          "ensemble_signal": "tăng mạnh", "weekly_trend": "uptrend", "news_sentiment": 0.2,
          "market_regime": "BULL_TREND", "cash_available": 100_000_000}


# ---- parse_verdict ------------------------------------------------------------------

def test_parse_valid_verdict():
    assert parse_verdict(dict(VALID)) == VALID
    assert parse_verdict({**VALID, "decision": " giữ ", "confidence": 55.4})["decision"] == "GIỮ"
    assert parse_verdict({**VALID, "decision": "BAN"})["decision"] == "BÁN"
    assert parse_verdict({**VALID, "risks": []})["risks"] == []
    assert parse_verdict({**VALID, "extra": 1})["confidence"] == 72


@pytest.mark.parametrize("raw", [
    None, {}, [], "MUA",
    {k: v for k, v in VALID.items() if k != "decision"},
    {k: v for k, v in VALID.items() if k != "risks"},
    {**VALID, "decision": "BUY"},
    {**VALID, "decision": "MUA/GIỮ"},
    {**VALID, "action": "MUA", "decision": None},
    {**VALID, "confidence": "70"},
    {**VALID, "confidence": True},
    {**VALID, "confidence": 101},
    {**VALID, "confidence": -1},
    {**VALID, "confidence": float("nan")},
    {**VALID, "reasons": []},
    {**VALID, "reasons": "Ensemble tăng"},
    {**VALID, "reasons": ["ok", ""]},
    {**VALID, "risks": [1]},
    {"action": "GIỮ", "confidence": 60},                     # old schema
])
def test_parse_rejects_off_schema(raw):
    assert parse_verdict(raw) is None


def test_examples_follow_the_contract():
    replies = [json.loads(line) for line in VERDICT_EXAMPLES.splitlines() if line.startswith("{")]
    assert sorted(parse_verdict(r)["decision"] for r in replies) == ["BÁN", "GIỮ", "MUA"]


# ---- prompt structure ---------------------------------------------------------------

def _captured_prompts(monkeypatch, reply):
    import debate_agents
    import llm_router

    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return reply

    monkeypatch.setattr(llm_router, "call_llm_json", fake)
    monkeypatch.setattr(debate_agents, "_load_debate_log", lambda: [])
    monkeypatch.setattr(debate_agents, "_save_debate_log", lambda _logs: None)
    entry = debate_agents.run_debate("SSI", dict(MARKET))
    return calls, entry


def test_debate_prompts_put_context_first_and_format_last(monkeypatch):
    calls, entry = _captured_prompts(monkeypatch, dict(VALID))
    assert len(calls) == 3
    for call in calls:
        prompt = call["prompt"]
        assert prompt.startswith("<context>")
        assert prompt.index("</context>") < prompt.index("<instructions>") < prompt.index("<examples>")
        assert prompt.rstrip().endswith(VERDICT_OUTPUT_FORMAT)
        assert not re.search(r"CRITICAL|MUST|PHẢI|BẮT BUỘC", prompt + call["system"])
    judge = calls[2]["prompt"]
    assert "<bull_case" in judge and "<bear_case" in judge and "Ensemble: tăng mạnh" in judge
    # Decision thresholds are unchanged.
    assert "Bull confidence > 70 và Bear confidence < 40" in judge and "> 1.5" in judge
    decision = entry["final_decision"]
    assert decision["action"] == "MUA" and decision["agreed_with"] == "bull" and "llm_unavailable" not in decision


def test_off_schema_judge_reply_is_unavailable_not_hold(monkeypatch):
    _calls, entry = _captured_prompts(monkeypatch, {"action": "GIỮ", "confidence": 60, "key_reason": "x"})
    assert entry["final_decision"]["llm_unavailable"] is True
    assert entry["bull_case"]["llm_unavailable"] is True and entry["bear_case"]["llm_unavailable"] is True


# ---- single-LLM fallback in stage 2 ------------------------------------------------

def _stage2_single_llm(monkeypatch, reply):
    import sys
    import types

    import pandas as pd

    import auto_trader
    import data_fetcher
    import learning_engine
    import llm_router
    import risk_limits

    bars = pd.DataFrame({"close": [30_000.0] * 80})
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: bars)
    monkeypatch.setattr(data_fetcher, "get_weekly_trend", lambda *_a, **_k: {"trend": 1})
    monkeypatch.setattr(data_fetcher, "get_news_sentiment_fast", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(learning_engine, "build_llm_context", lambda *_a, **_k: "")
    monkeypatch.setattr(learning_engine, "log_prediction", lambda **_k: None)
    monkeypatch.setitem(sys.modules, "train_ensemble", types.SimpleNamespace(
        ensemble_predict=lambda *_a, **_k: {"direction": 1, "reliable": True, "signal": "tăng", "confidence": 70}))
    monkeypatch.setattr(risk_limits, "market_uptrend_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(auto_trader, "load_ai_fund_config", lambda: {})
    monkeypatch.setattr(auto_trader, "_get_market_regime_simple", lambda: "UNKNOWN")
    monkeypatch.setattr(auto_trader, "load_cash", lambda: 100_000_000.0)
    monkeypatch.setattr(llm_router, "call_llm_json", lambda **_k: reply)
    candidate = {"ticker": "SSI", "score": 3.0, "weighted_score": 3.0, "price": 30_000}
    return auto_trader.stage2_deep_analysis([candidate], use_debate=False)[0]


def test_single_llm_valid_buy_passes(monkeypatch):
    row = _stage2_single_llm(monkeypatch, dict(VALID))
    assert row["tradeable"] is True and row["llm_unavailable"] is False and row["llm"]["action"] == "MUA"


def test_single_llm_valid_hold_blocks(monkeypatch):
    row = _stage2_single_llm(monkeypatch, {**VALID, "decision": "GIỮ"})
    assert row["tradeable"] is False and row["llm_unavailable"] is False


def test_single_llm_off_schema_reply_is_unavailable(monkeypatch):
    row = _stage2_single_llm(monkeypatch, {"action": "GIU", "confidence": 60, "reason": "x"})
    assert row["llm_unavailable"] is True and row["tradeable"] is True
