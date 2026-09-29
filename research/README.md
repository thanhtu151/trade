# research/ — backtest trung thực cho chiến lược xếp hạng chéo

Module độc lập, **không** ảnh hưởng hệ thống paper trading đang chạy (`scheduler.py`, `auto_trader.py`).
Mục tiêu: trả lời câu hỏi "chiến lược X có edge sau chi phí trên HOSE/HNX không?" bằng một quy trình
khó tự lừa mình.

## Chạy

```bash
# 1. Tải snapshot dữ liệu (~2 giờ ở tài khoản khách vnstock, 20 lượt/phút)
python -m research.data                                  # tạo research/data/<ngày>/
python -m research.data --resume research/data/<ngày>    # chạy tiếp nếu bị ngắt

# 2. Thí nghiệm momentum (giai đoạn phát triển 2011–2023)
python -m research.run_momentum dev

# 3. Holdout 2024–nay: CHỈ CHẠY MỘT LẦN (có file khóa research/holdout_used.json)
python -m research.run_momentum holdout

# 4. Thí nghiệm giá trị (E/P) — cần BCTC quý trước
python -m research.fundamentals                          # ~1 giờ, 300 mã từng vào vũ trụ từ 2018
python -m research.run_value dev

# 5. Báo cáo markdown
python -m research.report momentum value                 # → research/reports/{momentum,value}.md
```

Kết luận các thí nghiệm: [reports/FINDINGS.md](reports/FINDINGS.md).

## Thành phần

| File | Vai trò |
|---|---|
| `data.py` | Tải mọi cổ phiếu HOSE/HNX/UPCoM/**đã hủy niêm yết** từ 2010 (VCI, phân trang ~2.000 phiên/lần). Lưu parquet theo ngày tải để tái lập. |
| `engine.py` | Mô phỏng theo ngày: khớp ở giá mở cửa phiên sau, phí môi giới + phí Sở, thuế bán 0,1%, trượt giá, lô 100, T+2, không khớp khi kịch trần/sàn lúc mở cửa, giới hạn 5% ADV20 (phần dư chuyển sang phiên sau), stop tính theo gap, xóa sổ mã ngừng giao dịch. |
| `strategies.py` | Vũ trụ point-in-time (top 125 theo giá trị giao dịch trung vị 60 phiên → bỏ 20% mã giá thấp nhất → giữ top 100; niêm yết ≥ 252 phiên). Momentum long-only có bộ đệm thứ hạng, bộ lọc VN-Index/MA200 tùy chọn, bộ chọn ngẫu nhiên cho placebo. |
| `metrics.py` | Sharpe, PSR, MinTRL, Deflated Sharpe, PBO (CSCV), block bootstrap, drawdown. Đã kiểm với ví dụ trong paper gốc. |
| `run_momentum.py` | 4 biến thể **đăng ký trước**, ghi mọi lần chạy vào `trials.jsonl` (số lần thử N cho DSR), placebo, chấm Cửa 1. |
| `fundamentals.py` | BCTC quý từ VCI (2018-Q1 →) với ngày công bố thật; E/P TTM theo ngày (4 quý liên tiếp, hết hạn sau 200 ngày). |
| `run_value.py` | 3 biến thể giá trị đăng ký trước: E/P, E/P + turnover thấp, E/P + momentum. |
| `report.py` | Xuất báo cáo tiếng Việt. |

## Mặc định chi phí (`engine.Costs`)

| Khoản | Mức |
|---|---|
| Môi giới | 0,15% mỗi chiều |
| Phí Sở | 0,03% mỗi chiều |
| Thuế TNCN khi bán | 0,1% |
| Trượt giá | 0,15% mỗi chiều |
| **Tổng khứ hồi** | **≈ 0,76%** (bảo thủ; broker 0 phí còn ≈ 0,46%) |

## Giới hạn đã biết

- **Giá đã điều chỉnh**: vnstock chỉ trả giá điều chỉnh, nên kịch trần/sàn được suy ra từ % biến động
  (mở cửa lệch ≥ 6,5% so với đóng cửa trước và nằm ở cực trị phiên), không so với giá trần/sàn tuyệt đối.
  Vì vậy ngưỡng giá ≥ 10.000đ (theo đề xuất ban đầu) được thay bằng ngưỡng tương đối: chẩn đoán cho thấy ngưỡng tuyệt đối trên giá điều chỉnh chỉ để lại 13 mã năm 2011 và 9 mã năm 2012. Quyết định này đưa ra trước khi xem kết quả chiến lược.
- **Sống sót**: có 232 mã "DELISTED" và mã đã chuyển UPCoM, nhưng mã hủy niêm yết từ lâu mà nguồn không còn
  lưu thì vẫn thiếu. Kết quả vì vậy có thể còn lạc quan hơn thực tế.
- **Sàn giao dịch theo thời gian**: không biết mã nằm ở sàn nào trong quá khứ, nên vũ trụ chỉ dựa vào thanh
  khoản (có thể gồm mã UPCoM thanh khoản cao như ACV, BSR).
- **BCTC chỉ có từ 2018** (VCI), nên thí nghiệm dùng BCTC có giai đoạn phát triển ngắn (07/2019–2023).
- Thuế 5% cổ tức tiền mặt chưa mô hình (giá điều chỉnh đã gộp cổ tức trước thuế).
- Benchmark là chỉ số giá (không gồm cổ tức, không phí) → hơi bất lợi cho benchmark.
