"""
vn_finance_kb.py — Kho tri thức Tài chính Việt Nam cho Chuyên gia Tài chính AI.

Tổng hợp từ 3 vòng deep-research đã kiểm chứng đối kháng (2026-07-12).
Nguyên tắc thiết kế:
  * CHỈ hardcode các sự kiện ỔN ĐỊNH, có trích dẫn (thuế, pháp lý, cấu trúc quỹ,
    quy tắc tài chính cá nhân).
  * KHÔNG hardcode dữ liệu BIẾN ĐỘNG (lãi suất cụ thể, giá vàng, NAV) — chỉ ghi
    nhận cấu trúc/xu hướng và luôn khuyên người dùng tra cứu số liệu cập nhật.
  * Mọi mục đều kèm nguồn để advisor grounding, tránh bịa (chống hallucination).

Dùng: from vn_finance_kb import grounding_context  → chèn vào system/prompt LLM.
"""

from __future__ import annotations

# Ngày chốt dữ liệu nghiên cứu (để advisor biết độ mới).
KB_AS_OF = "2026-07-12"

# ---------------------------------------------------------------------------
# 1. THUẾ ĐẦU TƯ CÁ NHÂN (có trích dẫn — vòng 3)
# ---------------------------------------------------------------------------
TAX = {
    "securities_transfer": {
        "rate": "0,1% trên GIÁ TRỊ BÁN mỗi lần giao dịch (không trừ giá vốn)",
        "note": "Thu ngay cả khi bán LỖ. Áp dụng cho cả chứng khoán phái sinh từ 01/7/2026.",
        "law": "Thông tư 111/2013/TT-BTC (sửa bởi TT 92/2015); TT 87/2026/TT-BTC",
        "caveat": "Đang có đề xuất đổi sang 20% trên lãi ròng — CHƯA ban hành, hiện vẫn 0,1%.",
    },
    "cash_dividend": {
        "rate": "5% (khấu trừ tại nguồn khi công ty chi trả)",
        "note": "Thu nhập từ đầu tư vốn.",
        "law": "Thông tư 111/2013/TT-BTC",
    },
    "savings_interest": {
        "rate": "MIỄN thuế TNCN cho cá nhân",
        "note": "Cá nhân gửi tiết kiệm không phải khai/nộp thuế lãi. (Chỉ áp dụng cá nhân, không áp dụng tổ chức.)",
        "law": "Điều 3 TT 111/2013/TT-BTC; giữ nguyên trong Luật Thuế TNCN 109/2025/QH15 (hiệu lực 01/7/2026)",
    },
    "new_pit_law": {
        "summary": "Luật Thuế TNCN 109/2025/QH15 hiệu lực 01/7/2026: 10 nhóm thu nhập chịu thuế + 21 nhóm miễn thuế.",
        "caveat": "Mới, nên nhắc người dùng đây là quy định sắp/mới hiệu lực và cần xác nhận nghị định hướng dẫn cuối.",
    },
    "gaps": "Thuế cổ tức bằng cổ phiếu, thuế trái phiếu, thuế vàng, thuế crypto: CHƯA có nguồn chắc — advisor phải nói rõ không chắc và khuyên tra Tổng cục Thuế.",
}

# ---------------------------------------------------------------------------
# 2. PHÁP LÝ CRYPTO / TÀI SẢN SỐ (có trích dẫn — vòng 3)
# ---------------------------------------------------------------------------
CRYPTO = {
    "payment_ban": (
        "Ngân hàng Nhà nước CẤM dùng tiền mã hóa làm phương tiện thanh toán: "
        "Bitcoin/crypto KHÔNG phải tiền pháp quy, không phải tiền điện tử, không phải "
        "phương tiện thanh toán hợp pháp. Phát hành/cung ứng/sử dụng để thanh toán có "
        "thể bị phạt hành chính 50-100 triệu VND hoặc truy cứu hình sự (Điều 206 BLHS)."
    ),
    "asset_recognition": (
        "Luật Công nghiệp công nghệ số 71/2025/QH15 (điều khoản tài sản số hiệu lực "
        "01/01/2026) lần đầu định nghĩa TÀI SẢN SỐ là tài sản theo Bộ luật Dân sự; phân "
        "loại tài sản ảo / tài sản mã hóa / tài sản số khác (loại trừ chứng khoán, tiền "
        "pháp định số). Tài sản ảo được dùng để TRAO ĐỔI/ĐẦU TƯ nhưng KHÔNG được dùng thanh toán."
    ),
    "law": "Chỉ thị 10/2014, 5747/NHNN-PC; Nghị định 52/2024/NĐ-CP; Luật 71/2025/QH15; Nghị quyết 05/2025/NQ-CP (thí điểm)",
    "advisor_disclaimer": (
        "Khi nói về crypto, advisor PHẢI nêu: (1) crypto KHÔNG phải phương tiện thanh toán "
        "hợp pháp tại VN; (2) mới chỉ được công nhận là tài sản/tài sản số; (3) khung pháp lý "
        "và thuế đang hình thành, rủi ro pháp lý & biến động rất cao."
    ),
}

