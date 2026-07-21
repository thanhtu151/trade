"""
financial_advisor.py — Chuyên gia Tài chính Cá nhân (Personal Financial Advisor).

Giai đoạn 1 (MVP):
  * Hồ sơ rủi ro theo chuẩn CFA Institute (3 chiều: nhu cầu rủi ro, năng lực chịu
    rủi ro khách quan, mức chịu đựng mất mát hành vi) + dòng tiền → risk_profile.json
  * Nhánh "Giáo dục & Giải thích": chat hội thoại giải thích kiến thức tài chính,
    cá nhân hóa theo hồ sơ người dùng.
  * Guardrail THẬN TRỌNG: luôn có disclaimer, KHÔNG ra lệnh mua/bán mã cụ thể,
    khuyến khích tự kiểm chứng + tham vấn chuyên gia, ghi audit log.

Thiết kế dựa trên nghiên cứu đã kiểm chứng (xem memory research-ai-advisor-findings):
  - CFA Institute Investment Risk Profiling (Klement 2020): risk profile = min(risk
    capacity, risk tolerance); không dùng mỗi tuổi tác.
  - FinPersona (ECIR 2025): guardrail "not intended to recommend specific stocks...
    users should consult professional financial advisors".
  - arXiv 2509.09922: audit log + disclaimer + grounding cho AI financial planning.

Module độc lập, chỉ phụ thuộc streamlit + llm_router (fallback nhẹ nếu thiếu).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(BASE_DIR, "risk_profile.json")
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "advisor_audit_log.json")
AUDIT_LOG_MAX = 500  # giữ tối đa N lượt gần nhất

# --- Import LLM router (nền tảng sẵn có) -----------------------------------
try:
    from llm_router import call_llm
    _HAS_LLM = True
except Exception:  # pragma: no cover - môi trường thiếu router
    _HAS_LLM = False

    def call_llm(prompt, system="", max_tokens=800, **kwargs):  # type: ignore
        return {"success": False, "content": None,
                "error": "llm_router không khả dụng"}

# --- Kho tri thức Việt Nam (grounding, chống hallucination) -----------------
try:
    from vn_finance_kb import grounding_context, CHANNELS, channels_for_band
except Exception:  # pragma: no cover
    CHANNELS = []

    def grounding_context(topics=None):  # type: ignore
        return ""

    def channels_for_band(band):  # type: ignore
        return []

# --- Dữ liệu thị trường biến động (giá vàng, lãi suất — best-effort + fallback) ---
try:
    from vn_live_data import get_gold_price, get_deposit_rates, market_snapshot_text
except Exception:  # pragma: no cover
    def get_gold_price(force=False):  # type: ignore
        return {"live": False, "as_of": "?", "note": "vn_live_data không khả dụng", "items": []}

    def get_deposit_rates(force=False):  # type: ignore
        return {"live": False, "as_of": "?", "note": "vn_live_data không khả dụng", "rows": []}

    def market_snapshot_text():  # type: ignore
        return ""


DISCLAIMER = (
    "⚠️ *Thông tin trên chỉ mang tính giáo dục và tham khảo, KHÔNG phải lời khuyên "
    "đầu tư cá nhân. Mọi quyết định đầu tư là của riêng bạn — hãy tự kiểm chứng dữ "
    "liệu và cân nhắc tham vấn chuyên gia tài chính được cấp phép trước khi hành động.*"
)


# ===========================================================================
# 1. HỒ SƠ RỦI RO (CFA 3 chiều + dòng tiền)
# ===========================================================================

def load_profile() -> dict | None:
    try:
        with open(PROFILE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data else None
    except Exception:
        return None


def save_profile(profile: dict) -> None:
    profile = dict(profile)
    profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# Bản đồ điểm cho từng câu trả lời (thang 1..5, cao = chịu rủi ro tốt hơn).
_CAPACITY_HORIZON = {
    "Dưới 1 năm": 1, "1–3 năm": 2, "3–5 năm": 3, "5–10 năm": 4, "Trên 10 năm": 5,
}
_CAPACITY_EMERGENCY = {
    "Chưa có": 1, "Dưới 1 tháng": 2, "1–3 tháng": 3, "3–6 tháng": 4, "Trên 6 tháng": 5,
}
_CAPACITY_NETWORTH = {  # % tài sản ròng đem đầu tư rủi ro
    "Trên 75%": 1, "50–75%": 2, "25–50%": 3, "10–25%": 4, "Dưới 10%": 5,
}
_TOLERANCE_DROP = {  # phản ứng khi danh mục -20%
    "Bán hết ngay": 1, "Bán bớt một phần": 2, "Giữ nguyên, chờ hồi phục": 4,
    "Mua thêm vì giá rẻ": 5,
}
_TOLERANCE_PREF = {
    "Ổn định, lời ít nhưng ít biến động": 1,
    "Cân bằng giữa ổn định và tăng trưởng": 3,
    "Chấp nhận biến động mạnh để lời cao": 5,
}
_NEED_GOAL = {
    "Bảo toàn vốn là chính": 1,
    "Tăng trưởng đều, ổn định": 3,
    "Tăng trưởng nhanh, chấp nhận rủi ro": 5,
}

RISK_BANDS = [
    (0, 1.8, "Thận trọng", "Ưu tiên bảo toàn vốn; phù hợp tiền gửi, trái phiếu, quỹ trái phiếu, tỷ trọng cổ phiếu thấp."),
    (1.8, 2.6, "Thận trọng – Cân bằng", "Nghiêng về an toàn nhưng chấp nhận một phần cổ phiếu/quỹ cân bằng."),
    (2.6, 3.4, "Cân bằng", "Phân bổ cân bằng giữa tài sản an toàn và tài sản tăng trưởng."),
    (3.4, 4.2, "Tăng trưởng", "Nghiêng về cổ phiếu/quỹ cổ phiếu để tăng trưởng, chịu được biến động."),
    (4.2, 5.1, "Mạo hiểm", "Chấp nhận rủi ro cao để tối đa tăng trưởng; biến động lớn."),
]


def _avg(values: list[float]) -> float:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 3.0


def compute_risk_profile(answers: dict) -> dict:
    """Tính hồ sơ rủi ro từ câu trả lời form. Theo CFA: điểm cuối = min(capacity,
    tolerance), vì đầu tư chỉ phù hợp khi nằm trong giới hạn của CẢ HAI chiều."""
    capacity_scores = [
        _CAPACITY_HORIZON.get(answers.get("horizon")),
        _CAPACITY_EMERGENCY.get(answers.get("emergency")),
        _CAPACITY_NETWORTH.get(answers.get("networth_pct")),
    ]
    # Dòng tiền dương (thu > chi) cộng thêm năng lực chịu rủi ro.
    income = float(answers.get("income") or 0)
    expense = float(answers.get("expense") or 0)
    if income > 0:
        surplus_ratio = max(0.0, (income - expense) / income)
        capacity_scores.append(1 + surplus_ratio * 4)  # 1..5

    tolerance_scores = [
        _TOLERANCE_DROP.get(answers.get("drop_reaction")),
        _TOLERANCE_PREF.get(answers.get("preference")),
    ]
    need_score = _NEED_GOAL.get(answers.get("goal"), 3)

    capacity = _avg(capacity_scores)
    tolerance = _avg(tolerance_scores)
    # Nguyên tắc CFA: lấy giá trị thấp hơn giữa năng lực và mức chịu đựng.
    final_score = min(capacity, tolerance)

    band_label, band_desc = "Cân bằng", ""
    for lo, hi, label, desc in RISK_BANDS:
        if lo <= final_score < hi:
            band_label, band_desc = label, desc
            break

    return {
        "answers": answers,
        "capacity_score": round(capacity, 2),
        "tolerance_score": round(tolerance, 2),
        "need_score": round(float(need_score), 2),
        "final_score": round(final_score, 2),
        "risk_band": band_label,
        "risk_band_desc": band_desc,
    }


def profile_summary_text(profile: dict) -> str:
    """Tóm tắt hồ sơ để tiêm vào prompt LLM (personalization)."""
    if not profile:
        return "Người dùng chưa thiết lập hồ sơ rủi ro."
    a = profile.get("answers", {})
    return (
        f"Hồ sơ rủi ro của người dùng: nhóm '{profile.get('risk_band')}' "
        f"(điểm {profile.get('final_score')}/5; năng lực chịu rủi ro "
        f"{profile.get('capacity_score')}, mức chịu đựng mất mát "
        f"{profile.get('tolerance_score')}). "
        f"Tầm nhìn đầu tư: {a.get('horizon','?')}. "
        f"Quỹ khẩn cấp: {a.get('emergency','?')}. "
        f"Mục tiêu: {a.get('goal','?')}. "
        f"Kinh nghiệm: {a.get('experience','?')}."
    )


# ===========================================================================
# 2. GUARDRAIL + AUDIT LOG
# ===========================================================================

# Các mẫu ra lệnh mua/bán cụ thể cần chặn (guardrail thận trọng).
_DIRECTIVE_PATTERNS = [
    r"\bbạn nên mua\b", r"\bhãy mua\b", r"\bnên bán\b", r"\bhãy bán\b",
    r"\bkhuyến nghị mua\b", r"\bkhuyến nghị bán\b", r"\bmua ngay\b", r"\bbán ngay\b",
    r"\bchốt lời\b", r"\bcắt lỗ ngay\b",
]


def apply_guardrails(text: str) -> tuple[str, list[str]]:
    """Hậu kiểm output. Trả về (text_đã_xử_lý, danh_sách_cờ). KHÔNG chặn cứng nội
    dung giáo dục, chỉ gắn cảnh báo nếu phát hiện có vẻ ra lệnh cụ thể, và luôn
    bảo đảm có disclaimer ở cuối."""
    flags = []
    lowered = (text or "").lower()
    for pat in _DIRECTIVE_PATTERNS:
        if re.search(pat, lowered):
            flags.append(pat.strip("\\b"))
    if flags:
        text = (
            "> 🚫 *Lưu ý: nội dung dưới đây có thể chứa gợi ý hành động cụ thể. "
            "Ở chế độ thận trọng, đây KHÔNG phải khuyến nghị mua/bán — chỉ là thông "
            "tin để bạn tự cân nhắc.*\n\n" + (text or "")
        )
    if DISCLAIMER.split("*")[1][:20] not in (text or ""):
        text = (text or "") + "\n\n" + DISCLAIMER
    return text, flags


def _load_audit() -> list:
    try:
        with open(AUDIT_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def append_audit(entry: dict) -> None:
    log = _load_audit()
    log.append(entry)
    log = log[-AUDIT_LOG_MAX:]
    try:
        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ===========================================================================
# 3. NHÁNH GIÁO DỤC & GIẢI THÍCH
# ===========================================================================

EDUCATION_SYSTEM = """Bạn là "Chuyên gia Tài chính Cá nhân" — một trợ lý AI giáo dục tài chính cho nhà đầu tư cá nhân Việt Nam.

