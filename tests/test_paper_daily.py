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
