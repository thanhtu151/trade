import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_system_status_tracks_success_and_consecutive_failures(tmp_path):
    from system_status import load_status, update_task

    update_task(tmp_path, "analysis", "running", run_id="1", now="2026-09-28T01:30:00+00:00")
    update_task(tmp_path, "analysis", "failed", error="timeout", run_id="1", now="2026-09-28T01:31:00+00:00")
    update_task(tmp_path, "analysis", "failed", error="timeout again", run_id="2", now="2026-09-28T01:35:00+00:00")
    assert load_status(tmp_path)["tasks"]["analysis"]["consecutive_failures"] == 2
    update_task(tmp_path, "analysis", "success", run_id="3", now="2026-09-28T01:40:00+00:00")
    row = load_status(tmp_path)["tasks"]["analysis"]
    assert row["consecutive_failures"] == 0
    assert row["last_success"] == "2026-09-28T01:40:00+00:00"
    update_task(tmp_path, "trade", "blocked", error="kill switch enabled", now="2026-09-28T02:20:00+00:00")
    blocked = load_status(tmp_path)["tasks"]["trade"]
    assert blocked["state"] == "blocked"
    assert blocked["consecutive_failures"] == 0


def test_watchdog_applies_sla_and_trade_sessions():
    from watchdog import evaluate

    ict = ZoneInfo("Asia/Ho_Chi_Minh")
    status = {"tasks": {"sentinel": {"state": "success"}}}
    before_sla = evaluate(status, datetime(2026, 9, 28, 9, 39, tzinfo=ict), trading_day=True)
    assert "trade" not in before_sla["missed"]
    in_session = evaluate(status, datetime(2026, 9, 28, 9, 40, tzinfo=ict), trading_day=True)
    assert "trade" in in_session["missed"]
    lunch = evaluate(status, datetime(2026, 9, 28, 12, 0, tzinfo=ict), trading_day=True)
    assert "trade" not in lunch["missed"]


def test_watchdog_escalates_repeated_failure_and_hung_task():
    from watchdog import evaluate

    ict = ZoneInfo("Asia/Ho_Chi_Minh")
    status = {"tasks": {
        "trade": {"state": "failed", "consecutive_failures": 3},
        "analysis": {"state": "running", "started_at": "2026-09-28T06:00:00+07:00", "consecutive_failures": 0},
    }}
    decision = evaluate(status, datetime(2026, 9, 28, 8, 0, tzinfo=ict), trading_day=True)
    assert decision["failing"] == ["trade"]
    assert decision["hung"] == ["analysis"]
    assert decision["disable_trade"] is True


def test_watchdog_empty_status_fails_closed_without_dispatch():
    from watchdog import evaluate

    now = datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    decision = evaluate({}, now, True, status_kind="legacy", deployed_at="2026-09-28T09:00:00+07:00")
    assert decision["status_readable"] is False
    assert decision["missed"] == []
    assert decision["disable_trade"] is False
    assert decision["legacy_grace"] is True
    assert decision["issue_required"] is False


def test_corrupt_status_still_requires_issue_without_grace():
    from watchdog import evaluate

    now = datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    decision = evaluate({}, now, True, status_kind="corrupt", deployed_at="2026-09-28T09:59:00+07:00")
    assert decision["legacy_grace"] is False
    assert decision["issue_required"] is True


