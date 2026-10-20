# T192 phần B — Stage1 lịch sử 2018–2023, giữ 3 phiên, chỉ MUA

Tỉ lệ nền (mọi mã-phiên top-50, vào mở t+1 ra đóng t+3): n=74502, tăng 48.4%, exp sau phí -0.47%
Sự kiện score≥3: 20286

## Lưới 18 bộ lọc — FULL 2018–2023 (chỉ mô tả)
| bộ lọc | FULL | /tháng |
|---|---|---|
| {'thr': 3, 'mkt': 'none', 'liq': 'top50'} | n=20286 (phiên 1466) prec 48.1% [Wilson 47.4–48.8] exp sau phí -0.46% (trung vị -0.50%, thắng sau phí 43.6%) | 281.8 |
| {'thr': 3, 'mkt': 'none', 'liq': 'top20'} | n=8407 (phiên 1382) prec 48.6% [Wilson 47.5–49.6] exp sau phí -0.45% (trung vị -0.50%, thắng sau phí 43.8%) | 116.8 |
| {'thr': 3, 'mkt': 'vn30_ma10m', 'liq': 'top50'} | n=10794 (phiên 655) prec 50.3% [Wilson 49.4–51.2] exp sau phí -0.20% (trung vị -0.37%, thắng sau phí 46.1%) | 149.9 |
| {'thr': 3, 'mkt': 'vn30_ma10m', 'liq': 'top20'} | n=4528 (phiên 644) prec 50.8% [Wilson 49.4–52.3] exp sau phí -0.16% (trung vị -0.33%, thắng sau phí 46.0%) | 62.9 |
| {'thr': 3, 'mkt': 'vni_ma50', 'liq': 'top50'} | n=15504 (phiên 885) prec 47.4% [Wilson 46.6–48.2] exp sau phí -0.46% (trung vị -0.50%, thắng sau phí 42.9%) | 215.3 |
| {'thr': 3, 'mkt': 'vni_ma50', 'liq': 'top20'} | n=6456 (phiên 878) prec 48.0% [Wilson 46.8–49.2] exp sau phí -0.45% (trung vị -0.50%, thắng sau phí 43.2%) | 89.7 |
| {'thr': 4, 'mkt': 'none', 'liq': 'top50'} | n=3085 (phiên 1082) prec 45.9% [Wilson 44.2–47.7] exp sau phí -0.48% (trung vị -0.75%, thắng sau phí 41.0%) | 42.8 |
| {'thr': 4, 'mkt': 'none', 'liq': 'top20'} | n=1228 (phiên 684) prec 45.8% [Wilson 43.1–48.6] exp sau phí -0.57% (trung vị -0.73%, thắng sau phí 40.4%) | 17.1 |
| {'thr': 4, 'mkt': 'vn30_ma10m', 'liq': 'top50'} | n=1641 (phiên 528) prec 48.2% [Wilson 45.8–50.6] exp sau phí -0.13% (trung vị -0.50%, thắng sau phí 43.8%) | 22.8 |
| {'thr': 4, 'mkt': 'vn30_ma10m', 'liq': 'top20'} | n=668 (phiên 370) prec 49.7% [Wilson 45.9–53.5] exp sau phí +0.01% (trung vị -0.50%, thắng sau phí 44.0%) | 9.3 |
| {'thr': 4, 'mkt': 'vni_ma50', 'liq': 'top50'} | n=2369 (phiên 752) prec 44.3% [Wilson 42.3–46.3] exp sau phí -0.59% (trung vị -0.88%, thắng sau phí 39.0%) | 32.9 |
| {'thr': 4, 'mkt': 'vni_ma50', 'liq': 'top20'} | n=946 (phiên 513) prec 44.2% [Wilson 41.1–47.4] exp sau phí -0.71% (trung vị -0.87%, thắng sau phí 37.8%) | 13.1 |
| {'thr': 5, 'mkt': 'none', 'liq': 'top50'} | n=29 (phiên 27) prec 27.6% [Wilson 14.7–45.7] exp sau phí -3.78% (trung vị -2.39%, thắng sau phí 24.1%) | 0.4 |
| {'thr': 5, 'mkt': 'none', 'liq': 'top20'} | n=12 (phiên 10) prec 25.0% [Wilson 8.9–53.2] exp sau phí -6.98% (trung vị -5.87%, thắng sau phí 16.7%) | 0.2 |
| {'thr': 5, 'mkt': 'vn30_ma10m', 'liq': 'top50'} | n=13 (phiên 12) prec 23.1% [Wilson 8.2–50.3] exp sau phí -5.39% (trung vị -2.39%, thắng sau phí 15.4%) | 0.2 |
| {'thr': 5, 'mkt': 'vn30_ma10m', 'liq': 'top20'} | n=6 (phiên 5) prec 16.7% [Wilson 3.0–56.4] exp sau phí -11.16% (trung vị -8.68%, thắng sau phí 0.0%) | 0.1 |
| {'thr': 5, 'mkt': 'vni_ma50', 'liq': 'top50'} | n=14 (phiên 14) prec 42.9% [Wilson 21.4–67.4] exp sau phí -0.72% (trung vị -1.05%, thắng sau phí 35.7%) | 0.2 |
| {'thr': 5, 'mkt': 'vni_ma50', 'liq': 'top20'} | n=5 (phiên 5) prec 60.0% [Wilson 23.1–88.2] exp sau phí -0.70% (trung vị -0.20%, thắng sau phí 40.0%) | 0.1 |

