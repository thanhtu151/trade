"""
Bull vs Bear Debate Agents
Lấy cảm hứng từ TradingAgents (TauricResearch)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
import time


BASE_DIR = Path(__file__).parent
log = logging.getLogger("debate_agents")
DEBATE_LOG_FILE = BASE_DIR / "debate_log.json"


def _load_debate_log():
    try:
        with open(DEBATE_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_debate_log(logs):
    logs = list(logs or [])[-100:]
    with open(DEBATE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2, default=str)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def get_past_decisions(ticker, max_recent=3):
    """
    Lấy quyết định gần nhất cho ticker để inject vào context.
    Giống Decision Log của TradingAgents.
    """
    logs = _load_debate_log()
    ticker_logs = [l for l in logs if l.get("ticker") == ticker and l.get("final_decision")]
    recent = ticker_logs[-max_recent:]

    if not recent:
        return ""

    lines = [f"\nLỊCH SỬ QUYẾT ĐỊNH {ticker} ({len(recent)} lần gần nhất):"]
    for entry in recent:
        decision = entry.get("final_decision", {}) or {}
        action = decision.get("action", "?")
        confidence = decision.get("confidence", 0)
        outcome = entry.get("outcome", "chưa có")
        reflection = entry.get("reflection", "")
        date_text = entry.get("date", "?")
        lines.append(
            f"  [{date_text}] {action} (conf={confidence}%)  Kết quả: {outcome}"
            + (f"\n    Bài học: {reflection}" if reflection else "")
        )

    return "\n".join(lines)


def bull_analyst(ticker, market_data, learning_context=""):
    """
    Bull Analyst: Tìm lý do TẠI SAO NÊN MUA.
    """
    from llm_router import call_llm_json

    prompt = f"""Bạn là Bull Analyst chuyên gia tìm cơ hội MUA.
Nhiệm vụ: Đưa ra CASE TỐT NHẤT để MUA {ticker}.
Chỉ tập trung vào upside, đừng đề cập downside.

DỮ LIỆU:
- Giá: {_safe_float(market_data.get('price', 0)):,.0f} VND
- RSI: {market_data.get('rsi', 'N/A')}
- MACD: {"bullish" if market_data.get('macd_bull') else "bearish"}
- Volume: {_safe_float(market_data.get('vol_ratio', 1), 1.0):.1f}x TB20
- Stage1 Score: {_safe_float(market_data.get('score', 0)):,.1f}/7
- Ensemble: {market_data.get('ensemble_signal', 'N/A')}
- Weekly trend: {market_data.get('weekly_trend', 'N/A')}
- News sentiment: {_safe_float(market_data.get('news_sentiment', 0), 0.0):.2f}
{learning_context}

Trả về JSON:
{{
  "stance": "BULL",
  "confidence": <0-100>,
  "top_3_reasons": ["lý do 1", "lý do 2", "lý do 3"],
  "target_price": <giá mục tiêu>,
  "catalyst": "<trigger chính để giá tăng>",
  "summary": "<1 câu tóm tắt case bull>"
}}"""

    result = call_llm_json(
        prompt=prompt,
        system="Bạn là bull analyst chuyên nghiệp. Trả về chỉ JSON.",
        max_tokens=400,
    )
    if not isinstance(result, dict) or not result:
        return {
            "stance": "BULL",
            "confidence": 50,
            "top_3_reasons": ["Không đủ data"],
            "summary": "Bull case không xác định",
        }
    result.setdefault("stance", "BULL")
    result.setdefault("confidence", 50)
    result.setdefault("top_3_reasons", ["Không đủ data"])
    result.setdefault("summary", "Bull case không xác định")
    return result


def bear_analyst(ticker, market_data, learning_context=""):
    """
    Bear Analyst: Tìm lý do TẠI SAO KHÔNG NÊN MUA / NÊN BÁN.
    """
    from llm_router import call_llm_json

    prompt = f"""Bạn là Bear Analyst chuyên gia nhận diện rủi ro.
