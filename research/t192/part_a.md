# T192 phần A — prediction_log walk-forward

Dòng hợp lệ đã có kết quả: 210 (2026-06-15 → 2026-10-02); MUA 139, BÁN 71
Số bộ lọc trong lưới: 160

Toàn bộ: n=210 precision 47.1% (Wilson 40.5–53.9%), kỳ vọng MUA sau phí -1.79%/lệnh
  MUA: n=139 precision 40.3% (Wilson 32.5–48.6%)
  BÁN: n=71 precision 60.6% (Wilson 48.9–71.1%)

| Tháng test | n train | Bộ lọc chọn trên train | train n / precision / Wilson lo | test n / precision (Wilson) | test kỳ vọng MUA sau phí |
|---|---|---|---|---|---|
| 2026-07 | 25 | không bộ lọc nào n≥30 | – | – | – |
| 2026-08 | 94 | {'dir': 'all', 'conf': 0, 's1': 0, 'src': 'all', 'llm': 'all'} (không đạt 80% trên train → mô tả, Wilson lo cao nhất) | 94 / 44.7% / 35.0% | 49 / 57.1% (43.3–70.0%) | 0.05% (n MUA 39) |
| 2026-09 | 143 | {'dir': 'all', 'conf': 0, 's1': 0, 'src': 'all', 'llm': 'all'} (không đạt 80% trên train → mô tả, Wilson lo cao nhất) | 143 / 49.0% / 40.9% | 58 / 44.8% (32.7–57.5%) | -2.75% (n MUA 32) |
| 2026-10 | 201 | {'dir': 'all', 'conf': 0, 's1': 0, 'src': 'all', 'llm': 'all'} (không đạt 80% trên train → mô tả, Wilson lo cao nhất) | 201 / 47.8% / 41.0% | 9 / 33.3% (12.1–64.6%) ⚠ n<30 | -1.82% (n MUA 5) |

Top 5 precision in-sample (n≥30, CHỈ MÔ TẢ, có chọn lọc hậu nghiệm):
- {'dir': 'all', 'conf': 30, 's1': 0, 'src': 'all', 'llm': 'all'}: n=132 47.7% (Wilson 39.4–56.2%), exp MUA sau phí -1.90%
- {'dir': 'all', 'conf': 0, 's1': 0, 'src': 'all', 'llm': 'all'}: n=210 47.1% (Wilson 40.5–53.9%), exp MUA sau phí -1.79%
- {'dir': 'all', 'conf': 50, 's1': 0, 'src': 'all', 'llm': 'all'}: n=34 47.1% (Wilson 31.5–63.3%), exp MUA sau phí -1.31%
- {'dir': 'all', 'conf': 30, 's1': 2, 'src': 'all', 'llm': 'all'}: n=117 47.0% (Wilson 38.2–56.0%), exp MUA sau phí -1.97%
- {'dir': 'all', 'conf': 0, 's1': 2, 'src': 'all', 'llm': 'all'}: n=180 46.7% (Wilson 39.5–53.9%), exp MUA sau phí -1.83%
Bộ lọc precision ≥80% với n≥5 (in-sample): 0; lớn nhất n=0

Precision theo khoảng confidence (toàn bộ):
- [0,30): n=78 46.2% (Wilson 35.5–57.1%)
- [30,40): n=54 50.0% (Wilson 37.1–62.9%)
- [40,50): n=44 45.5% (Wilson 31.7–59.9%)
- [50,60): n=19 36.8% (Wilson 19.1–59.0%)
- [60,101): n=15 60.0% (Wilson 35.7–80.2%)
