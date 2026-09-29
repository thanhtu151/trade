# Kích hoạt đúng giờ bằng cron bên ngoài

## Vì sao cần

Cron của GitHub Actions (`on: schedule`) chỉ chạy theo kiểu "best effort" và thường trễ. Số liệu thực tế:

- 28/09/2026: task `eod` trễ 8 giờ, task `learning` bị bỏ qua.
- 29/09/2026:
  - `prep` (08:00) chạy lúc 13:25;
  - `analysis` (08:30) chạy lúc 14:21;
  - `trade` (09:20) chạy lúc **15:42**, sau giờ đóng cửa.

Scheduler đã được bảo vệ khỏi hậu quả xấu nhất: `trade` chỉ chạy trong phiên liên tục (09:15–11:25, 13:00–14:25), còn `eod` và ETF core xử lý bù theo dữ liệu. Tuy vậy, chạy trễ vẫn làm mất cả ngày phân tích và giao dịch.

Gọi `workflow_dispatch` từ một dịch vụ cron bên ngoài thường chính xác đến từng phút. Cron của GitHub vẫn được giữ làm dự phòng. Các task đều idempotent (`already_ran_today`), nên chạy trùng không gây hại.

## Bước 1: tạo token GitHub (quyền tối thiểu)

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Cấu hình token:
   - **Repository access**: *Only select repositories* → `thanhtu151/trade`.
   - **Permissions** → Repository → **Actions: Read and write**. Các quyền khác giữ *No access*.
   - **Expiration**: 1 năm. Đặt nhắc gia hạn.
3. Lưu token. Không commit token vào repo.

## Bước 2: tạo các job trên cron-job.org (miễn phí)

Mỗi job dùng chung cấu hình sau:

- **URL**: `https://api.github.com/repos/thanhtu151/trade/actions/workflows/scheduler.yml/dispatches`
- **Method**: `POST`
- **Headers**:
  - `Authorization: Bearer <TOKEN>`
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
- **Body**: `{"ref":"main","inputs":{"task":"<TASK>"}}`
- **Timezone**: `Asia/Ho_Chi_Minh`
- **Schedule**: Thứ 2 đến Thứ 6, theo bảng dưới.

| Giờ (ICT) | `<TASK>` | Ghi chú |
|---|---|---|
| 08:00 | `prep` | Kèm đồng bộ lịch giao dịch và duyệt candidate |
| 08:30 | `analysis` | |
| 09:20 | `trade` | Bị chặn nếu ngoài phiên hoặc `TRADING_ENABLED=false` |
| 15:05 | `eod` | Sau ATC; ETF core tính tín hiệu cuối tháng tại đây |
| 16:00 | `learning` | |
| Thứ 2 07:00 | `rebacktest` | Mỗi lần chạy xử lý 5 mã; watchdog tự chạy tiếp các lượt còn lại |

Ngày nghỉ lễ không cần xử lý riêng: scheduler tự bỏ qua theo `trading_calendar`.

## Bước 3: kiểm tra

- Bấm **Test run** trên cron-job.org cho task `prep`. Kết quả phải là HTTP **204**.
- Trong GitHub → **Actions** phải xuất hiện run tên `scheduler-prep`, với event là `workflow_dispatch`.
- Lỗi thường gặp:
  - **401/403**: token sai hoặc thiếu quyền Actions.
  - **422**: tên task không nằm trong danh sách `options` của workflow.
