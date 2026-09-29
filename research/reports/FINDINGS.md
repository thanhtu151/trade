# Kết luận thí nghiệm 1 — Momentum long-only (29/09/2026)

Số liệu chi tiết: [momentum.md](momentum.md) · dữ liệu thô: `momentum_dev.json` · sổ lần thử: `../trials.jsonl`

## Kết quả

> Số liệu cập nhật sau khi sửa lỗi engine: thứ tự khớp lệnh khi thiếu tiền phụ thuộc hash seed ngẫu nhiên của Python (lần chạy đầu cho CAGR −3,1%). Engine giờ khớp lệnh theo thứ tự xếp hạng, kết quả cố định giữa các lần chạy. Kết luận không đổi.

**Momentum xếp hạng chéo long-only KHÔNG có edge trên HOSE/HNX giai đoạn 2011–2023**, sau chi phí thực tế.

| | Momentum 6-1 (chính) | Tốt nhất trong 4 biến thể (6-1 + lọc MA200) | VN30 (chỉ số) |
|---|---|---|---|
| CAGR | −1,8% | −0,2% | +7,5% |
| Sharpe năm | 0,06 | 0,10 | 0,49 |
| Max drawdown | −80% | −49% | −48% |
| Deflated Sharpe | 0,62 | 0,65 | — |

- Trượt cả 9 tiêu chí của Cửa 1. PBO = 0,96: biến thể "tốt nhất" trong giai đoạn huấn luyện gần như luôn tụt xuống nửa dưới ở giai đoạn kiểm tra.
- Placebo: chiến lược chính đứng ở phân vị 94% so với chọn mã ngẫu nhiên. Tín hiệu có nhỉnh hơn ngẫu nhiên một chút, nhưng mức Sharpe tuyệt đối quá thấp để dùng.
- Khi giảm chi phí về mức broker 0 phí, CAGR vẫn âm (−0,3%). Chi phí làm tệ thêm nhưng không phải nguyên nhân chính.
- Lợi nhuận theo năm biến động dữ dội: +38% (2015), +42% (2021) rồi −35% (2018), −66% (2022).

## Vì sao thất bại (chẩn đoán, không phải biến thể mới)

1. **Vũ trụ thanh khoản ở VN bị chi phối bởi cổ phiếu đầu cơ.** Chia đều cho toàn bộ top 100 theo giá trị giao dịch, không phí, chỉ đạt CAGR 2,4% so với 6,8% của VN-Index. Chỉ số được kéo bởi VCB, VIC, VHM. Nhóm top 100 thanh khoản thì đầy mã "vòng quay cao" như FLC, ROS, HQC, OGC.
2. **Momentum mua đúng đỉnh của các đợt đầu cơ.** Danh mục cuối 2021 gồm DPG, VOS, CEO, APS, PVL, TAR, BCC, DIG. Đây đều là mã tăng nóng năm 2021 và sập năm 2022. Stop −20% khớp thực tế ở −23% đến −32% vì giá mở cửa gap giảm và kẹt sàn.
3. Điều này khớp với Huang, Liu & Shu (2023): ở VN, **turnover cao dự báo lợi nhuận thấp** (VN-4 factor). Momentum trong vũ trụ này vô tình nghiêng về turnover cao.

## Kiểm tra engine (không có lỗi phát hiện)

- Mua và giữ VNINDEX không phí khớp chính xác lợi nhuận chỉ số (0,94906 vs 0,94906). Có phí thì chênh đúng bằng phí và trượt giá.
- Tiền mặt nhàn rỗi trung bình 6%.
- 9 + 6 test đơn vị cho T+2, trần/sàn, lô, ADV, stop gap, hủy niêm yết, vũ trụ point-in-time, PSR/MinTRL/DSR (khớp ví dụ trong paper gốc).

## Quyết định

- **Không đưa momentum vào paper trading.**
- **Holdout 2024–2026 chưa bị dùng.** Holdout chỉ chạy cho chiến lược đã qua giai đoạn phát triển. Nó được giữ cho ứng viên tiếp theo.
- Sổ lần thử hiện có N = 4. Mọi biến thể momentum mới (đổi cửa sổ, đổi vũ trụ...) đều phải cộng vào N.

