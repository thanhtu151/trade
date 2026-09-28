"""Read-only watchdog decision engine for scheduler state."""

import argparse
import hashlib
import json
import re
import subprocess
import os
import tempfile
from pathlib import Path
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


def reconcile_finished_runs(status, runs, now=None):
    """Reconcile durable running rows against authoritative Actions run state."""
    by_id = {str(row.get("id") or row.get("databaseId")): row for row in (runs or [])}
    changed = []
    timestamp = now or datetime.now(timezone.utc).isoformat()
    for task, row in status.get("tasks", {}).items():
        if row.get("state") != "running":
            continue
        run = by_id.get(str(row.get("run_id")))
        if not run or run.get("status") != "completed":
            continue
        conclusion = str(run.get("conclusion") or "failed")
        if conclusion == "success":
            # A successful workflow cannot normally leave running state; classify
            # it as failed because its task completion was not durably persisted.
            conclusion = "failed"
        row["state"] = "cancelled" if conclusion == "cancelled" else "failed"
        row["last_update"] = timestamp
        row["last_error"] = {"at": timestamp, "message": f"Actions run {row.get('run_id')} concluded {run.get('conclusion')} before task completion was persisted"}
        row["consecutive_failures"] = int(row.get("consecutive_failures", 0)) + 1
        row.pop("started_at", None)
        row.pop("deadline_at", None)
        changed.append(task)
    if changed:
        status["updated_at"] = timestamp
    return changed


def load_actions_runs(text):
    """Parse the documented Actions API response, rejecting CLI/table output."""
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Actions API response is not JSON") from exc
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else payload
    if not isinstance(runs, list) or any(not isinstance(row, dict) for row in runs):
        raise ValueError("Actions API response has no workflow_runs list")
    return runs


def reconcile_status_file(status_file, runs_file):
    path = Path(status_file)
    status = json.loads(path.read_text(encoding="utf-8"))
    runs = load_actions_runs(Path(runs_file).read_text(encoding="utf-8"))
    changed = reconcile_finished_runs(status, runs)
    if changed:
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(status, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    return changed


CATCHUP_PRIORITY = ("prep", "analysis", "trade", "eod", "learning", "rebacktest")


def plan_catchup(decision, runs, now=None):
    missed = set(decision.get("missed") or [])
    ordered = [task for task in CATCHUP_PRIORITY if task in missed]
    resumable = set(decision.get("resumable") or [])
    eligible = [task for task in ordered if task in resumable or not dispatched_today(runs, task, now)]
    return {"dispatch": eligible[0] if eligible else None, "deferred": eligible[1:]}


def evaluate(status, now=None, trading_day=True, status_kind=None, deployed_at=None):
    now = (now or datetime.now(ICT)).astimezone(ICT)
    tasks = status.get("tasks", {}) if isinstance(status, dict) else {}
    status_readable = isinstance(tasks, dict) and bool(tasks)
    missed, hung, failing, resumable = [], [], [], []
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
        if row.get("state") == "deferred":
            resumable.append(task)
        started = _parse(row.get("started_at")) if row.get("state") == "running" else None
        deadline = _parse(row.get("deadline_at")) if row.get("state") == "running" else None
        if (deadline and now > deadline.astimezone(ICT)) or (not deadline and started and now - started.astimezone(ICT) > HUNG_AFTER):
            hung.append(task)
        if int(row.get("consecutive_failures", 0)) >= FAILURE_THRESHOLD:
            failing.append(task)
    critical = {"analysis", "trade"}
    disable_trade = bool(critical.intersection(failing) or critical.intersection(hung))
    return {"status_readable": True, "status_kind": "ok", "legacy_grace": False, "issue_required": bool(failing or hung),
            "missed": missed, "hung": hung, "failing": failing, "resumable": resumable, "disable_trade": disable_trade}


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
    parser.add_argument("--reconcile-status", default="")
    parser.add_argument("--runs-file", default="")
    args = parser.parse_args()
    if args.reconcile_status:
        if not args.runs_file:
            parser.error("--runs-file is required with --reconcile-status")
        print(json.dumps({"reconciled": reconcile_status_file(args.reconcile_status, args.runs_file)}))
        raise SystemExit(0)
    now = datetime.now(ICT)
    status, kind = load_from_state_ref(args.state_ref)
    decision = evaluate(status, now, is_trading_day(now.date()), status_kind=kind, deployed_at=args.deployed_at)
    decision["fingerprint"] = decision_fingerprint(decision)
    print(json.dumps(decision, separators=(",", ":")))

