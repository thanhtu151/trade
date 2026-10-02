# T54 — Event test ý #4 (sự kiện index/ETF) và ý #5 (positive-MAX)

Dữ liệu CHỈ 2018-01-01..2023-12-31 (holdout 2024–2026 không đọc; `t42/HOLDOUT_APPROVED.txt` chưa có). Đăng ký trước: `trial_log.md` mục T54 + `t54/runs.json` (ghi trước khi viết/chạy backtest). Cấu hình mới: **2** (M1, M2) → N tổng DSR = 97 + 2 = **99**. Lưới dự phòng không dùng.

## Kết luận
| Ý | Kết luận | Lý do (1 câu) |
|---|---|---|
| #4 Sự kiện index/ETF | **Không kết luận – thiếu dữ liệu** | Không có lịch sử thành phần rổ VN30/VNDiamond/FTSE Vietnam theo thời điểm (ngày công bố + ngày hiệu lực + mã thêm/bớt) cho 2018–2023, cả trong kho lẫn nguồn công khai hợp nhất; không dựng danh sách, không chạy cấu hình nào. |
| #5 Positive-MAX | **Không đạt** | Cấu hình tốt nhất M1: CAGR −4,2%, Sharpe 0,01, MaxDD −61,6%, kỳ vọng/lệnh −0,11% (trung vị −1,22%), gate_a 2/11. |

## Ý #4 — kiểm dữ liệu
- Kho `research/data/2026-09-29/`: chỉ có giá OHLCV, `listing.csv`, dữ liệu cơ bản. Tìm (Grep) toàn repo: không có file thành phần rổ nào.
- Nguồn công khai đã kiểm: bài/bản tin CTCK rời rạc cho từng kỳ (vd vietnambiz 12/2018 chỉ có kỳ 7/2018; VNDirect ETF Monitor một số kỳ; tài liệu FiinGroup 7/2025 chỉ nói về quy tắc v4.0, không có lịch sử). Thông báo FTSE Russell tìm được là các kỳ 2024–2026. Không có bảng chính thức HOSE hợp nhất 12 kỳ VN30 + các kỳ VNDiamond + 24 kỳ FTSE; bài báo lẫn dự báo và kết quả → không đạt chuẩn PIT (PROTOCOL mục 0).
- Kể cả khi có dữ liệu: VN30 ~2–3 mã thêm/kỳ × 12 kỳ ≈ 25–35 sự kiện → PROTOCOL mục 1 chỉ cho báo hiệu ứng (<30 sự kiện thì không vào danh mục) và <200 lệnh nên không thể qua gate_a.

## Ý #5 — thiết kế (đã khóa)
Universe PIT top-50 thanh khoản (gồm mã hủy niêm yết). Phiên cuối tháng m: MAX = lợi suất ngày lớn nhất tháng m; đủ điều kiện nếu MAX ≥ 6,5% (proxy ≥1 phiên trần), xếp MAX giảm dần. Khớp mở cửa phiên sau; mở cửa ở trần → hủy lệnh mua (`limits=True`); kẹt sàn → không bán được. `protocol_config` khứ hồi 0,5%, T+2, 10 vị thế × 10%, haircut hủy niêm yết 30%.
- M1: top 10, giữ 1 tháng (mã vẫn trong top thì giữ tiếp). M2: cohort top 5/tháng, giữ 2 tháng (chồng lấp).
- Độ phủ: TB 17,2 mã đủ điều kiện/tháng (ít nhất 1); 21/72 tháng <10 mã. Kiểm nhân quả: **0/72** vi phạm.

## Ý #5 — kết quả (FULL 2018–2023, sau phí)
| Chỉ số | M1 (1 tháng) | M2 (2 tháng) | Lõi ETF E1 | VN-Index | VN30 | E1VFVN30 mua-giữ |
|---|---|---|---|---|---|---|
| CAGR | −4,2% | −6,2% | 7,4% | 2,1% | 2,2% | 2,9% |
| Sharpe | 0,01 | −0,10 | 0,57 | 0,21 | 0,21 | — |
| MaxDD | −61,6% | −63,4% | −23,1% | −45,3% | −48,1% | −47,7% |
| CAGR nửa A / B | −11,4% / +7,4% | −17,1% / +4,2% | | | | |
| Số lệnh | 403 | 191 | | | | |
| Kỳ vọng/lệnh sau phí TB / trung vị | −0,11% / −1,22% | −1,75% / −3,75% | | | | |
| PF | 0,86 | 0,70 | | | | |
| Giữ TB (phiên) | 30,1 | 58,6 | | | | |
| z Sharpe vs 50 seed ngẫu nhiên | −0,27 | −0,90 | | | | |
| Lệnh mua bị chặn do mở cửa trần | 5 | 3 | | | | |
| Lệnh bán bị chặn do sàn (thực tế) | 0 | 0 | | | | |
| Lệnh tệ nhất (thực tế) | −59,4% (ROS 2/2020) | −81,0% (ART 10/2018–3/2019) | | | | |
| Stress kẹt sàn 2 phiên (giá bán × 0,93²): lệnh tệ nhất / TB | −64,9% / −13,6% | −83,5% / −15,0% | | | | |
| Stress kẹt sàn 3 phiên (× 0,93³): lệnh tệ nhất / TB | −67,3% / −19,7% | −84,7% / −21,0% | | | | |
| Tác động NAV/vị thế 10% (3 phiên, lệnh tệ nhất) | −6,7% | −8,5% | | | | |
| DSR (N=99) | 0,001 | 0,000 | | | | |
| gate_a (11 tiêu chí) | 2/11 | 1/11 | | | | |

- **PBO** (CSCV S=16, 12.870 cách chia, M1+M2+E1): 0,314 (> 0,2 → trượt); chỉ 2 cấu hình: 0,565.
- **Walk-forward** 3 cửa sổ: chọn M1 cả 3 lần; Sharpe IS TB −0,17 (âm) → OOS 0,20 (2021 1,40; 2022 −1,35; 2023 1,02) → không đạt (IS âm); ETF cùng kỳ OOS 0,52.
- gate_a M1 chỉ đạt: lệnh ≥200, số tham số ≤ lệnh/50. M2 còn <200 lệnh (tự nó là "không kết luận"), nhưng cấu hình chọn theo luật (gate cao nhất = M1) không đạt → ý #5 loại, không tinh chỉnh thêm.
- Ghi chú: mô phỏng xếp hàng mua trần chỉ ở mức mở cửa; biên độ theo sàn hiện tại (xấp xỉ cho mã chuyển sàn); ngưỡng 6,5% chỉ là proxy trần cho HOSE (HNX/UPCoM có biên 10%/15%). CPCV không chạy (như T51).

## File
`t54/run_t54.py`, `runs.json`, `coverage_causal.json`, `coverage_rebalance.csv`, `summary.csv`, `gate_a.json`, `pbo.json`, `walk_forward.json`, `entries_per_year.csv`, `returns_full.csv`, `trades_M1_h1.csv`, `trades_M2_h2.csv`.
