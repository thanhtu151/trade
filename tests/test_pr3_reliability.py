import json
import subprocess
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


def test_watchdog_applies_sla_and_trade_sessions():
    from watchdog import evaluate

    ict = ZoneInfo("Asia/Ho_Chi_Minh")
    status = {"tasks": {}}
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
    assert "gh variable set TRADING_ENABLED --body false" in workflow


def test_dashboard_status_does_not_erase_scheduler_task_history(monkeypatch, tmp_path):
    import dashboard_vn

    status_path = tmp_path / "system_status.json"
    status_path.write_text(json.dumps({"tasks": {"trade": {"last_success": "2026-09-28T02:20:00+00:00"}}}), encoding="utf-8")
    monkeypatch.setattr(dashboard_vn, "SYSTEM_STATUS_FILE", str(status_path))
    dashboard_vn.set_system_status("idle", "done")
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["tasks"]["trade"]["last_success"] == "2026-09-28T02:20:00+00:00"
    assert saved["status"] == "idle"

