"""Đo độ trễ cron GitHub Actions cho workflow 'VN Trading Scheduler'.

Dùng:
  gh run list -R thanhtu151/trade -L 200 --json event,displayTitle,createdAt > runs.json
  python measure_delay.py runs.json [--days 28]

Với event=schedule, displayTitle = "scheduler-<cron>" (run-name trong scheduler.yml).
Trễ = createdAt (UTC) - thời điểm cron danh nghĩa cùng ngày UTC (lấy ngày của createdAt;
nếu trễ vượt qua nửa đêm UTC thì thử ngày trước đó và chọn ứng viên trễ >= 0 nhỏ nhất).
Chỉ tính createdAt (lúc run được tạo), chưa gồm thời gian xếp hàng concurrency.
"""
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

JOB = {
    "0 1 * * 1-5": "prep 08:00",
    "30 1 * * 1-5": "analysis 08:30",
    "20 2 * * 1-5": "trade 09:20",
    "0 8 * * 1-5": "eod 15:00",
    "0 9 * * 1-5": "learning 16:00",
    "0 0 * * 1": "rebacktest Mon 07:00",
}


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def delay_minutes(cron, created):
    m, h = (int(x) for x in cron.split()[:2])
    best = None
    for back in (0, 1):
        day = (created - timedelta(days=back)).date()
        nominal = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
        d = (created - nominal).total_seconds() / 60
        if d >= 0 and (best is None or d < best):
            best = d
    return best


def pct(sorted_vals, p):
    i = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def main():
    path = sys.argv[1]
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 28
    runs = json.load(open(path, encoding="utf-8"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_job = defaultdict(list)
    for r in runs:
        if r.get("event") != "schedule":
            continue
        title = r.get("displayTitle", "")
        cron = title.removeprefix("scheduler-")
        if cron not in JOB:
            continue
        created = parse(r["createdAt"])
        if created < cutoff:
            continue
        d = delay_minutes(cron, created)
        if d is not None:
            by_job[JOB[cron]].append(d)
    print(f"{'job':24}{'n':>4}{'median':>9}{'p90':>9}{'max':>9}  (phút)")
    for job, vals in sorted(by_job.items()):
        v = sorted(vals)
        print(f"{job:24}{len(v):>4}{statistics.median(v):>9.0f}{pct(v, .9):>9.0f}{v[-1]:>9.0f}")


if __name__ == "__main__":
    main()
