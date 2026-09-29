# Thí nghiệm 3 — Lọc xu hướng trên ETF VN30 (phát triển 2013-01-01 → 2023-12-31)

Đại diện ETF = chỉ số VN30, trừ phí quản lý 0,65%/năm khi nắm giữ; mỗi lần vào/ra chịu phí môi giới, phí Sở, thuế 0,1% và trượt giá. Tiền mặt lãi 0% (độ nhạy: 4%/năm).

| | CAGR | Sharpe | Max DD | Biến động | Lần vào/ra/năm | % thời gian nắm giữ | CAGR nếu tiền mặt 4% |
|---|---|---|---|---|---|---|---|
| Mua & giữ ETF VN30 | +7,2% | 0,46 | -48,7% | +19,0% | — | 100% | — |
| VN30 ≥ MA200 ngày | +5,7% | 0,48 | -34,0% | +13,3% | 5.8 | 59% | +7,5% |
| VN30 ≥ MA 10 tháng | +7,1% | 0,58 | -23,8% | +13,4% | 1.7 | 56% | +9,0% |

| Năm | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Mua & giữ | +13,0% | +6,3% | -1,6% | +4,8% | +54,2% | -12,9% | +2,2% | +21,0% | +42,4% | -34,9% | +11,8% |
| VN30 ≥ MA200 ngày | +9,8% | +0,1% | -13,5% | +2,4% | +50,7% | -9,3% | -10,0% | +28,0% | +42,3% | -6,4% | -10,2% |
| VN30 ≥ MA 10 tháng | -4,3% | +13,4% | -13,1% | +3,8% | +48,3% | -3,6% | -1,5% | +29,2% | +42,2% | -8,4% | -8,7% |

**VN30 ≥ MA200 ngày (chính): KHÔNG ĐẠT cửa C**

- ✅ Sharpe ≥ mua & giữ
- ❌ Max DD ≤ 60% của mua & giữ
- ✅ CAGR ≥ mua & giữ − 3%/năm
- ✅ ≤ 6 lần vào/ra mỗi năm
- ❌ Sharpe và DD vẫn đạt ở cả hai nửa giai đoạn
  - Nửa 1 (2013-01-02 → 2018-07-09): Sharpe 0,44 vs 0,70, Max DD -29,2% vs -25,0%
  - Nửa 2 (2018-07-10 → 2023-12-29): Sharpe 0,53 vs 0,28, Max DD -21,4% vs -42,8%

**VN30 ≥ MA 10 tháng: KHÔNG ĐẠT cửa C**

- ✅ Sharpe ≥ mua & giữ
- ✅ Max DD ≤ 60% của mua & giữ
- ✅ CAGR ≥ mua & giữ − 3%/năm
- ✅ ≤ 6 lần vào/ra mỗi năm
- ❌ Sharpe và DD vẫn đạt ở cả hai nửa giai đoạn
  - Nửa 1 (2013-01-02 → 2018-07-09): Sharpe 0,51 vs 0,70, Max DD -23,8% vs -25,0%
  - Nửa 2 (2018-07-10 → 2023-12-29): Sharpe 0,67 vs 0,28, Max DD -18,3% vs -42,8%
