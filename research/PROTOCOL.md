# PROTOCOL nghiên cứu — ĐÓNG BĂNG ngày 2026-09-30

Nguồn: `~/Downloads/office/reports/deep_research_result.md` (30/9/2026). Sau ngày này KHÔNG sửa file này; muốn đổi phải tạo `PROTOCOL_v2.md` có chữ ký duyệt của CEO và ghi vào `trial_log.md`. Mọi thứ dưới đây chỉ dùng dữ liệu ≤ 2023-12-31. **Holdout 2024–2026 khóa**: chỉ mở khi CEO tạo `HOLDOUT_APPROVED.txt`; mỗi ứng viên chạy đúng 1 lần (tiêu chí holdout đã chốt trong `trial_log.md`).

## 0. Nguyên tắc
- Mục tiêu = kỳ vọng lợi nhuận sau phí/lệnh và Sharpe/CAGR sau phí so với **lõi ETF VN30>MA10 tháng** (mốc: FULL 2018–2023 CAGR 7,4% / Sharpe 0,56 / DD −23,1%, engine chuẩn N=93). Không dùng win rate làm tiêu chí.
- Mỗi cấu hình chạy 1 lần, ghi `trial_log.md` TRƯỚC khi tính chỉ số (khóa `runs.json` như T46). Cấu hình bỏ dở/lỗi vẫn tính vào N.
- Mọi ý tưởng dùng dữ liệu theo thời điểm (PIT): universe top-50 thanh khoản gồm mã hủy niêm yết; ngày công bố KQKD/BCTC thực tế + độ trễ; kiểm `causal_check` không có ngày tương lai.
- Thiếu dữ liệu PIT cho ý nào → ý đó "không kết luận", không thay bằng dữ liệu xấp xỉ có look-ahead.

## 1. Năm ý tưởng (theo thứ tự ưu tiên) và lưới tham số
| # | Ý tưởng | Lưới (tất cả tham số khác CỐ ĐỊNH) | Số cấu hình |
|---|---|---|---|
| 1 | Lõi regime ETF VN30 (E1VFVN30) | Độ dài trend {MA 10 tháng (cuối tháng, áp tháng sau), MA 200 ngày} = 2. Xác nhận breadth: % top-50 có close > MA200, ngưỡng {50%, 60%} (tối đa 1 xác nhận, ≤2 giá trị) áp lên cả 2 độ dài = 4. Trần tỷ trọng theo margin (dữ liệu dư nợ margin, cố định 1 luật: giảm tỷ trọng ETF còn 70% khi margin/vốn hóa ở phân vị ≥90 trong 3 năm trước; KHÔNG dùng làm tín hiệu timing) = 1 (áp lên cấu hình tốt nhất theo quy tắc mục 5). Báo DD từng đợt crash (2018, 3/2020, 2022; 3/2026 chỉ mô tả, ngoài mẫu). | 7 |
| 2 | PEAD top-50 | Bất ngờ lợi nhuận SUE (EPS quý vs cùng kỳ năm trước, chuẩn hóa bằng độ lệch 8 quý) cố định: top 20% cắt ngang, SUE>0; mua mở cửa phiên sau ngày công bố (+độ trễ PIT). Giữ {20, 40, 60} phiên. Stop/size cố định 10 vị thế × 10%. | 3 |
| 3 | Value + profitability (quý) | Value = {E/P, B/P, trung bình hạng của cả hai} × Profitability = ROE TTM (cố định); hạng đều trọng số, top 10 mã/50, cân bằng quý cố định. | 3 |
| 4 | Sự kiện index/ETF (FTSE, VN30/VNDiamond review) | Test sự kiện nhỏ: {mua trước ngày hiệu lực X phiên → bán sau} 2 cửa sổ cố định (-10/+0, -5/+5). Ít sự kiện → chỉ báo hiệu ứng, không vào danh mục nếu <30 sự kiện. | 2 |
| 5 | Positive-MAX / tiếp diễn trần (tháng) | Mã có MAX ngày tháng trước ≥ 6,5% (proxy chạm trần) → giữ 1 tháng; khớp CHỈ ở mở cửa, khóa trần chặn mua (`limits=True`); 1 tham số cố định. Cửa sổ giữ {1 tháng, 2 tháng}. | 2 |

Cộng: 7 + 3 + 3 + 2 + 2 = **17 cấu hình**. Không thêm tham số ngoài lưới (kể cả "tiện tay"). Tránh (không thử): momentum/breakout độc lập, mean reversion ngắn, tin tức/LLM, size/illiquidity, timing vĩ mô nhiều biến, bình quân giá xuống có margin, agent tự tune không ghi log.

