"""Fail-closed operational gates shared by every paper-trade entry point."""

from __future__ import annotations

import json
import os
import argparse
import tempfile
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DISABLE_FILE = "trading_disabled.json"
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
ENABLE_CONFIRMATION = "ENABLE_TRADING"


def vietnam_now() -> datetime:
    return datetime.now(VIETNAM_TZ)


def kill_switch_reason(base_dir=None) -> str | None:
    enabled = os.getenv("TRADING_ENABLED", "true").strip().lower()
    if enabled in _FALSE_VALUES:
        return "TRADING_ENABLED disables trading"

    path = Path(base_dir or Path(__file__).resolve().parent) / DISABLE_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"kill-switch file is unreadable: {exc}"
    if not isinstance(payload, dict):
        return "kill-switch file has invalid schema"
    if payload.get("disabled", True):
        return str(payload.get("reason") or "persisted kill switch disables trading")
    return None


def market_session_reason(now: datetime | None = None) -> str | None:
    current = now or vietnam_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=VIETNAM_TZ)
    current = current.astimezone(VIETNAM_TZ)
    if current.weekday() >= 5:
        return "market is closed on weekends"
    wall_time = current.time().replace(tzinfo=None)
    morning = time(9, 15) <= wall_time <= time(11, 25)
    afternoon = time(13, 0) <= wall_time <= time(14, 25)
    if not (morning or afternoon):
        return "outside configured trading sessions (09:15-11:25, 13:00-14:25 ICT)"
    return None


def operational_gate(base_dir=None, now: datetime | None = None) -> tuple[bool, str]:
    reason = kill_switch_reason(base_dir)
    if reason:
        return False, reason
    reason = market_session_reason(now)
    if reason:
        return False, reason
    return True, "operational gates passed"


def _atomic_json(path, value):
    path = Path(path)
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


def _append_switch_audit(base, event):
    path = base / "trading_switch_audit.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            rows = []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        rows = []
    rows.append(event)
    _atomic_json(path, rows)


def disable_trading(base_dir=None, reason="", actor="", run_id=""):
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("disable-trading requires a reason")
    base = Path(base_dir or Path(__file__).resolve().parent)
    event = {
        "action": "disable-trading", "disabled": True, "reason": reason,
        "actor": str(actor or os.getenv("GITHUB_ACTOR") or "unknown"),
        "run_id": str(run_id or os.getenv("GITHUB_RUN_ID") or "unknown"),
        "at": vietnam_now().isoformat(),
    }
    _atomic_json(base / DISABLE_FILE, event)
    _append_switch_audit(base, event)
    return event


def enable_trading(base_dir=None, confirmation="", reason="", actor="", run_id=""):
    if confirmation != ENABLE_CONFIRMATION:
        raise ValueError(f"confirmation must equal {ENABLE_CONFIRMATION}")
    base = Path(base_dir or Path(__file__).resolve().parent)
    event = {
        "action": "enable-trading", "reason": str(reason or "operator confirmed reopening").strip(),
        "actor": str(actor or os.getenv("GITHUB_ACTOR") or "unknown"),
        "run_id": str(run_id or os.getenv("GITHUB_RUN_ID") or "unknown"),
        "at": vietnam_now().isoformat(),
    }
    (base / DISABLE_FILE).unlink(missing_ok=True)
    _append_switch_audit(base, event)
    return event


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--disable", action="store_true")
    group.add_argument("--enable", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--actor", default="")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    if args.disable:
        result = disable_trading(reason=args.reason, actor=args.actor, run_id=args.run_id)
    else:
        result = enable_trading(confirmation=args.confirmation, reason=args.reason, actor=args.actor, run_id=args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
