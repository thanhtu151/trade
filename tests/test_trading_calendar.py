import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import calendar_sync  # noqa: E402
import trading_calendar as tc  # noqa: E402

LUNAR_NEW_YEAR = {
    2000: "2000-02-05", 2007: "2007-02-17", 2010: "2010-02-14", 2015: "2015-02-19", 2018: "2018-02-16",
    2020: "2020-01-25", 2021: "2021-02-12", 2022: "2022-02-01", 2023: "2023-01-22", 2024: "2024-02-10",
    2025: "2025-01-29", 2026: "2026-02-17", 2027: "2027-02-06", 2028: "2028-01-26",
}


@pytest.fixture
def calendar_file(tmp_path, monkeypatch):
    path = tmp_path / "trading_calendar.json"
    monkeypatch.setattr(tc, "CALENDAR_FILE", path)
    monkeypatch.setattr(tc, "_cache", {"mtime": None, "data": None})
    return path


@pytest.mark.parametrize("year,expected", sorted(LUNAR_NEW_YEAR.items()))
def test_vietnamese_lunar_new_year(year, expected):
    assert tc.lunar_to_solar(year, 1, 1).isoformat() == expected


def test_hung_kings_day_2026_matches_verified_calendar():
    assert tc.lunar_to_solar(2026, 3, 10) == date(2026, 4, 26)  # Sunday; 27 April is the verified day off


def test_provisional_year_closes_statutory_days_only(calendar_file):
    assert tc.year_status(2027) == "provisional"
    closed = tc.closures(2027)
    assert {"2027-01-01", "2027-02-05", "2027-02-08", "2027-02-09", "2027-04-30", "2027-09-02"} <= closed
    assert tc.is_trading_day(date(2027, 1, 4)) is True


def test_announced_list_replaces_provisional_rules(calendar_file):
    calendar_file.write_text(json.dumps({"years": {"2027": {"status": "announced",
                                                            "closed": ["2027-02-04", "2027-02-05"]}}}))
    assert tc.year_status(2027) == "announced"
    assert tc.is_trading_day(date(2027, 2, 4)) is False
    assert tc.is_trading_day(date(2027, 2, 8)) is True  # not in the announced list


def test_observed_session_overrides_every_list(calendar_file):
    calendar_file.write_text(json.dumps({"years": {}, "observed": {"open": ["2026-02-16"]}}))
    assert tc.is_trading_day(date(2026, 2, 16)) is True


def vnindex(days):
    return pd.DataFrame({"time": pd.to_datetime(days), "close": 1000.0})


def test_sync_builds_statuses_observations_and_discrepancies(calendar_file):
    traded = [d for d in pd.bdate_range("2026-01-05", "2026-09-28") if d.strftime("%Y-%m-%d") not in
              tc.VN_EXCHANGE_HOLIDAYS[2026] and d != pd.Timestamp("2026-06-01")]
    announced = {2025: {"2025-01-27", "2025-01-28", "2025-01-29"}, 2026: {"2026-11-24", "2026-03-02"}}
    result = calendar_sync.sync(today=date(2026, 10, 5), fetch=lambda: vnindex(traded), announced=announced,
                                path=calendar_file, notify=False)
    data = json.loads(calendar_file.read_text())
    assert result["years"] == {"2025": "announced", "2026": "verified", "2027": "provisional"}
    assert "2026-06-01" in data["years"]["2026"]["closed"]  # observed closure, no list had it
    assert any("2026-11-24" in d for d in data["discrepancies"])
    assert any("2026-03-02" in d and "traded" in d for d in data["discrepancies"])
    assert any("2027" in a for a in result["alerts"])  # next year still provisional in October
    assert tc.is_trading_day(date(2026, 6, 1)) is False
    assert tc.is_trading_day(date(2026, 11, 24)) is True  # verified year keeps its own list


def test_sync_survives_missing_market_data(calendar_file):
    def broken():
        raise RuntimeError("offline")

    result = calendar_sync.sync(today=date(2026, 5, 4), fetch=broken, announced={}, path=calendar_file, notify=False)
    assert result["years"]["2027"] == "provisional" and result["alerts"] == []
