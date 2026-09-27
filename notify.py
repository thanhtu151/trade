"""Best-effort Discord notifications. This module is stdlib-only and never raises."""

import argparse
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


log = logging.getLogger(__name__)
COLORS = {"critical": 0xE74C3C, "warning": 0xF1C40F, "info": 0x2ECC71, "success": 0x2ECC71}
STATE_FILE = "discord_notification_state.json"


def _safe_warning(message):
    log.warning("Discord notification failed: %s", str(message).replace(os.getenv("DISCORD_WEBHOOK_URL", "__unset__"), "[redacted]"))


def _retry_delay(exc):
    if getattr(exc, "code", None) != 429:
        return 0.5
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return min(max(float(payload.get("retry_after", 1)), 0), 30)
    except Exception:
        return 1.0


def send_embed(title, description, level="info", fields=None, webhook_url=None, opener=None, sleep=time.sleep):
    webhook = webhook_url if webhook_url is not None else os.getenv("DISCORD_WEBHOOK_URL", "")
    if not str(webhook).strip():
        return False
    payload = {"embeds": [{
        "title": str(title)[:256],
        "description": str(description)[:4096],
        "color": COLORS.get(level, COLORS["info"]),
        "timestamp": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
        "fields": [{"name": str(k)[:256], "value": str(v)[:1024], "inline": True} for k, v in (fields or [])],
    }]}
    request = urllib.request.Request(
        str(webhook), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "trade-notifier/1"}, method="POST",
    )
    open_url = opener or urllib.request.urlopen
    for attempt in range(2):
        try:
            response = open_url(request, timeout=10)
            status = int(getattr(response, "status", 204))
            if 200 <= status < 300:
                return True
            raise urllib.error.HTTPError(str(webhook), status, "Discord HTTP error", {}, None)
        except Exception as exc:
            if attempt == 0:
                sleep(_retry_delay(exc))
                continue
            _safe_warning(f"{type(exc).__name__} (after retry)")
    return False


def send_once(fingerprint, title, description, level="info", fields=None, base_dir=None, **kwargs):
    base = Path(base_dir or Path(__file__).resolve().parent)
    path = base / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        sent = set(state.get("sent", [])) if isinstance(state, dict) else set()
    except Exception:
        sent = set()
    if fingerprint and fingerprint in sent:
        return False
    delivered = send_embed(title, description, level=level, fields=fields, **kwargs)
    if delivered and fingerprint:
        try:
            sent.add(fingerprint)
            recent = sorted(sent)[-500:]
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps({"sent": recent}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except Exception as exc:
            _safe_warning(f"could not persist notification fingerprint: {type(exc).__name__}")
    return delivered


def run_url():
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "local run"


def notify_task_failed(task, error, fingerprint):
    return send_once(fingerprint, "Task FAILED", f"**{task}** failed\n{run_url()}\n`{str(error)[:500]}`", "critical")


def notify_task_blocked(task, reason):
    fp = hashlib.sha256(f"blocked|{task}|{reason}|{os.getenv('GITHUB_RUN_ID','local')}".encode()).hexdigest()
    return send_once(fp, "Task blocked", f"**{task}** blocked: {reason}\n{run_url()}", "warning")


def notify_watchdog(decision, fingerprint):
    return send_once(
        fingerprint, "Watchdog warning",
        f"missed={decision.get('missed', [])}\nhung={decision.get('hung', [])}\nfailing={decision.get('failing', [])}\n{run_url()}",
        "critical" if decision.get("disable_trade") else "warning",
    )


def notify_kill_switch(action, reason, actor="unknown", run_id="unknown"):
    level = "critical" if action == "disable-trading" else "info"
    return send_once(f"switch|{action}|{run_id}", "Trading kill switch changed", f"**{action}**: {reason}", level, [("Actor", actor), ("Run", run_id)])


def notify_trade(symbol, side, qty, price, pnl=None):
    value = float(qty) * float(price)
    fields = [("Side", side), ("Quantity", f"{float(qty):,.3f}".rstrip("0").rstrip(".")),
              ("Price", format_vnd(price)), ("Value", format_vnd(value))]
    if str(side).upper() == "SELL" and pnl is not None:
        fields.append(("PnL", format_vnd(pnl)))
    return send_embed(f"Paper trade executed: {symbol}", "Paper trading order persisted successfully.", "info", fields)


def format_vnd(value):
    return f"{float(value):,.0f} VND"


def build_eod_summary(base_dir):
    base = Path(base_dir)
    def load(name, default):
        try:
            return json.loads((base / name).read_text(encoding="utf-8"))
        except Exception:
            return default
    portfolio = load("paper_portfolio.json", {})
    trades = load("paper_trades.json", [])
    status = load("system_status.json", {}).get("tasks", {})
    healing = load("self_healing_state.json", {})
    positions = portfolio.get("positions", {}) or {}
    cash = float(portfolio.get("cash", 0) or 0)
    market = sum(float(p.get("market_value", float(p.get("qty", 0)) * float(p.get("current_price", p.get("avg_price", 0))))) for p in positions.values())
    equity = cash + market
    snapshots = load("portfolio_snapshots.json", [])
    prior = float(snapshots[-1].get("equity", equity)) if snapshots else equity
    change = ((equity / prior) - 1) * 100 if prior else 0.0
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    today_trades = sum(1 for row in trades if str(row.get("time", ""))[:10] == today and str(row.get("type", "TRADE")).upper() != "RESET")
    task_text = ", ".join(f"{name}:{row.get('state','?')}" for name, row in sorted(status.items())) or "none"
    critical = healing.get("critical") or []
    description = (
        f"Cash: {format_vnd(cash)}\nTotal assets: {format_vnd(equity)}\nDaily change: {change:+.2f}%\n"
        f"Positions: {len(positions)}\nToday's trades: {today_trades}\nTasks: {task_text}\n"
        f"Trading allowed: {bool(healing.get('trading_allowed', not critical))}\nCritical: {critical or 'none'}"
    )
    return description


def notify_eod(base_dir):
    try:
        return send_embed("End-of-day summary", build_eod_summary(base_dir), "info")
    except Exception as exc:
        _safe_warning(f"could not build EOD summary: {type(exc).__name__}")
        return False


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("event", choices=("failed", "watchdog", "blocked"))
        parser.add_argument("--task", default="")
        parser.add_argument("--error", default="")
        parser.add_argument("--fingerprint", default="")
        parser.add_argument("--decision", default="{}")
        args = parser.parse_args()
        if args.event == "failed":
            notify_task_failed(args.task, args.error, args.fingerprint)
        elif args.event == "blocked":
            notify_task_blocked(args.task, args.error)
        else:
            notify_watchdog(json.loads(args.decision), args.fingerprint)
    except Exception as exc:
        _safe_warning(type(exc).__name__)
