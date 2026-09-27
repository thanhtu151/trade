"""Deterministic self-healing and fail-safe checks for paper-trading state.

This module intentionally repairs only facts that can be derived without a
market opinion: JSON recovery, duplicate events, and calculated position
fields. Ambiguous accounting errors are reported and trading is blocked rather
than guessed at.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
import hashlib
import argparse
import copy
from datetime import datetime
from pathlib import Path


INITIAL_CASH = 100_000_000.0
REPORT_FILE = "self_healing_state.json"
AUDIT_FILE = "self_healing_audit.json"
MIGRATION_CONFIRMATION = "MIGRATE_LEDGER_EPOCH_2026_07_09"


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


def snapshot_state(base_dir=None, task="unknown"):
    """Create a bounded pre-task snapshot of the coupled accounting files."""
    base = Path(base_dir or Path(__file__).resolve().parent)
    from ledger_store import recover_pending_transaction

    recover_pending_transaction(base)
    manifest = {"task": str(task), "created_at": _now(), "files": {}}
    for name in ("paper_portfolio.json", "paper_trades.json"):
        source = base / name
        if not source.exists():
            continue
        target = base / (name + ".pre_task.bak")
        shutil.copy2(source, target)
        manifest["files"][name] = target.name
    _atomic_json_write(base / "pre_task_snapshot.json", manifest, backup=False)
    return manifest


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
                round(float(trade.get("qty", 0) or 0), 9),
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
        if qty is None or qty <= 0:
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
    from ledger_store import recover_pending_transaction

    try:
        recovered_transaction = recover_pending_transaction(base)
        if recovered_transaction:
            actions.append(f"recovered ledger transaction {recovered_transaction}")
    except Exception as exc:
        critical.append(f"ledger transaction recovery failed: {exc}")

    switch_reason = kill_switch_reason(base)
    if switch_reason:
        critical.append(f"trading kill switch: {switch_reason}")
    try:
        previous_report = json.loads(report_path.read_text(encoding="utf-8"))
        previous_checkpoint = previous_report.get("checkpoint") or {}
        previous_baseline = previous_report.get("ledger_baseline") or {}
    except Exception:
        previous_report = {}
        previous_checkpoint = {}
        previous_baseline = {}

    portfolio = _load_json_with_recovery(
        portfolio_path, dict, {"initial_cash": INITIAL_CASH, "cash": 0, "positions": {}}, actions, critical
    )
    trades = _load_json_with_recovery(trades_path, list, [], actions, critical)

    portfolio_changed = _repair_portfolio(portfolio, actions, warnings, critical)
    positions = portfolio.get("positions", {}) if isinstance(portfolio.get("positions"), dict) else {}
    for symbol, position in positions.items():
        if not isinstance(position, dict):
            continue
        unit_prices = []
        for field in ("avg_price", "entry_price", "current_price", "stop_loss", "initial_stop_loss", "target_price"):
            value = _finite_number(position.get(field))
            if value is not None and value > 0:
                unit_prices.append((field, value))
        plan = position.get("plan") if isinstance(position.get("plan"), dict) else {}
        for field in ("stop_loss", "initial_stop_loss", "target_price"):
            value = _finite_number(plan.get(field))
            if value is not None and value > 0:
                unit_prices.append((f"plan.{field}", value))
        bad = [(field, value) for field, value in unit_prices if value < 1000]
        if bad:
            critical.append(
                f"open position {symbol} has non-VND unit price(s): "
                + ", ".join(f"{field}={value:g}" for field, value in bad)
            )
    clean_trades, duplicates, malformed = _deduplicate_trades(trades)
    if malformed:
        critical.append(f"trade ledger contains {malformed} malformed event(s)")
    if duplicates:
        actions.append(f"removed {len(duplicates)} duplicate automated trade event(s)")

    # RESET starts a new accounting epoch. Older trades remain append-only history
    # but cannot influence current cash or position invariants.
    reset_indexes = []
    last_epoch = -1
    for index, event in enumerate(clean_trades):
        if not isinstance(event, dict) or str(event.get("type", "")).upper() != "RESET":
            continue
        cash_after = _finite_number(event.get("cash_after"))
        try:
            epoch_id = int(event.get("epoch_id"))
        except (TypeError, ValueError):
            epoch_id = -1
        if cash_after is None or cash_after < 0:
            critical.append(f"RESET event {index} has invalid cash_after")
        if event.get("positions_after") != {}:
            critical.append(f"RESET event {index} must have empty positions_after")
        if not str(event.get("reason", "")).strip() or not str(event.get("actor", "")).strip():
            critical.append(f"RESET event {index} is missing reason or actor")
        if epoch_id <= last_epoch:
            critical.append(f"RESET event {index} has non-increasing epoch_id")
        last_epoch = epoch_id
        reset_indexes.append(index)

    epoch_start = reset_indexes[-1] if reset_indexes else -1
    current_epoch = clean_trades[epoch_start:]
    low_price_events = []
    expected_cash = (
        float(clean_trades[epoch_start]["cash_after"])
        if epoch_start >= 0 and _finite_number(clean_trades[epoch_start].get("cash_after")) is not None
        else float(portfolio.get("initial_cash", INITIAL_CASH))
    )
    for event in clean_trades[epoch_start + 1:]:
        side = str(event.get("side", "")).upper()
        value = _finite_number(event.get("value"))
        qty = _finite_number(event.get("qty"))
        price = _finite_number(event.get("price"))
        if side not in {"BUY", "SELL"} or value is None or value < 0 or qty is None or qty <= 0 or price is None or price <= 0:
            critical.append(f"current epoch contains invalid trade event at index {clean_trades.index(event)}")
            continue
        try:
            event_epoch = int(event.get("epoch_id", -1))
        except (TypeError, ValueError):
            event_epoch = -1
        if reset_indexes and event_epoch != last_epoch:
            critical.append(f"current epoch trade has wrong epoch_id at index {clean_trades.index(event)}")
        if abs(value - qty * price) > max(1.0, abs(value) * 0.000001):
            critical.append(f"trade value mismatch at index {clean_trades.index(event)}")
        cost_basis = _finite_number(event.get("cost_basis"))
        if side == "SELL" and cost_basis is not None and (cost_basis <= 0 or value > cost_basis * 3):
            critical.append(
                f"SELL proceeds exceed 3x cost basis at index {clean_trades.index(event)}: "
                f"{value:,.0f} vs {cost_basis:,.0f}"
            )
        if price < 1000:
            low_price_events.append({"symbol": event.get("symbol"), "time": event.get("time"), "price": price})
        expected_cash += value if side == "SELL" else -value
    if low_price_events:
        warnings.append(f"current epoch contains {len(low_price_events)} legacy price(s) below 1,000 VND; history was not modified")
    actual_cash = _finite_number(portfolio.get("cash"))
    current_equity = float(actual_cash or 0)
    for position in positions.values():
        if isinstance(position, dict):
            market_value = _finite_number(position.get("market_value"))
            if market_value is None:
                market_value = float(position.get("qty", 0) or 0) * float(position.get("current_price") or position.get("avg_price") or 0)
            current_equity += market_value
    previous_equity = _finite_number(previous_checkpoint.get("equity"))
    if previous_equity and current_equity > previous_equity * 1.5:
        critical.append(
            f"equity increased more than 50% since checkpoint: {previous_equity:,.0f} -> {current_equity:,.0f}"
        )
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
        checkpoint_expected_cash = previous_cash
        for event in new_events:
            if str(event.get("type", "")).upper() == "RESET":
                checkpoint_expected_cash = float(event["cash_after"])
                continue
            side = str(event.get("side", "")).upper()
            value = float(event.get("value", 0) or 0)
            if side == "BUY":
                checkpoint_expected_cash -= value
            elif side == "SELL":
                checkpoint_expected_cash += value
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
        "equity": current_equity,
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
            "current_epoch": last_epoch if reset_indexes else 0,
            "current_epoch_events": len(current_epoch),
            "expected_cash": expected_cash,
            "cash_drift": expected_cash - actual_cash if actual_cash is not None else None,
            "low_price_events": len(low_price_events),
        },
        "checkpoint": next_checkpoint,
        "ledger_baseline": previous_baseline,
    }
    if repair:
        _atomic_json_write(report_path, report, backup=False)
        new_critical = sorted(set(critical) - set(previous_report.get("critical") or []))
        if new_critical:
            try:
                from notify import send_once
                digest = hashlib.sha256("\n".join(new_critical).encode("utf-8")).hexdigest()
                level = "warning" if all(item.startswith("trading kill switch:") for item in new_critical) else "critical"
                send_once(f"healing|{digest}", "Self-healing critical", "\n".join(new_critical), level, base_dir=base)
            except Exception as exc:
                logging.getLogger(__name__).warning("Self-healing notification failed safely: %s", type(exc).__name__)
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


def audit_exit_code(report):
    """Kill switch is an intentional block; other critical findings fail CI."""
    non_switch = [item for item in report.get("critical", []) if not str(item).startswith("trading kill switch:")]
    return 2 if non_switch else 0


def write_github_blocked_summary(report):
    if audit_exit_code(report) != 0:
        return
    switch_items = [item for item in report.get("critical", []) if str(item).startswith("trading kill switch:")]
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if switch_items and summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("### Trading blocked\n\n")
            handle.write("Kill switch is active; non-trading task state remains healthy.\n")


def trading_is_allowed(base_dir=None):
    """Backward-compatible boolean gate; new callers should retain the reason."""
    return trading_permission(base_dir)[0]


def rebaseline(base_dir=None, reason="", operator=""):
    """Acknowledge current accounting state without inventing or changing it."""
    if not str(reason).strip():
        raise ValueError("--reason is required for rebaseline")
    base = Path(base_dir or Path(__file__).resolve().parent)
    report = run_self_healing(base, repair=False)
    non_drift = [
        item for item in report["critical"]
        if not item.startswith("historical ledger drift:") and not item.startswith("trading kill switch:")
    ]
    if non_drift:
        raise RuntimeError("cannot rebaseline unsafe state: " + "; ".join(non_drift))

    checkpoint = report["checkpoint"]
    portfolio = json.loads((base / "paper_portfolio.json").read_text(encoding="utf-8"))
    trades = json.loads((base / "paper_trades.json").read_text(encoding="utf-8"))
    resets = [i for i, event in enumerate(trades) if str(event.get("type", "")).upper() == "RESET"]
    start = resets[-1] if resets else -1
    expected_cash = float(trades[start]["cash_after"]) if start >= 0 else float(portfolio.get("initial_cash", INITIAL_CASH))
    for event in trades[start + 1:]:
        value = float(event.get("value", 0) or 0)
        expected_cash += value if str(event.get("side", "")).upper() == "SELL" else -value
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


def _normalize_open_positions(portfolio):
    changes = []
    for symbol, position in (portfolio.get("positions") or {}).items():
        avg_price = _finite_number(position.get("avg_price")) if isinstance(position, dict) else None
        if avg_price is None or avg_price >= 1000:
            continue
        before = copy.deepcopy(position)
        for field in ("avg_price", "entry_price", "current_price", "target_price", "stop_loss", "atr"):
            value = _finite_number(position.get(field))
            if value is not None:
                position[field] = value * 1000.0
        plan = position.get("plan") if isinstance(position.get("plan"), dict) else {}
        for field in ("stop_loss", "initial_stop_loss", "target_price", "atr"):
            value = _finite_number(plan.get(field))
            if value is not None:
                plan[field] = value * 1000.0
        position["qty"] = float(position.get("qty", 0)) / 1000.0
        changes.append({"symbol": symbol, "before": before, "after": copy.deepcopy(position)})
    return changes


def _state_price_file_report(base):
    return [
        {"file": "paper_portfolio.json", "handling": "normalize open-position unit prices and quantity atomically"},
        {"file": "paper_trades.json", "handling": "append-only evidence; label epochs but never rewrite historical prices"},
        {"file": "tracked_positions.json", "handling": "independent manual tracker; report only, no automatic mutation"},
        {"file": "intraday_alerts.json", "handling": "immutable display messages; expire/prune normally, no parsing or mutation"},
        {"file": "portfolio_snapshots.json", "handling": "historical aggregate values, no per-position unit price; preserve"},
        {"file": "analysis_results.json", "handling": "ephemeral analysis cache; provider boundary normalizes future values; preserve history"},
        {"file": "debate_log.json", "handling": "historical model evidence; preserve, with future inputs normalized at provider boundary"},
        {"file": "prediction_history.json", "handling": "historical outcomes; preserve and default learning/reflection to current epoch"},
        {"file": "prediction_log.json", "handling": "historical predictions; preserve and default learning statistics to current epoch"},
    ]


def migrate_reconstructed_reset(base_dir=None, confirmation="", operator="", dry_run=True):
    """Insert the single audited RESET reconstructed during the PR2 investigation."""
    if confirmation != MIGRATION_CONFIRMATION:
        raise ValueError(f"confirmation must equal {MIGRATION_CONFIRMATION}")
    base = Path(base_dir or Path(__file__).resolve().parent)
    from ledger_store import commit_portfolio_and_ledger, label_epochs

    portfolio = json.loads((base / "paper_portfolio.json").read_text(encoding="utf-8"))
    trades = json.loads((base / "paper_trades.json").read_text(encoding="utf-8"))
    if any(str(event.get("reason", "")) == "reconstructed: untracked reset found in PR2 investigation" for event in trades):
        raise RuntimeError("reconstructed RESET migration has already been applied")
    boundary = next(
        (index for index, event in enumerate(trades)
         if event.get("time") == "2026-07-09 10:50:55" and event.get("symbol") == "PVD" and event.get("side") == "SELL"),
        None,
    )
    if boundary is None or boundary + 1 >= len(trades):
        raise RuntimeError("verified PVD reset boundary was not found")
    following = trades[boundary + 1]
    if not (following.get("time") == "2026-07-10 10:44:22" and following.get("symbol") == "PVD"):
        raise RuntimeError("ledger does not match the investigated reset boundary")
    actor = str(operator or os.getenv("GITHUB_ACTOR") or "unknown")
    reset = {
        "type": "RESET",
        "time": "2026-07-09 10:50:56",
        "cash_after": 100_000_000.0,
        "positions_after": {},
        "reason": "reconstructed: untracked reset found in PR2 investigation",
        "actor": actor,
        "epoch_id": 1,
    }
    migrated = label_epochs(trades[:boundary + 1] + [reset] + trades[boundary + 1:])
    portfolio["ledger_epoch"] = 1
    position_changes = _normalize_open_positions(portfolio)
    expected_cash = reset["cash_after"]
    for event in migrated[boundary + 2:]:
        value = float(event.get("value", 0) or 0)
        expected_cash += value if str(event.get("side", "")).upper() == "SELL" else -value
    preview = {
        "mode": "dry-run" if dry_run else "applied",
        "reset_insertion": {"after_index": boundary, "after": trades[boundary], "before": following, "reset": reset},
        "position_changes": position_changes,
        "state_price_files": _state_price_file_report(base),
        "post_migration_drift": expected_cash - float(portfolio.get("cash", 0)),
    }
    if dry_run:
        return preview
    commit_portfolio_and_ledger(base, portfolio, migrated, operation="ledger_epoch_migration")
    audit_path = base / AUDIT_FILE
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        audit = []
    audit.append({
        "action": "migrate_ledger_epoch",
        "at": _now(),
        "operator": actor,
        "confirmation": confirmation,
        "reset": reset,
        "position_changes": position_changes,
        "state_price_files": preview["state_price_files"],
    })
    _atomic_json_write(audit_path, audit, backup=False)
    report_path = base / REPORT_FILE
    report_path.unlink(missing_ok=True)
    report = run_self_healing(base, repair=True)
    migration_critical = [item for item in report["critical"] if not item.startswith("trading kill switch:")]
    if migration_critical or abs(float(report["metrics"]["cash_drift"])) > 1.0:
        raise RuntimeError("migration verification failed: " + "; ".join(migration_critical))
    return {**preview, "report": report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit or explicitly rebaseline trading state")
    parser.add_argument("--rebaseline", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--operator", default="")
    parser.add_argument("--migrate-ledger-epoch", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--maintenance-audit", action="store_true")
    parser.add_argument("--apply", action="store_true", help="apply migration; default is read-only dry-run")
    args = parser.parse_args()
    if args.migrate_ledger_epoch:
        result = migrate_reconstructed_reset(confirmation=args.confirm, operator=args.operator, dry_run=not args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if args.rebaseline:
        result = rebaseline(reason=args.reason, operator=args.operator)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    result = run_self_healing()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    write_github_blocked_summary(result)
    if args.maintenance_audit:
        raise SystemExit(audit_exit_code(result))
    raise SystemExit(audit_exit_code(result))
