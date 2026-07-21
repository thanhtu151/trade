"""
vn_live_data.py — Lấy dữ liệu thị trường VN biến động (giá vàng, lãi suất tiết kiệm)
cho Chuyên gia Tài chính AI.

THỰC TẾ QUAN TRỌNG:
  * Không có API MIỄN PHÍ, ỔN ĐỊNH cho lãi suất tiết kiệm VN.
  * Các nguồn giá vàng (SJC, BTMC, DOJI) thường bị Cloudflare/chặn hoặc render JS.
Do đó module thiết kế theo hướng BỀN & TRUNG THỰC:
  1. THỬ fetch động từ vài nguồn (best-effort — có thể chạy được trên mạng thường).
  2. Nếu thất bại → FALLBACK về snapshot nghiên cứu, LUÔN kèm dấu thời gian & cảnh báo
     "có thể đã cũ". Không bao giờ hiển thị số cũ như thể là số thời gian thực.
  3. Cache kết quả (TTL) để tránh gọi mạng liên tục.

Trả về cấu trúc thống nhất: {"live": bool, "as_of": str, "note": str, ...dữ liệu}.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "vn_live_data_cache.json")
CACHE_TTL_MIN = 60  # phút

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------------------------------------------------------------------------
# Cache đơn giản (file JSON, TTL theo phút)
# ---------------------------------------------------------------------------
def _load_cache(key: str):
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        entry = data.get(key)
        if not entry:
            return None
        ts = datetime.fromisoformat(entry["_cached_at"])
        if datetime.now() - ts < timedelta(minutes=CACHE_TTL_MIN):
            return entry["value"]
    except Exception:
        pass
    return None


def _save_cache(key: str, value) -> None:
    data = {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data[key] = {"_cached_at": datetime.now().isoformat(timespec="seconds"), "value": value}
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ===========================================================================
# 1. GIÁ VÀNG
# ===========================================================================

# Snapshot nghiên cứu (vòng 2, task w6g8vk194) — DÙNG KHI KHÔNG FETCH ĐƯỢC.
_GOLD_FALLBACK = {
    "live": False,
    "as_of": "2025 (cuối năm) — theo nghiên cứu, CÓ THỂ ĐÃ CŨ",
    "note": ("Không lấy được giá vàng thời gian thực (nguồn SJC/BTMC bị chặn). Đây là "
             "khoảng theo nghiên cứu cuối 2025 — giá vàng biến động HÀNG NGÀY, hãy tra cứu "
             "giá mới nhất tại SJC/PNJ/DOJI/ngân hàng trước khi ra quyết định."),
    "items": [
        {"name": "Vàng miếng SJC (1 lượng)", "range": "~150–159 triệu VND (đỉnh cuối 2025)"},
        {"name": "Vàng nhẫn 9999", "range": "~145–155 triệu VND (đỉnh cuối 2025)"},
    ],
}


def _try_fetch_gold_btmc():
    """Thử API Bảo Tín Minh Châu (JSON). Trả về list item hoặc None."""
    if requests is None:
        return None
    url = "https://api.btmc.vn/api/BTMCAPI/getpricebtmc?key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v"
    try:
        r = requests.get(url, headers=_UA, timeout=10)
        if r.status_code != 200 or not r.text.strip():
            return None
        data = r.json()
        rows = data.get("DataList", {}).get("Data", [])
        items = []
        for i, row in enumerate(rows[:8]):
            name = row.get(f"@n_{i}") or row.get("@n") or "Vàng"
            buy = row.get(f"@pb_{i}") or row.get("@pb")
            sell = row.get(f"@ps_{i}") or row.get("@ps")
            if buy or sell:
                items.append({"name": str(name), "buy": buy, "sell": sell})
        return items or None
    except Exception:
        return None


def get_gold_price(force: bool = False) -> dict:
    """Giá vàng: thử fetch động, fallback snapshot. Luôn có 'live' & 'note'."""
    if not force:
        cached = _load_cache("gold")
        if cached:
            return cached

    items = _try_fetch_gold_btmc()
    if items:
        result = {
            "live": True,
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": "Nguồn: BTMC (thời gian thực). Giá vàng biến động liên tục.",
            "items": items,
        }
        _save_cache("gold", result)
        return result

    # Thất bại → fallback (không cache lâu để lần sau còn thử lại).
    return dict(_GOLD_FALLBACK)


# ===========================================================================
# 2. LÃI SUẤT TIẾT KIỆM
# ===========================================================================

# Không có API miễn phí ổn định → snapshot nghiên cứu (vòng 2), đóng dấu thời gian.
_DEPOSIT_SNAPSHOT = {
    "live": False,
    "as_of": "đầu 2026 — theo nghiên cứu, CẦN KIỂM CHỨNG",
    "note": ("Không có API lãi suất miễn phí ổn định. Đây là số liệu tham khảo từ nghiên cứu "
             "(đầu 2026). Lãi suất thay đổi liên tục & khác nhau theo kênh (quầy/online) — hãy "
             "xem bảng lãi suất chính thức trên website/app ngân hàng để có số mới nhất."),
    "structure": ("Nhóm Big 4 quốc doanh (Agribank/BIDV/VietinBank/Vietcombank) LUÔN trả lãi "
                  "THẤP hơn ngân hàng cổ phần 0,5–1,5%/năm, đổi lại độ an toàn cảm nhận cao hơn."),
    "rows": [
        {"nhom": "Big 4 quốc doanh", "ky_han_12m": "~5,9%/năm", "ghi_chu": "Agribank thường cao nhất nhóm short-term"},
        {"nhom": "NH cổ phần top", "ky_han_12m": "~7,0–7,4%/năm", "ghi_chu": "VD HLBank, OceanBank, PG Bank, Cake"},
    ],
}


def get_deposit_rates(force: bool = False) -> dict:
    """Lãi suất tiết kiệm: hiện chưa có nguồn live ổn định → snapshot nghiên cứu."""
    # Cache để đồng nhất, dù là snapshot (cho phép sau này cắm nguồn live vào đây).
    cached = None if force else _load_cache("deposit")
    if cached:
        return cached
    result = dict(_DEPOSIT_SNAPSHOT)
    _save_cache("deposit", result)
    return result


# ===========================================================================
# 3. TÓM TẮT CHO GROUNDING / HIỂN THỊ
# ===========================================================================

def market_snapshot_text() -> str:
    """Khối text ngắn để chèn vào prompt LLM khi câu hỏi liên quan giá vàng/lãi suất."""
    gold = get_gold_price()
    dep = get_deposit_rates()
    g = "; ".join(
        f"{it.get('name')}: " + (f"mua {it.get('buy')} / bán {it.get('sell')}" if it.get("sell")
                                 else it.get("range", "?"))
        for it in gold.get("items", [])
    )
    d = "; ".join(f"{r['nhom']} 12 tháng {r['ky_han_12m']}" for r in dep.get("rows", []))
    live_g = "THỜI GIAN THỰC" if gold.get("live") else "THAM KHẢO (có thể cũ)"
    return (
        f"[DỮ LIỆU THỊ TRƯỜNG — {live_g}]\n"
        f"Giá vàng ({gold.get('as_of')}): {g}\n"
        f"Lãi suất tiết kiệm ({dep.get('as_of')}): {d}. {dep.get('structure','')}\n"
        f"LƯU Ý: dữ liệu biến động; khuyên người dùng tra cứu nguồn chính thức mới nhất."
    )


if __name__ == "__main__":
    print(json.dumps(get_gold_price(), ensure_ascii=False, indent=2))
    print(json.dumps(get_deposit_rates(), ensure_ascii=False, indent=2))
    print("\n" + market_snapshot_text())
