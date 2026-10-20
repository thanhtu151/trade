# PROTOCOL v2 — Win-rate v2 (meta-labeling) — NHÁP ngày 2026-10-16 (T546)

sếp cho mở 16/10, CEO đã ký 16/10 theo ràng buộc dưới.

File này BỔ SUNG `PROTOCOL.md` (đóng băng 2026-09-30), không thay thế. Mọi điều PROTOCOL.md không nói lại ở đây vẫn giữ nguyên (phí mục 3, kiểm định mục 4, cổng (a) mục 5). Ghi vào `trial_log.md` mục T546 TRƯỚC mọi lần chạy. Sau khi CEO duyệt bản này, không sửa; muốn đổi phải có PROTOCOL_v3.

## 0. Ràng buộc (CEO, 16/10)
- Lý do mở: sếp hỏi cách tăng tỉ lệ thắng (16/10) → cho mở hướng mới ngoài lưới 17 cấu hình (30/9).
- **Ngân sách: tối đa 6 cấu hình**, lấy từ **12 suất dự phòng còn lại** (30 − 18 đã dùng: T48 6, T51 6, T54 2, T330 2, T331 2). Cả 6 tính vào N/DSR. Cấu hình bỏ dở/lỗi vẫn tính. Sau v2 còn tối thiểu 6 suất.
- **N tổng DSR hiện tại = 103** → sau v2: 103 + số cấu hình đã dùng (tối đa 109).
- **Chỉ dữ liệu ≤ 2023-12-31.** KHÔNG đọc holdout 2024–2026 (holdout đã dùng 1 lần cho E1/E6 ở T55; `t42/HOLDOUT_APPROVED.txt` KHÔNG áp cho v2). Không chạy lại GA (T330).
- **Không lặp hướng đã thử:** T38/T42 (momentum/breakout/RSI2 làm luật độc lập, lưới tham số), T192 (lưới bộ lọc precision ≥80% trên prediction_log và Stage1: 0 bộ lọc đạt), T330 (GA — overfit), T331 (tỷ trọng Stage1/ETF), T348 (khả thi tiền thật). v2 KHÔNG đổi tham số tín hiệu gốc, KHÔNG thêm lưới bộ lọc thủ công; chỉ thêm 1 mô hình meta cố định.

## 1. Thước đo
- **Chính: precision** = tỉ lệ lệnh có lợi nhuận **sau phí** (khứ hồi 0,5%, `engine.bench.protocol_config()`) > 0.
- Precision CHỈ được báo là kết quả khi đồng thời: (i) kỳ vọng/lệnh sau phí > 0; (ii) ≥ 200 lệnh trên OOS nối. Thiếu một trong hai → "không kết luận".
- So sánh bắt buộc: (a) tín hiệu gốc không lọc; (b) **lọc ngẫu nhiên 50 seed cùng tỉ lệ giữ lệnh** từng cửa sổ (z, phân vị); (c) **lõi ETF E1** (VN30 ≥ MA10 tháng → E1VFVN30) cùng kỳ, cùng phí; (d) VN-Index, VN30.
- Phụ (mô tả + cổng): kỳ vọng/lệnh TB & trung vị, CI bootstrap 95% (10.000 lần), CAGR/Sharpe/MaxDD/PF danh mục, DSR (N tổng), `gate_a` 11 tiêu chí.

## 2. Thiết kế Phần A (chạy ở task sau; KHÔNG chạy trong T546)
**Tín hiệu gốc (giữ nguyên tham số, code `engine/candidates.py`):**
- S1 = breakout10 N=50, stop/trail 2ATR, PIT top-50 gồm mã hủy niêm yết, haircut 30%, cổng VN30 MA10 tháng (trial_log N 294: 293 lệnh danh mục, thắng 38,2%).
- S2 = momentum5p L=60, stop 2ATR, top5/keep10 (N 293: 303 lệnh, thắng 45,2%).

**Đơn vị nhãn = sự kiện tín hiệu, không phải lệnh danh mục.** Lý do (đo trên `t42/trades_*_pit.csv`): lệnh danh mục 2021–2023 chỉ có 174 (S1) và 184 (S2) < 200 ngay cả trước khi lọc → nếu dùng lệnh danh mục thì chắc chắn "không kết luận". Sự kiện = mỗi (mã, ngày t) mà tín hiệu gốc cho vào lệnh (mã trong PIT top-50 tại t, cổng VN30 bật, S1: close lập đỉnh 50 phiên; S2: lọt top-5 hạng momentum và đang không có sự kiện mở cùng mã). Mỗi sự kiện mô phỏng độc lập 1 lệnh; bỏ sự kiện mới của cùng mã khi sự kiện trước chưa đóng (không đếm trùng).

**Nhãn triple-barrier theo đúng luật thoát hiện có:**
- Vào: mở cửa t+1, +trượt 0,075%; mở cửa ở trần (biên ±7% HOSE, `limits=True`) → sự kiện HỦY (không nhãn).
- Rào dưới: stop/trail 2ATR (S1: đóng cửa < đỉnh − 2ATR; stop cứng 2ATR trong phiên). S2: stop 2ATR.
- Rào thời gian/sự kiện: S1 trail như trên; S2 rời top-10. Không có rào trên cố định (luật gốc không có chốt lời).
- T+2: không bán trước t+3 (khớp t+1, bán sớm nhất phiên thứ 2 sau khớp); kẹt sàn → giữ đến phiên có khớp; mã ngừng giao dịch → haircut 30%.
- Nhãn y = 1 nếu lợi nhuận sau khứ hồi 0,5% > 0, ngược lại 0. Lưu cả `t_exit` để purge.

