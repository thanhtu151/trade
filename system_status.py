"""Durable per-task execution status for unattended scheduling and watchdogs."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


STATUS_NAME = "system_status.json"
TASK_DEADLINES = {"rebacktest": 85, "prep": 55, "analysis": 55, "trade": 25, "eod": 55, "learning": 55, "heal": 25}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_status(base_dir):
    path = Path(base_dir) / STATUS_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status root is not an object")
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        value = {}
    value.setdefault("version", 1)
    value.setdefault("tasks", {})
    return value


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def update_task(base_dir, task, state, error=None, run_id=None, now=None):
    timestamp = now or utc_now()
    status = load_status(base_dir)
    row = status["tasks"].setdefault(str(task), {"consecutive_failures": 0})
    row["state"] = state
    row["last_update"] = timestamp
    row["run_id"] = str(run_id or os.getenv("GITHUB_RUN_ID") or "local")
    if state == "running":
        row["started_at"] = timestamp
        started = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        row["deadline_at"] = (started + timedelta(minutes=TASK_DEADLINES.get(str(task), 55))).isoformat()
    elif state == "success":
        row["last_success"] = timestamp
        row["last_error"] = None
        row["consecutive_failures"] = 0
        row.pop("started_at", None)
        row.pop("deadline_at", None)
    elif state == "failed":
        row["last_error"] = {"at": timestamp, "message": str(error or "unknown error")[:2000]}
        row["consecutive_failures"] = int(row.get("consecutive_failures", 0)) + 1
        row.pop("started_at", None)
        row.pop("deadline_at", None)
        row.pop("blocked_reason", None)
    elif state == "blocked":
        row["blocked_reason"] = str(error or "blocked by safety gate")[:2000]
        row["last_error"] = None
        row["consecutive_failures"] = 0
        row.pop("started_at", None)
        row.pop("deadline_at", None)
    elif state in {"cancelled", "deferred"}:
        row["last_error"] = {"at": timestamp, "message": str(error or state)[:2000]}
        if state == "cancelled":
            row["consecutive_failures"] = int(row.get("consecutive_failures", 0)) + 1
        row.pop("started_at", None)
        row.pop("deadline_at", None)
    status["updated_at"] = timestamp
    _write(Path(base_dir) / STATUS_NAME, status)
    return row

