"""Download a survivorship-aware daily price snapshot for the VN research backtester.

Every stock vnstock knows about (HOSE, HNX, UPCoM and DELISTED) is fetched from
2010 onward. The VCI source returns at most about 2,000 sessions per request
(the most recent ones before `end`), so long histories are paged backwards. Each symbol is stored as one parquet file inside a snapshot
directory named after the download date, so a backtest can always be reproduced
from the exact data it used.

    python -m research.data                 # new snapshot dated today
    python -m research.data --resume DIR    # continue an interrupted snapshot
"""

import argparse
import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger("research.data")

ROOT = Path(__file__).resolve().parent
SNAPSHOT_ROOT = ROOT / "data"
START = "2010-01-01"
PAGE_FULL = 1900  # a page this long may have older sessions before it
INDEXES = ("VNINDEX", "VN30", "HNXINDEX", "E1VFVN30")
# Guest tier: 20 requests/minute, 1,200/hour, 5,000/day, enforced client-side
# by vnai (it calls sys.exit when exceeded). vnstock's own retries after a
# timeout also count, so keep well under the per-minute limit.
MIN_INTERVAL = float(os.getenv("VNSTOCK_MIN_INTERVAL", "6"))
# Fetch order: indexes and exchange-listed/delisted names before the long UPCoM tail.
EXCHANGE_PRIORITY = {"HSX": 0, "HNX": 1, "DELISTED": 2, "UPCOM": 3}


class _Throttle:
    def __init__(self, interval):
        self.interval = interval
        self.last = 0.0

    def wait(self):
        delay = self.last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def list_stocks():
    from vnstock import Listing

    listing = Listing(source="VCI").symbols_by_exchange()
    stocks = listing[listing["type"] == "STOCK"]
    return stocks[["symbol", "exchange", "icb_code2"]].drop_duplicates("symbol").reset_index(drop=True)


def _fetch(symbol, start, end, throttle, attempts=4):
    from vnstock import Quote

    for attempt in range(attempts):
        throttle.wait()
        try:
            frame = Quote(symbol=symbol, source="VCI").history(start=start, end=end, interval="1D")
            return frame if frame is not None else pd.DataFrame()
        except SystemExit as exc:  # vnai quota guard terminates instead of raising
            log.warning("%s: rate limit reached (%s); waiting 65s", symbol, str(exc)[:80])
            time.sleep(65)
            continue
        except Exception as exc:
            text = str(exc).lower()
            if any(key in text for key in ("không tìm thấy", "not found", "no data", "empty", "invalid symbol")):
                return pd.DataFrame()
            wait = 30 * (attempt + 1) if ("rate" in text or "quota" in text or "429" in text) else 5 * (attempt + 1)
            log.warning("%s %s..%s attempt %s failed: %s", symbol, start, end, attempt + 1, str(exc)[:160])
            time.sleep(wait)
    raise RuntimeError(f"{symbol}: giving up after {attempts} attempts")


def fetch_history(symbol, throttle, today, max_pages=4):
    """Page backwards: VCI returns at most ~2,000 of the most recent sessions before `end`."""
    frames = []
    end = today
    for _ in range(max_pages):
        frame = _fetch(symbol, START, end, throttle)
        if frame.empty:
            break
        frames.append(frame)
        first = pd.Timestamp(frame["time"].min())
        if len(frame) < PAGE_FULL or first <= pd.Timestamp(START) + pd.Timedelta(days=10):
            break
        end = (first - pd.Timedelta(days=1)).date().isoformat()
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, ignore_index=True)
    data["time"] = pd.to_datetime(data["time"]).dt.normalize()
    data = data.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return data[["time", "open", "high", "low", "close", "volume"]]


def download(snapshot_dir, symbols=None):
    snapshot_dir = Path(snapshot_dir)
    prices_dir = snapshot_dir / "prices"
    prices_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    throttle = _Throttle(MIN_INTERVAL)

    listing_path = snapshot_dir / "listing.csv"
    if listing_path.exists():
        listing = pd.read_csv(listing_path)
    else:
        listing = list_stocks()
        listing.to_csv(listing_path, index=False)
    if symbols:
        wanted = list(symbols)
    else:
        ordered = listing.assign(_p=listing["exchange"].map(EXCHANGE_PRIORITY).fillna(9)).sort_values(["_p", "symbol"])
        wanted = list(INDEXES) + ordered["symbol"].tolist()

    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "created_at": datetime.now().isoformat(timespec="seconds"), "source": "vnstock VCI", "empty": [], "failed": {},
    }
    done = {p.stem for p in prices_dir.glob("*.parquet")} | set(manifest["empty"])
    todo = [s for s in wanted if s not in done]
    log.info("snapshot %s: %s done, %s to fetch", snapshot_dir.name, len(done), len(todo))

    for i, symbol in enumerate(todo, 1):
        try:
            data = fetch_history(symbol, throttle, today)
        except Exception as exc:
            manifest["failed"][symbol] = str(exc)[:300]
            continue
        if data.empty:
            manifest["empty"].append(symbol)
        else:
            data.to_parquet(prices_dir / f"{symbol}.parquet", index=False)
            manifest["failed"].pop(symbol, None)
        if i % 25 == 0 or i == len(todo):
            manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
            log.info("%s/%s fetched (latest %s)", i, len(todo), symbol)
    manifest["completed_at"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return snapshot_dir


def latest_snapshot():
    snapshots = sorted(p for p in SNAPSHOT_ROOT.glob("20*") if (p / "prices").is_dir())
    if not snapshots:
        raise FileNotFoundError(f"no snapshot under {SNAPSHOT_ROOT}; run python -m research.data")
    return snapshots[-1]


def load_prices(snapshot_dir=None):
    """Return a long frame: time, symbol, open, high, low, close, volume."""
    snapshot_dir = Path(snapshot_dir or latest_snapshot())
    frames = []
    for path in sorted((snapshot_dir / "prices").glob("*.parquet")):
        frame = pd.read_parquet(path)
        frame["symbol"] = path.stem
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.environ.setdefault("VNSTOCK_TELEMETRY", "off")
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", help="existing snapshot directory to continue")
    parser.add_argument("--symbols", help="comma-separated subset (for testing)")
    args = parser.parse_args()
    target = Path(args.resume) if args.resume else SNAPSHOT_ROOT / date.today().isoformat()
    symbols = args.symbols.split(",") if args.symbols else None
    print(download(target, symbols))
