"""
Bull vs Bear Debate Agents
Lấy cảm hứng từ TradingAgents (TauricResearch)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
import time

from llm_verdict import VERDICT_EXAMPLES, VERDICT_OUTPUT_FORMAT, parse_verdict

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


def _market_context(ticker, market_data, learning_context="", extra=""):
    """Data block placed first in every debate prompt; instructions follow it."""
    md = market_data or {}
    past = (learning_context or "").strip() or "Chưa có quyết định trước cho mã này."
    return f"""<context>
<ticker>{ticker}</ticker>
<technical>
Giá: {_safe_float(md.get('price', 0)):,.0f} VND
RSI: {md.get('rsi', 'N/A')}
MACD: {"bullish" if md.get('macd_bull') else "bearish"}
Volume: {_safe_float(md.get('vol_ratio', 1), 1.0):.1f}x TB20
Stage1 score: {_safe_float(md.get('score', 0)):,.1f}/7
</technical>
<ml>Ensemble: {md.get('ensemble_signal', 'N/A')}</ml>
<news>Sentiment tin tức (-1 đến 1): {_safe_float(md.get('news_sentiment', 0), 0.0):.2f}</news>
<market>
Weekly trend: {md.get('weekly_trend', 'N/A')}
Market regime: {md.get('market_regime', 'UNKNOWN')}
</market>
<past_decisions>
{past}
</past_decisions>{extra}
</context>"""


def _case_text(case, legacy_key):
    if not case or case.get("llm_unavailable"):
        return "Không có ý kiến (LLM không phản hồi)."
    reasons = case.get("reasons") or case.get(legacy_key) or []
    risks = case.get("risks") or []
    return (
        f"Quyết định: {case.get('decision', 'N/A')}\n"
        f"Luận điểm: {'; '.join(map(str, reasons)) or 'N/A'}\n"
        f"Điểm yếu: {'; '.join(map(str, risks)) or 'N/A'}"
    )


def bull_analyst(ticker, market_data, learning_context=""):
    """
    Bull Analyst: Tìm lý do TẠI SAO NÊN MUA.
    """
    from llm_router import call_llm_json

    prompt = f"""{_market_context(ticker, market_data, learning_context)}

<instructions>
Bạn đóng vai Bull trong cuộc tranh luận về {ticker}. Hãy trình bày luận điểm mua mạnh nhất mà dữ liệu trong <context> cho phép; Bear sẽ trình bày phía ngược lại nên bạn không cần cân bằng hai phía.
- Chỉ dùng số liệu có trong <context>, không thêm tin tức hay con số khác.
- Chọn MUA khi có luận điểm tăng giá đáng tin; nếu dữ liệu không ủng hộ, chọn GIỮ với confidence thấp.
- reasons là các luận điểm tăng giá; risks là điều kiện khiến luận điểm mua sai.
</instructions>

{VERDICT_EXAMPLES}

{VERDICT_OUTPUT_FORMAT}"""

    verdict = parse_verdict(call_llm_json(
        prompt=prompt,
        system="Bạn là chuyên viên phân tích cổ phiếu Việt Nam (HoSE/HNX), đóng vai Bull trong cuộc tranh luận Bull/Bear. Bạn trả lời bằng một JSON object.",
        max_tokens=400,
    ))
    if verdict is None:
        return {
            "stance": "BULL",
            "confidence": 50,
            "top_3_reasons": ["Không đủ data"],
            "summary": "Bull case không xác định",
            "llm_unavailable": True,
        }
    return {"stance": "BULL", **verdict, "top_3_reasons": verdict["reasons"][:3], "summary": verdict["reasons"][0]}


def bear_analyst(ticker, market_data, learning_context=""):
    """
    Bear Analyst: Tìm lý do TẠI SAO KHÔNG NÊN MUA / NÊN BÁN.
    """
    from llm_router import call_llm_json

    prompt = f"""{_market_context(ticker, market_data, learning_context)}

