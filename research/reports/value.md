# Kết quả thí nghiệm giá trị (E/P) trên HOSE/HNX

Snapshot dữ liệu: `2026-09-29` · 933 mã trong panel · tạo lúc 2026-09-29T10:10:22

## Giai đoạn phát triển (2019-07-01 → 2023-12-31)

| Biến thể | CAGR | Sharpe năm | Max DD | DSR | PSR>0 | Vượt VN30/năm | t-stat | Chi phí/gộp | Vòng quay/năm | Lệnh |
|---|---|---|---|---|---|---|---|---|---|---|
| E/P (chính) | -17,5% | -0,48 | -73,5% | 0,16 | 0,29 | -20,4% | -1,60 | — | 2,6× | 295 |
| E/P + turnover thấp | -2,8% | -0,01 | -50,1% | 0,37 | 0,54 | -8,1% | -1,31 | — | 2,0× | 220 |
| E/P + momentum 6-1 | -18,2% | -0,52 | -75,9% | 0,13 | 0,25 | -21,9% | -1,73 | — | 3,9× | 407 |
| VNINDEX (chỉ số, không phí) | +3,6% | 0,29 | -40,3% | — | — | — | — | — | — | — |
| VN30 (chỉ số, không phí) | +5,9% | 0,38 | -42,5% | — | — | — | — | — | — | — |

- PBO (3 biến thể, CSCV 8 khối): **0,29**
- Độ nhạy chi phí (biến thể chính, broker 0 phí, trượt giá 0,1%): CAGR -17,0%, Sharpe -0,47
- Placebo chọn mã ngẫu nhiên (200 lần): Sharpe trung vị 0,08, phân vị 95% 0,43; chiến lược chính đứng ở phân vị **1%**

Lợi nhuận theo năm (E/P (chính), sau phí):

| 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|
| -15,9% | +17,9% | +9,0% | -67,9% | +21,4% |

**Cửa 1 (backtest → paper) cho biến thể chính: KHÔNG ĐẠT**

- ❌ Sharpe năm ≥ 0,8
- ❌ Sharpe cao hơn VN30
- ❌ Deflated Sharpe ≥ 0,95
- ❌ PBO < 0,2
- ❌ t-stat lợi nhuận vượt VN30 ≥ 2
- ❌ Max drawdown ≤ 30%
- ❌ Chi phí ≤ 30% lợi nhuận gộp
- ❌ Phân vị 5% Sharpe (bootstrap) > 0
- ❌ Vẫn lãi khi bỏ năm tốt nhất