## 2. Ngân sách thử và DSR
- **Ngân sách MỚI kể từ 2026-09-30: tối đa 30 cấu hình cho toàn dự án** (lưới trên dùng 17; 13 còn lại là dự phòng, chỉ dùng khi ghi lý do vào `trial_log.md` trước khi chạy). Vượt 30 → dừng, không nghiên cứu thêm nếu CEO chưa duyệt.
- **N tích lũy hiện tại = 85 cấu hình** (theo cách tính DSR đã dùng ở T42/T46: 77 tính đến hết T42 + 8 của T46; các seed ngẫu nhiên baseline không tính là cấu hình). Lưu ý: cột N trong `trial_log.md` chạy tới 458 vì đánh số cả các seed baseline — khi tính DSR dùng 85, không dùng 458.
- **DSR** = `engine.bench.deflated_sharpe(nav, n_trials=N_tổng, sr_trials_annual=...)` với **N_tổng = 85 + số cấu hình mới đã thử** (tối đa 115; 85+17 = 102 nếu chỉ chạy lưới trên), tính cả cấu hình bỏ dở/lỗi. `sr_trials_annual` = Sharpe năm hóa FULL của TẤT CẢ cấu hình đã thử (cũ + mới); nếu không khôi phục được Sharpe của cấu hình cũ, dùng phương sai lớn hơn trong hai (cũ đã dùng ở T42, mới) — không được dùng phương sai nhỏ hơn. Ngưỡng **DSR ≥ 0,95**.

## 3. Phí, trượt giá, luật sàn
- Khứ hồi **0,50%** = phí+thuế 0,35% (môi giới 0,125%×2 + thuế bán 0,1%) + trượt giá 0,15% (0,075%/chiều). Dùng `engine.bench.protocol_config()`. (Engine cũ `Config()` mặc định = 0,60% khứ hồi: 0,15%×2+0,1%+0,1%×2 — giữ nguyên để tái tạo T34–T46, KHÔNG chạy lại các backtest cũ; so sánh với mốc ETF phải cùng một mức phí, ghi rõ nếu mốc là số cũ.) Cân bằng danh mục 2 túi: 0,6% × giá trị chuyển (bảo thủ, như T46).
- **T+2**: cổ phiếu mua ở t chỉ bán được từ t+2 (`settle=2`); tiền bán về sau T+2; tín hiệu tính trên đóng cửa t, khớp giá mở cửa t+1. Lô 100.
- **Biên ±7% HOSE** (`limits=True`): mở cửa ở trần → lệnh mua hủy; kẹt sàn → không bán được, phải giữ đến phiên có khớp. Báo lỗ xấu nhất khi kẹt sàn: 2 phiên sàn −13,5%, 3 phiên −19,6% (stress bắt buộc trong báo cáo từng ý). Mã ngừng giao dịch: haircut thanh lý 30%.

## 4. Kiểm định
- **Walk-forward mở rộng**: train ≥ 3 năm, test 12 tháng, đóng băng tham số trước mỗi cửa sổ. Với dữ liệu 2018–2023: 3 cửa sổ (train 2018–20 → test 2021; 2018–21 → 2022; 2018–22 → 2023). Chọn tham số chỉ bằng dữ liệu train. **OOS Sharpe ≥ 50% IS**.
- **CPCV**: 8 nhóm liên tiếp (được phép 6–10; chốt 8), 2 nhóm test → C(8,2)=28 tổ hợp, 7 đường OOS. Purge = độ dài giữ lệnh (tối đa 60 phiên cho PEAD) + **embargo ≥ 5 phiên** sau mỗi nhóm test.
- **PBO** (CSCV, S=16, `engine.portfolio.cscv_pbo`) trên ma trận lợi suất ngày của mọi cấu hình cùng ý tưởng + tham chiếu (ETF lõi). **PBO ≤ 0,2**.
- Cỡ mẫu: phát hiện lợi 0,5%/lệnh (sd 5%) cần ~400 lệnh; <200 lệnh → không kết luận. Cấu hình phải có `n_params ≤ lệnh/50`.
- Baseline ngẫu nhiên 50 seed cùng luật thoát → z Sharpe ≥ 2.

## 5. Cổng (a) — go/no-go từng ý (`engine.bench.gate_a`, 11 tiêu chí, phải đạt ĐỦ)
Tám tiêu chí cũ (lệnh ≥200; CAGR dương cả 2 nửa; Sharpe ≥0,5; MaxDD ≤ VN-Index; PF ≥1,2; CAGR > VN-Index & E1VFVN30; z ≥2; tham số ≤ lệnh/50) **cộng**: (a) **vượt lõi ETF VN30>MA10 cả CAGR lẫn Sharpe** (cùng phí giao thức); (b) **DSR ≥ 0,95**; (c) **PBO ≤ 0,2**. Cấu hình tốt nhất trong mỗi ý chọn bằng luật cố định: gate_a cao nhất, hòa → Sharpe FULL cao hơn. Ý không qua → loại, không "tinh chỉnh thêm". Vệ tinh đạt chuẩn ≤10% vốn mỗi cái; lõi ETF luôn là mặc định.

## 6. Thứ tự và mốc
T1 đóng băng (file này) → T2 dữ liệu PIT → T3–4 ý 1, rồi 2, 3 (walk-forward + CPCV) → T5 ý 4, 5 (event test nhỏ), go/no-go → T5–8 paper cùng code path với live (≥40 tín hiệu hoặc 8 tuần; lệch trượt giá ≤0,1 điểm %; 0 lỗi T+2) → T7–8 live nhỏ nếu CEO duyệt. Dừng live nếu DD live > 1,5× DD backtest tệ nhất cùng kỳ. 2 tháng paper chỉ kiểm pipeline, không chứng minh alpha (MinTRL Sharpe 1,0 ≈ 35 tháng).
