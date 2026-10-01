# research/tools

- `python3 research/tools/repo_map.py` — sinh `REPO_MAP.md` (module, dòng, hàm:dòng, module→test); chỉ đọc mã, chỉ ghi REPO_MAP.md.
- `python3 research/tools/paper_status.py [--ref state]` — tóm tắt sổ paper ≤15 dòng qua `git show state:<file>` (tự thử `origin/state` nếu không có nhánh local `state`); không checkout.

Trạng thái: chưa chạy thử được (guard chặn lệnh Bash, xem báo cáo T16).
