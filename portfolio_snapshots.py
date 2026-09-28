"""Durable end-of-session portfolio snapshots and comparable daily returns."""

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from trading_calendar import is_trading_day


SNAPSHOT_FILE = "portfolio_snapshots.json"


def _atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def load_snapshots(base_dir):
    try:
        value = json.loads((Path(base_dir) / SNAPSHOT_FILE).read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return []


def portfolio_equity(portfolio):
    positions = portfolio.get("positions", {}) or {}
    market = sum(float(row.get("market_value", float(row.get("qty", 0) or 0) *
                             float(row.get("current_price", row.get("avg_price", 0)) or 0)))
                 for row in positions.values())
    return float(portfolio.get("cash", 0) or 0) + market, market


def record_snapshot(base_dir, portfolio, session_date, recorded_at=None):
    rows = load_snapshots(base_dir)
    equity, market = portfolio_equity(portfolio)
    positions = portfolio.get("positions", {}) or {}
    item = {
        "date": session_date.isoformat(),
        "time": (recorded_at or datetime.now().astimezone()).isoformat(),
        "ledger_epoch": int(portfolio.get("ledger_epoch", 0) or 0),
        "cash": float(portfolio.get("cash", 0) or 0),
        "market_value": market,
        "equity": equity,
        "positions": sorted(positions),
        "n_positions": len(positions),
    }
    rows = [row for row in rows if row.get("date") != item["date"]]
    rows.append(item)
    rows.sort(key=lambda row: str(row.get("date", "")))
    _atomic_write(Path(base_dir) / SNAPSHOT_FILE, rows[-365:])
    return item


def previous_trading_day(day):
    candidate = day - timedelta(days=1)
    for _ in range(370):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


def daily_change(equity, snapshots, session_date, ledger_epoch):
    previous = previous_trading_day(session_date)
    if previous is None:
        return None, "previous HOSE session is outside the verified calendar"
    candidates = [row for row in snapshots if row.get("date") == previous.isoformat()]
    if not candidates:
        return None, f"no snapshot for previous HOSE session {previous.isoformat()}"
    row = candidates[-1]
    if int(row.get("ledger_epoch", 0) or 0) != int(ledger_epoch or 0):
        return None, "previous snapshot belongs to a different ledger epoch"
    try:
        prior = float(row["equity"])
    except (KeyError, TypeError, ValueError):
        return None, "previous snapshot has invalid equity"
    if prior <= 0:
        return None, "previous snapshot has non-positive equity"
    return round((float(equity) / prior - 1) * 100, 10), None