## Bước tiếp theo đề xuất (theo thứ tự)

1. **Value E/P + turnover thấp** (Huang, Liu & Shu 2023: E/P và turnover là hai factor giải thích nhiều nhất ở VN). Cần tải BCTC quý. vnstock/VCI chỉ có từ 2018-Q1 nên giai đoạn phát triển sẽ ngắn (2019–2023). Cần tìm nguồn BCTC dài hơn.
2. **Bộ lọc chất lượng vũ trụ**: loại mã turnover cao nhất trước khi xếp hạng. Đây là giả thuyết mới, phải đăng ký trước và tính vào N.
3. Giữ hệ thống paper trading hiện tại ở chế độ quan sát. Chưa có chiến lược nào đủ bằng chứng để thay nó.

---

# Kết luận thí nghiệm 2 — Giá trị E/P long-only (29/09/2026)

Số liệu chi tiết: [value.md](value.md) · dữ liệu thô: `value_dev.json`

BCTC quý lấy từ VCI, dùng **ngày công bố thật** (`publicDate`). Lợi nhuận TTM phải gồm 4 quý liên tiếp và
hết hiệu lực sau 200 ngày. Vốn hóa bằng 0 được coi là thiếu dữ liệu. Dữ liệu chỉ có từ 2018, nên giai đoạn
phát triển chỉ từ 07/2019 đến 12/2023.

| | E/P (chính) | E/P + turnover thấp | E/P + momentum | VN30 (chỉ số) |
|---|---|---|---|---|
| CAGR | −17,5% | −2,8% | −18,2% | +5,9% |
| Sharpe năm | −0,48 | −0,01 | −0,52 | 0,38 |
| Max drawdown | −74% | −50% | −76% | −43% |
| Deflated Sharpe (N = 7) | 0,16 | 0,37 | 0,13 | — |

- Trượt cả 9 tiêu chí Cửa 1. Placebo: E/P đứng ở **phân vị 1%**, nghĩa là **tệ hơn chọn mã ngẫu nhiên** trong
  cùng nhóm mã có lãi.
- **Nguyên nhân: bẫy giá trị.** Mã P/E thấp nhất liên tục là những mã sắp sụp lợi nhuận hoặc có lãi đột biến
  một lần: PDR, NVL, GIL (P/E 1,2–1,6 cuối 2022), APS (P/E 1,4 giữa 2022, lãi tự doanh 2021), LDG, VRC (2019),
  FLC (2021). 77/135 lệnh bán là do chạm stop.
- Turnover thấp làm giảm thiệt hại đáng kể (−2,8% so với −17,5%, drawdown −50% so với −74%). Điều này khớp với
  bằng chứng turnover ở VN, nhưng vẫn không thắng VN30.

## Tổng kết sau 2 thí nghiệm (N = 7 lần thử)

- **Chưa chiến lược xếp hạng chéo nào thắng được VN30** sau chi phí, trên vũ trụ thanh khoản point-in-time.
- Vấn đề chung: top 100 thanh khoản ở VN chứa nhiều mã đầu cơ và mã chất lượng lợi nhuận thấp. Chọn theo tín
  hiệu đơn giản (giá tăng, P/E thấp) đều rơi đúng vào nhóm này.
- **Holdout 2024–2026 vẫn chưa bị dùng.**

## Giả thuyết đáng thử tiếp (mỗi cái tính thêm vào N)

1. **Chất lượng + giá trị:** chỉ xét mã có lợi nhuận ổn định (ví dụ không lỗ quý nào trong 8 quý, ROE ≥ 15%,
   lợi nhuận không phụ thuộc thu nhập tài chính), rồi mới xếp theo E/P. Đây là cách chuẩn để tránh bẫy giá trị.
2. **Giới hạn vũ trụ vào VN30** (vốn hóa lớn), thay vì top 100 thanh khoản.
3. **Phương án thực dụng:** nắm ETF VN30 và dùng bộ lọc VN-Index/MA200 để giảm drawdown. Mục tiêu không phải
   alpha mà là đi cùng thị trường với rủi ro thấp hơn.
