"""Read-only watchdog decision engine for scheduler state."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, time, timedelta, timezone
from trading_calendar import is_trading_day


ICT = timezone(timedelta(hours=7), "ICT")
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


def dispatched_today(runs, task, now=None):
    today = (now or datetime.now(ICT)).astimezone(ICT).date()
    expected_title = f"scheduler-{task}"
    for run in runs or []:
        created = _parse(run.get("createdAt"))
        if run.get("displayTitle") == expected_title and created and created.astimezone(ICT).date() == today:
            return True
    return False


def _blocked_today(row, now):
    updated = _parse(row.get("last_update"))
    return row.get("state") == "blocked" and updated is not None and updated.astimezone(ICT).date() == now.date()


def decision_fingerprint(decision):
    payload = {
        "status_readable": bool(decision.get("status_readable")),
        "failing": sorted(decision.get("failing") or []),
        "hung": sorted(decision.get("hung") or []),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def fingerprint_marker(fingerprint):
    return f"<!-- fp:{fingerprint} -->"


def latest_issue_fingerprint(issue):
    comments = issue.get("comments") or []
    text = str(comments[-1].get("body", "")) if comments else str(issue.get("body", ""))
    match = re.search(r"<!-- fp:([0-9a-f]+) -->", text)
    return match.group(1) if match else None


def trading_disabled_payload_enabled(text):
    try:
        value = json.loads(text)
        return isinstance(value, dict) and value.get("disabled", True) is True
    except (TypeError, json.JSONDecodeError):
        return False


def evaluate(status, now=None, trading_day=True, status_kind=None, deployed_at=None):
    now = (now or datetime.now(ICT)).astimezone(ICT)
    tasks = status.get("tasks", {}) if isinstance(status, dict) else {}
    status_readable = isinstance(tasks, dict) and bool(tasks)
    missed, hung, failing = [], [], []
    if not status_readable:
        kind = status_kind or "legacy"
        deployed = _parse(deployed_at)
        legacy_grace = kind == "legacy" and (deployed is None or now - deployed.astimezone(ICT) < timedelta(hours=24))
        return {
            "status_readable": False, "status_kind": kind, "legacy_grace": legacy_grace,
            "issue_required": kind != "legacy" or not legacy_grace,
            "missed": [], "hung": [], "failing": [], "disable_trade": False,
        }
    if trading_day:
        for task, scheduled in SCHEDULE.items():
            due = datetime.combine(now.date(), scheduled, ICT) + SLA_DELAY
            row = tasks.get(task, {})
            if _blocked_today(row, now):
                continue
            last_success = _parse(row.get("last_success"))
            if now >= due and (last_success is None or last_success.astimezone(ICT).date() != now.date()):
                if task != "trade" or trade_session_open(now):
                    missed.append(task)
    if now.weekday() == 0:
        row = tasks.get("rebacktest", {})
        due = datetime.combine(now.date(), time(7, 0), ICT) + SLA_DELAY
        success = _parse(row.get("last_success"))
        if not _blocked_today(row, now) and now >= due and (success is None or success.astimezone(ICT).date() != now.date()):
            missed.append("rebacktest")
    for task, row in tasks.items():
        started = _parse(row.get("started_at")) if row.get("state") == "running" else None
        if started and now - started.astimezone(ICT) > HUNG_AFTER:
            hung.append(task)
        if int(row.get("consecutive_failures", 0)) >= FAILURE_THRESHOLD:
            failing.append(task)
    critical = {"analysis", "trade"}
    disable_trade = bool(critical.intersection(failing) or critical.intersection(hung))
    return {"status_readable": True, "status_kind": "ok", "legacy_grace": False, "issue_required": bool(failing or hung),
            "missed": missed, "hung": hung, "failing": failing, "disable_trade": disable_trade}


def load_from_state_ref(ref="origin/state:system_status.json"):
    result = subprocess.run(["git", "show", ref], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return {}, "missing"
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            return {}, "corrupt"
        tasks = value.get("tasks")
        return (value, "ok") if isinstance(tasks, dict) and tasks else (value, "legacy")
    except json.JSONDecodeError:
        return {}, "corrupt"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-ref", default="origin/state:system_status.json")
    parser.add_argument("--deployed-at", default="")
    args = parser.parse_args()
    now = datetime.now(ICT)
    status, kind = load_from_state_ref(args.state_ref)
    decision = evaluate(status, now, is_trading_day(now.date()), status_kind=kind, deployed_at=args.deployed_at)
    decision["fingerprint"] = decision_fingerprint(decision)
    print(json.dumps(decision, separators=(",", ":")))

