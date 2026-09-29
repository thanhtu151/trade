import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ohlcv(rows=300):
    rng = np.random.default_rng(7)
    close = 20_000 + np.cumsum(rng.normal(0, 200, rows))
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=rows, freq="B").astype(str),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": rng.integers(1_000_000, 2_000_000, rows),
    })


def test_macro_fetch_falls_back_when_yfinance_transport_breaks(monkeypatch):
    import yfinance
    import data_fetcher

    def broken(*_a, **_k):
        raise RuntimeError("Impersonating chrome150 is not supported")

    fallback = pd.DataFrame({"Close": [15.0, 16.0]}, index=pd.to_datetime(["2026-09-25", "2026-09-28"]))
    fallback.index.name = "Date"
    requested = []
    monkeypatch.setattr(yfinance, "download", broken)
    monkeypatch.setattr(data_fetcher, "_load_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(data_fetcher, "_save_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(data_fetcher, "_yahoo_chart_close",
                        lambda symbol, start, end: requested.append(symbol) or fallback)

    vix = data_fetcher.fetch_vix(1)
    assert list(vix["vix"]) == [15.0, 16.0]
    assert list(vix["time"]) == ["2026-09-25", "2026-09-28"]
    usd = data_fetcher.fetch_usdvnd(1)
    assert list(usd["usdvnd"]) == [15.0, 16.0]
    assert requested == ["^VIX", "USDVND=X"]


def test_eod_summary_sent_once_per_day(monkeypatch, tmp_path):
    import notify

    sent = []
    monkeypatch.setattr(notify, "build_eod_summary", lambda base_dir: "summary")
    monkeypatch.setattr(notify, "send_embed", lambda *a, **k: sent.append(a) or True)
    assert notify.notify_eod(tmp_path) is True
    assert notify.notify_eod(tmp_path) is False
    assert len(sent) == 1