def test_blocked_task_is_not_caught_up_and_noncritical_failure_does_not_disable():
    from watchdog import evaluate

    status = {"tasks": {
        "trade": {"state": "blocked", "last_update": "2026-09-28T09:45:00+07:00", "consecutive_failures": 0},
        "prep": {"state": "failed", "consecutive_failures": 3},
        "learning": {"state": "running", "started_at": "2026-09-28T07:00:00+07:00", "consecutive_failures": 0},
    }}
    decision = evaluate(status, datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")), True)
    assert "trade" not in decision["missed"]
    assert "prep" in decision["failing"]
    assert "learning" in decision["hung"]
    assert decision["disable_trade"] is False


def test_blocked_from_previous_day_is_missed_again():
    from watchdog import evaluate

    status = {"tasks": {
        "trade": {"state": "blocked", "last_update": "2026-09-27T10:00:00+07:00", "consecutive_failures": 0},
    }}
    decision = evaluate(status, datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")), True)
    assert "trade" in decision["missed"]


def test_catchup_is_limited_to_one_dispatch_per_ict_day():
    from watchdog import dispatched_today

    runs = [{"displayTitle": "scheduler-analysis", "createdAt": "2026-09-28T01:45:00Z"}]
    now = datetime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert dispatched_today(runs, "analysis", now) is True
    assert dispatched_today(runs, "trade", now) is False


def test_watchdog_runs_in_isolated_stdlib_environment(tmp_path):
    root = Path(__file__).resolve().parent.parent
    shutil.copy2(root / "watchdog.py", tmp_path / "watchdog.py")
    shutil.copy2(root / "trading_calendar.py", tmp_path / "trading_calendar.py")
    result = subprocess.run(
        [sys.executable, "-S", str(tmp_path / "watchdog.py"), "--state-ref", "missing:system_status.json"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status_readable"] is False


def test_issue_fingerprint_is_stable_and_suppresses_same_latest_comment():
    from watchdog import decision_fingerprint, fingerprint_marker, latest_issue_fingerprint

    first = {"status_readable": True, "failing": ["trade", "analysis"], "hung": ["trade"]}
    reordered = {"status_readable": True, "failing": ["analysis", "trade"], "hung": ["trade"]}
    fingerprint = decision_fingerprint(first)
    assert fingerprint == decision_fingerprint(reordered)
    issue = {"body": "old", "comments": [{"body": "details\n" + fingerprint_marker(fingerprint)}]}
    assert latest_issue_fingerprint(issue) == fingerprint


def test_existing_enabled_state_kill_switch_skips_disable_dispatch():
    from watchdog import trading_disabled_payload_enabled

    assert trading_disabled_payload_enabled('{"disabled": true, "reason": "watchdog"}') is True
    assert trading_disabled_payload_enabled('{"disabled": false}') is False
    assert trading_disabled_payload_enabled("broken") is False


def test_state_push_succeeds_only_from_loaded_sha_and_rejects_conflict(tmp_path):
    from state_push import StateConflict, publish

    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "test")
    (seed / "system_status.json").write_text("{}", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "initial")
    git(seed, "branch", "-M", "state")
    git(seed, "push", "-u", "origin", "state")
    loaded = git(seed, "rev-parse", "HEAD")

    runner = tmp_path / "runner"
    subprocess.run(["git", "clone", "--branch", "state", str(remote), str(runner)], check=True, capture_output=True)
    git(runner, "config", "user.email", "bot@example.com")
    git(runner, "config", "user.name", "bot")
    (runner / "system_status.json").write_text('{"ok":true}', encoding="utf-8")
    assert publish(runner, loaded, "analysis", sleeper=lambda _delay: None) == "pushed"

    stale = git(runner, "rev-parse", "HEAD")
    (seed / "other.json").write_text("{}", encoding="utf-8")
    git(seed, "pull", "--rebase", "origin", "state")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "concurrent")
    git(seed, "push", "origin", "state")
    (runner / "local.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StateConflict, match="refusing merge"):
        publish(runner, stale, "eod", sleeper=lambda _delay: None)


def test_watchdog_workflow_is_read_only_for_state_branch():
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "watchdog.yml").read_text(encoding="utf-8")
    assert "contents: read" in workflow
    assert "git push" not in workflow
    assert "gh workflow run scheduler.yml" in workflow
    assert "gh variable set" not in workflow
    assert "task=disable-trading" in workflow
    assert workflow.index("Create or update watchdog Issue first") < workflow.index("Dispatch persisted trading kill switch")
    assert "continue-on-error: true" in workflow
    assert "watchdog cannot read status" in workflow
    assert "Report legacy status grace once" in workflow
    assert 'if [ "$PRIOR" -eq 0 ]' in workflow
    assert "Issue fingerprint unchanged; no comment added." in workflow
    assert "Persisted trading kill switch is already enabled; dispatch skipped." in workflow


def test_persisted_kill_switch_requires_confirmation_to_reopen(tmp_path):
    from trading_safety import ENABLE_CONFIRMATION, disable_trading, enable_trading, kill_switch_reason

    disabled = disable_trading(tmp_path, reason="analysis failed three times", actor="watchdog", run_id="42")
    assert disabled["run_id"] == "42"
    assert "analysis failed" in kill_switch_reason(tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        enable_trading(tmp_path, confirmation="yes", actor="operator")
    enable_trading(tmp_path, confirmation=ENABLE_CONFIRMATION, reason="incident resolved", actor="operator")
    assert kill_switch_reason(tmp_path) is None
    audit = json.loads((tmp_path / "trading_switch_audit.json").read_text(encoding="utf-8"))
    assert [row["action"] for row in audit] == ["disable-trading", "enable-trading"]


def test_scheduler_records_safety_gate_as_blocked_not_failed(monkeypatch, tmp_path):
    import scheduler

    monkeypatch.setattr(scheduler, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "task_auto_trade", lambda: {"status": "blocked", "reason": "kill switch enabled"})
    result = scheduler.run_now("trade")
    status = json.loads((tmp_path / "system_status.json").read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert status["tasks"]["trade"]["state"] == "blocked"
    assert status["tasks"]["trade"]["consecutive_failures"] == 0
    assert status["tasks"]["trade"]["blocked_reason"] == "kill switch enabled"


def test_dashboard_status_does_not_erase_scheduler_task_history(monkeypatch, tmp_path):
    import dashboard_vn

    status_path = tmp_path / "system_status.json"
    status_path.write_text(json.dumps({"tasks": {"trade": {"last_success": "2026-09-28T02:20:00+00:00"}}}), encoding="utf-8")
    monkeypatch.setattr(dashboard_vn, "SYSTEM_STATUS_FILE", str(status_path))
    dashboard_vn.set_system_status("idle", "done")
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["tasks"]["trade"]["last_success"] == "2026-09-28T02:20:00+00:00"
    assert saved["status"] == "idle"

