"""DNSE (Entrade) daily OHLCV client - the vnstock-independent price source.

Public endpoint, no key/account:
    https://services.entrade.com.vn/chart-api/v2/ohlcs/stock?symbol=FPT&resolution=1D&from=<unix>&to=<unix>
Response: {"t": [unix...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}.

Contract with the rest of the code base (what vnstock's VCI/MSN frames looked like in
data_cache/vnstock): columns time/open/high/low/close/volume, one row per exchange
session, ascending, `time` a tz-naive Timestamp at 00:00 (Vietnam calendar day), prices
in full VND. DNSE quotes prices in thousand VND, so they are multiplied by 1000 here.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd

log = logging.getLogger("dnse_client")

DNSE_URL = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"
DNSE_TIMEOUT = (5, 12)  # (connect, read) seconds
DNSE_PRICE_UNIT = 1000.0  # DNSE prices are in thousand VND
VN_TZ = "Asia/Ho_Chi_Minh"
_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class DNSEError(RuntimeError):
    """DNSE request/response problem. `transient` says whether a retry makes sense."""

    def __init__(self, message, transient=True):
        super().__init__(message)
        self.transient = transient


def _to_unix(day, end_of_day=False):
    ts = pd.Timestamp(day).tz_localize(VN_TZ) if pd.Timestamp(day).tzinfo is None else pd.Timestamp(day)
    if end_of_day:
        ts = ts + timedelta(days=1) - timedelta(seconds=1)
    return int(ts.timestamp())


def parse_payload(payload):
    """Convert a DNSE JSON payload into the normalized frame (pure function, no network)."""
    if not isinstance(payload, dict) or "t" not in payload:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise DNSEError(f"unexpected DNSE payload: {message or type(payload).__name__}", transient=False)
    arrays = {key: payload.get(key) or [] for key in ("t", "o", "h", "l", "c", "v")}
    lengths = {len(values) for values in arrays.values()}
    if len(lengths) != 1:
        raise DNSEError(f"DNSE payload arrays differ in length: {sorted(lengths)}", transient=False)
    if not arrays["t"]:
        return pd.DataFrame(columns=_COLUMNS)

    # Bars are stamped at the 09:00 Vietnam open; take the Vietnam calendar day, drop tz.
    days = pd.to_datetime(arrays["t"], unit="s", utc=True).tz_convert(VN_TZ).tz_localize(None).normalize()
    frame = pd.DataFrame({
        "time": days,
        "open": pd.to_numeric(pd.Series(arrays["o"]), errors="coerce") * DNSE_PRICE_UNIT,
        "high": pd.to_numeric(pd.Series(arrays["h"]), errors="coerce") * DNSE_PRICE_UNIT,
        "low": pd.to_numeric(pd.Series(arrays["l"]), errors="coerce") * DNSE_PRICE_UNIT,
        "close": pd.to_numeric(pd.Series(arrays["c"]), errors="coerce") * DNSE_PRICE_UNIT,
        "volume": pd.to_numeric(pd.Series(arrays["v"]), errors="coerce"),
    })
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].round(2)  # strip 49.55*1000 float noise
    frame = frame.dropna(subset=["close"])
    frame["volume"] = frame["volume"].fillna(0).astype("int64")
    return frame.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)


def fetch_history(ticker, start, end, interval="1D", timeout=DNSE_TIMEOUT, http_get=None):
    """Download daily bars for `ticker` between `start` and `end` (YYYY-MM-DD, inclusive).

    `http_get(url, params=..., timeout=...)` is injectable so tests never touch the network.
    Raises DNSEError (transient=True for timeouts/5xx/429, False for 4xx/garbage).
    """
    if str(interval).upper() not in ("1D", "D", "1DAY"):
        raise DNSEError(f"DNSE client only supports daily bars, got {interval!r}", transient=False)
    if http_get is None:
        import requests
        http_get = requests.get
    params = {
        "symbol": str(ticker).upper(),
        "resolution": "1D",
        "from": _to_unix(start),
        "to": _to_unix(end, end_of_day=True),
    }
    try:
        response = http_get(DNSE_URL, params=params, timeout=timeout)
    except Exception as exc:  # requests.Timeout / ConnectionError / anything from the transport
        raise DNSEError(f"DNSE request failed: {exc}", transient=True) from exc
    status = getattr(response, "status_code", 200)
    if status != 200:
        raise DNSEError(f"DNSE HTTP {status} for {params['symbol']}", transient=status >= 500 or status == 429)
    try:
        payload = response.json()
    except ValueError as exc:
        raise DNSEError("DNSE returned non-JSON body", transient=True) from exc
    frame = parse_payload(payload)
    log.info("  %s: DNSE returned %d bars", params["symbol"], len(frame))
    return frame
