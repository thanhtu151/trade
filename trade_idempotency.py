"""Persistent, cross-run idempotency reservations for paper-trade execution."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


STATE_BUCKET = "trade_idempotency"


@contextmanager
def _locked(path: Path):
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def build_key(trade_date: str, ticker: str, side: str, signal_id: str) -> str:
    fields = [trade_date, ticker.upper(), side.upper(), signal_id]
    if any(not str(field).strip() for field in fields):
        raise ValueError("idempotency key fields must be non-empty")
    return ":".join(str(field).strip() for field in fields)


def _write_atomic(path: Path, payload):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def reserve(state_file, key: str, metadata: dict) -> bool:
    """Atomically reserve key; an existing reservation always wins."""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("scheduler state must be an object")
        except FileNotFoundError:
            state = {}
        bucket = state.setdefault(STATE_BUCKET, {})
        if not isinstance(bucket, dict):
            raise ValueError("trade idempotency state must be an object")
        if key in bucket:
            return False
        bucket[key] = {
            "status": "reserved",
            "reserved_at": datetime.now().astimezone().isoformat(),
            **metadata,
        }
        _write_atomic(path, state)
        return True


def complete(state_file, key: str, success: bool, detail: str = ""):
    path = Path(state_file)
    with _locked(path):
        state = json.loads(path.read_text(encoding="utf-8"))
        record = state[STATE_BUCKET][key]
        record["status"] = "completed" if success else "failed_safe"
        record["completed_at"] = datetime.now().astimezone().isoformat()
        record["detail"] = str(detail)[:500]
        _write_atomic(path, state)