VAI TRÒ (Giai đoạn 1 — chỉ GIÁO DỤC & GIẢI THÍCH):
- Giải thích rõ ràng, dễ hiểu các khái niệm tài chính/đầu tư (cổ phiếu, quỹ mở/ETF, trái phiếu, lãi kép, phân bổ tài sản, quỹ khẩn cấp, rủi ro, P/E, cổ tức...).
- Dùng ví dụ thực tế, con số minh họa, và bối cảnh thị trường Việt Nam khi phù hợp.
- Cá nhân hóa cách giải thích theo hồ sơ rủi ro của người dùng (nếu có).
- Dạy TƯ DUY và NGUYÊN TẮC, giúp người dùng tự ra quyết định tốt hơn.

GUARDRAIL BẮT BUỘC (chế độ thận trọng):
1. TUYỆT ĐỐI KHÔNG ra lệnh "mua/bán mã X", không phán cổ phiếu nào sẽ tăng/giảm, không dự đoán giá cụ thể.
2. Nếu người dùng hỏi "nên mua mã gì / mã này có nên mua không", hãy: (a) từ chối đưa lệnh cụ thể, (b) thay vào đó giải thích PHƯƠNG PHÁP để họ tự đánh giá (các tiêu chí, chỉ số cần xem).
3. Không bịa số liệu. Nếu không chắc chắn một con số/sự kiện cụ thể (giá, lãi suất hiện hành, kết quả kinh doanh), hãy nói rõ là bạn không chắc và khuyên người dùng kiểm chứng từ nguồn chính thức (báo cáo tài chính, website UBCKNN, sàn HoSE/HNX, ngân hàng).
4. Không hứa hẹn lợi nhuận, không tạo cảm giác "chắc thắng". Luôn nhắc rủi ro đi kèm.
5. Trả lời bằng tiếng Việt, ngắn gọn, có cấu trúc (gạch đầu dòng khi cần).

