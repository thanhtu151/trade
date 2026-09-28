# Kết quả thí nghiệm momentum trên HOSE/HNX

Snapshot dữ liệu: `2026-09-29` · 933 mã trong panel · tạo lúc 2026-09-29T06:15:13

## Giai đoạn phát triển (2011-01-01 → 2023-12-31)

| Biến thể | CAGR | Sharpe năm | Max DD | DSR | PSR>0 | Vượt VN30/năm | t-stat | Chi phí/gộp | Vòng quay/năm | Lệnh |
|---|---|---|---|---|---|---|---|---|---|---|
| Momentum 6-1 (chính) | -3,1% | 0,01 | -83,2% | 0,50 | 0,58 | -7,5% | -1,07 | — | 3,4× | 2184 |
| Momentum 12-1 | -3,6% | -0,01 | -75,5% | 0,46 | 0,54 | -8,4% | -1,31 | — | 2,3× | 1273 |
| 6-1 + lọc MA200 (0%) | +0,4% | 0,12 | -49,0% | 0,63 | 0,70 | -5,6% | -0,78 | +86% | 2,7× | 1373 |
| 6-1 + lọc MA200 (50%) | -0,7% | 0,07 | -68,7% | 0,57 | 0,64 | -6,6% | -1,05 | +149% | 3,0× | 2162 |
| VNINDEX (chỉ số, không phí) | +6,8% | 0,45 | -45,3% | — | — | — | — | — | — | — |
| VN30 (chỉ số, không phí) | +7,5% | 0,49 | -48,1% | — | — | — | — | — | — | — |

- PBO (4 biến thể, CSCV 8 khối): **0,69**
- Độ nhạy chi phí (6-1, broker 0 phí, trượt giá 0,1%): CAGR -1,8%, Sharpe 0,06
- Placebo chọn mã ngẫu nhiên (200 lần): Sharpe trung vị -0,16, phân vị 95% 0,07; chiến lược chính đứng ở phân vị **89%**

Lợi nhuận theo năm (Momentum 6-1, sau phí):

| 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -17,0% | +1,5% | +20,1% | +32,5% | +41,4% | +1,5% | +16,2% | -33,3% | +1,2% | -13,1% | +41,1% | -67,6% | +10,9% |

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