# ---------------------------------------------------------------------------
# 3. QUY ĐỊNH TƯ VẤN ĐẦU TƯ (ranh giới pháp lý — vòng 2)
# ---------------------------------------------------------------------------
REGULATION = {
    "regulator": "Ủy ban Chứng khoán Nhà nước (UBCKNN/SSC) quản lý & cấp phép; SRTC cấp chứng chỉ hành nghề.",
    "advisory_gated": (
        "'Tư vấn đầu tư chứng khoán' là hoạt động PHẢI CÓ CHỨNG CHỈ HÀNH NGHỀ "
        "(Thông tư 135/2025/TT-BTC)."
    ),
    "boundary": (
        "Một AI KHÔNG có giấy phép TUYỆT ĐỐI KHÔNG được tư vấn đầu tư chứng khoán cá nhân "
        "hóa. Phải đóng khung mọi nội dung là GIÁO DỤC/THÔNG TIN, không phải 'tư vấn đầu tư'."
    ),
}

# ---------------------------------------------------------------------------
# 4. SẢN PHẨM ĐẦU TƯ (cấu trúc ổn định — vòng 2 & 3)
# ---------------------------------------------------------------------------
PRODUCTS = {
    "savings_structure": (
        "Nhóm Big 4 quốc doanh (Agribank/BIDV/VietinBank/Vietcombank) LUÔN trả lãi suất "
        "THẤP hơn ngân hàng cổ phần 0,5-1,5%/năm (đổi lại độ an toàn cảm nhận cao hơn). "
        "SỐ LIỆU LÃI SUẤT CỤ THỂ BIẾN ĐỘNG LIÊN TỤC — luôn khuyên người dùng tra cứu mới nhất."
    ),
    "mutual_funds": (
        "Quỹ mở phổ biến: VESAF/VEOF/VIBF (VinaCapital), TCBF/TCEF (Techcom Capital), "
        "VCBF, SSISCA, DCDS (Dragon Capital). Mua qua Fmarket, TCBS/TCInvest, hoặc app ngân hàng. "
        "Ví dụ phí VESAF: mua 0%, bán 2% (<12 tháng)/1% (12-24 tháng)/0% (>24 tháng) — phí bán "
        "giảm dần khuyến khích nắm giữ dài hạn. (Phí cần xác nhận lại trước khi dùng.)"
    ),
    "etf": (
        "ETF nội mô phỏng chỉ số, GIAO DỊCH NHƯ CỔ PHIẾU trên sàn qua tài khoản chứng khoán "
        "(khác quỹ mở mua/bán theo NAV cuối ngày). Ví dụ FUEVFVND (DCVFMVN Diamond, Dragon "
        "Capital) mô phỏng VN DIAMOND, phí quản lý ~0,80%, niêm yết HOSE từ 12/5/2020. "
        "Các ETF khác: E1VFVN30 (VN30), FUESSVFL (VNFIN Lead), FUEVN100."
    ),
    "gold": (
        "Vàng SJC biến động CỰC MẠNH: năm 2025 tăng sốc từ ~82-84 triệu lên gần 159 triệu "
        "VND/lượng. Là kênh trú ẩn phổ biến nhưng rủi ro biến động lớn, chênh mua-bán cao. "
        "Giá thay đổi hàng ngày — không hardcode."
    ),
    "bonds_realestate": (
        "Trái phiếu chính phủ (an toàn, lãi thấp) và trái phiếu doanh nghiệp (lãi cao hơn, "
        "rủi ro tín dụng cao hơn — từng có sự cố 2022-2023). Bất động sản: vốn lớn, thanh "
        "khoản thấp. Chi tiết thuế/quy định cần tra cứu nguồn chính thống."
    ),
}