Nếu câu hỏi nằm ngoài phạm vi tài chính/đầu tư, lịch sự đưa người dùng quay lại chủ đề."""


def ask_education(question: str, profile: dict | None = None,
                  history: list | None = None) -> dict:
    """Gọi LLM cho nhánh giáo dục, áp guardrail, ghi audit log.
    Trả về {answer, flags, provider, success}."""
    profile_ctx = profile_summary_text(profile)
    convo = ""
    for turn in (history or [])[-6:]:
        role = "Người dùng" if turn.get("role") == "user" else "Chuyên gia"
        convo += f"{role}: {turn.get('content','')}\n"

    kb = grounding_context()
    prompt = (
        f"{kb}\n\n"
        f"{profile_ctx}\n\n"
        f"{('Lịch sử hội thoại gần đây:' + chr(10) + convo) if convo else ''}"
        f"Câu hỏi hiện tại của người dùng: {question}\n\n"
        f"Hãy trả lời với vai trò chuyên gia giáo dục tài chính, ưu tiên dùng dữ kiện "
        f"trong KHO TRI THỨC ở trên khi liên quan, và tuân thủ đầy đủ guardrail."
    )

    result = call_llm(prompt, system=EDUCATION_SYSTEM, max_tokens=900)
    if not result.get("success") or not result.get("content"):
        return {
            "answer": ("Xin lỗi, hiện chưa kết nối được tới mô hình AI "
                       f"({result.get('error','không rõ lý do')}). Bạn thử lại sau nhé.\n\n"
                       + DISCLAIMER),
            "flags": [], "provider": None, "success": False,
        }

    answer, flags = apply_guardrails(result["content"])
    append_audit({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "branch": "education",
        "question": question,
        "provider": result.get("provider"),
        "flags": flags,
        "risk_band": (profile or {}).get("risk_band"),
    })
    return {"answer": answer, "flags": flags,
            "provider": result.get("provider"), "success": True}


# ===========================================================================
# 4. NHÁNH KẾ HOẠCH TÀI CHÍNH CÁ NHÂN (Giai đoạn 2)
# ===========================================================================

# Gợi ý phân bổ tài sản theo nhóm rủi ro (chỉ là ĐIỂM KHỞI ĐẦU mang tính giáo dục).
# 3 lớp: An toàn (tiết kiệm/trái phiếu) · Tăng trưởng (cổ phiếu/quỹ CP/ETF) · Phòng thủ (vàng/khác).
ASSET_ALLOCATION = {
    "Thận trọng":            {"An toàn": 75, "Tăng trưởng": 15, "Phòng thủ (vàng)": 10},
    "Thận trọng – Cân bằng": {"An toàn": 60, "Tăng trưởng": 30, "Phòng thủ (vàng)": 10},
    "Cân bằng":              {"An toàn": 45, "Tăng trưởng": 45, "Phòng thủ (vàng)": 10},
    "Tăng trưởng":           {"An toàn": 30, "Tăng trưởng": 60, "Phòng thủ (vàng)": 10},
    "Mạo hiểm":              {"An toàn": 15, "Tăng trưởng": 75, "Phòng thủ (vàng)": 10},
}


def _emergency_current_months(emergency_answer: str) -> tuple[float, float]:
    """Chuyển câu trả lời hạng mục quỹ khẩn cấp thành khoảng số tháng ước lượng."""
    return {
        "Chưa có": (0, 0), "Dưới 1 tháng": (0, 1), "1–3 tháng": (1, 3),
        "3–6 tháng": (3, 6), "Trên 6 tháng": (6, 12),
    }.get(emergency_answer, (0, 0))


def build_financial_plan(profile: dict | None) -> dict | None:
    """Tính kế hoạch tài chính từ hồ sơ (logic thuần, không LLM — dễ kiểm thử).
    Trả về dict gồm budget 50/30/20, quỹ khẩn cấp, phân bổ tài sản. None nếu thiếu hồ sơ."""
    if not profile:
        return None
    a = profile.get("answers", {})
    income = float(a.get("income") or 0)
    expense = float(a.get("expense") or 0)
    band = profile.get("risk_band", "Cân bằng")

    plan = {"income": income, "expense": expense, "risk_band": band,
            "surplus": max(0.0, income - expense)}

    # Ngân sách 50/30/20 (trên thu nhập)
    if income > 0:
        plan["budget"] = {
            "Nhu cầu thiết yếu (50%)": income * 0.50,
            "Mong muốn (30%)": income * 0.30,
            "Tiết kiệm & Đầu tư (20%)": income * 0.20,
        }
        # Cảnh báo nếu chi tiêu thực tế vượt 80% thu nhập (nhu cầu+mong muốn)
        plan["overspending"] = expense > income * 0.80 if expense else False

    # Quỹ khẩn cấp: mục tiêu 3-6 tháng chi tiêu
    if expense > 0:
        cur_lo, cur_hi = _emergency_current_months(a.get("emergency"))
        plan["emergency"] = {
            "monthly_expense": expense,
            "target_min": expense * 3,
            "target_max": expense * 6,
            "current_months_range": (cur_lo, cur_hi),
            "current_est": expense * cur_lo,  # ước lượng thận trọng (cận dưới)
            "gap_to_min": max(0.0, expense * 3 - expense * cur_lo),
        }

    # Phân bổ tài sản theo nhóm rủi ro (áp lên khoản đầu tư định kỳ hàng tháng)
    alloc_pct = ASSET_ALLOCATION.get(band, ASSET_ALLOCATION["Cân bằng"])
    monthly_invest = income * 0.20 if income > 0 else 0.0  # theo quy tắc 20%
    plan["allocation"] = {
        "monthly_invest": monthly_invest,
        "percentages": alloc_pct,
        "amounts": {k: monthly_invest * v / 100 for k, v in alloc_pct.items()},
    }
    return plan


PLANNING_SYSTEM = """Bạn là "Chuyên gia Tài chính Cá nhân" — trợ lý AI giáo dục tài chính cho nhà đầu tư cá nhân Việt Nam, nhánh LẬP KẾ HOẠCH TÀI CHÍNH.

VAI TRÒ:
- Dựa trên các con số kế hoạch đã tính sẵn (ngân sách 50/30/20, quỹ khẩn cấp, phân bổ tài sản theo nhóm rủi ro), đưa ra NHẬN XÉT và HƯỚNG DẪN cá nhân hóa, thực dụng, động viên.
- Ưu tiên thứ tự đúng: (1) ổn định dòng tiền & kiểm soát chi tiêu, (2) xây quỹ khẩn cấp 3-6 tháng TRƯỚC, (3) rồi mới đầu tư tăng trưởng.
- Giải thích Ý NGHĨA các con số và bước hành động cụ thể tiếp theo cho người dùng.