<instructions>
Bạn đóng vai Bear trong cuộc tranh luận về {ticker}. Hãy trình bày luận điểm mạnh nhất để không mua hoặc bán mã này dựa trên dữ liệu trong <context>; Bull sẽ trình bày phía ngược lại nên bạn không cần cân bằng hai phía.
- Chỉ dùng số liệu có trong <context>, không thêm tin tức hay con số khác.
- Chọn BÁN khi rủi ro giảm giá rõ ràng; nếu dữ liệu không cho thấy rủi ro đáng kể, chọn GIỮ với confidence thấp.
- reasons là các rủi ro giảm giá; risks là điều kiện khiến luận điểm Bear sai.
</instructions>

{VERDICT_EXAMPLES}

{VERDICT_OUTPUT_FORMAT}"""

    verdict = parse_verdict(call_llm_json(
        prompt=prompt,
        system="Bạn là chuyên viên quản trị rủi ro cổ phiếu Việt Nam (HoSE/HNX), đóng vai Bear trong cuộc tranh luận Bull/Bear. Bạn trả lời bằng một JSON object.",
        max_tokens=400,
    ))
    if verdict is None:
        return {
            "stance": "BEAR",
            "confidence": 50,
            "top_3_risks": ["Không đủ data"],
            "summary": "Bear case không xác định",
            "llm_unavailable": True,
        }
    return {"stance": "BEAR", **verdict, "top_3_risks": verdict["reasons"][:3], "summary": verdict["reasons"][0]}


def portfolio_manager(ticker, bull_case, bear_case, market_data, learning_context=""):
    """
    Portfolio Manager: Nghe cả 2 phía, ra quyết định cuối cùng.
    """
    from llm_router import call_llm_json

    bull_conf = _safe_float(bull_case.get("confidence", 50), 50)
    bear_conf = _safe_float(bear_case.get("confidence", 50), 50)
    debate = f"""
<bull_case confidence="{bull_conf:.0f}">
{_case_text(bull_case, "top_3_reasons")}
</bull_case>
<bear_case confidence="{bear_conf:.0f}">
{_case_text(bear_case, "top_3_risks")}
</bear_case>
<portfolio>Tiền mặt khả dụng: {_safe_float((market_data or {}).get('cash_available', 0)):,.0f} VND</portfolio>"""

    prompt = f"""{_market_context(ticker, market_data, learning_context, extra=debate)}

<instructions>
Bạn là Portfolio Manager, người ra quyết định cuối cùng cho {ticker} sau khi nghe Bull và Bear tranh luận. Cân nhắc cả hai phía dựa trên số liệu trong <context> rồi đưa ra một quyết định rõ ràng: MUA, GIỮ hoặc BÁN.
Quy tắc quyết định:
1. Khi market regime là BEAR_TREND, chỉ chọn MUA nếu Bull confidence > 70 và Bear confidence < 40.
2. Khi ensemble bearish, không chọn MUA dù luận điểm Bull mạnh.
3. Chỉ chọn MUA khi Risk/Reward (lợi nhuận kỳ vọng / mức lỗ tới điểm cắt lỗ) > 1.5; ghi tỷ lệ ước tính trong reasons.
</instructions>

{VERDICT_EXAMPLES}

{VERDICT_OUTPUT_FORMAT}"""

    verdict = parse_verdict(call_llm_json(
        prompt=prompt,
        system="Bạn là Portfolio Manager của một quỹ cổ phiếu Việt Nam (HoSE/HNX), ra quyết định cuối cùng sau tranh luận Bull/Bear. Bạn trả lời bằng một JSON object.",
        max_tokens=400,
    ))
    if verdict is None:
        # No usable verdict (router down, empty reply, or reply outside the schema).
        # Flag it so the buy gate does not read this placeholder GIỮ as a veto.
        log.warning("  %s: portfolio manager got no LLM verdict — placeholder GIỮ, llm_unavailable", ticker)
        return {
            "action": "GIỮ",
            "confidence": 30,
            "position_size_pct": 0,
            "agreed_with": "neither",
            "key_reason": "Không đủ thông tin để quyết định",
            "llm_unavailable": True,
        }
    return {
        **verdict,
        "action": verdict["decision"],
        "agreed_with": {"MUA": "bull", "BÁN": "bear"}.get(verdict["decision"], "neither"),
        "key_reason": verdict["reasons"][0],
    }


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