# ---------------------------------------------------------------------------
# 5. QUY TẮC TÀI CHÍNH CÁ NHÂN (chuẩn mực — vòng 3 + kiến thức phổ quát)
# ---------------------------------------------------------------------------
PERSONAL_FINANCE = {
    "budget_50_30_20": (
        "Quy tắc 50/30/20: 50% thu nhập cho NHU CẦU thiết yếu (nhà ở, ăn uống, điện nước, "
        "bảo hiểm, lãi vay), 30% cho MONG MUỐN (giải trí, mua sắm), 20% cho TIẾT KIỆM & ĐẦU TƯ. "
        "(Nguồn: VnExpress + các tổ chức tài chính quốc tế — ổn định, không nhạy thời gian.)"
    ),
    "emergency_fund": (
        "Quỹ khẩn cấp nên bằng ÍT NHẤT 3-6 tháng chi phí sinh hoạt, để nơi dễ rút (tiết kiệm/"
        "tiền gửi ngắn hạn), phòng mất việc hay biến cố. Xây quỹ này TRƯỚC khi đầu tư rủi ro."
    ),
    "asset_allocation_age": (
        "Quy tắc tham khảo '110 (hoặc 100) trừ tuổi' = tỷ trọng % nên dành cho cổ phiếu/tài "
        "sản tăng trưởng; phần còn lại cho tài sản an toàn. Chỉ là điểm KHỞI ĐẦU, cần điều "
        "chỉnh theo hồ sơ rủi ro cá nhân (kiến thức phổ quát — nêu như hướng dẫn, không tuyệt đối)."
    ),
    "compounding": (
        "Lãi kép: lãi sinh lãi theo thời gian → đầu tư SỚM và ĐỀU quan trọng hơn số tiền lớn "
        "một lần. Quy tắc 72: số năm để gấp đôi vốn ≈ 72 / lãi suất %/năm (VD 12%/năm → ~6 năm)."
    ),
    "dca": (
        "Trung bình giá (DCA): đầu tư một số tiền cố định đều đặn bất kể giá lên/xuống → giảm "
        "rủi ro chọn sai thời điểm, phù hợp nhà đầu tư dài hạn không chuyên."
    ),
}