GUARDRAIL BẮT BUỘC (chế độ thận trọng):
1. Được gợi ý phân bổ theo LỚP TÀI SẢN (tiết kiệm/quỹ/cổ phiếu/vàng) như định hướng giáo dục, NHƯNG TUYỆT ĐỐI KHÔNG ra lệnh mua mã cổ phiếu/quỹ cụ thể nào.
2. Không bịa số liệu lãi suất/giá cả cụ thể. Nếu cần con số cập nhật, khuyên người dùng tra cứu nguồn chính thức.
3. Nhấn mạnh đây là gợi ý mang tính giáo dục, con số phân bổ chỉ là điểm khởi đầu tham khảo.
4. Không hứa hẹn lợi nhuận. Luôn nhắc rủi ro và tầm quan trọng của quỹ khẩn cấp.
5. Tiếng Việt, ngắn gọn, có cấu trúc, giọng khích lệ nhưng trung thực."""


def _fmt_vnd(x: float) -> str:
    """Định dạng số tiền VND gọn (triệu/tỷ)."""
    x = float(x or 0)
    if x >= 1e9:
        return f"{x/1e9:.2f} tỷ"
    if x >= 1e6:
        return f"{x/1e6:.1f} triệu"
    return f"{x:,.0f} đ"


def financial_plan_narrative(profile: dict, plan: dict) -> dict:
    """Sinh nhận xét kế hoạch cá nhân hóa từ LLM (grounding + guardrail + audit)."""
    if not plan:
        return {"answer": "Chưa đủ dữ liệu hồ sơ để lập kế hoạch.", "success": False, "provider": None}

    lines = [f"Nhóm rủi ro: {plan['risk_band']}",
             f"Thu nhập/tháng: {_fmt_vnd(plan['income'])}",
             f"Chi tiêu/tháng: {_fmt_vnd(plan['expense'])}",
             f"Thặng dư/tháng: {_fmt_vnd(plan['surplus'])}"]
    if plan.get("emergency"):
        e = plan["emergency"]
        lines.append(f"Quỹ khẩn cấp mục tiêu: {_fmt_vnd(e['target_min'])}–{_fmt_vnd(e['target_max'])} "
                     f"(còn thiếu ~{_fmt_vnd(e['gap_to_min'])} để đạt mức tối thiểu 3 tháng)")
    if plan.get("allocation"):
        pct = plan["allocation"]["percentages"]
        lines.append("Gợi ý phân bổ khoản đầu tư định kỳ: "
                     + ", ".join(f"{k} {v}%" for k, v in pct.items()))
    facts = "\n".join(lines)

    kb = grounding_context(topics=["personal_finance", "products", "tax"])
    prompt = (
        f"{kb}\n\n"
        f"Các con số kế hoạch đã tính cho người dùng:\n{facts}\n\n"
        f"Hãy đưa ra nhận xét và hướng dẫn cá nhân hóa (4-7 gạch đầu dòng): đánh giá tình hình "
        f"dòng tiền, nhắc thứ tự ưu tiên (quỹ khẩn cấp trước), giải thích gợi ý phân bổ theo lớp "
        f"tài sản phù hợp nhóm rủi ro '{plan['risk_band']}', và bước hành động tiếp theo. Tuân thủ guardrail."
    )
    result = call_llm(prompt, system=PLANNING_SYSTEM, max_tokens=900)
    if not result.get("success") or not result.get("content"):
        return {"answer": "Chưa kết nối được AI để soạn nhận xét. Bạn vẫn xem được các con số ở trên.\n\n"
                + DISCLAIMER, "success": False, "provider": None}

    answer, flags = apply_guardrails(result["content"])
    append_audit({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "branch": "planning", "question": "financial_plan",
        "provider": result.get("provider"), "flags": flags,
        "risk_band": plan.get("risk_band"),
    })
    return {"answer": answer, "flags": flags, "provider": result.get("provider"), "success": True}


# ===========================================================================
# 4b. NHÁNH ĐẦU TƯ ĐA KÊNH (Giai đoạn 3)
# ===========================================================================

CHANNELS_SYSTEM = """Bạn là "Chuyên gia Tài chính Cá nhân" — trợ lý AI giáo dục tài chính cho nhà đầu tư cá nhân Việt Nam, nhánh SO SÁNH KÊNH ĐẦU TƯ.

VAI TRÒ:
- Giải thích và SO SÁNH các kênh đầu tư (tiết kiệm, trái phiếu, quỹ mở, ETF, cổ phiếu, vàng, bất động sản, crypto) theo các tiêu chí: rủi ro, thanh khoản, vốn tối thiểu, thuế, cách tham gia.
- Gợi ý các LỚP/KÊNH tài sản phù hợp với nhóm rủi ro & tình hình của người dùng, giải thích VÌ SAO.
- Nhắc nguyên tắc: xây quỹ khẩn cấp trước, đa dạng hóa, chỉ đầu tư kênh rủi ro cao bằng tiền có thể chấp nhận mất.

GUARDRAIL BẮT BUỘC (chế độ thận trọng):
1. Được gợi ý theo LỚP/KÊNH tài sản như định hướng giáo dục, NHƯNG TUYỆT ĐỐI KHÔNG ra lệnh mua mã cổ phiếu/quỹ/coin cụ thể nào.
2. Với crypto: luôn nêu rõ KHÔNG phải phương tiện thanh toán hợp pháp tại VN, pháp lý đang hình thành, rủi ro rất cao.
3. Không bịa số liệu lãi suất/giá/hiệu suất cụ thể; khuyên tra cứu nguồn cập nhật.
4. Không hứa hẹn lợi nhuận; luôn nêu rủi ro của từng kênh.
5. Tiếng Việt, ngắn gọn, có cấu trúc."""


def channels_narrative(profile: dict | None) -> dict:
    """Sinh nhận xét so sánh kênh đầu tư cá nhân hóa theo nhóm rủi ro (grounding + guardrail)."""
    band = (profile or {}).get("risk_band", "Cân bằng")
    suitable = channels_for_band(band)
    names = ", ".join(c["name"] for c in suitable) or "(chưa xác định)"

    kb = grounding_context(topics=["products", "tax", "crypto", "regulation"])
    live = market_snapshot_text()
    prompt = (
        f"{kb}\n\n{live}\n\n"
        f"{profile_summary_text(profile)}\n\n"
        f"Các kênh đầu tư phù hợp sơ bộ với nhóm rủi ro '{band}': {names}.\n\n"
        f"Hãy đưa ra nhận xét cá nhân hóa (5-8 gạch đầu dòng): với nhóm rủi ro '{band}', nên "
        f"ưu tiên những LỚP/KÊNH tài sản nào và vì sao, cảnh báo rủi ro của từng kênh chính, "
        f"và nhắc nguyên tắc đa dạng hóa + quỹ khẩn cấp trước. Tuân thủ guardrail (không gợi ý sản phẩm cụ thể)."
    )
    result = call_llm(prompt, system=CHANNELS_SYSTEM, max_tokens=1000)
    if not result.get("success") or not result.get("content"):
        return {"answer": "Chưa kết nối được AI để soạn nhận xét. Bạn vẫn xem được bảng so sánh ở trên.\n\n"
                + DISCLAIMER, "success": False, "provider": None}

    answer, flags = apply_guardrails(result["content"])
    append_audit({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "branch": "channels", "question": f"compare_channels[{band}]",
        "provider": result.get("provider"), "flags": flags, "risk_band": band,
    })
    return {"answer": answer, "flags": flags, "provider": result.get("provider"), "success": True}


# ===========================================================================
# 4c. NHÁNH PHÂN TÍCH CỔ PHIẾU (Giai đoạn 4 — tái dùng debate_agents, đóng khung GIÁO DỤC)
# ===========================================================================

def _fetch_market_data_light(ticker: str) -> dict | None:
    """Lấy dữ liệu OHLCV thật (qua data_fetcher, có cache) và tính RSI/MACD/volume.
    Trả về market_data cho bull/bear analyst. None nếu không lấy được."""
    try:
        from data_fetcher import get_stock_data_cached
    except Exception:
        return None
    try:
        df = get_stock_data_cached(ticker, years=1)
    except Exception:
        return None
    if df is None or len(df) < 30:
        return None

    # Chuẩn hóa tên cột.
    cols = {c.lower(): c for c in df.columns}
    close_col = cols.get("close") or cols.get("close_price")
    vol_col = cols.get("volume") or cols.get("vol")
    if not close_col:
        return None
    close = df[close_col].astype(float)

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = (100 - 100 / (1 + rs)).iloc[-1]

    # MACD (12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_bull = bool(macd_line.iloc[-1] > signal.iloc[-1])

    vol_ratio = 1.0
    if vol_col:
        vol = df[vol_col].astype(float)
        avg20 = vol.rolling(20).mean().iloc[-1]
        if avg20 and avg20 > 0:
            vol_ratio = float(vol.iloc[-1] / avg20)

    # Xu hướng tuần đơn giản: so giá hiện tại với TB20.
    ma20 = close.rolling(20).mean().iloc[-1]
    weekly_trend = "tăng" if close.iloc[-1] > ma20 else "giảm"

    return {
        "price": float(close.iloc[-1]),
        "rsi": round(float(rsi), 1) if rsi == rsi else "N/A",  # NaN check
        "macd_bull": macd_bull,
        "vol_ratio": round(vol_ratio, 2),
        "weekly_trend": weekly_trend,
        "ma20": float(ma20) if ma20 == ma20 else None,
    }


STOCK_EDU_SYSTEM = """Bạn là "Chuyên gia Tài chính Cá nhân" — trợ lý AI GIÁO DỤC cho nhà đầu tư cá nhân Việt Nam, nhánh PHÂN TÍCH CỔ PHIẾU MANG TÍNH GIÁO DỤC.

