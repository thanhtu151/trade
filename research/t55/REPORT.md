# T55 — Holdout một lần cho E1 và E6 (2024-01-01 → 2026-09-28, 679 phiên)

Duyệt: research/t42/HOLDOUT_APPROVED.txt (CEO Minh, 2026-09-30). Chạy đúng 1 lần (khóa: t55/holdout_runs.json). Không đổi tham số; code tín hiệu = t47/run_t47.py (signals, WantETF). Phí protocol_config (khứ hồi 0,5%), T+2, 1 vị thế 100% E1VFVN30. Dữ liệu tới phiên 2026-09-28 (snapshot 09-29, phiên 09-29 chưa có trong panel).

## Kết quả holdout
| | CAGR | Sharpe | MaxDD | Lệnh (đóng) | Tháng nắm giữ | Sharpe IS | OOS/IS | Kết luận |
|---|---|---|---|---|---|---|---|---|
| E1 (VN30 ≥ MA10 tháng) | 13,5% | 0,86 | -19,5% | 3 (+1 đang mở) | 29,4 (618 phiên) | 0,57 | 1,50 | XÁC NHẬN |
| E6 (MA200 ngày + breadth>60%) | 8,7% | 0,76 | -15,0% | 19 | 16,5 (347 phiên) | 0,81 | 0,94 | XÁC NHẬN |
| E1VFVN30 mua-giữ (cùng phí) | 23,6% | 1,31 | -15,8% | – | – | | | |
| VN-Index (giá) | 18,3% | 1,01 | -18,1% | – | – | | | |

Tiêu chí đăng ký trước: Sharpe OOS ≥ 50% IS (E1 ≥ 0,285; E6 ≥ 0,405). Cả hai đạt. "Tháng nắm giữ" = số phiên tín hiệu bật / 21.

## Cảnh báo trung thực
- Cả hai KHÔNG thắng mua-giữ E1VFVN30 hay VN-Index về CAGR/Sharpe trong holdout (thị trường tăng mạnh; bộ lọc xu hướng bỏ lỡ đà tăng). Tiêu chí đăng ký chỉ là OOS/IS Sharpe nên "xác nhận" ≠ "thắng thị trường".
- E1 có MaxDD -19,5%, tệ hơn mua-giữ (-15,8%) và VN-Index (-18,1%): bán muộn (31/3/2026) rồi mua lại muộn.
- Mẫu nhỏ: E1 chỉ 3-4 lệnh → Sharpe 0,86 rất nhiễu.

## DD từng đợt giảm (đỉnh→đáy của VN-Index; thay đổi NAV trong cùng cửa sổ)
| Đợt | VNI | E1 | E6 | Mua-giữ |
|---|---|---|---|---|
| 17/3→9/4/2025 (1336→1094) | -18,1% | -15,2% | -12,4% | -15,2% |
| 16/10→10/11/2025 (1767→1581) | -10,5% | -9,5% | -9,5% | -9,5% |
| 13/1→23/3/2026 (1903→1591) | -16,4% | -15,9% | -10,4% | -15,8% |
| 18/5→22/7/2026 (1928→1669, ≈ đợt "1920→1686") | -13,5% | -8,8% | 0,0% | -8,8% |
| Cửa sổ 2–31/3/2026 (gồm cú 9/3: VNI 1768→1653 ngày 9/3) | -13,8% (ret -9,3%) | -12,7% (ret -9,1%) | -2,6% | -12,7% |

Ghi chú: đợt "1920→1686" xác định theo dữ liệu là đỉnh 18/5/2026 (1928) → đáy 22/7/2026 (1669); chưa xác nhận đúng đợt CEO nhắc (giá 1686 cũng xuất hiện tháng 8–10/2025). E1 không tránh được cú 9/3 (tín hiệu tháng, đang giữ); E6 thoát trước nhờ MA200/breadth (chỉ -2,6%).

## Kết luận
- E1: XÁC NHẬN (Sharpe OOS 0,86 ≥ 50% IS 0,57).
- E6: XÁC NHẬN (0,76 ≥ 50% IS 0,81 → 94%).
- Cấu hình dùng cho paper: giữ E1 (đang là lõi etf_core.py). Theo quy tắc T48 đã đăng ký ("chỉ đổi nếu hơn E1 cả CAGR và Sharpe"), E6 kém E1 cả CAGR (8,7 vs 13,5) và Sharpe (0,76 vs 0,86) trên holdout → không đổi. E6 có DD nhỏ hơn ở các cú sốc nhưng 19 lệnh (phí) và chỉ nắm 16,5/33 tháng. Đề xuất chạy E6 song song "shadow" nếu muốn.
- Tín hiệu hiện tại (phiên 2026-09-28):
  - E6: TIỀN MẶT (VN30 1922,4 < MA200 1960,9; breadth 26% < 60%).
  - E1: đang NẮM GIỮ (tín hiệu cuối tháng 8 = bật, lệnh mở). Lưu ý: script coi phiên cuối dữ liệu 28/9 như cuối tháng tạm → "tắt" (VN30 1922,4 < MA10 tháng). Đây chỉ là tạm thời; tín hiệu thật chốt ở đóng cửa 30/9. Nếu VN30 vẫn dưới MA10 tháng, E1 chuyển TIỀN MẶT (bán ở open 1/10).
  - Chưa tính MA10 tháng chính xác ở mốc 30/9 (không đo được vì thiếu dữ liệu phiên 29–30/9).

Tệp: run_t55.py, holdout_result.json, trades_E1.csv, trades_E6.csv, navs.csv, signals.csv.
