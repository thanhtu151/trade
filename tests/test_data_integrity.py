import json
from datetime import datetime

import pandas as pd
import pytest


def _frame(start, periods):
    dates = pd.bdate_range(start, periods=periods)
    return pd.DataFrame({
        "time": dates,
        "open": range(periods),
        "high": range(periods),
        "low": range(periods),
        "close": range(periods),
        "volume": [1000] * periods,
    })


def test_truncated_msn_is_rejected_then_complete_vci_is_accepted(monkeypatch):
    import data_fetcher
    import market_data_adapter
    import source_manager

    monkeypatch.setattr(market_data_adapter, "provider_availability", lambda: {"available": True})

    calls, successes, failures = [], [], []
    monkeypatch.setattr(source_manager, "get_source", lambda: "MSN")
    monkeypatch.setattr(source_manager, "report_success", successes.append)
    monkeypatch.setattr(source_manager, "report_failure", failures.append)

    def history(_ticker, source, _start, _end, _interval):
        calls.append(source)
        return _frame("2026-08-24", 24) if source == "MSN" else _frame("2024-09-30", 520)

    monkeypatch.setattr(data_fetcher, "_history_via_worker", history)
    df, source = data_fetcher.fetch_with_fallback("DXG", "2024-09-28", "2026-09-28")

    assert calls == ["MSN", "VCI"]
    assert failures == ["MSN"]
    assert successes == ["VCI"]
    assert source == "VCI"
    assert len(df) == 520
    assert df.attrs["data_quality"]["status"] == "ok"


def test_all_truncated_sources_raise_explicit_insufficient_data(monkeypatch):
    import data_fetcher
    import market_data_adapter
    import source_manager

    monkeypatch.setattr(market_data_adapter, "provider_availability", lambda: {"available": True})

    monkeypatch.setattr(source_manager, "get_source", lambda: "MSN")
    monkeypatch.setattr(source_manager, "report_success", lambda _source: pytest.fail("must not report success"))
    monkeypatch.setattr(source_manager, "report_failure", lambda _source: None)
    monkeypatch.setattr(data_fetcher, "_history_via_worker", lambda *_a, **_k: _frame("2026-08-24", 24))

    with pytest.raises(data_fetcher.InsufficientDataError) as exc_info:
        data_fetcher.fetch_with_fallback("DXG", "2024-09-28", "2026-09-28")
    assert exc_info.value.status == "insufficient_data"
    assert "coverage" in str(exc_info.value)


def test_legacy_cache_without_quality_metadata_is_ignored(monkeypatch, tmp_path):
    import data_fetcher

    monkeypatch.setattr(data_fetcher, "VNSTOCK_CACHE_DIR", str(tmp_path))
    cache_path = tmp_path / "DXG_2y.json"
    legacy = {
        "ticker": "DXG",
        "years": 2,
        "cached_at": datetime.now().isoformat(),
        "rows": 24,
        "data": data_fetcher._serialize_frame_records(_frame("2026-08-24", 24)),
    }
    cache_path.write_text(json.dumps(legacy), encoding="utf-8")
    fresh = _frame("2024-09-30", 520)
    calls = []

    def fetch(ticker, start, end, interval="1D"):
        calls.append((ticker, start, end, interval))
        fresh.attrs["data_quality"] = {"status": "ok", "coverage": 1.0}
        return fresh, "VCI"

    monkeypatch.setattr(data_fetcher, "fetch_with_fallback", fetch)
    monkeypatch.setattr(data_fetcher.time, "sleep", lambda _seconds: None)
    result = data_fetcher.get_stock_data_cached("DXG", years=2)

    assert calls
    assert len(result) == 520
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["source"] == "VCI"
    assert saved["requested_start"]
    assert saved["requested_end"]
    assert saved["row_count"] == 520
    assert saved["fetched_at"]


@pytest.mark.parametrize("end,years", [
    ("2026-09-28", 0.1),   # spans 31/8-2/9
    ("2026-03-10", 0.1),   # spans Lunar New Year 16-20/2
    ("2026-05-20", 0.1),   # spans 27/4, 30/4, 1/5
    ("2026-09-04", 0.02),  # one week containing 2/9
])
def test_complete_sessions_across_holidays_are_accepted(end, years):
    from datetime import timedelta
    from data_fetcher import assess_history_quality
    from trading_calendar import is_trading_day

    end = pd.Timestamp(end)
    start = end - timedelta(days=int(years * 365))
    sessions = [d for d in pd.bdate_range(start, end) if is_trading_day(d.date())]
    frame = pd.DataFrame({"time": sessions, "close": 1.0})
    assert assess_history_quality(frame, start, end)["status"] == "ok"


def test_short_window_tolerates_missing_latest_session_but_not_truncation():
    from data_fetcher import assess_history_quality
    from trading_calendar import is_trading_day

    start, end = pd.Timestamp("2026-08-24"), pd.Timestamp("2026-09-28")
    sessions = [d for d in pd.bdate_range(start, end) if is_trading_day(d.date())]
    assert assess_history_quality(pd.DataFrame({"time": sessions[:-1]}), start, end)["status"] == "ok"
    assert assess_history_quality(pd.DataFrame({"time": sessions[-5:]}), start, end)["status"] == "insufficient_data"


def test_trades_record_the_provenance_of_the_data_behind_them():
    import auto_trader
    import data_fetcher

    frame = _frame("2026-08-24", 24)
    frame.attrs.update(source="VCI", data_quality={"coverage": 1.0, "first": "2026-08-24", "last": "2026-09-24"})
    data_fetcher._remember_provenance("dxg", frame, "fresh")
    trades = []
    auto_trader.log_trade(trades, "DXG", "BUY", 100, 10_400, "test")
    assert trades[0]["data_provenance"]["source"] == "VCI"
    assert trades[0]["data_provenance"]["coverage"] == 1.0
    auto_trader.log_trade(trades, "NEW", "BUY", 100, 10_000, "test")
    assert "data_provenance" not in trades[1]
