# Lịch chạy paper hằng ngày (MẪU, chưa cài)

Script: `scripts/paper_daily.sh` — chỉ PaperBroker, không `--reset`, không đặt lệnh thật.
Bỏ qua thứ 7/CN; ngày không có phiên thì ghi `no session` và thoát 0.

## Crontab mẫu (15:30 thứ 2–6, giờ Việt Nam)

```
30 15 * * 1-5 /ĐƯỜNG/DẪN/TỚI/REPO/scripts/paper_daily.sh
```

- Cron chạy theo múi giờ của máy. Nếu máy không đặt giờ VN (UTC+7), đổi giờ cho khớp
  (ví dụ máy UTC: `30 8 * * 1-5`). Script tự đặt `TZ=Asia/Ho_Chi_Minh` khi tính "hôm nay".
- HOSE đóng cửa 14:45, chạy 15:30 để dữ liệu ngày đã cập nhật.
- Log: `paper_logs/daily_YYYY-MM-DD.log`; tóm tắt: `paper_logs/summary.csv`
  (`date,e1,e6,vn30,nav,orders`, mỗi ngày một dòng).
- Cài thủ công khi được duyệt: `crontab -e`, dán dòng trên. Cần `vnstock` đã cài trong `.venv`
  (xem `requirements-vnstock.txt`).