# ---------------------------------------------------------------------------
# 6. SO SÁNH CÁC KÊNH ĐẦU TƯ (đa kênh — Giai đoạn 3)
# ---------------------------------------------------------------------------
# risk: 1 (rất thấp) .. 5 (rất cao). return_note: định tính, KHÔNG hardcode số
# biến động. bands: nhóm rủi ro phù hợp (dùng để lọc gợi ý).
CHANNELS = [
    {
        "name": "Tiền gửi tiết kiệm", "class": "An toàn", "risk": 1,
        "liquidity": "Trung bình–Cao (rút trước hạn mất lãi)",
        "return_note": "Lãi cố định, thấp; Big 4 thấp hơn NH cổ phần 0,5-1,5%",
        "tax": "Lãi MIỄN thuế TNCN (cá nhân)", "min_capital": "Rất nhỏ (vài trăm nghìn)",
        "how": "Ngân hàng / app ngân hàng", "bands": ["Thận trọng", "Thận trọng – Cân bằng", "Cân bằng", "Tăng trưởng", "Mạo hiểm"],
        "note": "Nền tảng cho quỹ khẩn cấp & phần 'An toàn' của danh mục.",
    },
    {
        "name": "Trái phiếu chính phủ", "class": "An toàn", "risk": 1,
        "liquidity": "Trung bình", "return_note": "Lãi thấp–vừa, an toàn cao",
        "tax": "Có ưu đãi/miễn tùy loại — cần tra cứu", "min_capital": "Vừa",
        "how": "Qua quỹ trái phiếu hoặc đại lý", "bands": ["Thận trọng", "Thận trọng – Cân bằng", "Cân bằng"],
        "note": "Ổn định, phù hợp bảo toàn vốn.",
    },
    {
        "name": "Trái phiếu doanh nghiệp", "class": "An toàn–Tăng trưởng", "risk": 3,
        "liquidity": "Thấp–Trung bình", "return_note": "Lãi cao hơn TP chính phủ, kèm RỦI RO TÍN DỤNG",
        "tax": "Cần tra cứu", "min_capital": "Vừa–Lớn",
        "how": "Qua quỹ trái phiếu (an toàn hơn mua lẻ)", "bands": ["Cân bằng", "Tăng trưởng"],
        "note": "Từng có sự cố vỡ nợ 2022-2023 — ưu tiên qua quỹ, chọn tổ chức uy tín.",
    },
    {
        "name": "Quỹ mở (cân bằng/trái phiếu/cổ phiếu)", "class": "Tăng trưởng", "risk": 3,
        "liquidity": "Trung bình (mua/bán theo NAV cuối ngày)",
        "return_note": "Theo thị trường; quản lý chuyên nghiệp; có phí quản lý & phí bán sớm",
        "tax": "Như chứng khoán khi bán", "min_capital": "Nhỏ (từ vài trăm nghìn)",
        "how": "Fmarket, TCBS/TCInvest, app ngân hàng", "bands": ["Thận trọng – Cân bằng", "Cân bằng", "Tăng trưởng", "Mạo hiểm"],
        "note": "Phù hợp người không có thời gian tự chọn cổ phiếu. VD VESAF/VEOF, TCBF, VCBF, DCDS.",
    },
    {
        "name": "ETF (mô phỏng chỉ số)", "class": "Tăng trưởng", "risk": 4,
        "liquidity": "Cao (giao dịch như cổ phiếu trên sàn)",
        "return_note": "Theo chỉ số thị trường; phí quản lý thấp (~0,8%)",
        "tax": "0,1% giá bán mỗi lần (như cổ phiếu)", "min_capital": "Nhỏ",
        "how": "Tài khoản chứng khoán, khớp lệnh trên HOSE", "bands": ["Cân bằng", "Tăng trưởng", "Mạo hiểm"],
        "note": "Đa dạng hóa rẻ. VD FUEVFVND (VN DIAMOND), E1VFVN30 (VN30).",
    },
    {
        "name": "Cổ phiếu (mua trực tiếp)", "class": "Tăng trưởng", "risk": 5,
        "liquidity": "Cao", "return_note": "Tiềm năng cao, biến động MẠNH, cần kiến thức",
        "tax": "0,1% giá bán mỗi lần (kể cả bán lỗ); cổ tức tiền mặt 5%", "min_capital": "Nhỏ",
        "how": "Tài khoản chứng khoán (HOSE/HNX)", "bands": ["Tăng trưởng", "Mạo hiểm"],
        "note": "Đòi hỏi tự nghiên cứu; người mới nên qua quỹ/ETF trước.",
    },
    {
        "name": "Vàng (SJC / vàng nhẫn)", "class": "Phòng thủ", "risk": 3,
        "liquidity": "Cao", "return_note": "Trú ẩn, BIẾN ĐỘNG MẠNH, chênh mua-bán cao",
        "tax": "Cần tra cứu", "min_capital": "Vừa",
        "how": "Tiệm vàng / doanh nghiệp vàng", "bands": ["Thận trọng", "Cân bằng", "Tăng trưởng", "Mạo hiểm"],
        "note": "Chỉ nên chiếm tỷ trọng nhỏ (phòng thủ), không dồn toàn bộ.",
    },
    {
        "name": "Bất động sản", "class": "Tăng trưởng", "risk": 4,
        "liquidity": "Rất thấp", "return_note": "Vốn lớn; tiềm năng dài hạn; kém thanh khoản",
        "tax": "Thuế chuyển nhượng BĐS riêng — tra cứu", "min_capital": "Rất lớn",
        "how": "Trực tiếp hoặc qua quỹ BĐS", "bands": ["Tăng trưởng", "Mạo hiểm"],
        "note": "Cần vốn lớn & dài hạn; khó rút nhanh khi cần tiền.",
    },
    {
        "name": "Crypto / tài sản số", "class": "Đầu cơ", "risk": 5,
        "liquidity": "Cao", "return_note": "Rủi ro RẤT CAO, biến động cực mạnh",
        "tax": "Khung thuế đang hình thành", "min_capital": "Nhỏ",
        "how": "Sàn crypto (rủi ro pháp lý)", "bands": ["Mạo hiểm"],
        "note": "KHÔNG phải phương tiện thanh toán hợp pháp tại VN; chỉ mới công nhận là tài sản số; pháp lý đang hình thành. Chỉ dùng tiền có thể chấp nhận mất.",
    },
]


