import json
from datetime import date

import pandas as pd

import paper_run


def _frames():
    idx = pd.bdate_range("2025-01-01", "2026-03-31")
    n = len(idx)
    etf = pd.DataFrame({"time": idx, "open": 30000.0, "high": 30000.0, "low": 30000.0, "close": 30000.0,
                        "volume": 1})
    vn30 = pd.Series([1000.0 + i for i in range(n)], index=idx)
    stocks = pd.DataFrame({"A": range(n)}, index=idx, dtype=float)
    return paper_run._bars(etf), vn30, stocks, stocks


def _run(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(paper_run, "load_data_fetcher", lambda uni: _frames())
    monkeypatch.setattr(paper_run, "liquid_candidates", lambda n=70: ["A"])
    args = ["--date", "2026-03-31", "--source", "data_fetcher", "--logs-dir", str(tmp_path / "logs"),
            "--summary-csv", str(tmp_path / "logs" / "summary.csv"), *extra]
    return paper_run.main(args)


def test_same_day_twice_does_not_duplicate_orders_signals_or_summary(monkeypatch, tmp_path, capsys):
    assert _run(monkeypatch, tmp_path) == 0
    logs = tmp_path / "logs"
    first = {p.name: p.read_text() for p in logs.iterdir()}
    assert _run(monkeypatch, tmp_path) == 0
    assert "already_processed" in capsys.readouterr().out
    assert {p.name: p.read_text() for p in logs.iterdir()} == first
    rows = (logs / "summary.csv").read_text().splitlines()
    assert rows[0] == "date,e1,e6,vn30,nav,orders" and len(rows) == 2 and rows[1].startswith("2026-03-31,")
    assert len((logs / "signals.jsonl").read_text().splitlines()) == 2      # E1 + E6, once


def test_require_exact_reports_no_session_and_exits_zero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(paper_run, "load_data_fetcher", lambda uni: _frames())
    monkeypatch.setattr(paper_run, "liquid_candidates", lambda n=70: ["A"])
    rc = paper_run.main(["--date", "2026-04-11", "--source", "data_fetcher", "--require-exact",
                         "--logs-dir", str(tmp_path / "logs"),
                         "--summary-csv", str(tmp_path / "logs" / "summary.csv")])
    assert rc == 0 and "no session" in capsys.readouterr().out
    assert not (tmp_path / "logs" / "summary.csv").exists()
    assert not (tmp_path / "logs" / "signals.jsonl").exists()


def _run_on(monkeypatch, tmp_path, day, *extra):
    monkeypatch.setattr(paper_run, "load_data_fetcher", lambda uni: _frames())
    monkeypatch.setattr(paper_run, "liquid_candidates", lambda n=70: ["A"])
    return paper_run.main(["--date", day, "--source", "data_fetcher", "--require-exact", "--catch-up",
                           "--logs-dir", str(tmp_path / "logs"),
                           "--summary-csv", str(tmp_path / "logs" / "summary.csv"), *extra])


def _days(tmp_path):
    rows = (tmp_path / "logs" / "summary.csv").read_text().splitlines()
    return [r.split(",")[0] for r in rows[1:]]


def test_catch_up_runs_two_missed_sessions_in_order_without_duplicates(monkeypatch, tmp_path, capsys):
    assert _run_on(monkeypatch, tmp_path, "2026-03-26") == 0
    assert _days(tmp_path) == ["2026-03-26"]
    # machine off on 27/3 (Fri) and 30/3 (Mon); next run is 31/3
    assert _run_on(monkeypatch, tmp_path, "2026-03-31") == 0
    assert "Chạy bù 2 phiên" in capsys.readouterr().out
    assert _days(tmp_path) == ["2026-03-26", "2026-03-27", "2026-03-30", "2026-03-31"]
    sig_days = [json.loads(l)["day"] if "day" in json.loads(l) else json.loads(l)["asof"]
                for l in (tmp_path / "logs" / "signals.jsonl").read_text().splitlines()]
    assert len(sig_days) == 8 and sig_days == sorted(sig_days)
    before = {p.name: p.read_text() for p in (tmp_path / "logs").iterdir()}
    assert _run_on(monkeypatch, tmp_path, "2026-03-31") == 0                  # re-run: nothing new
    assert {p.name: p.read_text() for p in (tmp_path / "logs").iterdir()} == before


def test_universe_falls_back_to_committed_list_without_research_dir(monkeypatch):
    monkeypatch.setattr(paper_run, "RESEARCH_PRICES", "/nonexistent/prices/")
    syms = paper_run.liquid_candidates(70)
    assert len(syms) == 70 and len(set(syms)) == 70 and "E1VFVN30" not in syms


def test_workflow_draft_is_valid_paper_only_and_uses_own_state_branch():
    import yaml
    from pathlib import Path
    text = (Path(paper_run.BASE) / "scripts" / "paper-daily.workflow.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    on = wf.get(True, wf.get("on"))                       # PyYAML reads bare `on` as True
    assert on["schedule"] == [{"cron": "30 8 * * 1-5"}] and "workflow_dispatch" in on
    assert on["push"]["branches"] == ["feat/paper-etf-broker"]
    assert wf["permissions"] == {"contents": "write"}
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["jobs"]["paper"]["env"]["STATE_BRANCH"] == "paper-state"
    assert "--reset" not in text and "place_order" not in text


def test_catch_up_on_non_session_today_still_fills_earlier_gap(monkeypatch, tmp_path, capsys):
    assert _run_on(monkeypatch, tmp_path, "2026-03-26") == 0
    assert _run_on(monkeypatch, tmp_path, "2026-04-04") == 0                  # Sat: no bar; 27/3..31/3 missed
    assert _days(tmp_path) == ["2026-03-26", "2026-03-27", "2026-03-30", "2026-03-31"]
