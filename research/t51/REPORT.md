# T51 — Value+profitability (ý #3) và PEAD (ý #2)

Dữ liệu 2018–2023 (holdout 2024–2026 không đụng). protocol_config (phí khứ hồi 0,5%), universe PIT top-50 ∩ 300 mã có báo cáo tài chính. DSR N=97. etf_core = E1 (CAGR 7,4% / Sharpe 0,57). Chạy foreground 35s, 6 cấu hình đúng lưới đã khóa (runs.json); 2 lần chạy nền trước bị kill trước khi ra kết quả (chưa có kết quả nào bị xem/chọn lọc).

## Kết quả FULL 2018–2023
| Cấu hình | CAGR | Sharpe | MaxDD | Lệnh | PF | TB/lệnh sau phí | Trung vị/lệnh | Thắng | gate_a | DSR |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 E/P+ROE | -0,2% | 0,11 | -58,6% | 80 | 0,90 | +4,1% | -2,8% | 46% | 0/11 | 0,002 |
| V2 B/P+ROE | -1,8% | 0,06 | -61,5% | 93 | 0,86 | +2,3% | -3,6% | 44% | 0/11 | 0,001 |
| V3 E/P+B/P+ROE | -2,3% | 0,03 | -59,5% | 84 | 0,86 | +3,3% | -3,3% | 45% | 0/11 | 0,001 |
| P1 PEAD h20 | 5,9% | 0,56 | -16,7% | 105 | 1,87 | +4,7% | +1,0% | 52% | 4/11 | 0,032 |
| P2 PEAD h40 | 2,8% | 0,27 | -30,9% | 77 | 1,24 | +3,2% | -0,5% | 48% | 2/11 | 0,006 |
| P3 PEAD h60 | -2,7% | -0,13 | -48,0% | 66 | 0,77 | -1,2% | -2,7% | 47% | 0/11 | 0,000 |

Tham chiếu: VN-Index 2,1%, E1VFVN30 2,9%, MaxDD VN-Index -45,3%. TB/lệnh = trung bình net_ret trong trades_*.csv (đã trừ phí); TB dương nhưng CAGR âm ở value vì phân phối lệch phải (vài lệnh thắng lớn, trung vị âm), không phải kỳ vọng ổn định.

## PBO / walk-forward
- PBO (CSCV S=16, 12.870 tách): value 0,375; PEAD 0,480 (cả hai > 0,2).
- Walk-forward (3 cửa sổ): value chọn V1 cả 3, OOS Sharpe 0,21, CAGR 2,1% (ETF OOS Sharpe 0,52). PEAD OOS Sharpe -0,60, CAGR -11,1% (không ok).

## Look-ahead / dữ liệu
- Ngày dùng = phiên đầu tiên sau max(publicDate, cuối quý+45d; Q4 +90d). 6.630 dòng có publicDate; 6 dòng thiếu publicDate + 179 dòng trễ >100 ngày (0 dòng âm) = 185 dòng bị thay bằng ngày ước lượng. 79% dòng bị lùi bởi sàn 45/90 ngày (trung vị 17 ngày).
- Causal check: 0/228 vi phạm (57 ngày).
- Ánh xạ isaN (đối chiếu số thật VCB/FPT/VNM 2019): isa16 = LNTT (VCB 23.122 tỷ), isa20 = LNST (VCB 18.526; FPT 3.912; VNM 10.554), isa22 = LNST cổ đông mẹ (FPT 3.135), isa23 = EPS cơ bản (FPT 4.225 vs công bố 4.220; VNM 1.452+1.501 = 2.953 = EPS 6T công bố).
- E/P, B/P tự tính: (1/pe_q hoặc 1/pb_q) × adj_close(cuối quý)/adj_close(t) vì giá cache đã điều chỉnh; ROE lấy từ ratio TTM.
- Coverage: universe TB 49,5 mã, hợp lệ TB ~43–45; 24 ngày cân bằng, lần đầu đủ 10 mã 2018-06-29. PEAD: 109 sự kiện, đầu tiên 2021-04-01, không có sự kiện 2018–2020 (nên nửa A = 0,0%).

## Kết luận
- **Value+profitability: KHÔNG ĐẠT.** Cả 3 cấu hình CAGR âm/≈0, Sharpe ≤ 0,11, DD -59…-62% (tệ hơn VN-Index), 0/11 tiêu chí, âm cả hai nửa. Mẫu 80–93 lệnh (<200), nhưng tín hiệu không cho thấy edge nên kết luận âm là đủ.
- **PEAD: KHÔNG KẾT LUẬN.** P1 nhìn đẹp (Sharpe 0,56, PF 1,87, DD -16,7%) nhưng: chỉ có 3 năm sự kiện (2021–2023), 105 lệnh, z 1,11 < 2, DSR 0,032, PBO 0,48, walk-forward OOS âm, không vượt E1, và hiệu quả đảo dấu khi giữ dài (P3 âm) → không loại trừ nhiễu; không đủ bằng chứng để nói đạt. Không đạt gate_a.

## Rủi ro/hạn chế
EPS quý isa23 nhiễu (Q4 = năm − 9T, đổi số cổ phiếu); sàn 45 ngày làm chậm vào lệnh (bất lợi cho PEAD, bảo thủ); giá điều chỉnh ngược; chỉ 300 mã có BCTC; bắt đầu 2018; <200 lệnh cả 6 cấu hình. Chưa chạy stress (2/3 phiên sàn) riêng cho T51: không đo được trong task này. blocked_sell = 0, n_delist = 0 mọi cấu hình; lệnh xấu nhất: value -77,5%, PEAD -32,8% / -54,2% / -57,0%.

## Đề xuất
Không dùng holdout cho hai ý này (CEO cần tạo HOLDOUT_APPROVED.txt nếu muốn). Nếu muốn kiểm PEAD tiếp cần dữ liệu BCTC lịch sử dài hơn (trước 2018) và EPS chuẩn hóa; ngân sách thử: T48 6 + T51 6 = 12/17 cấu hình lưới đã dùng.

File: t51/summary.csv, gate_a.json, pbo.json, walk_forward.json, coverage_causal.json, entries_per_year.csv, returns_full.csv, trades_*.csv, runs.json.
