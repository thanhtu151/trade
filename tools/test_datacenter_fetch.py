"""
Datacenter connectivity probe.

Purpose: before committing to a GitHub Actions / Streamlit Cloud deployment,
verify that the market-data sources this platform depends on are actually
reachable — and not IP-blocked — from a datacenter IP (which is what CI runners
and free PaaS use). vnstock's VCI/TCBS endpoints and Yahoo Finance both
sometimes rate-limit or geo/ASN-block cloud IPs; if they do, the whole
"free serverless" architecture is off the table and we fall back to a VM.

Run locally:   python tools/test_datacenter_fetch.py
Run in CI:     see .github/workflows/test-fetch.yml

Exit code 0 = all critical sources reachable. Non-zero = at least one blocked.
"""

from __future__ import annotations

import json
import socket
import sys
import time
import traceback
from datetime import datetime, timedelta

# A liquid, always-traded ticker — if this can't be fetched, nothing can.
TEST_TICKER = "VCB"
RESULTS: list[dict] = []


def _record(name: str, ok: bool, detail: str, rows: int | None = None) -> None:
    RESULTS.append({"source": name, "ok": ok, "detail": detail, "rows": rows})
    mark = "PASS" if ok else "FAIL"
    extra = f" ({rows} rows)" if rows is not None else ""
    print(f"[{mark}] {name}: {detail}{extra}", flush=True)


def show_egress_ip() -> None:
    """Print the runner's public IP + hostname so we can see which ASN we're on."""
    print("=" * 60, flush=True)
    print("EGRESS / RUNTIME INFO", flush=True)
    print("=" * 60, flush=True)
    print(f"Host: {socket.gethostname()}", flush=True)
    print(f"Local time (naive datetime.now()): {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print(f"UTC time:                          {datetime.utcnow():%Y-%m-%d %H:%M:%S}", flush=True)
    try:
        import requests

        ip = requests.get("https://api.ipify.org", timeout=15).text.strip()
        print(f"Public egress IP: {ip}", flush=True)
        try:
            info = requests.get(f"https://ipinfo.io/{ip}/json", timeout=15).json()
            print(
                f"  -> {info.get('city')}, {info.get('region')}, {info.get('country')} "
                f"| org: {info.get('org')}",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  (ipinfo lookup failed: {e})", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"Could not determine egress IP: {e}", flush=True)
    print("", flush=True)


def _fetch_vnstock(source: str) -> None:
    """Fetch OHLCV via the exact API the codebase uses (vnstock_fetch_worker.py)."""
    try:
        from vnstock.api.quote import Quote

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        df = Quote(symbol=TEST_TICKER, source=source).history(start=start, end=end, interval="1D")
        rows = 0 if df is None else len(df)
        _record(f"vnstock/{source}", rows > 0, "OHLCV history" if rows > 0 else "empty frame", rows)
    except Exception as e:  # noqa: BLE001
        _record(f"vnstock/{source}", False, f"{type(e).__name__}: {e}")


def test_vnstock_vci() -> None:
    """Primary source: vnstock via VCI board."""
    _fetch_vnstock("VCI")


def test_vnstock_msn() -> None:
    """Second endpoint: vnstock via MSN (TCBS/DNSE removed in vnstock 4.x;
    available quote providers are vci/msn/kbs/fmp). MSN hits different
    infrastructure than VCI, so it's a useful independent IP-block signal."""
    _fetch_vnstock("msn")


def test_yahoo() -> None:
    """Macro data source: Yahoo Finance (USD/VND, VIX, VNIndex all use this)."""
    try:
        import yfinance as yf

        df = yf.download("^GSPC", period="5d", interval="1d", progress=False)
        rows = 0 if df is None else len(df)
        _record("yahoo/yfinance", rows > 0, "S&P500 daily" if rows > 0 else "empty frame", rows)
    except Exception as e:  # noqa: BLE001
        _record("yahoo/yfinance", False, f"{type(e).__name__}: {e}")


def main() -> int:
    show_egress_ip()
    print("=" * 60, flush=True)
    print("SOURCE REACHABILITY", flush=True)
    print("=" * 60, flush=True)

    for fn in (test_vnstock_vci, test_vnstock_msn, test_yahoo):
        try:
            fn()
        except Exception:  # noqa: BLE001 — never let one probe kill the run
            traceback.print_exc()
        time.sleep(1.5)  # be polite to rate-limited endpoints

    print("", flush=True)
    print("=" * 60, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 60, flush=True)
    print(json.dumps(RESULTS, indent=2, ensure_ascii=False), flush=True)

    # vnstock is the hard requirement. Yahoo is degradable (only macro context).
    vnstock_ok = any(r["ok"] for r in RESULTS if r["source"].startswith("vnstock"))
    yahoo_ok = any(r["ok"] for r in RESULTS if r["source"].startswith("yahoo"))

    print("", flush=True)
    if vnstock_ok:
        print("VERDICT: vnstock reachable -> serverless (GitHub Actions) deploy is VIABLE.", flush=True)
    else:
        print("VERDICT: vnstock BLOCKED from this IP -> need a VM (e.g. GCP e2-micro near VN).", flush=True)
    if not yahoo_ok:
        print("NOTE: Yahoo blocked -> macro data (USD/VND, VIX) degraded but not fatal.", flush=True)

    return 0 if vnstock_ok else 1


if __name__ == "__main__":
    sys.exit(main())