Nhiệm vụ: Đưa ra CASE TỐT NHẤT để KHÔNG MUA hoặc BÁN {ticker}.
Chỉ tập trung vào downside, đừng đề cập upside.

DỮ LIỆU:
- Giá: {_safe_float(market_data.get('price', 0)):,.0f} VND
- RSI: {market_data.get('rsi', 'N/A')}
- MACD: {"bullish" if market_data.get('macd_bull') else "bearish"}
- Volume: {_safe_float(market_data.get('vol_ratio', 1), 1.0):.1f}x TB20
- Stage1 Score: {_safe_float(market_data.get('score', 0)):,.1f}/7
- Ensemble: {market_data.get('ensemble_signal', 'N/A')}
- Weekly trend: {market_data.get('weekly_trend', 'N/A')}
- News sentiment: {_safe_float(market_data.get('news_sentiment', 0), 0.0):.2f}
- Market regime: {market_data.get('market_regime', 'UNKNOWN')}
{learning_context}

Trả về JSON:
{{
  "stance": "BEAR",
  "confidence": <0-100>,
  "top_3_risks": ["rủi ro 1", "rủi ro 2", "rủi ro 3"],
  "downside_target": <giá giảm có thể>,
  "main_risk": "<rủi ro lớn nhất>",
  "summary": "<1 câu tóm tắt case bear>"
}}"""

    result = call_llm_json(
        prompt=prompt,
        system="Bạn là bear analyst chuyên nghiệp. Trả về chỉ JSON.",
        max_tokens=400,
    )
    if not isinstance(result, dict) or not result:
        return {
            "stance": "BEAR",
            "confidence": 50,
            "top_3_risks": ["Không đủ data"],
            "summary": "Bear case không xác định",
        }
    result.setdefault("stance", "BEAR")
    result.setdefault("confidence", 50)
    result.setdefault("top_3_risks", ["Không đủ data"])
    result.setdefault("summary", "Bear case không xác định")
    return result


def portfolio_manager(ticker, bull_case, bear_case, market_data, learning_context=""):
    """
    Portfolio Manager: Nghe cả 2 phía, ra quyết định cuối cùng.
    """
    from llm_router import call_llm_json

    bull_conf = _safe_float(bull_case.get("confidence", 50), 50)
    bear_conf = _safe_float(bear_case.get("confidence", 50), 50)

    prompt = f"""Bạn là Portfolio Manager người ra quyết định cuối cùng.
Bạn đã nghe Bull Analyst và Bear Analyst tranh luận về {ticker}.
Hãy đưa ra quyết định CUỐI CÙNG dựa trên cả 2 case.

BULL CASE (confidence {bull_conf}%):
- {bull_case.get('summary', 'N/A')}
- Top reasons: {bull_case.get('top_3_reasons', [])}
- Target: {bull_case.get('target_price', 'N/A')}

BEAR CASE (confidence {bear_conf}%):
- {bear_case.get('summary', 'N/A')}
- Top risks: {bear_case.get('top_3_risks', [])}
- Downside: {bear_case.get('downside_target', 'N/A')}

CONTEXT:
- Market regime: {market_data.get('market_regime', 'UNKNOWN')}
- Giá hiện tại: {_safe_float(market_data.get('price', 0)):,.0f}
- Portfolio cash available: {_safe_float(market_data.get('cash_available', 0)):,.0f} VND
{learning_context}

QUY TẮC:
- Nếu market regime = BEAR_TREND chỉ MUA khi bull_conf > 70 VÀ bear_conf < 40
- Nếu ensemble bearish không MUA dù bull case mạnh
- Risk/Reward phải > 1.5 để MUA

