"""Fail-closed operational gates shared by every paper-trade entry point."""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DISABLE_FILE = "trading_disabled.json"
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


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