**Đặc trưng (10, cố định, chỉ thông tin tới đóng cửa t):** (1) VN30 / MA10 tháng (tháng trước) − 1; (2) breadth = % mã PIT top-50 có close > MA50 tại t; (3) ATR14/close; (4) log GTGD TB 20 phiên; (5) GTGD 5 phiên / 60 phiên; (6) close/MA20 − 1; (7) close/MA200 − 1; (8) RSI14; (9) lợi suất phiên t; (10) điểm tín hiệu gốc (S1: lợi suất 50 phiên; S2: lợi suất bỏ 5 phiên L=60). Kiểm `engine.bench.causal_check` (cắt panel tại t phải ra cùng đặc trưng) trước khi train; vi phạm > 0 → dừng.

**Mô hình:** LightGBM (có sẵn trong `.venv`, 4.6.0), siêu tham số CỐ ĐỊNH, không tune: `n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=546`. Một mô hình riêng cho mỗi tín hiệu.

**Walk-forward (PROTOCOL mục 4), 3 cửa sổ:** train 2018–20 → test 2021; 2018–21 → 2022; 2018–22 → 2023 (panel làm nóng từ 2016). Mô hình và ngưỡng đóng băng trước mỗi test.
- Purge: bỏ khỏi train mọi sự kiện có `t_exit` ≥ ngày test đầu tiên; **embargo 5 phiên** trước ngày test đầu tiên (sự kiện có t trong 5 phiên đó bị bỏ khỏi train).
- **Ngưỡng xác suất — 1 luật cố định trên train:** τ = trung vị xác suất OOF trên train, lấy từ purged K-fold 5 nhóm liên tiếp trên train (purge theo `t_exit`, embargo 5 phiên sau mỗi nhóm). Giữ sự kiện test có p ≥ τ. Không thử ngưỡng khác.
- Lọc ngẫu nhiên: 50 seed, mỗi cửa sổ giữ ngẫu nhiên đúng số sự kiện mà meta giữ → z precision, z kỳ vọng.
- Danh mục: chạy engine 10 vị thế × 10%, max_new như T42, chỉ nhận sự kiện được giữ → CAGR/Sharpe/MaxDD/PF, so lõi ETF E1 và VN-Index/VN30 cùng OOS 2021–2023; DSR với N tổng; `gate_a`.

**Cấu hình tính vào N (đăng ký trước):**
| # | Cấu hình |
|---|---|
| A1 | S1 sự kiện gốc, không lọc, phí giao thức (mốc) |
| A2 | S2 sự kiện gốc, không lọc, phí giao thức (mốc) |
| A3 | S1 + lọc meta LightGBM (sự kiện + danh mục) |
| A4 | S2 + lọc meta LightGBM (sự kiện + danh mục) |
| A5–A6 | DỰ TRỮ chỉ cho lần chạy lỗi/bỏ dở phải chạy lại (vẫn tính N). Không dùng cho ý mới. |
Seed ngẫu nhiên không tính là cấu hình. Nếu Phần B tìm được dữ liệu dòng tiền PIT, KHÔNG thêm vào A3/A4; muốn thử phải đăng ký cấu hình riêng (lấy từ A5–A6 cần CEO duyệt lại).

**n_params** = tham số gốc (S1 4, S2 5) + 10 đặc trưng + 1 ngưỡng = 15 / 16 → cần ≥ 750 / 800 sự kiện OOS để qua tiêu chí `n_params ≤ lệnh/50`; thiếu thì ghi rõ trượt tiêu chí này.

**Quy tắc quyết định (đăng ký trước), trên OOS 2021–2023 nối, từng tín hiệu:**
- ĐẠT (win-rate) nếu đủ: (1) ≥ 200 sự kiện giữ; (2) kỳ vọng/lệnh sau phí > 0; (3) precision lọc − precision gốc ≥ 5 điểm % VÀ z so với 50 seed lọc ngẫu nhiên ≥ 2; (4) precision lọc ≥ gốc ở ≥ 2/3 cửa sổ.
- ĐỦ TƯ CÁCH vào paper chỉ khi thêm `gate_a` 11/11 (vượt lõi ETF E1 cả CAGR lẫn Sharpe, DSR ≥ 0,95, PBO ≤ 0,2). Không đạt → loại, không tinh chỉnh thêm.
- Mỗi cấu hình chạy 1 lần, khóa `research/t546a/runs.json` trước khi tính chỉ số. Chạy thử nhỏ (1 cửa sổ, `--dry-run`, không tính chỉ số OOS) trước khi chạy đủ.

## 3. Rủi ro đã biết (ghi trước)
- B 2021–2023 đã được xem ở T38/T42 khi chọn S1/S2 → cửa sổ test không hoàn toàn sạch đối với tín hiệu gốc; bộ lọc meta là mới.
- Sự kiện chồng lấp theo thời gian (nhiều mã cùng ngày, cùng regime) → n hiệu dụng < n; báo thêm CI bootstrap theo khối tháng.
- Precision gốc thấp (38–45%) và lợi thế gốc mỏng → bộ lọc giữ 50% có thể tăng precision nhưng giảm số lệnh dưới 200.