## Walk-forward (chọn trên train, đóng băng, kiểm năm sau)
- Train 2018→2020, test 2021: KHÔNG bộ lọc nào đạt ≥80% & exp>0 trên train → mô tả bộ lọc Wilson lo cao nhất; chọn {'thr': 3, 'mkt': 'vn30_ma10m', 'liq': 'top50'}
  - train: n=3919 (phiên 246) prec 50.2% [Wilson 48.6–51.8] exp sau phí +0.01% (trung vị -0.37%, thắng sau phí 46.2%)
  - test : n=4450 (phiên 250) prec 52.7% [Wilson 51.3–54.2] exp sau phí +0.19% (trung vị -0.13%, thắng sau phí 48.6%); 370.8 tín hiệu/tháng
- Train 2018→2021, test 2022: KHÔNG bộ lọc nào đạt ≥80% & exp>0 trên train → mô tả bộ lọc Wilson lo cao nhất; chọn {'thr': 3, 'mkt': 'vn30_ma10m', 'liq': 'top50'}
  - train: n=8369 (phiên 496) prec 51.5% [Wilson 50.5–52.6] exp sau phí +0.10% (trung vị -0.23%, thắng sau phí 47.4%)
  - test : n=915 (phiên 76) prec 35.8% [Wilson 32.8–39.0] exp sau phí -2.62% (trung vị -2.24%, thắng sau phí 32.6%); 76.2 tín hiệu/tháng
- Train 2018→2022, test 2023: KHÔNG bộ lọc nào đạt ≥80% & exp>0 trên train → mô tả bộ lọc Wilson lo cao nhất; chọn {'thr': 3, 'mkt': 'vn30_ma10m', 'liq': 'top50'}
  - train: n=9284 (phiên 572) prec 50.0% [Wilson 49.0–51.0] exp sau phí -0.16% (trung vị -0.46%, thắng sau phí 46.0%)
  - test : n=1510 (phiên 83) prec 52.1% [Wilson 49.6–54.6] exp sau phí -0.44% (trung vị -0.22%, thắng sau phí 46.8%); 125.8 tín hiệu/tháng
OOS gộp: không có cửa sổ nào chọn được bộ lọc đạt trên train → KHÔNG ĐẠT

Precision FULL cao nhất trong lưới (hậu nghiệm, chỉ mô tả): {'thr': 5, 'mkt': 'vni_ma50', 'liq': 'top20'} → n=5 (phiên 5) prec 60.0% [Wilson 23.1–88.2] exp sau phí -0.70% (trung vị -0.20%, thắng sau phí 40.0%)
