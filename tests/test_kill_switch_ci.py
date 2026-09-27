import json
from pathlib import Path


def test_kill_switch_only_exits_zero_and_writes_blocked_summary(monkeypatch, tmp_path):
    from self_healing import audit_exit_code, write_github_blocked_summary

    report = {"critical": ["trading kill switch: TRADING_ENABLED disables trading"]}
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert audit_exit_code(report) == 0
    write_github_blocked_summary(report)
    assert "Trading blocked" in summary.read_text(encoding="utf-8")


def test_kill_switch_plus_drift_exits_two(monkeypatch, tmp_path):
    from self_healing import audit_exit_code, write_github_blocked_summary

    report = {"critical": [
        "trading kill switch: TRADING_ENABLED disables trading",
        "historical ledger drift: expected cash 1, actual 2",
    ]}
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert audit_exit_code(report) == 2
    write_github_blocked_summary(report)
    assert not summary.exists()


def test_scheduler_alert_duplicate_fingerprint_is_detected():
    from workflow_alerts import latest_fingerprint, marker, scheduler_failure_fingerprint

    fingerprint = scheduler_failure_fingerprint("analysis", ["healing_audit", "scheduler_task"], "2026-09-27")
    same = scheduler_failure_fingerprint("analysis", ["scheduler_task", "healing_audit"], "2026-09-27")
    issue = {"body": "old", "comments": [{"body": "failure\n" + marker(fingerprint)}]}
    assert same == fingerprint
    assert latest_fingerprint(issue) == fingerprint


def test_scheduler_workflow_skips_duplicate_failure_comment():
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "scheduler.yml").read_text(encoding="utf-8")
    assert "Scheduler failure fingerprint unchanged; no comment added." in workflow
    assert "scheduler_failure_fingerprint" in workflow
