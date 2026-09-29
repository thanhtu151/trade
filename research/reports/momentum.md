# Kết quả thí nghiệm momentum trên HOSE/HNX

Snapshot dữ liệu: `2026-09-29` · 933 mã trong panel · tạo lúc 2026-09-29T09:11:36

## Giai đoạn phát triển (2011-01-01 → 2023-12-31)

| Biến thể | CAGR | Sharpe năm | Max DD | DSR | PSR>0 | Vượt VN30/năm | t-stat | Chi phí/gộp | Vòng quay/năm | Lệnh |
|---|---|---|---|---|---|---|---|---|---|---|
| Momentum 6-1 (chính) | -1,8% | 0,06 | -79,7% | 0,62 | 0,64 | -6,0% | -0,86 | +275% | 3,4× | 2215 |
| Momentum 12-1 | -1,9% | 0,05 | -77,2% | 0,59 | 0,62 | -6,7% | -1,06 | — | 2,3× | 1297 |
| 6-1 + lọc MA200 (0%) | -0,2% | 0,10 | -48,8% | 0,65 | 0,67 | -6,1% | -0,82 | +108% | 2,7× | 1324 |
| 6-1 + lọc MA200 (50%) | -0,7% | 0,08 | -67,3% | 0,62 | 0,65 | -6,5% | -1,01 | +151% | 3,0× | 2156 |
| VNINDEX (chỉ số, không phí) | +6,8% | 0,45 | -45,3% | — | — | — | — | — | — | — |
| VN30 (chỉ số, không phí) | +7,5% | 0,49 | -48,1% | — | — | — | — | — | — | — |

- PBO (4 biến thể, CSCV 8 khối): **0,96**
- Độ nhạy chi phí (biến thể chính, broker 0 phí, trượt giá 0,1%): CAGR -0,3%, Sharpe 0,12
- Placebo chọn mã ngẫu nhiên (200 lần): Sharpe trung vị -0,16, phân vị 95% 0,07; chiến lược chính đứng ở phân vị **94%**

Lợi nhuận theo năm (Momentum 6-1 (chính), sau phí):

| 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -17,7% | +7,2% | +24,3% | +23,9% | +38,2% | +17,4% | +14,1% | -35,2% | +5,3% | -14,3% | +41,8% | -65,8% | +10,7% |

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
