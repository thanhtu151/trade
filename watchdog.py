"""Read-only watchdog decision engine for scheduler state."""

import argparse
import json
import subprocess
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


ICT = ZoneInfo("Asia/Ho_Chi_Minh")
SLA_DELAY = timedelta(minutes=20)
HUNG_AFTER = timedelta(minutes=110)
FAILURE_THRESHOLD = 3
SCHEDULE = {
    "prep": time(8, 0),
    "analysis": time(8, 30),
    "trade": time(9, 20),
    "eod": time(15, 0),
    "learning": time(16, 0),
}


def _parse(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ICT)


def trade_session_open(now):
    local_time = now.astimezone(ICT).time()
    return time(9, 15) <= local_time <= time(11, 25) or time(13, 0) <= local_time <= time(14, 25)


def evaluate(status, now=None, trading_day=True):
    now = (now or datetime.now(ICT)).astimezone(ICT)
    tasks = status.get("tasks", {}) if isinstance(status, dict) else {}
    missed, hung, failing = [], [], []
    if trading_day:
        for task, scheduled in SCHEDULE.items():
            due = datetime.combine(now.date(), scheduled, ICT) + SLA_DELAY
            row = tasks.get(task, {})
            last_success = _parse(row.get("last_success"))
            if now >= due and (last_success is None or last_success.astimezone(ICT).date() != now.date()):
                if task != "trade" or trade_session_open(now):
                    missed.append(task)
    if now.weekday() == 0:
        row = tasks.get("rebacktest", {})
        due = datetime.combine(now.date(), time(7, 0), ICT) + SLA_DELAY
        success = _parse(row.get("last_success"))
        if now >= due and (success is None or success.astimezone(ICT).date() != now.date()):
            missed.append("rebacktest")
    for task, row in tasks.items():
        started = _parse(row.get("started_at")) if row.get("state") == "running" else None
        if started and now - started.astimezone(ICT) > HUNG_AFTER:
            hung.append(task)
        if int(row.get("consecutive_failures", 0)) >= FAILURE_THRESHOLD:
            failing.append(task)
    return {"missed": missed, "hung": hung, "failing": failing, "disable_trade": bool(failing or hung)}


def load_from_state_ref(ref="origin/state:system_status.json"):
    result = subprocess.run(["git", "show", ref], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-ref", default="origin/state:system_status.json")
    args = parser.parse_args()
    from scheduler import is_trading_day

    now = datetime.now(ICT)
    print(json.dumps(evaluate(load_from_state_ref(args.state_ref), now, is_trading_day(now.date())), separators=(",", ":")))

