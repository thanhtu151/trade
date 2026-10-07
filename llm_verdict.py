"""Shared output contract for the buy-gate LLM prompts (debate roles + single-LLM fallback).

Every prompt asks for one JSON object
    {"decision": "MUA" | "GIỮ" | "BÁN", "confidence": 0-100, "reasons": [...], "risks": [...]}
and ``parse_verdict`` validates the reply strictly. A reply that does not fit the
schema returns None, which callers treat as llm_unavailable (T284), not as a GIỮ.
"""

import math
import unicodedata

# Unaccented spellings are accepted because some providers strip Vietnamese diacritics.
VERDICT_DECISIONS = {"MUA": "MUA", "GIỮ": "GIỮ", "GIU": "GIỮ", "BÁN": "BÁN", "BAN": "BÁN"}

VERDICT_EXAMPLES = """<examples>
<example>
Tình huống: RSI 58, MACD bullish, volume 1.8x TB20, score 5/7, ensemble tăng mạnh, weekly uptrend, regime BULL_TREND.
{"decision": "MUA", "confidence": 72, "reasons": ["Ensemble tăng mạnh cùng chiều weekly uptrend", "MACD bullish, volume 1.8x TB20 xác nhận dòng tiền", "Risk/Reward ước tính 2.0"], "risks": ["RSI 58 sẽ vào vùng quá mua nếu tăng thêm vài phiên"]}
</example>
<example>
Tình huống: RSI 49, MACD bullish, volume 0.9x TB20, score 3/7, ensemble trung tính, weekly sideways.
{"decision": "GIỮ", "confidence": 55, "reasons": ["Volume 0.9x TB20 chưa xác nhận tín hiệu MACD", "Risk/Reward ước tính 1.2, dưới ngưỡng 1.5"], "risks": ["Có thể lỡ nhịp tăng nếu volume tăng mạnh"]}
</example>
<example>
Tình huống: RSI 76, MACD bearish, score 3/7, ensemble giảm, regime BEAR_TREND, tin tức -0.40.
{"decision": "BÁN", "confidence": 68, "reasons": ["Ensemble giảm nên không đủ điều kiện mua", "RSI 76 quá mua trong regime BEAR_TREND", "Tin tức tiêu cực -0.40"], "risks": ["Nhịp hồi kỹ thuật có thể kéo giá lên ngắn hạn"]}
</example>
</examples>"""

VERDICT_OUTPUT_FORMAT = """<output_format>
Trả về một JSON object duy nhất, không markdown, không văn bản nào khác:
{"decision": "MUA" | "GIỮ" | "BÁN", "confidence": <số nguyên 0-100>, "reasons": ["..."], "risks": ["..."]}
- decision: một trong ba giá trị MUA, GIỮ, BÁN.
- confidence: số nguyên 0-100, mức tin vào decision.
- reasons: 1-3 câu ngắn ủng hộ decision, mỗi câu dựa trên một số liệu trong <context>.
- risks: 0-3 câu ngắn về điều có thể khiến decision sai.
</output_format>"""


def _string_list(value, min_items):
    if not isinstance(value, list) or len(value) < min_items:
        return None
    if not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return [item.strip() for item in value]


def parse_verdict(raw):
    """Return the normalised verdict, or None when ``raw`` does not match the schema."""
    if not isinstance(raw, dict):
        return None
    decision = raw.get("decision")
    if not isinstance(decision, str):
        return None
    decision = VERDICT_DECISIONS.get(unicodedata.normalize("NFC", decision).strip().upper())
    confidence = raw.get("confidence")
    if decision is None or isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not math.isfinite(confidence) or not 0 <= confidence <= 100:
        return None
    reasons = _string_list(raw.get("reasons"), 1)
    risks = _string_list(raw.get("risks"), 0)
    if reasons is None or risks is None:
        return None
    return {"decision": decision, "confidence": int(round(confidence)), "reasons": reasons, "risks": risks}
