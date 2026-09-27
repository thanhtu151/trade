"""Deterministic self-healing and fail-safe checks for paper-trading state.

This module intentionally repairs only facts that can be derived without a
market opinion: JSON recovery, duplicate events, and calculated position
fields. Ambiguous accounting errors are reported and trading is blocked rather
than guessed at.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import hashlib
import argparse
from datetime import datetime
from pathlib import Path


INITIAL_CASH = 100_000_000.0
REPORT_FILE = "self_healing_state.json"
AUDIT_FILE = "self_healing_audit.json"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic_json_write(path, data, backup=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists() and path.stat().st_size:
        shutil.copy2(path, str(path) + ".bak")
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def _load_json_with_recovery(path, expected_type, default, actions, critical):
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, expected_type):
            raise ValueError(f"expected {expected_type.__name__}")
        return data
    except Exception as exc:
        backup = Path(str(path) + ".bak")
        try:
            with backup.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, expected_type):
                raise ValueError("backup has wrong type")
            _atomic_json_write(path, data, backup=False)
            actions.append(f"restored {path.name} from backup")
            return data
        except Exception:
            critical.append(f"{path.name} unreadable: {exc}")
            return default


def _trade_epoch(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return None


def _deduplicate_trades(trades):
    """Remove automated duplicates with identical economics within 3 seconds."""
    kept = []
    removed = []
    malformed = 0
    recent = {}
    for trade in trades:
        if not isinstance(trade, dict):
            kept.append(trade)
            malformed += 1
            continue
        try:
            key = (
                str(trade.get("symbol", "")).upper(),
                str(trade.get("side", "")).upper(),
                int(float(trade.get("qty", 0) or 0)),
                round(float(trade.get("price", 0) or 0), 4),
                round(float(trade.get("value", 0) or 0), 2),
                str(trade.get("reason", "")),
            )
        except (TypeError, ValueError, OverflowError):
            kept.append(trade)
            malformed += 1
            continue
        timestamp = _trade_epoch(trade.get("time"))
        previous = recent.get(key)
        automated = "scheduler" in key[-1].lower() or "auto" in key[-1].lower()
        if automated and timestamp is not None and previous is not None and 0 <= timestamp - previous <= 3:
            removed.append(trade)
            continue
        kept.append(trade)
        if timestamp is not None:
            recent[key] = timestamp
    return kept, removed, malformed


def _finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _event_signature(trade):
    payload = json.dumps(trade, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _repair_portfolio(portfolio, actions, warnings, critical):
    changed = False
    cash = _finite_number(portfolio.get("cash"))
    if cash is None or cash < 0:
        critical.append("portfolio cash is missing, non-finite, or negative")
    initial_cash = _finite_number(portfolio.get("initial_cash"))
    if initial_cash is None or initial_cash <= 0:
        portfolio["initial_cash"] = INITIAL_CASH
        changed = True
        actions.append("restored missing initial_cash")

    positions = portfolio.get("positions")
    if not isinstance(positions, dict):
        critical.append("portfolio positions is not an object")
        return changed

    for symbol, position in positions.items():
        if not isinstance(position, dict):
            critical.append(f"{symbol}: position is not an object")
            continue
        qty = _finite_number(position.get("qty"))
        avg = _finite_number(position.get("avg_price"))
        current = _finite_number(position.get("current_price"))
        if qty is None or qty <= 0 or int(qty) != qty:
            critical.append(f"{symbol}: invalid quantity")
            continue
        if avg is None or avg <= 0:
            critical.append(f"{symbol}: invalid average price")
            continue
        if current is None or current <= 0:
            current = avg
            position["current_price"] = round(current, 2)
            changed = True
            actions.append(f"{symbol}: restored current_price from avg_price")
        ratio = current / avg
        if ratio < 0.5 or ratio > 2.0:
            critical.append(f"{symbol}: price ratio {ratio:.2f} outside safe range")
            continue

        expected_value = round(qty * current, 2)
        expected_pnl = round((current - avg) * qty, 2)
        expected_pct = round((current / avg - 1) * 100, 4)
        calculated = {
            "market_value": expected_value,
            "unrealized_pnl": expected_pnl,
            "pnl_pct": expected_pct,
        }
        for field, expected in calculated.items():
            actual = _finite_number(position.get(field))
            tolerance = max(1.0, abs(expected) * 0.001)
            if actual is None or abs(actual - expected) > tolerance:
                position[field] = expected
                changed = True
                actions.append(f"{symbol}: recalculated {field}")

    if len(positions) > 5:
        warnings.append(f"portfolio has {len(positions)} positions (configured maximum is 5)")
    return changed


def run_self_healing(base_dir=None, repair=True):
    base = Path(base_dir or Path(__file__).resolve().parent)
    actions, warnings, critical = [], [], []
    portfolio_path = base / "paper_portfolio.json"
    trades_path = base / "paper_trades.json"
    report_path = base / REPORT_FILE
    from trading_safety import kill_switch_reason

    switch_reason = kill_switch_reason(base)
    if switch_reason:
        critical.append(f"trading kill switch: {switch_reason}")
    try:
        previous_report = json.loads(report_path.read_text(encoding="utf-8"))
        previous_checkpoint = previous_report.get("checkpoint") or {}
        previous_baseline = previous_report.get("ledger_baseline") or {}
    except Exception:
        previous_checkpoint = {}
        previous_baseline = {}

    portfolio = _load_json_with_recovery(
        portfolio_path, dict, {"initial_cash": INITIAL_CASH, "cash": 0, "positions": {}}, actions, critical
    )
    trades = _load_json_with_recovery(trades_path, list, [], actions, critical)

    portfolio_changed = _repair_portfolio(portfolio, actions, warnings, critical)
    clean_trades, duplicates, malformed = _deduplicate_trades(trades)
    if malformed:
        critical.append(f"trade ledger contains {malformed} malformed event(s)")
    if duplicates:
        actions.append(f"removed {len(duplicates)} duplicate automated trade event(s)")

    # The historical ledger may contain manual resets/older strategy generations.
    # Report drift, but never invent cash or positions from that ambiguous history.
    buy_value = sum(float(t.get("value", 0) or 0) for t in clean_trades if str(t.get("side", "")).upper() == "BUY")
    sell_value = sum(float(t.get("value", 0) or 0) for t in clean_trades if str(t.get("side", "")).upper() == "SELL")
    expected_cash = float(portfolio.get("initial_cash", INITIAL_CASH)) - buy_value + sell_value
    actual_cash = _finite_number(portfolio.get("cash"))
    historical_drift = actual_cash is not None and abs(expected_cash - actual_cash) > max(1000, INITIAL_CASH * 0.005)

    raw_previous_count = previous_checkpoint.get("trade_events", -1)
    previous_count = int(raw_previous_count) if raw_previous_count is not None else -1
    previous_cash = _finite_number(previous_checkpoint.get("cash"))
    previous_signature = previous_checkpoint.get("last_event_signature")
    checkpoint_continuous = (
        previous_count >= 0
        and previous_count <= len(clean_trades)
        and (previous_count == 0 or (
            previous_signature
            and _event_signature(clean_trades[previous_count - 1]) == previous_signature
        ))
    )
    current_cash_offset = expected_cash - actual_cash if actual_cash is not None else None
    baseline_offset = _finite_number(previous_baseline.get("cash_offset")) if isinstance(previous_baseline, dict) else None
    offset_tolerance = 1.0
    baseline_acknowledged = (
        baseline_offset is not None
        and current_cash_offset is not None
        and checkpoint_continuous
        and abs(current_cash_offset - baseline_offset) <= offset_tolerance
    )
    if historical_drift:
        detail = f"historical ledger drift: expected cash {expected_cash:,.0f}, actual {actual_cash:,.0f}; not auto-repaired"
        if baseline_acknowledged and checkpoint_continuous:
            warnings.append(detail + "; acknowledged baseline remains continuous")
        else:
            critical.append(detail + "; run self_healing.py --rebaseline with an audit reason")
    if checkpoint_continuous and previous_cash is not None and not malformed:
        new_events = clean_trades[previous_count:]
        cash_delta = 0.0
        for event in new_events:
            side = str(event.get("side", "")).upper()
            value = float(event.get("value", 0) or 0)
            if side == "BUY":
                cash_delta -= value
            elif side == "SELL":
                cash_delta += value
        checkpoint_expected_cash = previous_cash + cash_delta
        if actual_cash is None or abs(checkpoint_expected_cash - actual_cash) > 1.0:
            critical.append(
                f"cash checkpoint mismatch: expected {checkpoint_expected_cash:,.0f}, actual {actual_cash or 0:,.0f}"
            )
    elif previous_checkpoint:
        warnings.append("accounting checkpoint chain changed; established a new checkpoint")

    if repair and portfolio_changed and not critical:
        portfolio["updated_at"] = _now()
        _atomic_json_write(portfolio_path, portfolio)
    if repair and duplicates and not critical:
        _atomic_json_write(trades_path, clean_trades)

    status = "blocked" if critical else "healed" if actions else "healthy"
    next_checkpoint = {
        "cash": actual_cash,
        "trade_events": len(clean_trades),
        "last_event_signature": _event_signature(clean_trades[-1]) if clean_trades else None,
    }
    if critical and previous_checkpoint:
        next_checkpoint = previous_checkpoint

    report = {
        "status": status,
        "trading_allowed": not critical,
        "updated_at": _now(),
        "actions": actions,
        "warnings": warnings,
        "critical": critical,
        "metrics": {
            "cash": actual_cash,
            "positions": len(portfolio.get("positions", {})) if isinstance(portfolio.get("positions"), dict) else 0,
            "trade_events": len(clean_trades),
            "duplicate_events_removed": len(duplicates),
            "malformed_events": malformed,
        },
        "checkpoint": next_checkpoint,
        "ledger_baseline": previous_baseline,
    }
    if repair:
        _atomic_json_write(report_path, report, backup=False)
    return report


def trading_permission(base_dir=None):
    """Return a fail-closed decision and an operator-readable reason."""
    from trading_safety import operational_gate

    operational, reason = operational_gate(base_dir=base_dir)
    if not operational:
        return False, reason
    report = run_self_healing(base_dir=base_dir, repair=True)
    if not report["trading_allowed"]:
        return False, "unsafe trading state: " + "; ".join(report["critical"])
    return True, "trading gates passed"


def trading_is_allowed(base_dir=None):
    """Backward-compatible boolean gate; new callers should retain the reason."""
    return trading_permission(base_dir)[0]


def rebaseline(base_dir=None, reason="", operator=""):
    """Acknowledge current accounting state without inventing or changing it."""
    if not str(reason).strip():
        raise ValueError("--reason is required for rebaseline")
    base = Path(base_dir or Path(__file__).resolve().parent)
    report = run_self_healing(base, repair=False)
    non_drift = [item for item in report["critical"] if not item.startswith("historical ledger drift:")]
    if non_drift:
        raise RuntimeError("cannot rebaseline unsafe state: " + "; ".join(non_drift))

    checkpoint = report["checkpoint"]
    portfolio = json.loads((base / "paper_portfolio.json").read_text(encoding="utf-8"))
    trades = json.loads((base / "paper_trades.json").read_text(encoding="utf-8"))
    buy_value = sum(float(t.get("value", 0) or 0) for t in trades if str(t.get("side", "")).upper() == "BUY")
    sell_value = sum(float(t.get("value", 0) or 0) for t in trades if str(t.get("side", "")).upper() == "SELL")
    expected_cash = float(portfolio.get("initial_cash", INITIAL_CASH)) - buy_value + sell_value
    actual_cash = float(portfolio["cash"])
    baseline = {
        "cash_offset": expected_cash - actual_cash,
        "established_checkpoint": checkpoint,
        "established_at": _now(),
    }
    report["ledger_baseline"] = baseline
    report["critical"] = []
    report["trading_allowed"] = True
    report["status"] = "healthy"
    report["updated_at"] = _now()
    _atomic_json_write(base / REPORT_FILE, report, backup=False)

    audit_path = base / AUDIT_FILE
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(audit, list):
            raise ValueError("audit must be a list")
    except FileNotFoundError:
        audit = []
    event = {
        "action": "rebaseline",
        "at": _now(),
        "operator": str(operator or os.getenv("GITHUB_ACTOR") or os.getenv("USERNAME") or "unknown"),
        "reason": str(reason).strip(),
        "checkpoint": checkpoint,
        "cash_offset": baseline["cash_offset"],
    }
    audit.append(event)
    _atomic_json_write(audit_path, audit, backup=False)
    return event


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit or explicitly rebaseline trading state")
    parser.add_argument("--rebaseline", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--operator", default="")
    args = parser.parse_args()
    if args.rebaseline:
        result = rebaseline(reason=args.reason, operator=args.operator)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    result = run_self_healing()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["trading_allowed"] else 2)