BỐI CẢNH PHÁP LÝ (rất quan trọng): 'Tư vấn đầu tư chứng khoán' tại VN là hoạt động phải có chứng chỉ hành nghề (UBCKNN). Bạn KHÔNG có giấy phép, nên tuyệt đối KHÔNG được đưa ra khuyến nghị mua/bán mã cụ thể. Bạn CHỈ được cung cấp thông tin & giáo dục để người dùng TỰ quyết định.

VAI TRÒ:
- Bạn được cung cấp: dữ liệu kỹ thuật thật của mã, một "góc nhìn tích cực (Bull)" và một "góc nhìn rủi ro (Bear)" do các agent phân tích tạo ra.
- Nhiệm vụ: tổng hợp CÂN BẰNG cả hai góc nhìn và DẠY người dùng CÁCH TỰ CÂN NHẮC — những yếu tố nào cần xem, cách đối chiếu bull vs bear, câu hỏi họ nên tự trả lời.

GUARDRAIL BẮT BUỘC:
1. TUYỆT ĐỐI KHÔNG kết luận "nên MUA" hay "nên BÁN" mã này. KHÔNG đưa giá mục tiêu/cắt lỗ như một lệnh.
2. Trình bày cả hai phía công bằng; nhấn mạnh đây là các GÓC NHÌN để tham khảo, không phải sự thật chắc chắn.
3. Không bịa số liệu ngoài dữ liệu được cung cấp. Chỉ số kỹ thuật (RSI/MACD) chỉ là một phần, không đảm bảo tương lai.
4. Kết thúc bằng việc nhắc: đây là thông tin giáo dục, không phải tư vấn đầu tư; người dùng nên tự nghiên cứu thêm và cân nhắc tham vấn chuyên gia được cấp phép.
5. Tiếng Việt, có cấu trúc rõ ràng."""


def analyze_stock_educational(ticker: str, profile: dict | None = None) -> dict:
    """Tái dùng bull/bear analyst trên dữ liệu thật, đóng khung GIÁO DỤC (không chốt lệnh)."""
    ticker = str(ticker).strip().upper()
    if not ticker or not ticker.isalnum():
        return {"answer": "Mã không hợp lệ. Nhập mã như VCB, FPT, HPG...", "success": False, "provider": None}

    market_data = _fetch_market_data_light(ticker)
    if not market_data:
        return {"answer": f"Chưa lấy được dữ liệu cho mã **{ticker}** (có thể sai mã, hết dữ liệu hoặc "
                "bị giới hạn tần suất). Bạn thử lại hoặc kiểm tra mã.\n\n" + DISCLAIMER,
                "success": False, "provider": None}

    # Tái dùng engine tranh luận sẵn có (chỉ 2 góc nhìn, KHÔNG lấy lệnh của portfolio_manager).
    try:
        from debate_agents import bull_analyst, bear_analyst
        bull = bull_analyst(ticker, market_data) or {}
        bear = bear_analyst(ticker, market_data) or {}
    except Exception as exc:
        return {"answer": f"Không chạy được phân tích ({exc}).\n\n" + DISCLAIMER,
                "success": False, "provider": None}

    data_txt = (
        f"Dữ liệu kỹ thuật thật của {ticker}: giá {market_data['price']:,.0f} VND; "
        f"RSI(14)={market_data['rsi']}; MACD={'tích cực' if market_data['macd_bull'] else 'tiêu cực'}; "
        f"khối lượng {market_data['vol_ratio']}x TB20; xu hướng so TB20: {market_data['weekly_trend']}."
    )
    bull_txt = f"Góc nhìn tích cực (Bull, tự tin {bull.get('confidence','?')}%): " \
               f"{bull.get('summary','')}. Lý do: {bull.get('top_3_reasons', [])}"
    bear_txt = f"Góc nhìn rủi ro (Bear, tự tin {bear.get('confidence','?')}%): " \
               f"{bear.get('summary','')}. Rủi ro: {bear.get('top_3_risks', [])}"

    kb = grounding_context(topics=["tax", "regulation"])
    prompt = (
        f"{kb}\n\n{profile_summary_text(profile)}\n\n"
        f"{data_txt}\n\n{bull_txt}\n\n{bear_txt}\n\n"
        f"Hãy tổng hợp cân bằng hai góc nhìn trên và DẠY người dùng cách tự cân nhắc mã {ticker} "
        f"(các yếu tố cần xem, cách đối chiếu bull/bear, câu hỏi nên tự hỏi). Tuân thủ tuyệt đối "
        f"guardrail: KHÔNG kết luận nên mua hay bán."
    )
    result = call_llm(prompt, system=STOCK_EDU_SYSTEM, max_tokens=1000)
    if not result.get("success") or not result.get("content"):
        return {"answer": "Chưa kết nối được AI. Bạn vẫn xem được dữ liệu & hai góc nhìn ở trên.\n\n"
                + DISCLAIMER, "success": False, "provider": None,
                "market_data": market_data, "bull": bull, "bear": bear}

    answer, flags = apply_guardrails(result["content"])
    append_audit({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "branch": "stock_edu", "question": f"analyze[{ticker}]",
        "provider": result.get("provider"), "flags": flags,
        "risk_band": (profile or {}).get("risk_band"),
    })
    return {"answer": answer, "flags": flags, "provider": result.get("provider"),
            "success": True, "market_data": market_data, "bull": bull, "bear": bear}


# ===========================================================================
# 5. GIAO DIỆN STREAMLIT
# ===========================================================================

def render_financial_advisor_section() -> None:
    """Render trang 'Chuyên gia TC' trong dashboard. Import streamlit tại chỗ để
    module vẫn dùng được ở ngữ cảnh không có Streamlit (test/CLI)."""
    import streamlit as st

    st.markdown("### 🧑‍🏫 Chuyên gia Tài chính Cá nhân")
    st.caption("Giáo dục · Kế hoạch tài chính · Hồ sơ rủi ro · Chế độ guardrail thận trọng")

    profile = load_profile()
    sub_tabs = st.tabs(["💬 Hỏi đáp giáo dục", "📊 Kế hoạch tài chính",
                        "🏦 Đầu tư đa kênh", "📈 Phân tích cổ phiếu", "📋 Hồ sơ rủi ro"])

    # ---- Tab hồ sơ rủi ro ----
    with sub_tabs[4]:
        _render_profile_form(st, profile)

    # ---- Tab kế hoạch tài chính ----
    with sub_tabs[1]:
        _render_plan(st, profile)

    # ---- Tab đầu tư đa kênh ----
    with sub_tabs[2]:
        _render_channels(st, profile)

    # ---- Tab phân tích cổ phiếu ----
    with sub_tabs[3]:
        _render_stock(st, profile)

    # ---- Tab hỏi đáp ----
    with sub_tabs[0]:
        if not profile:
            st.info("💡 Bạn nên thiết lập **Hồ sơ rủi ro** (tab bên phải) trước để chuyên "
                    "gia cá nhân hóa lời giải thích theo tình hình của bạn. Không bắt buộc.")
        else:
            st.success(f"Hồ sơ: **{profile.get('risk_band')}** "
                       f"(điểm rủi ro {profile.get('final_score')}/5). "
                       f"{profile.get('risk_band_desc','')}")

        st.markdown(
            "Hỏi mình bất cứ điều gì về **kiến thức tài chính & đầu tư**: khái niệm, "
            "cách đọc chỉ số, cách phân bổ tài sản, quỹ khẩn cấp, lãi kép... "
            "*(Chế độ thận trọng: mình giải thích phương pháp, không phán mua/bán mã cụ thể.)*"
        )

        if "advisor_chat" not in st.session_state:
            st.session_state.advisor_chat = []

        # gợi ý câu hỏi
        cols = st.columns(3)
        suggestions = [
            "Quỹ khẩn cấp là gì và cần bao nhiêu?",
            "Phân bổ tài sản theo khẩu vị rủi ro thế nào?",
            "Chỉ số P/E dùng để làm gì?",
        ]
        picked = None
        for c, s in zip(cols, suggestions):
            if c.button(s, key=f"sugg_{hash(s)}", use_container_width=True):
                picked = s

        for turn in st.session_state.advisor_chat:
            with st.chat_message(turn["role"]):
                st.markdown(turn["content"])

        user_q = st.chat_input("Nhập câu hỏi về tài chính...") or picked
        if user_q:
            st.session_state.advisor_chat.append({"role": "user", "content": user_q})
            with st.chat_message("user"):
                st.markdown(user_q)
            with st.chat_message("assistant"):
                with st.spinner("Chuyên gia đang soạn câu trả lời..."):
                    res = ask_education(
                        user_q, profile=profile,
                        history=st.session_state.advisor_chat[:-1],
                    )
                st.markdown(res["answer"])
                if res.get("provider"):
                    st.caption(f"↳ nguồn AI: {res['provider']}"
                               + ("  ·  ⚑ đã gắn cảnh báo guardrail" if res.get("flags") else ""))
            st.session_state.advisor_chat.append(
                {"role": "assistant", "content": res["answer"]})

        if st.session_state.advisor_chat:
            if st.button("🗑️ Xóa hội thoại", key="clear_advisor_chat"):
                st.session_state.advisor_chat = []
                st.rerun()


def _render_profile_form(st, profile: dict | None) -> None:
    st.markdown("#### Thiết lập hồ sơ rủi ro (chuẩn CFA — 3 chiều)")
    st.caption("Hồ sơ tổng hợp *năng lực chịu rủi ro khách quan* và *mức chịu đựng mất "
               "mát tâm lý*; điểm cuối lấy giá trị thấp hơn của hai chiều (nguyên tắc CFA).")
    a = (profile or {}).get("answers", {})

    with st.form("risk_profile_form"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**A. Năng lực chịu rủi ro (khách quan)**")
            horizon = st.selectbox("Tầm nhìn đầu tư",
                list(_CAPACITY_HORIZON), index=_idx(list(_CAPACITY_HORIZON), a.get("horizon"), 2))
            emergency = st.selectbox("Quỹ khẩn cấp hiện có (số tháng chi tiêu)",
                list(_CAPACITY_EMERGENCY), index=_idx(list(_CAPACITY_EMERGENCY), a.get("emergency"), 2))
            networth_pct = st.selectbox("Tỷ trọng tài sản định đem đầu tư rủi ro",
                list(_CAPACITY_NETWORTH), index=_idx(list(_CAPACITY_NETWORTH), a.get("networth_pct"), 2))
            income = st.number_input("Thu nhập hàng tháng (triệu VND)",
                min_value=0.0, value=float(a.get("income", 0) or 0) / 1e6 if a.get("income") else 0.0, step=1.0)
            expense = st.number_input("Chi tiêu hàng tháng (triệu VND)",
                min_value=0.0, value=float(a.get("expense", 0) or 0) / 1e6 if a.get("expense") else 0.0, step=1.0)
        with c2:
            st.markdown("**B. Mức chịu đựng mất mát (tâm lý)**")
            drop_reaction = st.selectbox("Nếu danh mục giảm 20% trong 1 tháng, bạn sẽ:",
                list(_TOLERANCE_DROP), index=_idx(list(_TOLERANCE_DROP), a.get("drop_reaction"), 2))
            preference = st.selectbox("Bạn ưu tiên:",
                list(_TOLERANCE_PREF), index=_idx(list(_TOLERANCE_PREF), a.get("preference"), 1))
            st.markdown("**C. Mục tiêu & kinh nghiệm**")
            goal = st.selectbox("Mục tiêu đầu tư chính:",
                list(_NEED_GOAL), index=_idx(list(_NEED_GOAL), a.get("goal"), 1))
            experience = st.selectbox("Kinh nghiệm đầu tư:",
                ["Mới bắt đầu", "Dưới 1 năm", "1–3 năm", "Trên 3 năm"],
                index=_idx(["Mới bắt đầu", "Dưới 1 năm", "1–3 năm", "Trên 3 năm"], a.get("experience"), 0))

        submitted = st.form_submit_button("💾 Lưu & tính hồ sơ rủi ro", use_container_width=True)

    if submitted:
        answers = {
            "horizon": horizon, "emergency": emergency, "networth_pct": networth_pct,
            "income": income * 1e6, "expense": expense * 1e6,
            "drop_reaction": drop_reaction, "preference": preference,
            "goal": goal, "experience": experience,
        }
        new_profile = compute_risk_profile(answers)
        save_profile(new_profile)
        st.success(f"Đã lưu hồ sơ! Nhóm rủi ro của bạn: **{new_profile['risk_band']}** "
                   f"(điểm {new_profile['final_score']}/5)")
        st.info(new_profile["risk_band_desc"])
        m1, m2, m3 = st.columns(3)
        m1.metric("Năng lực chịu rủi ro", f"{new_profile['capacity_score']}/5")
        m2.metric("Mức chịu đựng mất mát", f"{new_profile['tolerance_score']}/5")
        m3.metric("Điểm rủi ro cuối", f"{new_profile['final_score']}/5")
        st.caption("💡 Điểm cuối = min(năng lực, chịu đựng) theo chuẩn CFA — đầu tư chỉ "
                   "phù hợp khi nằm trong giới hạn của cả hai chiều.")


def _render_plan(st, profile: dict | None) -> None:
    st.markdown("#### Kế hoạch tài chính cá nhân")
    if not profile:
        st.warning("⚠️ Bạn cần thiết lập **Hồ sơ rủi ro** (tab bên phải) — nhập thu nhập & chi "
                   "tiêu hàng tháng — thì mình mới tính được ngân sách, quỹ khẩn cấp và phân bổ.")
        return

    plan = build_financial_plan(profile)
    income = plan.get("income", 0)
    if not income:
        st.info("💡 Hồ sơ chưa có thu nhập hàng tháng. Vào tab Hồ sơ rủi ro nhập thu nhập & chi "
                "tiêu để có kế hoạch bằng số tiền cụ thể. Dưới đây là gợi ý phân bổ theo tỷ lệ.")

    # --- Ngân sách 50/30/20 ---
    if plan.get("budget"):
        st.markdown("##### 1️⃣ Ngân sách 50/30/20")
        b = plan["budget"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Nhu cầu thiết yếu (50%)", _fmt_vnd(b["Nhu cầu thiết yếu (50%)"]))
        c2.metric("Mong muốn (30%)", _fmt_vnd(b["Mong muốn (30%)"]))
        c3.metric("Tiết kiệm & Đầu tư (20%)", _fmt_vnd(b["Tiết kiệm & Đầu tư (20%)"]))
        if plan.get("overspending"):
            st.error(f"🚨 Chi tiêu hiện tại ({_fmt_vnd(plan['expense'])}) vượt 80% thu nhập — "
                     "vượt ngưỡng nhu cầu+mong muốn của quy tắc 50/30/20. Ưu tiên giảm chi trước khi đầu tư.")
        else:
            st.success(f"✅ Thặng dư hàng tháng: {_fmt_vnd(plan['surplus'])} — nguồn để xây quỹ khẩn cấp & đầu tư.")

    # --- Quỹ khẩn cấp ---
    if plan.get("emergency"):
        st.markdown("##### 2️⃣ Quỹ khẩn cấp (ưu tiên xây TRƯỚC khi đầu tư)")
        e = plan["emergency"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Mục tiêu tối thiểu (3 tháng)", _fmt_vnd(e["target_min"]))
        c2.metric("Mục tiêu lý tưởng (6 tháng)", _fmt_vnd(e["target_max"]))
        c3.metric("Còn thiếu (ước tính)", _fmt_vnd(e["gap_to_min"]))
        if e["gap_to_min"] > 0 and plan["surplus"] > 0:
            months = e["gap_to_min"] / plan["surplus"]
            st.caption(f"💡 Với thặng dư hiện tại, bạn cần ~{months:.0f} tháng để đạt mức quỹ khẩn cấp tối thiểu.")

    # --- Phân bổ tài sản ---
    if plan.get("allocation"):
        st.markdown(f"##### 3️⃣ Gợi ý phân bổ tài sản — nhóm *{plan['risk_band']}*")
        alloc = plan["allocation"]
        cols = st.columns(len(alloc["percentages"]))
        for col, (k, v) in zip(cols, alloc["percentages"].items()):
            amt = alloc["amounts"][k]
            col.metric(f"{k} ({v}%)", _fmt_vnd(amt) if income else f"{v}%")
        st.caption("⚖️ Đây chỉ là ĐIỂM KHỞI ĐẦU mang tính giáo dục theo nhóm rủi ro của bạn — "
                   "không phải khuyến nghị mua sản phẩm cụ thể. 'An toàn' = tiết kiệm/trái phiếu; "
                   "'Tăng trưởng' = cổ phiếu/quỹ mở/ETF; 'Phòng thủ' = vàng.")

    # --- Nhận xét AI cá nhân hóa ---
    st.markdown("##### 🧠 Nhận xét & hướng dẫn cá nhân hóa")
    if st.button("Tạo nhận xét từ chuyên gia AI", key="gen_plan_narrative", use_container_width=True):
        with st.spinner("Chuyên gia đang phân tích kế hoạch của bạn..."):
            res = financial_plan_narrative(profile, plan)
        st.markdown(res["answer"])
        if res.get("provider"):
            st.caption(f"↳ nguồn AI: {res['provider']}"
                       + ("  ·  ⚑ guardrail" if res.get("flags") else ""))


def _render_channels(st, profile: dict | None) -> None:
    import pandas as pd

    st.markdown("#### So sánh các kênh đầu tư tại Việt Nam")

    # --- Panel dữ liệu thị trường (giá vàng, lãi suất) ---
    with st.expander("💹 Dữ liệu thị trường tham khảo (vàng, lãi suất tiết kiệm)"):
        force = st.button("🔄 Làm mới", key="refresh_market_data")
        gold = get_gold_price(force=force)
        dep = get_deposit_rates(force=force)
        gc, dc = st.columns(2)
        with gc:
            badge = "🟢 Thời gian thực" if gold.get("live") else "🟡 Tham khảo (có thể cũ)"
            st.markdown(f"**🥇 Giá vàng** — {badge}")
            st.caption(f"Cập nhật: {gold.get('as_of','?')}")
            for it in gold.get("items", []):
                val = (f"mua {it.get('buy')} / bán {it.get('sell')}" if it.get("sell")
                       else it.get("range", "?"))
                st.markdown(f"- {it.get('name')}: {val}")
            st.caption(gold.get("note", ""))
        with dc:
            badge = "🟢 Thời gian thực" if dep.get("live") else "🟡 Tham khảo (có thể cũ)"
            st.markdown(f"**🏦 Lãi suất tiết kiệm** — {badge}")
            st.caption(f"Cập nhật: {dep.get('as_of','?')}")
            for r in dep.get("rows", []):
                st.markdown(f"- {r.get('nhom')}: 12 tháng {r.get('ky_han_12m')} ({r.get('ghi_chu','')})")
            st.caption(dep.get("note", ""))

    band = (profile or {}).get("risk_band")
    if band:
        st.success(f"Nhóm rủi ro của bạn: **{band}**. Các kênh **phù hợp sơ bộ** được ⭐ bên dưới.")
    else:
        st.info("💡 Thiết lập Hồ sơ rủi ro để mình đánh dấu kênh phù hợp với bạn. Bảng dưới áp dụng chung.")

    risk_label = {1: "Rất thấp", 2: "Thấp", 3: "Trung bình", 4: "Cao", 5: "Rất cao"}
    suitable_names = {c["name"] for c in channels_for_band(band)} if band else set()

    rows = []
    for c in CHANNELS:
        rows.append({
            "Kênh": ("⭐ " if c["name"] in suitable_names else "") + c["name"],
            "Nhóm": c["class"],
            "Rủi ro": risk_label.get(c["risk"], "?"),
            "Thanh khoản": c["liquidity"],
            "Lợi nhuận (định tính)": c["return_note"],
            "Vốn tối thiểu": c["min_capital"],
            "Thuế": c["tax"],
            "Cách tham gia": c["how"],
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    with st.expander("📌 Lưu ý từng kênh"):
        for c in CHANNELS:
            st.markdown(f"- **{c['name']}**: {c['note']}")

    st.caption("⚖️ Bảng mang tính GIÁO DỤC, không phải khuyến nghị mua sản phẩm cụ thể. "
               "Lợi nhuận để định tính vì lãi suất/giá biến động — hãy tra cứu số liệu cập nhật. "
               "Đầu tư kênh rủi ro cao chỉ bằng tiền có thể chấp nhận mất, sau khi đã có quỹ khẩn cấp.")

    st.markdown("##### 🧠 Nhận xét & gợi ý phân bổ đa kênh cá nhân hóa")
    if st.button("Tạo nhận xét từ chuyên gia AI", key="gen_channels_narrative", use_container_width=True):
        with st.spinner("Chuyên gia đang so sánh các kênh cho bạn..."):
            res = channels_narrative(profile)
        st.markdown(res["answer"])
        if res.get("provider"):
            st.caption(f"↳ nguồn AI: {res['provider']}"
                       + ("  ·  ⚑ guardrail" if res.get("flags") else ""))


def _render_stock(st, profile: dict | None) -> None:
    st.markdown("#### Phân tích cổ phiếu (giáo dục — 2 góc nhìn Bull vs Bear)")
    st.warning("⚖️ **Đây KHÔNG phải tư vấn đầu tư.** Theo quy định UBCKNN, tư vấn đầu tư chứng "
               "khoán cần chứng chỉ hành nghề. Mục này chỉ trình bày 2 góc nhìn trên dữ liệu thật "
               "và dạy bạn cách TỰ cân nhắc — không chốt lệnh mua/bán.")

    c1, c2 = st.columns([2, 1])
    ticker = c1.text_input("Nhập mã cổ phiếu", value="", placeholder="VD: VCB, FPT, HPG",
                           key="stock_edu_ticker").strip().upper()
    run = c2.button("Phân tích", key="run_stock_edu", use_container_width=True)

    if run and ticker:
        with st.spinner(f"Đang lấy dữ liệu & chạy 2 góc nhìn cho {ticker}..."):
            res = analyze_stock_educational(ticker, profile=profile)

        md = res.get("market_data")
        if md:
            st.markdown("##### 📊 Dữ liệu kỹ thuật thật")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Giá", f"{md['price']:,.0f}")
            m2.metric("RSI(14)", md["rsi"])
            m3.metric("MACD", "Tích cực" if md["macd_bull"] else "Tiêu cực")
            m4.metric("Volume", f"{md['vol_ratio']}x TB20")

        bull, bear = res.get("bull"), res.get("bear")
        if bull or bear:
            bc, bec = st.columns(2)
            with bc:
                st.markdown(f"**🟢 Góc nhìn tích cực** (tự tin {bull.get('confidence','?')}%)")
                for r in (bull.get("top_3_reasons") or [])[:3]:
                    st.markdown(f"- {r}")
            with bec:
                st.markdown(f"**🔴 Góc nhìn rủi ro** (tự tin {bear.get('confidence','?')}%)")
                for r in (bear.get("top_3_risks") or [])[:3]:
                    st.markdown(f"- {r}")

        st.markdown("##### 🧠 Tổng hợp & cách tự cân nhắc")
        st.markdown(res["answer"])
        if res.get("provider"):
            st.caption(f"↳ nguồn AI: {res['provider']}"
                       + ("  ·  ⚑ guardrail" if res.get("flags") else ""))
    elif run:
        st.info("Nhập mã cổ phiếu trước khi bấm Phân tích.")


def _idx(options: list, value, default: int) -> int:
    try:
        return options.index(value)
    except (ValueError, TypeError):
        return default


if __name__ == "__main__":
    # Kiểm thử nhanh không cần Streamlit.
    demo = compute_risk_profile({
        "horizon": "5–10 năm", "emergency": "3–6 tháng", "networth_pct": "10–25%",
        "income": 30e6, "expense": 18e6, "drop_reaction": "Giữ nguyên, chờ hồi phục",
        "preference": "Cân bằng giữa ổn định và tăng trưởng",
        "goal": "Tăng trưởng đều, ổn định", "experience": "1–3 năm",
    })
    print(json.dumps(demo, ensure_ascii=False, indent=2))
    print("\nTóm tắt:", profile_summary_text(demo))
    print("\nGuardrail test:", apply_guardrails("Bạn nên mua VCB ngay hôm nay.")[1])