Trả về JSON:
{{
  "action": "MUA/BÁN/GIỮ",
  "confidence": <0-100>,
  "position_size_pct": <% portfolio nên dùng, 0 nếu GIỮ>,
  "agreed_with": "bull/bear/neither",
  "key_reason": "<lý do quyết định chính>",
  "risk_reward": <tỷ lệ risk/reward>,
  "target": <giá mục tiêu>,
  "stoploss": <giá cắt lỗ>
}}"""

    result = call_llm_json(
        prompt=prompt,
        system="Bạn là portfolio manager chuyên nghiệp. Quyết định dứt khoát. Trả về chỉ JSON.",
        max_tokens=400,
    )
    if not isinstance(result, dict) or not result:
        return {
            "action": "GIỮ",
            "confidence": 30,
            "position_size_pct": 0,
            "agreed_with": "neither",
            "key_reason": "Không đủ thông tin để quyết định",
        }
    result.setdefault("action", "GIỮ")
    result.setdefault("confidence", 30)
    result.setdefault("position_size_pct", 0)
    result.setdefault("agreed_with", "neither")
    result.setdefault("key_reason", "Không đủ thông tin để quyết định")
    return result


def run_debate(ticker, market_data):
    """
    Chạy full debate pipeline: Bull -> Bear -> Portfolio Manager.
    """
    log.info("Starting Bull vs Bear debate for %s...", ticker)
    start = time.time()
    learning_context = get_past_decisions(ticker, max_recent=3)

    bull_case = bull_analyst(ticker, market_data, learning_context)
    log.info("  Bull: %s (conf=%s%%)", bull_case.get("summary", "N/A"), bull_case.get("confidence", 0))
    time.sleep(0.5)

    bear_case = bear_analyst(ticker, market_data, learning_context)
    log.info("  Bear: %s (conf=%s%%)", bear_case.get("summary", "N/A"), bear_case.get("confidence", 0))
    time.sleep(0.5)

    final_decision = portfolio_manager(ticker, bull_case, bear_case, market_data, learning_context)
    log.info(
        "  Decision: %s (conf=%s%% R/R=%s)",
        final_decision.get("action"),
        final_decision.get("confidence", 0),
        final_decision.get("risk_reward", "N/A"),
    )

    elapsed = time.time() - start
    debate_entry = {
        "ticker": ticker,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().isoformat(),
        "market_data": market_data,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "final_decision": final_decision,
        "elapsed_sec": round(elapsed, 1),
        "outcome": None,
        "reflection": None,
    }

    logs = _load_debate_log()
    logs.append(debate_entry)
    _save_debate_log(logs)
    log.info("Debate done in %.1fs", elapsed)
    return debate_entry


def resolve_debate(ticker, actual_price_3d, entry_price):
    """
    Sau 3 ngày, cập nhật outcome và tạo reflection.
    """
    from llm_router import call_llm_json

    logs = _load_debate_log()
    updated = 0

    for entry in logs:
        if (
            entry.get("ticker") == ticker
            and entry.get("outcome") is None
            and entry.get("final_decision")
        ):
            action = entry["final_decision"].get("action", "GIỮ")
            pnl_pct = (actual_price_3d - entry_price) / entry_price * 100 if entry_price else 0.0

            if action == "MUA":
                outcome = "correct" if pnl_pct > 0 else "incorrect"
            elif action == "BÁN":
                outcome = "correct" if pnl_pct < 0 else "incorrect"
            else:
                outcome = "neutral"

            entry["outcome"] = outcome
            entry["pnl_pct"] = round(pnl_pct, 2)
            entry["actual_price_3d"] = actual_price_3d

            try:
                bull_summary = entry.get("bull_case", {}).get("summary", "")
                bear_summary = entry.get("bear_case", {}).get("summary", "")
                reflection_prompt = f"""Quyết định {action} {ticker} là {outcome} (PnL: {pnl_pct:+.2f}%).
Bull case: {bull_summary}
Bear case: {bear_summary}

Viết 1 câu bài học ngắn gọn (tiếng Việt) từ kết quả này để cải thiện lần sau."""

                reflection_result = call_llm_json(
                    prompt=reflection_prompt,
                    system='Trả về JSON: {"reflection": "<1 câu bài học>"}',
                    max_tokens=100,
                )
                if isinstance(reflection_result, dict):
                    entry["reflection"] = reflection_result.get("reflection", "")
            except Exception as exc:
                log.warning("Reflection failed: %s", exc)

            updated += 1

    if updated:
        _save_debate_log(logs)
        log.info("Resolved %s debates for %s", updated, ticker)

    return updated
