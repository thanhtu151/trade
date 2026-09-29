"""Automatic review and promotion of backtest config candidates.

A rebacktest candidate is optimised and evaluated on the same two years of
data (see config_store.compare_configs' overfit note). Promoting it as is
would automate overfitting, so the reviewer only lets a ticker trade when its
backtest shows evidence strong enough to survive that search. Criteria are
fixed here, before any candidate was reviewed:

  C1 valid      candidate passed config_store.validate (no ticker with a data error)
  C2 fresh      candidate created within MAX_AGE_DAYS
  C3 trades     at least MIN_TRADES trades for the ticker
  C4 edge       EV > 0 and profit factor >= MIN_PROFIT_FACTOR
  C5 evidence   win rate beats the stop/target break-even (+ cost margin) with
                z >= MIN_Z. Exits are mostly at the ATR stop or target, so a
                trade is close to a Bernoulli outcome with break-even
                p0 = stop / (stop + target). Each ticker was picked from ~15
                parameter sets across ~50 tickers, hence z >= 3 instead of 2
                (multiple-testing haircut, Harvey & Liu).
  C6 breadth    at least MIN_QUALIFIED tickers pass C3-C5

Decision: tickers failing C3-C5 are marked "not_significant" and removed from
positive_ev_tickers. If C1, C2 and C6 pass and the reviewed config differs
from the active one, it is written as a new candidate and promoted (unless
CONFIG_AUTO_PROMOTE=false, which makes the review advisory). Every review is
appended to config_review_log.json and sent to Discord.

    python config_review.py            # review the pending candidate now
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config_store

ICT = timezone(timedelta(hours=7))
LOG_NAME = "config_review_log.json"
MAX_AGE_DAYS = 8
MIN_TRADES = 30
MIN_PROFIT_FACTOR = 1.2
MIN_Z = 3.0
COST_MARGIN = 0.02
MIN_QUALIFIED = 5
DEFAULT_STOP, DEFAULT_TARGET = 1.0, 2.0
AUTO_SOURCE_PREFIX = "auto-review"


def _base(base_dir=None):
    return Path(base_dir or Path(__file__).resolve().parent)


def auto_promote_enabled():
    return os.getenv("CONFIG_AUTO_PROMOTE", "true").strip().lower() not in ("0", "false", "no", "off")


def _params(config, active, ticker):
    row = (config.get("optimal_params_per_ticker") or {}).get(ticker) or {}
    fallback = active.get("optimal_config") or {}
    stop = row.get("atr_stop", fallback.get("atr_stop_mult", DEFAULT_STOP))
    target = row.get("atr_target", fallback.get("atr_target_mult", DEFAULT_TARGET))
    return float(stop), float(target)


def ticker_evidence(row, stop, target):
    """C3-C5 for one ticker. Returns (qualified, detail)."""
    trades = int(row.get("trades") or 0)
    ev = float(row.get("ev") or 0.0)
    profit_factor = float(row.get("profit_factor") or 0.0)
    win_rate = float(row.get("win_rate") or 0.0)
    if win_rate > 1:  # some backtesters report percent
        win_rate /= 100
    breakeven = min(0.95, stop / (stop + target) + COST_MARGIN) if stop > 0 and target > 0 else 0.5
    z = ((win_rate - breakeven) / math.sqrt(breakeven * (1 - breakeven) / trades)) if trades > 0 else float("-inf")
    checks = {
        "C3_trades": trades >= MIN_TRADES,
        "C4_edge": row.get("status") == "ok" and ev > 0 and profit_factor >= MIN_PROFIT_FACTOR,
        "C5_evidence": z >= MIN_Z,
    }
    detail = {"trades": trades, "ev": ev, "profit_factor": profit_factor, "win_rate": round(win_rate, 4),
              "breakeven": round(breakeven, 4), "z": round(z, 2) if math.isfinite(z) else None, **checks}
    return all(checks.values()), detail


def review(envelope, active, now=None):
    """Pure decision for one candidate envelope. Does not write anything."""
    now = now or datetime.now(ICT)
    config = envelope.get("config") or {}
    created = envelope.get("created_at")
    try:
        age = now - datetime.fromisoformat(created)
    except (TypeError, ValueError):
        age = timedelta(days=10**4)
    universe = list(config.get("backtest_universe") or [])
    ev_data = config.get("ev_data") or {}
    tickers, qualified = {}, []
    for ticker in universe:
        stop, target = _params(config, active, ticker)
        ok, detail = ticker_evidence(ev_data.get(ticker) or {}, stop, target)
        tickers[ticker] = detail
        if ok:
            qualified.append(ticker)
    checks = {
        "C1_valid": envelope.get("status") == "valid" and not envelope.get("validation_errors"),
        "C2_fresh": age <= timedelta(days=MAX_AGE_DAYS),
        "C6_breadth": len(qualified) >= MIN_QUALIFIED,
    }
    reviewed = json.loads(json.dumps(config))
    for ticker in universe:
        row = reviewed.setdefault("ev_data", {}).setdefault(ticker, {})
        if ticker not in qualified and row.get("status") == "ok":
            row["status"] = "not_significant"
    reviewed["positive_ev_tickers"] = [t for t in universe if t in qualified]
    reviewed["negative_ev_tickers"] = sorted(set(universe) - set(qualified))
    reviewed["review"] = {"at": now.isoformat(timespec="seconds"), "source_created_at": created,
                          "criteria": criteria()}
    changes = sorted(set(active.get("positive_ev_tickers") or []) ^ set(qualified))
    passed = all(checks.values())
    decision = "promote" if passed and changes else "no_change" if passed else "reject"
    return {"decision": decision, "checks": checks, "qualified": qualified, "universe_size": len(universe),
            "positive_before_review": list(config.get("positive_ev_tickers") or []),
            "changes_vs_active": changes, "tickers": tickers, "reviewed_config": reviewed,
            "candidate_created_at": created, "candidate_source": envelope.get("source")}


def criteria():
    return {"max_age_days": MAX_AGE_DAYS, "min_trades": MIN_TRADES, "min_profit_factor": MIN_PROFIT_FACTOR,
            "min_z": MIN_Z, "cost_margin": COST_MARGIN, "min_qualified": MIN_QUALIFIED}


def _load_log(base):
    try:
        rows = json.loads((base / LOG_NAME).read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (FileNotFoundError, OSError, ValueError):
        return []


def review_pending(base_dir=None, now=None, notify=True):
    """Review the current candidate once; promote when it passes. Returns the log row or None."""
    base = _base(base_dir)
    envelope = config_store.load_candidate(base)
    if not envelope or str(envelope.get("source", "")).startswith(AUTO_SOURCE_PREFIX):
        return None  # nothing pending, or our own reviewed candidate
    log_rows = _load_log(base)
    if any(row.get("candidate_created_at") == envelope.get("created_at") for row in log_rows):
        return None  # already reviewed
    result = review(envelope, config_store.load_active(base), now)
    row = {k: v for k, v in result.items() if k not in ("reviewed_config", "tickers")}
    row["at"] = (now or datetime.now(ICT)).isoformat(timespec="seconds")
    row["auto_promote"] = auto_promote_enabled()
    row["ticker_detail"] = {t: d for t, d in result["tickers"].items()
                            if t in result["qualified"] or t in result["positive_before_review"]}
    if result["decision"] == "promote" and row["auto_promote"]:
        written = config_store.write_candidate(f"{AUTO_SOURCE_PREFIX}:{envelope.get('created_at')}",
                                               result["reviewed_config"], base_dir=base, notify=False)
        if written["status"] != "valid":
            row["decision"] = "reject"
            row["error"] = "reviewed config invalid: " + "; ".join(written["validation_errors"])
        else:
            row["promotion"] = config_store.promote(config_store.PROMOTE_CONFIRMATION, actor="config_review",
                                                    run_id=os.getenv("GITHUB_RUN_ID", "local"), base_dir=base)
    elif result["decision"] == "promote":
        row["decision"] = "approved_not_promoted"
    log_rows.append(row)
    config_store._atomic_json(base / LOG_NAME, log_rows[-100:])
    if notify:
        _notify(row)
    return row


def _notify(row):
    try:
        from notify import send_once
    except Exception:
        return
    label = {"promote": "ĐÃ DUYỆT và áp dụng", "approved_not_promoted": "ĐẠT (chưa áp dụng: CONFIG_AUTO_PROMOTE=false)",
             "no_change": "ĐẠT nhưng không khác cấu hình hiện tại", "reject": "TỪ CHỐI"}[row["decision"]]
    checks = ", ".join(f"{k}={'✅' if v else '❌'}" for k, v in row["checks"].items())
    lines = [f"Candidate {row['candidate_created_at']} → **{label}**", checks,
             f"Mã đạt bằng chứng (C3–C5): {len(row['qualified'])}/{row['universe_size']} "
             f"(trước review: {len(row['positive_before_review'])} mã EV dương)"]
    if row["qualified"]:
        lines.append("Mã đạt: " + ", ".join(row["qualified"][:20]))
    if row.get("error"):
        lines.append(row["error"])
    send_once(f"config_review:{row['candidate_created_at']}", "Duyệt cấu hình backtest", "\n".join(lines),
              "info" if row["decision"] in ("promote", "no_change") else "warning")


if __name__ == "__main__":
    print(json.dumps(review_pending(notify=False), ensure_ascii=False, indent=1, default=str))
