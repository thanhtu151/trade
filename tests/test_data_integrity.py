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
    import source_manager

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
    import source_manager

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
