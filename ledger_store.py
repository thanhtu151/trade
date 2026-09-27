"""Crash-safe storage and epoch helpers for the paper-trading ledger."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


PORTFOLIO_NAME = "paper_portfolio.json"
LEDGER_NAME = "paper_trades.json"
JOURNAL_NAME = "paper_ledger_transaction.json"
LOCK_NAME = "paper_ledger_transaction.lock"


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


@contextmanager
def _ledger_lock(base_dir):
    lock_path = Path(base_dir) / LOCK_NAME
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


def _validate_journal(journal):
    if not isinstance(journal, dict) or journal.get("version") != 1:
        raise ValueError("unsupported ledger journal")
    portfolio = journal.get("portfolio")
    ledger = journal.get("ledger")
    if not isinstance(portfolio, dict) or not isinstance(ledger, list):
        raise ValueError("ledger journal payload has invalid schema")
    checksums = journal.get("checksums") or {}
    if checksums.get("portfolio") != _digest(portfolio) or checksums.get("ledger") != _digest(ledger):
        raise ValueError("ledger journal checksum mismatch")
    return portfolio, ledger


def _roll_forward(base, journal):
    portfolio, ledger = _validate_journal(journal)
    _atomic_write(base / PORTFOLIO_NAME, portfolio)
    _atomic_write(base / LEDGER_NAME, ledger)
    journal_path = base / JOURNAL_NAME
    journal_path.unlink(missing_ok=True)
    return journal.get("transaction_id")


def recover_pending_transaction(base_dir):
    """Complete an interrupted two-file commit; never guess from partial files."""
    base = Path(base_dir)
    journal_path = base / JOURNAL_NAME
    with _ledger_lock(base):
        if not journal_path.exists():
            return None
        with journal_path.open(encoding="utf-8") as handle:
            journal = json.load(handle)
        return _roll_forward(base, journal)


def commit_portfolio_and_ledger(base_dir, portfolio, ledger, operation="trade", failpoint=None):
    """Write portfolio and ledger as one recoverable transaction.

    ``failpoint`` exists only for deterministic crash tests.
    """
    if not isinstance(portfolio, dict) or not isinstance(ledger, list):
        raise TypeError("portfolio must be an object and ledger must be a list")
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    journal = {
        "version": 1,
        "transaction_id": uuid.uuid4().hex,
        "operation": str(operation),
        "prepared_at": datetime.now().astimezone().isoformat(),
        "portfolio": portfolio,
        "ledger": ledger,
        "checksums": {"portfolio": _digest(portfolio), "ledger": _digest(ledger)},
    }
    with _ledger_lock(base):
        for name in (PORTFOLIO_NAME, LEDGER_NAME):
            source = base / name
            if source.exists() and source.stat().st_size:
                shutil.copy2(source, base / (name + ".bak"))
        _atomic_write(base / JOURNAL_NAME, journal)
        if failpoint == "after_journal":
            raise RuntimeError("simulated crash after journal")
        _atomic_write(base / PORTFOLIO_NAME, portfolio)
        if failpoint == "after_portfolio":
            raise RuntimeError("simulated crash after portfolio")
        _atomic_write(base / LEDGER_NAME, ledger)
        if failpoint == "after_ledger":
            raise RuntimeError("simulated crash after ledger")
        (base / JOURNAL_NAME).unlink(missing_ok=True)
    return journal["transaction_id"]


def reset_events(ledger):
    return [event for event in ledger if isinstance(event, dict) and str(event.get("type", "")).upper() == "RESET"]


def current_epoch_id(ledger):
    resets = reset_events(ledger)
    return max((int(event.get("epoch_id", 0)) for event in resets), default=0)


def current_epoch_start(ledger):
    resets = reset_events(ledger)
    if not resets:
        return None
    return max(resets, key=lambda event: int(event.get("epoch_id", 0))).get("time")


def events_in_current_epoch(ledger):
    epoch_id = current_epoch_id(ledger)
    if not reset_events(ledger):
        return list(ledger)
    return [event for event in ledger if isinstance(event, dict) and int(event.get("epoch_id", -1)) == epoch_id]


def make_reset_event(cash_after, reason, actor, ledger, at=None):
    reason = str(reason or "").strip()
    actor = str(actor or "").strip()
    if not reason or not actor:
        raise ValueError("RESET requires non-empty reason and actor")
    return {
        "type": "RESET",
        "time": at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cash_after": float(cash_after),
        "positions_after": {},
        "reason": reason,
        "actor": actor,
        "epoch_id": current_epoch_id(ledger) + 1,
    }


def label_epochs(ledger):
    """Return a migrated copy with deterministic epoch labels."""
    epoch_id = 0
    labeled = []
    for event in ledger:
        row = dict(event)
        if str(row.get("type", "")).upper() == "RESET":
            epoch_id = int(row["epoch_id"])
        row["epoch_id"] = epoch_id
        labeled.append(row)
    return labeled