def channels_for_band(band: str) -> list[dict]:
    """Lọc các kênh phù hợp với nhóm rủi ro, sắp theo mức rủi ro tăng dần."""
    out = [c for c in CHANNELS if band in c.get("bands", [])]
    return sorted(out, key=lambda c: c["risk"])


def grounding_context(topics: list[str] | None = None) -> str:
    """Trả về khối văn bản kiến thức VN để chèn vào prompt LLM (grounding).
    topics: lọc nhóm cần thiết (tax/crypto/regulation/products/personal_finance);
    None = tất cả. Giữ ngắn gọn để tiết kiệm token."""
    all_blocks = {
        "regulation": _fmt_regulation(),
        "tax": _fmt_tax(),
        "crypto": _fmt_crypto(),
        "products": _fmt_products(),
        "personal_finance": _fmt_personal_finance(),
    }
    keys = topics or list(all_blocks)
    parts = [f"[KHO TRI THỨC TÀI CHÍNH VIỆT NAM — chốt dữ liệu {KB_AS_OF}, dùng để trả lời chính xác]"]
    for k in keys:
        if k in all_blocks:
            parts.append(all_blocks[k])
    parts.append(
        "LƯU Ý GROUNDING: Dùng các dữ kiện trên khi liên quan. Với lãi suất/giá vàng/NAV cụ "
        "thể (biến động), KHÔNG bịa số — nói rõ cần tra cứu nguồn cập nhật. Với mục ghi 'chưa "
        "chắc/cần tra cứu', nói thẳng là không chắc chắn."
    )
    return "\n\n".join(parts)


def _fmt_regulation() -> str:
    return ("• PHÁP LÝ TƯ VẤN: " + REGULATION["regulator"] + " " + REGULATION["advisory_gated"]
            + " " + REGULATION["boundary"])


def _fmt_tax() -> str:
    t = TAX
    return (
        "• THUẾ ĐẦU TƯ CÁ NHÂN:\n"
        f"  - Chuyển nhượng chứng khoán: {t['securities_transfer']['rate']}. "
        f"{t['securities_transfer']['note']} ({t['securities_transfer']['law']}). "
        f"{t['securities_transfer']['caveat']}\n"
        f"  - Cổ tức tiền mặt: {t['cash_dividend']['rate']}.\n"
        f"  - Lãi tiết kiệm cá nhân: {t['savings_interest']['rate']} ({t['savings_interest']['law']}).\n"
        f"  - {t['new_pit_law']['summary']} {t['new_pit_law']['caveat']}\n"
        f"  - CHƯA CHẮC: {t['gaps']}"
    )


def _fmt_crypto() -> str:
    return ("• CRYPTO/TÀI SẢN SỐ: " + CRYPTO["payment_ban"] + " " + CRYPTO["asset_recognition"]
            + " → " + CRYPTO["advisor_disclaimer"])


def _fmt_products() -> str:
    p = PRODUCTS
    return (
        "• SẢN PHẨM ĐẦU TƯ:\n"
        f"  - Tiết kiệm: {p['savings_structure']}\n"
        f"  - Quỹ mở: {p['mutual_funds']}\n"
        f"  - ETF: {p['etf']}\n"
        f"  - Vàng: {p['gold']}\n"
        f"  - Trái phiếu/BĐS: {p['bonds_realestate']}"
    )


def _fmt_personal_finance() -> str:
    pf = PERSONAL_FINANCE
    return (
        "• QUY TẮC TÀI CHÍNH CÁ NHÂN:\n"
        f"  - {pf['budget_50_30_20']}\n"
        f"  - {pf['emergency_fund']}\n"
        f"  - {pf['asset_allocation_age']}\n"
        f"  - {pf['compounding']}\n"
        f"  - {pf['dca']}"
    )


if __name__ == "__main__":
    print(grounding_context())
