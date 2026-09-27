# Token discipline
- KHÔNG đọc toàn bộ dashboard_vn.py, auto_trader.py, financial_advisor.py.
  Dùng `grep -n` tìm hàm, rồi chỉ đọc đoạn liên quan (±40 dòng).
- KHÔNG mở file JSON state (paper_trades.json, analysis_results.json, *_log.json...) trừ khi được yêu cầu;
  cần xem thì dùng `python -c` in ra số dòng hoặc vài bản ghi cuối.
- Test: luôn `pytest -q --tb=short`; chỉ chạy file test liên quan khi đang sửa, chạy full suite 1 lần ở cuối.
- Log CI: `gh run view <id> --log-failed | tail -80`, không đổ toàn bộ log.
- Không in lại nội dung file vừa sửa; chỉ tóm tắt diff.
- Báo cáo ngắn gọn dạng bảng, không lặp lại context đã biết.
