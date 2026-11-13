"""DNSE price source: parsing/format contract and the no-vnstock fallback path. Fully offline."""

import json

import pandas as pd
import pytest

import data_fetcher
import dnse_client
import market_data_adapter
import source_manager

# Real DNSE shape (FPT, 2026-11-05..11-12, trimmed): bars stamped 02:00 UTC = 09:00 Vietnam, prices in thousand VND.
SAMPLE = {
    "t": [1793844000, 1793930400, 1794189600, 1794276000, 1794362400, 1794448800],
    "o": [54.5, 51.4, 51.3, 51.6, 51, 51],
    "h": [54.6, 51.8, 51.6, 52.5, 51.1, 51],
    "l": [52.8, 50.8, 50.6, 50.9, 50.2, 49.45],
    "c": [52.9, 50.8, 51, 50.9, 50.9, 49.55],
    "v": [8006200, 12162200, 5362500, 7268500, 6533000, 17335200],
    "nextTime": 0,
}


class FakeResponse:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._payload, self._bad = status, payload, bad_json

    def json(self):
        if self._bad:
            raise ValueError("no json")
        return self._payload


def test_parse_payload_matches_vnstock_frame_contract():
    df = dnse_client.parse_payload(SAMPLE)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert str(df["time"].dtype) == "datetime64[ns]"  # tz-naive, like the vnstock cache
    assert [d.strftime("%Y-%m-%d") for d in df["time"]] == [
        "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
    assert df["time"].is_monotonic_increasing
    assert (df["time"] == df["time"].dt.normalize()).all()  # midnight stamps
    assert df["close"].iloc[-1] == 49550.0  # thousand VND -> VND, no 49550.00000000001 noise
    assert df["low"].iloc[-1] == 49450.0
    assert str(df["volume"].dtype) == "int64" and df["volume"].iloc[0] == 8006200


def test_parse_payload_sorts_dedups_and_drops_null_close():
    payload = {"t": [1794276000, 1793844000, 1793844000, 1793930400],
               "o": [1, 2, 3, 4], "h": [1, 2, 3, 4], "l": [1, 2, 3, 4],
               "c": [51.0, 52.0, 52.5, None], "v": [1, 2, 3, 4]}
    df = dnse_client.parse_payload(payload)
    assert [d.day for d in df["time"]] == [5, 10]  # null-close bar dropped, duplicate day collapsed (last wins)
    assert df["close"].tolist() == [52500.0, 51000.0]


def test_parse_payload_empty_and_malformed():
    empty = dnse_client.parse_payload({"t": [], "o": [], "h": [], "l": [], "c": [], "v": []})
    assert empty.empty and list(empty.columns) == ["time", "open", "high", "low", "close", "volume"]
    with pytest.raises(dnse_client.DNSEError) as bad_symbol:
        dnse_client.parse_payload({"status": 400, "code": "BAD_REQUEST", "message": "invalid symbol"})
    assert not bad_symbol.value.transient and "invalid symbol" in str(bad_symbol.value)
    with pytest.raises(dnse_client.DNSEError):
        dnse_client.parse_payload({"t": [1], "o": [1], "h": [1], "l": [1], "c": [1], "v": []})


def test_fetch_history_builds_request_and_converts_units():
    seen = {}

    def http_get(url, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return FakeResponse(200, SAMPLE)

    df = dnse_client.fetch_history("fpt", "2026-11-05", "2026-11-12", http_get=http_get)
    assert seen["url"] == "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"
    assert seen["params"]["symbol"] == "FPT" and seen["params"]["resolution"] == "1D"
    # window = [05 Nov 00:00, 12 Nov 23:59:59] Vietnam time, so the last session is included
    assert seen["params"]["from"] == 1793811600 and seen["params"]["to"] == 1794502799
    assert seen["timeout"] == dnse_client.DNSE_TIMEOUT and all(t > 0 for t in seen["timeout"])
    assert len(df) == 6 and df["close"].iloc[-1] == 49550.0


@pytest.mark.parametrize("status,transient", [(500, True), (503, True), (429, True), (400, False), (404, False)])
def test_fetch_history_http_errors_are_classified(status, transient):
    with pytest.raises(dnse_client.DNSEError) as exc:
        dnse_client.fetch_history("FPT", "2026-11-05", "2026-11-12", http_get=lambda *a, **k: FakeResponse(status, {}))
    assert exc.value.transient is transient


def test_fetch_history_network_error_and_bad_json_are_transient_and_intraday_rejected():
    def boom(*_a, **_k):
        raise TimeoutError("read timed out")

    with pytest.raises(dnse_client.DNSEError) as net:
        dnse_client.fetch_history("FPT", "2026-11-05", "2026-11-12", http_get=boom)
    assert net.value.transient
    with pytest.raises(dnse_client.DNSEError) as bad:
        dnse_client.fetch_history("FPT", "2026-11-05", "2026-11-12",
                                  http_get=lambda *a, **k: FakeResponse(200, bad_json=True))
    assert bad.value.transient
    with pytest.raises(dnse_client.DNSEError) as tf:
        dnse_client.fetch_history("FPT", "2026-11-05", "2026-11-12", interval="1H", http_get=boom)
    assert not tf.value.transient


def _year_payload(start, periods):
    dates = pd.bdate_range(start, periods=periods)
    stamps = [int(d.tz_localize("Asia/Ho_Chi_Minh").timestamp()) + 9 * 3600 for d in dates]
    price = [50.0 + i * 0.01 for i in range(periods)]
    return {"t": stamps, "o": price, "h": price, "l": price, "c": price, "v": [1000] * periods}


@pytest.fixture
def no_vnstock(monkeypatch, tmp_path):
    monkeypatch.setattr(market_data_adapter, "provider_availability",
                        lambda: {"available": False, "status": "degraded", "detail": "test"})
    monkeypatch.setattr(source_manager, "get_source", lambda: "VCI")
    monkeypatch.setattr(source_manager, "report_success", lambda _s: None)
    monkeypatch.setattr(source_manager, "report_failure", lambda _s: None)
    monkeypatch.setattr(data_fetcher, "VNSTOCK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data_fetcher.time, "sleep", lambda _s: None)

    def vnstock_must_not_run(*_a, **_k):
        pytest.fail("vnstock worker must not be used when vnstock is unavailable")

    monkeypatch.setattr(data_fetcher, "_history_via_worker", vnstock_must_not_run)
    from runtime_reliability import market_data_circuit
    monkeypatch.setattr(market_data_circuit, "allow", lambda _k: True)
    return tmp_path


def test_fetch_with_fallback_uses_dnse_when_vnstock_is_missing(no_vnstock, monkeypatch):
    calls = []

    def fake_dnse(ticker, start, end, interval="1D", **_k):
        calls.append((ticker, start, end))
        return dnse_client.parse_payload(_year_payload(start, 520))

    monkeypatch.setattr(dnse_client, "fetch_history", fake_dnse)
    df, source = data_fetcher.fetch_with_fallback("FPT", "2024-09-30", "2026-09-28")
    assert source == "DNSE" and df.attrs["source"] == "DNSE" and len(calls) == 1
    assert df.attrs["data_quality"]["status"] == "ok"
    assert df["close"].iloc[0] == 50000.0  # VND


def test_get_stock_data_cached_end_to_end_without_vnstock(no_vnstock, monkeypatch):
    monkeypatch.setattr(dnse_client, "fetch_history",
                        lambda ticker, start, end, interval="1D", **_k: dnse_client.parse_payload(_year_payload(start, 300)))
    monkeypatch.setattr(data_fetcher, "assess_history_quality",
                        lambda df, start, end, **_k: {"status": "ok", "rows": len(df), "coverage": 1.0,
                                                      "first": None, "last": None})
    df = data_fetcher.get_stock_data_cached("FPT", years=1, force_refresh=True)
    assert len(df) == 300 and df.attrs["source"] == "DNSE"
    assert data_fetcher.data_provenance("FPT")["source"] == "DNSE"
    cache = json.loads(next(no_vnstock.glob("FPT_*.json")).read_text(encoding="utf-8"))
    assert cache["source"] == "DNSE" and cache["row_count"] == 300 and cache["data"][0]["close"] == 50000.0


def test_transient_dnse_error_is_retried_but_permanent_one_is_not(no_vnstock, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    attempts = {"n": 0}

    def flaky(ticker, start, end, interval="1D", **_k):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise dnse_client.DNSEError("HTTP 503", transient=True)
        return dnse_client.parse_payload(_year_payload(start, 520))

    monkeypatch.setattr(dnse_client, "fetch_history", flaky)
    _, source = data_fetcher.fetch_with_fallback("FPT", "2024-09-30", "2026-09-28")
    assert source == "DNSE" and attempts["n"] == 3

    attempts["n"] = 0

    def permanent(*_a, **_k):
        attempts["n"] += 1
        raise dnse_client.DNSEError("invalid symbol", transient=False)

    monkeypatch.setattr(dnse_client, "fetch_history", permanent)
    with pytest.raises(dnse_client.DNSEError):
        data_fetcher.fetch_with_fallback("ZZZZ", "2024-09-30", "2026-09-28")
    assert attempts["n"] == 1


def test_vnstock_sources_still_tried_first_when_vnstock_is_available(monkeypatch):
    monkeypatch.setattr(market_data_adapter, "provider_availability", lambda: {"available": True})
    monkeypatch.setattr(source_manager, "get_source", lambda: "VCI")
    monkeypatch.setattr(source_manager, "report_success", lambda _s: None)
    monkeypatch.setattr(source_manager, "report_failure", lambda _s: None)
    order = []

    def fake_worker(ticker, source, start, end, interval="1D", **_k):
        order.append(source)
        raise RuntimeError("vendor down")

    def fake_dnse(ticker, start, end, interval="1D", **_k):
        order.append("DNSE")
        return dnse_client.parse_payload(_year_payload(start, 520))

    from runtime_reliability import market_data_circuit
    monkeypatch.setattr(market_data_circuit, "allow", lambda _k: True)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr(data_fetcher, "_history_via_worker", fake_worker)
    monkeypatch.setattr(dnse_client, "fetch_history", fake_dnse)
    _, source = data_fetcher.fetch_with_fallback("FPT", "2024-09-30", "2026-09-28")
    assert source == "DNSE"
    assert order[0] == "VCI" and "MSN" in order and order[-1] == "DNSE"
    assert source_manager.SOURCES == ["VCI", "MSN", "DNSE"]
