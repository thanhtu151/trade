"""Point-in-time quarterly fundamentals from VCI (via vnstock), 2018-Q1 onward.

For each symbol that ever entered the liquidity universe, two raw tables are
stored next to the price snapshot:
- income: quarterly income statement rows with `publicDate` (announcement
  date), so a value is used only once it was public;
- ratio: quarterly market cap and shares outstanding.

vnstock's public `income_statement()` returns only the latest 4 periods by
default; the underlying VCI response holds the full history, which the
library's own `_get_report(..., limit=...)` exposes.

    python -m research.fundamentals            # into the latest price snapshot
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.data import MIN_INTERVAL, _Throttle, latest_snapshot

log = logging.getLogger("research.fundamentals")
NET_INCOME_PARENT = "isa22"  # "Lợi nhuận của Cổ đông của Công ty mẹ"


def universe_members(since="2018-01-01"):
    """Every symbol in the liquidity universe at any month end since `since`."""
    from research import run_momentum as rm
    from research.strategies import Precomputed, UniverseRule, month_end_sessions, universe

    panel = rm.load_panel()
    rule = UniverseRule()
    pre = Precomputed(panel, rule)
    start = panel.dates.searchsorted(pd.Timestamp(since))
    members = set()
    for i in sorted(month_end_sessions(panel.dates)):
        if i >= start:
            members.update(universe(pre, rule, i))
    return sorted(members)


def _fetch_raw(symbol, report, throttle, attempts=4):
    from vnstock.explorer.vci.financial import Finance

    for attempt in range(attempts):
        throttle.wait()
        try:
            return Finance(symbol=symbol, period="quarter")._get_report(
                report, period="quarter", limit=400, mode="raw")
        except SystemExit as exc:
            log.warning("%s: rate limit reached (%s); waiting 65s", symbol, str(exc)[:80])
            time.sleep(65)
        except Exception as exc:
            log.warning("%s %s attempt %s failed: %s", symbol, report, attempt + 1, str(exc)[:160])
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{symbol} {report}: giving up")


def download(snapshot_dir=None, symbols=None):
    snapshot_dir = Path(snapshot_dir or latest_snapshot())
    out = snapshot_dir / "fundamentals"
    out.mkdir(exist_ok=True)
    symbols = symbols or universe_members()
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"failed": {}}
    throttle = _Throttle(MIN_INTERVAL)
    todo = [s for s in symbols if not (out / f"{s}.ratio.parquet").exists()]
    log.info("%s symbols, %s to fetch", len(symbols), len(todo))
    for n, symbol in enumerate(todo, 1):
        try:
            income = _fetch_raw(symbol, "income_statement", throttle)
            ratio = _fetch_raw(symbol, "ratio", throttle)
        except Exception as exc:
            manifest["failed"][symbol] = str(exc)[:300]
            continue
        income.to_parquet(out / f"{symbol}.income.parquet", index=False)
        ratio.to_parquet(out / f"{symbol}.ratio.parquet", index=False)
        manifest["failed"].pop(symbol, None)
        if n % 20 == 0 or n == len(todo):
            manifest.update(symbols=symbols, updated_at=datetime.now().isoformat(timespec="seconds"))
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
            log.info("%s/%s fetched (latest %s)", n, len(todo), symbol)
    return out


def load_fundamentals(snapshot_dir=None):
    """Return (earnings, caps) long frames.

    earnings: symbol, period_end, public_date, net_income (parent, quarterly VND)
    caps: symbol, period_end, market_cap (VND), shares
    """
    folder = Path(snapshot_dir or latest_snapshot()) / "fundamentals"
    earnings, caps = [], []
    for path in sorted(folder.glob("*.income.parquet")):
        symbol = path.name.split(".")[0]
        inc = pd.read_parquet(path)
        if inc.empty or NET_INCOME_PARENT not in inc.columns:
            continue
        inc = inc[inc["lengthReport"].between(1, 4)]
        earnings.append(pd.DataFrame({
            "symbol": symbol,
            "period_end": pd.PeriodIndex.from_fields(year=inc["yearReport"].astype(int), quarter=inc["lengthReport"].astype(int), freq="Q")
                                        .to_timestamp(how="end").normalize(),
            "public_date": pd.to_datetime(inc["publicDate"]).dt.normalize(),
            "net_income": pd.to_numeric(inc[NET_INCOME_PARENT], errors="coerce"),
        }))
        rpath = folder / f"{symbol}.ratio.parquet"
        if rpath.exists():
            rat = pd.read_parquet(rpath)
            rat = rat[rat["quarter"].between(1, 4)] if "quarter" in rat.columns else rat.iloc[0:0]
            if not rat.empty:
                caps.append(pd.DataFrame({
                    "symbol": symbol,
                    "period_end": pd.PeriodIndex.from_fields(year=rat["year"].astype(int), quarter=rat["quarter"].astype(int), freq="Q")
                                              .to_timestamp(how="end").normalize(),
                    "market_cap": pd.to_numeric(rat["marketCap"], errors="coerce"),
                    "shares": pd.to_numeric(rat["numberOfSharesMktCap"], errors="coerce"),
                }))
    earnings = pd.concat(earnings, ignore_index=True).dropna(subset=["public_date"]) if earnings else pd.DataFrame()
    caps = pd.concat(caps, ignore_index=True) if caps else pd.DataFrame()
    return (earnings.drop_duplicates(["symbol", "period_end"], keep="last"),
            caps.drop_duplicates(["symbol", "period_end"], keep="last"))


MAX_EARNINGS_AGE_DAYS = 200  # a TTM figure whose last quarter ended longer ago is stale


def earnings_yield_panel(panel, earnings, caps, max_age_days=MAX_EARNINGS_AGE_DAYS):
    """Daily E/P, TTM net income known on each date divided by market cap.

    TTM earnings on date t = sum of the latest 4 consecutive quarters whose
    announcement date is <= t. Market cap on t = market cap at the latest
    quarter end q <= t, scaled by the adjusted price move from q to t (exact
    when no new shares were issued in between). Non-positive market caps are
    treated as missing, and TTM earnings expire when the latest quarter in them
    ended more than `max_age_days` before t (the company stopped reporting).
    """
    close = panel.frame("last_close")
    ep = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    turnover_shares = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for symbol, e in earnings.groupby("symbol"):
        if symbol not in close.columns:
            continue
        e = e.sort_values("period_end")
        c = caps[caps["symbol"] == symbol].sort_values("period_end")
        if c.empty:
            continue
        # TTM sum only over 4 consecutive quarters, stamped at the last one's public date
        q = e.set_index("period_end")["net_income"]
        consecutive = q.index.to_period("Q").astype("int64").to_series(index=q.index).diff(3) == 3
        ttm = q.rolling(4).sum()[consecutive.values]
        public = e.set_index("period_end").loc[ttm.index, "public_date"].values
        known = pd.DataFrame({"ttm": ttm.values, "period_end": ttm.index}, index=public)
        known = known.groupby(level=0).last().sort_index()
        known_daily = known.reindex(close.index, method="ffill")
        age = close.index.to_series() - known_daily["period_end"]
        ttm_daily = known_daily["ttm"].where(age <= pd.Timedelta(days=max_age_days))
        # market cap at quarter end, rolled forward with the adjusted price
        cap_q = c.set_index("period_end")["market_cap"]
        cap_q = cap_q[cap_q > 0]
        px = close[symbol]
        px_q = px.reindex(cap_q.index, method="ffill")
        base = (cap_q / px_q).replace([np.inf, -np.inf], np.nan).dropna()
        cap_daily = base.reindex(close.index, method="ffill") * px
        ep[symbol] = ttm_daily / cap_daily
        turnover_shares[symbol] = c.set_index("period_end")["shares"].reindex(close.index, method="ffill")
    return ep, turnover_shares


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.environ.setdefault("VNSTOCK_TELEMETRY", "off")
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", help="comma-separated subset")
    args = parser.parse_args()
    print(download(symbols=args.symbols.split(",") if args.symbols else None))
