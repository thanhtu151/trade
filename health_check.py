"""Non-interactive health report; missing optional state degrades instead of crashing."""

import json
from datetime import date
from pathlib import Path

from market_data_adapter import provider_availability
from self_healing import run_self_healing


BASE = Path(__file__).resolve().parent


def _read(name, expected_type):
    path = BASE / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, expected_type):
            raise ValueError(f"expected {expected_type.__name__}")
        return value, None
    except Exception as exc:
        return expected_type(), str(exc)


def collect_health():
    checks = []
    healing = run_self_healing(BASE, repair=True)
    checks.append({
        "name": "self_healing",
        "ok": bool(healing.get("trading_allowed")),
        "detail": healing.get("status", "unknown") + "; " + "; ".join(healing.get("critical", [])),
    })
    portfolio, error = _read("paper_portfolio.json", dict)
    checks.append({
        "name": "portfolio",
        "ok": error is None and isinstance(portfolio.get("positions"), dict),
        "detail": error or f"{len(portfolio.get('positions', {}))} positions",
    })
    analysis, error = _read("analysis_results.json", dict)
    checks.append({
        "name": "analysis_today",
        "ok": error is None and analysis.get("date") == date.today().isoformat(),
        "detail": error or str(analysis.get("method", "unknown")),
    })
    state, error = _read("scheduler_state.json", dict)
    today = date.today().isoformat()
    for task in ("morning_prep", "market_analysis", "auto_trade"):
        checks.append({
            "name": f"scheduler_{task}",
            "ok": error is None and state.get(task) == today,
            "detail": error or str(state.get(task, "not run")),
        })
    provider = provider_availability()
    checks.append({
        "name": "market_data_provider",
        "ok": provider["available"],
        "detail": provider["detail"],
        "degraded": not provider["available"],
    })
    healthy = all(item["ok"] for item in checks if not item.get("degraded"))
    return {"healthy": healthy, "checks": checks}


if __name__ == "__main__":
    report = collect_health()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["healthy"] else 1)
