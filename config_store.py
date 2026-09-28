"""Single ownership boundary for active and candidate backtest configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ACTIVE_NAME = "backtest_config.json"
CANDIDATE_NAME = "backtest_config.candidate.json"
BACKUP_NAME = "backtest_config.backup.json"
AUDIT_NAME = "backtest_config_audit.json"
PROMOTE_CONFIRMATION = "PROMOTE_BACKTEST_CONFIG"
ROLLBACK_CONFIRMATION = "ROLLBACK_BACKTEST_CONFIG"
ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def _base(base_dir=None):
    return Path(base_dir or Path(__file__).resolve().parent)


def _atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _atomic_json(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    _atomic_bytes(path, content)


def _load(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return default


def load_active(base_dir=None):
    value = _load(_base(base_dir) / ACTIVE_NAME, {})
    return value if isinstance(value, dict) else {}


def load_candidate(base_dir=None):
    value = _load(_base(base_dir) / CANDIDATE_NAME, {})
    return value if isinstance(value, dict) else {}


def _finite(value):
    return not isinstance(value, float) or math.isfinite(value)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def validate(config):
    errors = []
    if not isinstance(config, dict):
        return ["config root must be an object"]
    universe = config.get("backtest_universe")
    ev_data = config.get("ev_data")
    positive = config.get("positive_ev_tickers")
    if not isinstance(universe, list) or not universe or any(not isinstance(item, str) or not item for item in universe):
        errors.append("backtest_universe must be a non-empty ticker list")
        universe = []
    if len(set(universe)) != len(universe):
        errors.append("backtest_universe contains duplicate tickers")
    if not isinstance(ev_data, dict):
        errors.append("ev_data must be an object")
        ev_data = {}
    missing = sorted(set(universe) - set(ev_data))
    if missing:
        errors.append("ev_data missing ticker(s): " + ", ".join(missing))
    if not isinstance(positive, list) or any(item not in universe for item in positive):
        errors.append("positive_ev_tickers must be a subset of backtest_universe")
        positive = []
    for ticker, row in ev_data.items():
        if not isinstance(row, dict):
            errors.append(f"ev_data.{ticker} must be an object")
            continue
        for field in ("ev", "win_rate", "trades", "sharpe", "profit_factor"):
            value = row.get(field, 0)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not _finite(value):
                errors.append(f"ev_data.{ticker}.{field} must be finite")
        status = row.get("status")
        if status not in {"ok", "insufficient_trades"}:
            errors.append(f"ev_data.{ticker}.status is invalid")
        trades = row.get("trades", 0)
        if isinstance(trades, (int, float)) and trades < 0:
            errors.append(f"ev_data.{ticker}.trades must be non-negative")
    params = config.get("optimal_params_per_ticker", {})
    if not isinstance(params, dict):
        errors.append("optimal_params_per_ticker must be an object")
        params = {}
    for ticker, row in params.items():
        if not isinstance(row, dict):
            errors.append(f"optimal_params_per_ticker.{ticker} must be an object")
            continue
        ranges = {"atr_stop": (0.5, 3.0), "atr_target": (1.0, 5.0), "confluence_min": (1, 10)}
        for field, (low, high) in ranges.items():
            value = row.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not _finite(value) or not low <= value <= high:
                errors.append(f"optimal_params_per_ticker.{ticker}.{field} outside [{low}, {high}]")
    return sorted(set(errors))


def compare_configs(active, candidate):
    active_positive = set(active.get("positive_ev_tickers") or [])
    candidate_positive = set(candidate.get("positive_ev_tickers") or [])
    active_ev = active.get("ev_data") or {}
    candidate_ev = candidate.get("ev_data") or {}
    active_params = active.get("optimal_params_per_ticker") or {}
    candidate_params = candidate.get("optimal_params_per_ticker") or {}
    active_global = active.get("optimal_config") or {}
    tickers = sorted(set(active_ev) | set(candidate_ev))
    details, parameter_changes, sign_changes = {}, {}, 0
    for ticker in tickers:
        old, new = active_ev.get(ticker, {}), candidate_ev.get(ticker, {})
        old_ev, new_ev = old.get("ev"), new.get("ev")
        if isinstance(old_ev, (int, float)) and isinstance(new_ev, (int, float)) and (old_ev > 0) != (new_ev > 0):
            sign_changes += 1
        details[ticker] = {
            "active_ev": old_ev, "candidate_ev": new_ev,
            "active_trades": old.get("trades"), "candidate_trades": new.get("trades"),
            "candidate_status": new.get("status"),
        }
        changes = {}
        for field in ("atr_stop", "atr_target", "confluence_min"):
            global_field = {"atr_stop": "atr_stop_mult", "atr_target": "atr_target_mult",
                            "confluence_min": "min_confluence"}[field]
            before = active_params.get(ticker, {}).get(field, active_global.get(global_field))
            after = candidate_params.get(ticker, {}).get(field)
            if before != after:
                changes[field] = {"active": before, "candidate": after}
        if changes:
            parameter_changes[ticker] = changes
    return {
        "universe_added": sorted(candidate_positive - active_positive),
        "universe_removed": sorted(active_positive - candidate_positive),
        "ev_sign_changes": sign_changes,
        "parameter_changes": parameter_changes,
        "tickers": details,
        "overfit_note": "Optimized on the same historical sample; promotion requires human out-of-sample review.",
    }


def write_candidate(source, payload, base_dir=None):
    base = _base(base_dir)
    errors = validate(payload)
    safe_payload = _json_safe(payload)
    envelope = {
        "schema_version": 1,
        "source": str(source),
        "created_at": datetime.now(ICT).isoformat(),
        "status": "invalid" if errors else "valid",
        "validation_errors": errors,
        "config": safe_payload,
        "comparison": compare_configs(load_active(base), safe_payload),
    }
    _atomic_json(base / CANDIDATE_NAME, envelope)
    try:
        from notify import notify_backtest_candidate
        notify_backtest_candidate(envelope)
    except Exception:
        pass
    return envelope


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _append_audit(base, event):
    rows = _load(base / AUDIT_NAME, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(event)
    _atomic_json(base / AUDIT_NAME, rows)


def promote(confirmation, actor="", run_id="", base_dir=None):
    if confirmation != PROMOTE_CONFIRMATION:
        raise ValueError(f"confirmation must equal {PROMOTE_CONFIRMATION}")
    base = _base(base_dir)
    candidate = load_candidate(base)
    if candidate.get("status") != "valid" or candidate.get("validation_errors"):
        raise ValueError("candidate is invalid and cannot be promoted")
    payload = candidate.get("config")
    errors = validate(payload)
    if errors:
        raise ValueError("candidate is invalid: " + "; ".join(errors))
    active_path = base / ACTIVE_NAME
    old_content = active_path.read_bytes() if active_path.exists() else b"{}"
    new_content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    _atomic_bytes(base / BACKUP_NAME, old_content)
    _atomic_bytes(active_path, new_content)
    event = {"action": "promote", "at": datetime.now(ICT).isoformat(), "actor": str(actor or "unknown"),
             "run_id": str(run_id or "unknown"), "source": candidate.get("source"),
             "old_hash": _digest(old_content), "new_hash": _digest(new_content)}
    _append_audit(base, event)
    return event


def rollback(confirmation, actor="", run_id="", base_dir=None):
    if confirmation != ROLLBACK_CONFIRMATION:
        raise ValueError(f"confirmation must equal {ROLLBACK_CONFIRMATION}")
    base = _base(base_dir)
    backup_path, active_path = base / BACKUP_NAME, base / ACTIVE_NAME
    if not backup_path.exists():
        raise FileNotFoundError("no active config backup is available")
    old_content = active_path.read_bytes() if active_path.exists() else b"{}"
    new_content = backup_path.read_bytes()
    _atomic_bytes(active_path, new_content)
    event = {"action": "rollback", "at": datetime.now(ICT).isoformat(), "actor": str(actor or "unknown"),
             "run_id": str(run_id or "unknown"), "old_hash": _digest(old_content), "new_hash": _digest(new_content)}
    _append_audit(base, event)
    return event


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--promote", action="store_true")
    action.add_argument("--rollback", action="store_true")
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--actor", default=os.getenv("GITHUB_ACTOR", "unknown"))
    parser.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", "unknown"))
    args = parser.parse_args()
    result = promote(args.confirmation, args.actor, args.run_id) if args.promote else rollback(args.confirmation, args.actor, args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
