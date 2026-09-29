"""
Autonomous trading scheduler.

Run:
    python scheduler.py
    python scheduler.py prep
    python scheduler.py analysis
"""

import json
import logging
import os
import threading
import time
import hashlib
from datetime import date, datetime, timedelta

import schedule
from trading_calendar import is_trading_day as calendar_is_trading_day


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"scheduler_{date.today()}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scheduler")

STATE_FILE = os.path.join(BASE_DIR, "scheduler_state.json")
ANALYSIS_RESULTS_FILE = os.path.join(BASE_DIR, "analysis_results.json")
INTRADAY_ALERTS_FILE = os.path.join(BASE_DIR, "intraday_alerts.json")
REBACKTEST_CHECKPOINT = "rebacktest_checkpoint.json"
# Six years give ~50-80 trades per ticker, enough for config_review's evidence
# criteria (two years gave at most ~23).
REBACKTEST_YEARS = 6
REBACKTEST_CHUNK_SIZE = 5
INSTANCE_LOCK_FILE = os.path.join(BASE_DIR, "scheduler.pid.lock")
_instance_lock_handle = None


def ict_now():
    from trading_safety import VIETNAM_TZ

    return datetime.now(VIETNAM_TZ)


def ict_today():
    return ict_now().date()


def is_trading_day(day=None):
    return calendar_is_trading_day(day or ict_today())


def acquire_single_instance_lock():
    """
    Prevent two scheduler.py processes from running at once (e.g. a logon
    trigger and the 07:50 failsafe trigger firing close together). Holds the
    lock file open for the life of the process; the OS releases it automatically
    on exit or crash, so it can never go stale.
    """
    global _instance_lock_handle
    lock_f = open(INSTANCE_LOCK_FILE, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            lock_f.seek(0)
            msvcrt.locking(lock_f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_f.close()
        log.error("Another scheduler.py instance is already running (lock held on %s). Exiting.", INSTANCE_LOCK_FILE)
        raise SystemExit(1)
    _instance_lock_handle = lock_f  # keep a reference so the lock isn't GC'd/released early
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    temp_path = STATE_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, STATE_FILE)


def already_ran_today(task_name):
    state = load_state()
    return state.get(task_name) == ict_today().isoformat()


def mark_ran_today(task_name):
    state = load_state()
    state[task_name] = ict_today().isoformat()
    save_state(state)


def prefetch_stock_data(tickers, years=2):
    """Pre-warm stock data cache for tickers used during the day."""
    from data_fetcher import get_stock_data_cached

    log.info("Pre-warming cache for %s tickers...", len(tickers))
    for ticker in tickers:
        try:
            df = get_stock_data_cached(ticker, years=years, force_refresh=True)
            log.info("  %s: %s rows cached", ticker, len(df))
            time.sleep(2)
        except Exception as exc:
            log.warning("  %s: prefetch failed: %s", ticker, exc)


def sync_trading_calendar(max_age_days=6):
    """Refresh trading_calendar.json weekly; never lets a failure block the caller."""
    import json as _json

    from trading_calendar import CALENDAR_FILE

    try:
        generated = _json.loads(CALENDAR_FILE.read_text(encoding="utf-8")).get("generated_at")
        age = ict_now() - datetime.fromisoformat(generated)
        if age < timedelta(days=max_age_days):
            return None
    except Exception:
        pass  # missing or unreadable file: sync now
    try:
        from calendar_sync import sync

        result = sync()
        log.info("Trading calendar synced: %s", result)
        return result
    except Exception as exc:
        log.warning("Trading calendar sync failed: %s", exc)
        return None


def review_backtest_candidate():
    """Review a pending rebacktest candidate against config_review's criteria (never raises)."""
    try:
        from config_review import review_pending

        row = review_pending(BASE_DIR)
        if row:
            log.info("Config review: %s (%s qualified) %s", row["decision"], len(row["qualified"]), row["checks"])
        return row
    except Exception as exc:
        log.warning("Config review failed: %s", exc)
        return None


def task_morning_prep():
    """08:00 - clear cache, refresh external data, train missing EV-positive models."""
    sync_trading_calendar()
    review_backtest_candidate()
    if already_ran_today("morning_prep"):
        log.info("morning_prep already ran today, skipping")
        return

    log.info("=" * 50)
    failures = []
    log.info("TASK: Morning Prep")
    log.info("=" * 50)

    try:
        from data_fetcher import fetch_usdvnd, fetch_vix, fetch_vnindex
        from backtester import load_backtest_config_file

        fetch_usdvnd(years=6)
        log.info("USD/VND data refreshed")
        fetch_vix(years=6)
        log.info("VIX data refreshed")
        fetch_vnindex(years=6)
        log.info("VNIndex data refreshed")
        config = load_backtest_config_file()
        prefetch_stock_data(config.get("positive_ev_tickers", []), years=2)
    except Exception as exc:
        log.warning("External data refresh failed: %s", exc)
        failures.append(f"external data refresh: {exc}")

    try:
        from backtester import load_backtest_config_file
        from train_ensemble import train_all
        from train_lstm import get_data

        config = load_backtest_config_file()
        positive_tickers = config.get("positive_ev_tickers", [])
        for ticker in positive_tickers:
            ticker = str(ticker).upper()
            model_paths = [
                os.path.join(BASE_DIR, "lstm_models", f"{ticker}_xgb.pkl"),
                os.path.join(BASE_DIR, "lstm_models", f"{ticker}_lgbm.pkl"),
                os.path.join(BASE_DIR, "lstm_models", f"{ticker}_rf.pkl"),
            ]
            if all(os.path.exists(path) for path in model_paths):
                log.info("%s ensemble already exists, skipping", ticker)
                continue

            log.info("Training ensemble for %s...", ticker)
            try:
                df = get_data(ticker, years=6)
                train_all(ticker, df)
                log.info("%s ensemble trained OK", ticker)
                time.sleep(30)
            except Exception as exc:
                log.warning("%s train failed: %s", ticker, exc)
                failures.append(f"train {ticker}: {exc}")
    except Exception as exc:
        log.warning("Missing ensemble training failed: %s", exc)
        failures.append(f"ensemble setup: {exc}")

    try:
        from auto_trader import close_negative_ev_positions

        log.info("Checking for negative-EV positions to exit...")
        closed = close_negative_ev_positions()
        if closed:
            log.info("Exited negative-EV positions: %s", closed)
        else:
            log.info("No negative-EV positions to exit")
    except Exception as exc:
        log.warning("close_negative_ev_positions failed: %s", exc)
        failures.append(f"negative-EV close: {exc}")

    if failures:
        raise RuntimeError("Morning prep incomplete: " + "; ".join(failures))
    mark_ran_today("morning_prep")
    log.info("Morning prep DONE")


def _technical_snapshot(df):
    if df is None or len(df) < 50:
        raise ValueError("Not enough rows for technical snapshot")
    close = df["close"].astype(float)
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1]
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    macd = macd_line.iloc[-1]
    macd_signal = macd_line.ewm(span=9).mean().iloc[-1]
    volume_ma20 = df["volume"].astype(float).rolling(20).mean()
    volume_ratio = (df["volume"].astype(float) / (volume_ma20 + 1e-9)).iloc[-1]
    current_price = close.iloc[-1]

    confluence = 0
    if rsi < 35:
        confluence += 1
    if macd > macd_signal:
        confluence += 1
    if sma20 > sma50:
        confluence += 1
    if volume_ratio > 1.2:
        confluence += 1

    return {
        "price": float(current_price),
        "rsi": float(rsi),
        "macd_signal": "bullish" if macd > macd_signal else "bearish",
        "sma_cross": "golden" if sma20 > sma50 else "death",
        "confluence": int(confluence),
        "volume_ratio": float(volume_ratio),
    }


def _load_scan_watchlist():
    """
    Build the Two-Stage scan watchlist:
    1. Start with full training_watchlist.json (50 tickers).
    2. Fall back to positive_ev_tickers from backtest_config if watchlist file missing.
    3. Remove confirmed negative-EV tickers so Stage 1 doesn't waste time on them.
    """
    from backtester import load_backtest_config_file

    config = load_backtest_config_file()
    negative_ev = set(str(t).upper() for t in config.get("negative_ev_tickers", []))

    watchlist_path = os.path.join(BASE_DIR, "training_watchlist.json")
    try:
        with open(watchlist_path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            raw = list(raw.keys())
        full = [str(t).upper() for t in raw if str(t).strip()]
    except Exception:
        full = []

    if not full:
        full = config.get("positive_ev_tickers", ["MBB", "ACB", "VCB", "TCB"])

    return [t for t in full if t not in negative_ev]


def task_market_analysis():
    """08:30 - two-stage scan full watchlist, save analysis_results.json."""
    if not is_trading_day():
        log.info("Not a trading day, skipping market analysis")
        mark_ran_today("market_analysis")
        return
    if already_ran_today("market_analysis"):
        log.info("market_analysis already ran today, skipping")
        return

    log.info("=" * 50)
    log.info("TASK: Market Analysis")
    log.info("=" * 50)

    try:
        from auto_trader import two_stage_scan

        watchlist = _load_scan_watchlist()
        log.info("Scan watchlist: %s tickers", len(watchlist))
        stage2_results, tradeable = two_stage_scan(
            watchlist=watchlist,
            top_n_stage1=10,
            top_n_final=3,
            use_llm=True,
            use_ensemble=True,
        )
    except Exception as exc:
        log.error("Market analysis failed: %s", exc)
        raise

    tradeable_tickers = [row.get("ticker") for row in tradeable if row.get("ticker")]

    with open(ANALYSIS_RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "date": ict_today().isoformat(),
                "method": "two_stage",
                "prediction_horizon_days": 3,
                "prediction_horizon_sessions": 6,
                "prediction_horizon_sessions_min": 5,
                "prediction_horizon_sessions_max": 6,
                "tradeable_meaning": "5-6 session horizon candidates",
                "stage2_results": stage2_results,
                "tradeable": tradeable,
                "tradeable_tickers": tradeable_tickers,
                "eligible_tickers": tradeable_tickers,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    mark_ran_today("market_analysis")
    log.info(
        "Market analysis DONE - %s stage2 candidates, %s tradeable (5-6 session horizon)",
        len(stage2_results),
        len(tradeable),
    )


def task_auto_trade():
    """09:20 - choose top candidates and execute paper trades when supported."""
    if not is_trading_day():
        log.info("Not a trading day, skipping auto trade")
        mark_ran_today("auto_trade")
        return {"status": "blocked", "reason": "not a verified trading day"}
    if already_ran_today("auto_trade"):
        log.info("auto_trade already ran today, skipping")
        return
    # GitHub Actions cron can start hours late (2026-09-29: the 09:20 trade run
    # started at 15:42). Never place orders outside the continuous session.
    from watchdog import trade_session_open

    now = ict_now()
    if not trade_session_open(now):
        if now.time() > datetime.strptime("14:25", "%H:%M").time():
            mark_ran_today("auto_trade")  # the session is over; do not retry today
        log.warning("Auto trade skipped: %s is outside the trading session", now.strftime("%H:%M"))
        return {"status": "blocked", "reason": "outside trading session (late run)"}

    log.info("=" * 50)
    log.info("TASK: Auto Trade")
    log.info("=" * 50)

    from self_healing import run_self_healing
    from trading_safety import operational_gate

    operational, gate_reason = operational_gate(BASE_DIR)
    if not operational:
        log.warning("Auto trade BLOCKED by operational gate: %s", gate_reason)
        return {"status": "blocked", "reason": gate_reason}
    healing = run_self_healing(BASE_DIR, repair=True)
    if not healing["trading_allowed"]:
        log.error("Auto trade BLOCKED by self-healing: %s", healing.get("critical"))
        return {"status": "blocked", "reason": "self-healing safety gate: " + "; ".join(healing.get("critical") or [])}
    if healing["status"] == "healed":
        log.warning("Self-healing repaired state before trading: %s", healing.get("actions"))

    try:
        if not os.path.exists(ANALYSIS_RESULTS_FILE):
            raise FileNotFoundError("No analysis_results.json found")
        with open(ANALYSIS_RESULTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != ict_today().isoformat():
            raise RuntimeError("Analysis results are not from today")

        tradeable = data.get("tradeable") or []
        if not tradeable:
            log.info("No buy candidates today")
            mark_ran_today("auto_trade")
            return

        from auto_trader import execute_paper_trade

        failures = []
        for trade in tradeable:
            try:
                log.info("  Executing paper BUY: %s @ %s", trade["ticker"], trade.get("price"))
                confidence = int(float((trade.get("llm") or {}).get("confidence", 50) if trade.get("llm") else 50))
                canonical_signal = json.dumps(
                    {"date": data["date"], "trade": trade},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                )
                signal_id = hashlib.sha256(canonical_signal.encode("utf-8")).hexdigest()[:20]
                outcome = None
                for attempt in range(2):
                    outcome = execute_paper_trade(
                        ticker=trade["ticker"],
                        action="BUY",
                        price=trade.get("price"),
                        confidence=confidence,
                        source="two_stage_scheduler",
                        signal_id=signal_id,
                        run_id=os.getenv("GITHUB_RUN_ID", "local"),
                        trade_date=data["date"],
                    )
                    if outcome["status"] != "transient":
                        break
                    log.warning(
                        "  %s transient attempt %s/2: %s",
                        trade["ticker"], attempt + 1, outcome["detail"],
                    )
                    if attempt + 1 < 2:
                        from runtime_reliability import exponential_backoff

                        delay = exponential_backoff(attempt, base_delay=1.0, max_delay=4.0)
                        log.info("  %s retrying after %.2fs backoff", trade["ticker"], delay)
                        time.sleep(delay)
                if outcome["status"] in {"failed", "transient"}:
                    failures.append(f"{trade['ticker']}: {outcome['detail']}")
                elif outcome["status"] in {"skipped", "duplicate", "blocked"}:
                    log.info("  %s %s: %s", trade["ticker"], outcome["status"], outcome["detail"])
            except Exception as exc:
                log.warning("  %s paper trade failed: %s", trade["ticker"], exc)
                failures.append(f"{trade['ticker']}: {exc}")
        if failures:
            raise RuntimeError("paper trade failures: " + "; ".join(failures))
    except Exception as exc:
        log.error("Auto trade failed: %s", exc)
        raise

    mark_ran_today("auto_trade")
    log.info("Auto trade DONE")


def load_portfolio_direct():
    """Load the unified paper portfolio JSON directly."""
    try:
        from auto_trader import _safe_read_portfolio

        return _safe_read_portfolio()
    except Exception:
        return {"cash": 100_000_000, "positions": {}}


def _save_portfolio_direct(portfolio):
    from auto_trader import _safe_write_portfolio

    _safe_write_portfolio(portfolio)


def _close_position_direct(portfolio, ticker, price, reason, market_df=None):
    from self_healing import trading_is_allowed

    if not trading_is_allowed(BASE_DIR, exit_order=True):
        log.error("Direct close blocked by trading safety gate for %s", ticker)
        return False
    from auto_trader import fill_price_block_reason, log_trade, save_portfolio_and_trades, trade_costs

    positions = portfolio.get("positions", {}) or {}
    pos = positions.get(ticker)
    if not pos:
        return False
    blocked = fill_price_block_reason(ticker, price, df=market_df)
    if blocked:
        log.error("Direct close of %s (%s) blocked by price gate: %s", ticker, reason, blocked)
        return False

    qty = float(pos.get("qty", 0) or 0)
    avg_price = float(pos.get("avg_price", price))
    proceeds = qty * float(price)
    costs = trade_costs("SELL", proceeds)
    pnl = (float(price) - avg_price) * qty - costs["fees"] - float(pos.get("entry_fees", 0) or 0)

    portfolio["cash"] = float(portfolio.get("cash", 0)) + proceeds - costs["fees"]
    positions.pop(ticker, None)
    portfolio["positions"] = positions

    trades_path = os.path.join(BASE_DIR, "paper_trades.json")
    try:
        with open(trades_path, encoding="utf-8") as f:
            trades = json.load(f)
        if not isinstance(trades, list):
            trades = []
    except Exception:
        trades = []

    log_trade(trades, ticker, "SELL", qty, price, reason, pnl=pnl, **costs)
    save_portfolio_and_trades(portfolio, trades, operation="scheduled_close")
    from notify import notify_trade
    notify_trade(ticker, "SELL", qty, price, pnl=pnl)
    log.info("  Closed %s (%s): %s cp @ %.0f = %.0f VND", ticker, reason, qty, price, proceeds)
    return True


def _is_plausible_price(price, reference_price, min_ratio=0.5, max_ratio=2.0):
    """
    Guard against bad fallback-source quotes (e.g. MSN returning a price in a
    different unit scale than VCI/TCBS, or a garbage tick) before that price is
    ever used to trigger a stop-loss/target close. VN exchange daily price bands
    are ~7%, so a fetched price outside [0.5x, 2x] of the position's own
    entry/stop/target range can never be a real intraday move - only bad data.
    """
    if price is None or reference_price is None or reference_price <= 0:
        return False
    ratio = price / reference_price
    return min_ratio <= ratio <= max_ratio


def task_intraday_monitor():
    """
    Run during trading hours and close positions immediately when stop/target hits.
    """
    if not is_trading_day():
        return

    now = ict_now()
    hour = now.hour + now.minute / 60.0
    if not (9.0 <= hour <= 14.85):
        return

    log.info("Intraday monitor check...")

    try:
        from auto_trader import position_stop_loss, set_position_stop_loss
        from data_fetcher import get_stock_data_cached

        portfolio = load_portfolio_direct()
        positions = portfolio.get("positions", {}) or {}
        if not positions:
            return

        updated = False
        alerts = []

        for ticker, pos in list(positions.items()):
            try:
                df = get_stock_data_cached(ticker, years=0.02, force_refresh=True)
                if df is None or len(df) == 0:
                    continue

                current_price = float(df["close"].iloc[-1])
                entry_price = float(pos.get("avg_price", current_price) or current_price or 0)
                stop_loss = position_stop_loss(pos)
                target = float(pos.get("target_price", 0) or 0)
                atr = float(pos.get("atr", 0) or (entry_price * 0.02 if entry_price > 0 else 0))
                qty = float(pos.get("qty", 0) or 0)

                if not _is_plausible_price(current_price, entry_price):
                    log.warning(
                        "  %s: implausible price %.2f vs entry %.2f (bad/mis-scaled source data?), skipping this check",
                        ticker, current_price, entry_price,
                    )
                    continue

                pos["current_price"] = current_price
                pos["market_value"] = round(current_price * qty, 2)
                pos["unrealized_pnl"] = round((current_price - entry_price) * qty, 2)
                pos["pnl_pct"] = round((current_price / entry_price - 1) * 100, 4) if entry_price > 0 else 0.0
                updated = True

                if stop_loss > 0 and current_price <= stop_loss:
                    log.warning("  STOP LOSS HIT: %s @ %.1f (stop=%.1f)", ticker, current_price, stop_loss)
                    _close_position_direct(portfolio, ticker, current_price, "stop_loss_intraday", market_df=df)
                    alerts.append({"time": now.isoformat(), "message": f"🔴 {ticker}: STOP LOSS @ {current_price:,.1f}"})
                    updated = True
                    continue

                if target > 0 and current_price >= target:
                    log.info("  TARGET HIT: %s @ %.1f (target=%.1f)", ticker, current_price, target)
                    _close_position_direct(portfolio, ticker, current_price, "target_intraday", market_df=df)
                    alerts.append({"time": now.isoformat(), "message": f"🟢 {ticker}: TARGET @ {current_price:,.1f}"})
                    updated = True
                    continue

                if atr > 0:
                    if current_price >= entry_price + atr:
                        new_stop = max(stop_loss, entry_price)
                        if new_stop > stop_loss:
                            set_position_stop_loss(pos, new_stop)
                            stop_loss = new_stop
                            updated = True
                            log.info("  Trailing stop %s -> break-even %.1f", ticker, new_stop)

                    if current_price >= entry_price + 2 * atr:
                        new_stop = max(stop_loss, entry_price + atr)
                        if new_stop > stop_loss:
                            set_position_stop_loss(pos, new_stop)
                            stop_loss = new_stop
                            updated = True
                            log.info("  Trailing stop %s -> +1ATR %.1f", ticker, new_stop)

                if stop_loss > 0:
                    distance_pct = ((current_price - stop_loss) / current_price) * 100 if current_price > 0 else 0
                    if distance_pct <= 2.0:
                        log.warning("  %s near stop: %.1f stop %.1f (%.1f%%)", ticker, current_price, stop_loss, distance_pct)
                        alerts.append({"time": now.isoformat(), "message": f"🟠 {ticker}: gần stop ({distance_pct:.1f}%)"})

                if target > 0:
                    distance_to_target = ((target - current_price) / current_price) * 100 if current_price > 0 else 0
                    if distance_to_target <= 2.0:
                        log.info("  %s near target: %.1f target %.1f (%.1f%%)", ticker, current_price, target, distance_to_target)
                        alerts.append({"time": now.isoformat(), "message": f"🎯 {ticker}: gần target ({distance_to_target:.1f}%)"})
            except Exception as exc:
                log.warning("  Intraday %s: %s", ticker, exc)

        if updated:
            portfolio["updated_at"] = ict_now().isoformat()
            _save_portfolio_direct(portfolio)
        if alerts:
            existing = []
            try:
                if os.path.exists(INTRADAY_ALERTS_FILE):
                    with open(INTRADAY_ALERTS_FILE, encoding="utf-8") as f:
                        existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
            existing.extend(alerts)
            existing = existing[-50:]
            with open(INTRADAY_ALERTS_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

        log.info("Intraday check done: %s positions, %s alerts", len(positions), len(alerts))
    except Exception as exc:
        log.error("Intraday monitor failed: %s", exc)


def start_intraday_monitor():
    """
    Run intraday monitor in a daemon thread every 5 minutes.
    """
    def run():
        while True:
            try:
                task_intraday_monitor()
            except Exception as exc:
                log.error("Intraday monitor thread error: %s", exc)
            time.sleep(300)

    thread = threading.Thread(target=run, daemon=True, name="intraday_monitor")
    thread.start()
    log.info("Intraday monitor started (every 5 min, 9:00-14:50)")
    return thread


class IncompleteMarketData(ConnectionError):
    """A transient provider response that cannot safely value a position."""


def fetch_eod_market_data(fetcher, sleeper=None, random_fn=None):
    from runtime_reliability import is_transient_network_error, retry_transient

    def validated_fetch():
        value = fetcher()
        if value is None or len(value) < 2:
            raise IncompleteMarketData("insufficient market data for EOD valuation")
        return value

    return retry_transient(
        validated_fetch, attempts=3, base_delay=1.0, max_delay=4.0,
        is_transient=is_transient_network_error, sleeper=sleeper, random_fn=random_fn,
    )


def run_etf_core():
    """Advance the ETF core paper sleeve; isolated so it never breaks the EOD task."""
    from system_status import update_task

    update_task(BASE_DIR, "etf_core", "running")
    try:
        from etf_core import run_daily

        info = run_daily()
    except Exception as exc:
        log.error("ETF core failed: %s", exc)
        update_task(BASE_DIR, "etf_core", "failed", error=exc)
        try:
            from notify import notify_task_failed

            notify_task_failed("etf_core", exc, f"etf_core:{ict_today().isoformat()}")
        except Exception:
            pass
        return None
    update_task(BASE_DIR, "etf_core", "success")
    log.info("ETF core: signal=%s equity=%s pending=%s", info["signal"], info["equity"], info["pending"])
    return info


def task_eod_update():
    """15:00 - update trailing stops, exit stopped positions, and refresh PnL."""
    if not is_trading_day():
        log.info("Not a trading day, skipping EOD update")
        mark_ran_today("eod_update")
        return
    # Has its own per-session idempotency, so it also runs when the LLM EOD already did.
    run_etf_core()
    if already_ran_today("eod_update"):
        log.info("eod_update already ran today, skipping")
        return

    log.info("=" * 50)
    log.info("TASK: EOD Update")
    log.info("=" * 50)
    failures = []
    try:
        from auto_trader import position_stop_loss, set_position_stop_loss
        from data_fetcher import get_stock_data_cached

        portfolio = load_portfolio_direct()
        positions = portfolio.get("positions", {}) or {}
        updated = False
        closed = 0

        for ticker, pos in list(positions.items()):
            try:
                df = fetch_eod_market_data(lambda: get_stock_data_cached(ticker, years=0.1))

                current_price = float(df["close"].iloc[-1])
                entry_price = float(pos.get("avg_price", current_price))
                stop_loss = position_stop_loss(pos) or (entry_price * 0.95)
                target = float(pos.get("target_price", 0) or (entry_price * 1.10))
                atr = float(pos.get("atr", 0) or 0)
                qty = float(pos.get("qty", 0) or 0)

                if not _is_plausible_price(current_price, entry_price):
                    log.warning(
                        "  %s: implausible price %.2f vs entry %.2f (bad/mis-scaled source data?), skipping EOD update",
                        ticker, current_price, entry_price,
                    )
                    continue

                pos["current_price"] = current_price
                pos["market_value"] = round(current_price * qty, 2)
                pos["unrealized_pnl"] = round((current_price - entry_price) * qty, 2)
                pos["pnl_pct"] = round((current_price / entry_price - 1) * 100, 4) if entry_price > 0 else 0.0
                pos["hold_days"] = int(pos.get("hold_days", 0)) + 1
                updated = True

                if atr > 0 and current_price >= entry_price + atr:
                    new_stop = max(stop_loss, entry_price)
                    if new_stop > stop_loss:
                        set_position_stop_loss(pos, new_stop)
                        stop_loss = new_stop
                        updated = True
                        log.info("  %s: trailing stop -> break-even %.0f", ticker, new_stop)

                if atr > 0 and current_price >= entry_price + 2 * atr:
                    new_stop = max(stop_loss, entry_price + atr)
                    if new_stop > stop_loss:
                        set_position_stop_loss(pos, new_stop)
                        stop_loss = new_stop
                        updated = True
                        log.info("  %s: trailing stop -> +1ATR %.0f", ticker, new_stop)

                if current_price <= stop_loss:
                    if _close_position_direct(portfolio, ticker, current_price, "stop_loss", market_df=df):
                        closed += 1
                        updated = True
                        continue

                if current_price >= target:
                    if _close_position_direct(portfolio, ticker, current_price, "target", market_df=df):
                        closed += 1
                        updated = True
                        continue

                if int(pos.get("hold_days", 0)) >= 15:
                    if _close_position_direct(portfolio, ticker, current_price, "timeout", market_df=df):
                        closed += 1
                        updated = True
                        continue
            except Exception as exc:
                log.warning("  EOD %s: %s", ticker, exc)
                failures.append(f"{ticker}: {exc}")

        if updated:
            portfolio["updated_at"] = ict_now().isoformat()
            _save_portfolio_direct(portfolio)
        log.info("Closed %s positions", closed)
    except Exception as exc:
        log.warning("EOD update failed: %s", exc)
        raise

    if failures:
        raise RuntimeError("EOD update incomplete: " + "; ".join(failures))
    from portfolio_snapshots import record_snapshot
    record_snapshot(BASE_DIR, portfolio, ict_today(), recorded_at=ict_now())
    mark_ran_today("eod_update")
    log.info("EOD update DONE")


def task_daily_learning():
    """16:00 - resolve predictions, refresh accuracy stats, and retrain if needed."""
    if already_ran_today("daily_learning"):
        log.info("daily_learning already ran today, skipping")
        return

    log.info("=" * 50)
    log.info("TASK: Daily Learning")
    log.info("=" * 50)
    try:
        from backtester import load_backtest_config_file
        from learning_engine import (
            calculate_accuracy_stats,
            generate_performance_report,
            resolve_predictions,
            retrain_if_needed,
            retrain_lstm_if_needed,
        )

        resolved = resolve_predictions()
        log.info("Resolved %s predictions", resolved)

        stats = calculate_accuracy_stats()
        overall = stats.get("_overall", {})
        log.info(
            "Overall accuracy: %s (%s predictions)",
            f"{overall.get('accuracy', 0):.0%}",
            overall.get("total", 0),
        )

        config = load_backtest_config_file()
        positive_tickers = config.get("positive_ev_tickers", [])
        retrained = retrain_if_needed(positive_tickers)
        if retrained:
            log.info("Retrained (ensemble): %s", retrained)

        retrained_lstm = retrain_lstm_if_needed(positive_tickers, max_per_day=3)
        if retrained_lstm:
            log.info("Retrained (LSTM): %s", retrained_lstm)

        generate_performance_report()

        try:
            from debate_agents import DEBATE_LOG_FILE, resolve_debate
            from data_fetcher import get_stock_data_cached

            debate_path = str(DEBATE_LOG_FILE)
            if os.path.exists(debate_path):
                with open(debate_path, encoding="utf-8") as f:
                    debate_logs = json.load(f)
                cutoff = ict_today() - timedelta(days=3)
                for entry in debate_logs if isinstance(debate_logs, list) else []:
                    if entry.get("outcome") is not None or not entry.get("final_decision"):
                        continue
                    try:
                        entry_date = datetime.strptime(str(entry.get("date", "")), "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if entry_date > cutoff:
                        continue
                    ticker = str(entry.get("ticker", "")).upper()
                    if not ticker:
                        continue
                    df = get_stock_data_cached(ticker, years=0.1)
                    if df is None or len(df) == 0:
                        continue
                    current_price = float(df["close"].iloc[-1])
                    entry_price = float((entry.get("market_data") or {}).get("price") or current_price)
                    updated = resolve_debate(ticker, current_price, entry_price)
                    if updated:
                        log.info("Resolved %s debate(s) for %s", updated, ticker)
        except Exception as exc:
            log.warning("Debate resolve failed: %s", exc)
            raise
    except Exception as exc:
        log.error("Daily learning failed: %s", exc)
        raise

    mark_ran_today("daily_learning")
    log.info("Daily learning DONE")


def task_weekly_rebacktest(force=False):
    """Monday 07:00 - rebacktest the watchlist and stage a promotion candidate."""
    today = ict_today()
    if today.weekday() != 0 and not force:
        return
    state = load_state()
    if state.get("weekly_rebacktest") == today.isoformat():
        return

    log.info("=" * 50)
    log.info("TASK: Weekly Rebacktest")
    log.info("=" * 50)
    try:
        try:
            from backtester_pro import run_portfolio_backtest_pro

            runner = run_portfolio_backtest_pro
            runner_kwargs = {"years": REBACKTEST_YEARS, "optimize": True, "source": "scheduler", "write_output": False}
        except Exception as exc:
            log.warning("backtester_pro unavailable, falling back to legacy backtester: %s", exc)
            from backtester import run_portfolio_backtest

            runner = run_portfolio_backtest
            runner_kwargs = {"years": REBACKTEST_YEARS, "atr_stop": 1.0, "atr_target": 2.0,
                             "source": "scheduler", "write_output": False}

        watchlist_path = os.path.join(BASE_DIR, "training_watchlist.json")
        with open(watchlist_path, encoding="utf-8") as f:
            watchlist = json.load(f)
        if isinstance(watchlist, dict):
            watchlist = list(watchlist.keys())
        checkpoint_path = os.path.join(BASE_DIR, REBACKTEST_CHECKPOINT)
        fingerprint = hashlib.sha256(json.dumps(watchlist, sort_keys=True).encode("utf-8")).hexdigest()
        try:
            with open(checkpoint_path, encoding="utf-8") as handle:
                checkpoint = json.load(handle)
            if checkpoint.get("date") != today.isoformat() or checkpoint.get("watchlist_hash") != fingerprint:
                checkpoint = {}
        except (FileNotFoundError, OSError, ValueError, TypeError):
            checkpoint = {}
        completed = list(checkpoint.get("completed") or [])
        accumulated = dict(checkpoint.get("results") or {})
        optimal_params = dict(checkpoint.get("optimal_params") or {})
        pending = [ticker for ticker in watchlist if ticker not in completed]
        chunk = pending[:REBACKTEST_CHUNK_SIZE]
        log.info("Re-backtesting chunk of %s (%s/%s already complete)...", len(chunk), len(completed), len(watchlist))
        results = runner(chunk, **runner_kwargs)
        if not isinstance(results, dict) or not results:
            raise RuntimeError("rebacktest produced no result set; preserving previous configuration")
        accumulated.update(results)
        for ticker, result in results.items():
            params = result.get("optimal_params") if isinstance(result, dict) else None
            if params:
                optimal_params[ticker] = params
            elif "error" not in result:
                optimal_params[ticker] = {"atr_stop": 1.0, "atr_target": 2.0, "confluence_min": 4}
        completed.extend(ticker for ticker in chunk if ticker not in completed)
        if len(completed) < len(watchlist):
            payload = {"date": today.isoformat(), "watchlist_hash": fingerprint,
                       "completed": completed, "results": accumulated, "optimal_params": optimal_params}
            temporary = checkpoint_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, checkpoint_path)
            log.info("Rebacktest checkpoint saved: %s/%s", len(completed), len(watchlist))
            return {"status": "deferred", "reason": f"checkpointed {len(completed)}/{len(watchlist)} tickers"}
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        positive_ev = [ticker for ticker, result in accumulated.items()
                       if result.get("expectancy_pct", -999) > 0 and result.get("trades", 0) >= 5]
        positive_ev.sort(key=lambda ticker: accumulated[ticker].get("expectancy_pct", 0), reverse=True)
        from backtester_pro import finite_metric_record
        from config_store import load_active
        final_config = dict(load_active(BASE_DIR))
        final_config.update({
            "backtest_universe": [str(ticker).upper() for ticker in watchlist],
            "positive_ev_tickers": positive_ev,
            "negative_ev_tickers": sorted(set(watchlist) - set(positive_ev)),
            "ev_data": {ticker: finite_metric_record(result) if "error" not in result else {
                "ev": 0.0, "win_rate": 0.0, "trades": 0, "sharpe": 0.0,
                "profit_factor": 0.0, "status": "error",
            } for ticker, result in accumulated.items()},
            "optimal_params_per_ticker": optimal_params,
            "last_updated": today.isoformat(), "backtester": "backtesting.py",
        })
        from config_store import write_candidate
        candidate = write_candidate("scheduler", final_config, base_dir=BASE_DIR)
        if candidate["status"] != "valid":
            raise RuntimeError("rebacktest candidate is invalid: " + "; ".join(candidate["validation_errors"]))
        review_backtest_candidate()
        log.info("Weekly rebacktest DONE")
    except Exception as exc:
        log.error("Weekly rebacktest failed: %s", exc)
        raise

    state["weekly_rebacktest"] = today.isoformat()
    save_state(state)


def setup_schedule():
    schedule.every().day.at("08:00").do(task_morning_prep)
    schedule.every().day.at("08:30").do(task_market_analysis)
    schedule.every().day.at("09:20").do(task_auto_trade)
    schedule.every().day.at("15:00").do(task_eod_update)
    schedule.every().day.at("16:00").do(task_daily_learning)
    schedule.every().monday.at("07:00").do(task_weekly_rebacktest, force=False)

    log.info("Schedule registered:")
    log.info("  08:00 Morning prep")
    log.info("  08:30 Market analysis")
    log.info("  09:20 Auto trade")
    log.info("  15:00 EOD update")
    log.info("  16:00 Daily learning")
    log.info("  Mon 07:00 Weekly rebacktest")


def run_now(task_name=None):
    def run_heal():
        report = __import__("self_healing").run_self_healing(BASE_DIR, repair=True)
        if not report["trading_allowed"]:
            critical = [str(item) for item in report.get("critical") or []]
            non_switch_critical = [item for item in critical if not item.lower().startswith("trading kill switch:")]
            if non_switch_critical:
                raise RuntimeError("self-healing found critical state: " + "; ".join(critical))
            inhibitors = [str(item) for item in report.get("inhibitors") or []]
            if not inhibitors and critical:
                # Backward compatibility while old state reports age out.
                reason = "; ".join(critical)
                return {"status": "blocked", "reason": reason, "report": report}
            log.info("Self-healing healthy; trading remains operationally blocked: %s", "; ".join(inhibitors))
            return report
        return report

    tasks = {
        "prep": task_morning_prep,
        "analysis": task_market_analysis,
        "trade": task_auto_trade,
        "eod": task_eod_update,
        "learning": task_daily_learning,
        "rebacktest": lambda: task_weekly_rebacktest(force=True),
        "heal": run_heal,
        "calendar": lambda: sync_trading_calendar(max_age_days=0),
        "review-config": review_backtest_candidate,
    }
    if task_name in tasks:
        log.info("Running %s NOW...", task_name)
        from self_healing import snapshot_state
        from runtime_reliability import is_transient_network_error, retry_transient
        from system_status import update_task

        snapshot_state(BASE_DIR, task_name)
        update_task(BASE_DIR, task_name, "running")
        try:
            result = retry_transient(
                tasks[task_name], attempts=3, base_delay=2, max_delay=30,
                is_transient=is_transient_network_error,
            )
        except Exception as exc:
            update_task(BASE_DIR, task_name, "failed", error=exc)
            raise
        if isinstance(result, dict) and result.get("status") == "blocked":
            update_task(BASE_DIR, task_name, "blocked", error=result.get("reason"))
            from notify import notify_task_blocked
            notify_task_blocked(task_name, result.get("reason") or "blocked by safety gate")
        elif isinstance(result, dict) and result.get("status") == "deferred":
            update_task(BASE_DIR, task_name, "deferred", error=result.get("reason"))
        else:
            update_task(BASE_DIR, task_name, "success")
            if task_name == "eod":
                from notify import notify_eod
                notify_eod(BASE_DIR)
        return result
    else:
        raise ValueError(f"Unknown task {task_name!r}; available tasks: {list(tasks.keys())}")


def catch_up_missed_tasks():
    now = ict_now()
    today = ict_today()
    current_hour = now.hour + now.minute / 60.0
    if not is_trading_day():
        log.info("Not a trading day - no catch-up needed")
        return

    log.info("Checking for missed tasks...")
    missed = []
    task_schedule = [
        ("morning_prep", "morning_prep", 8.0, task_morning_prep),
        ("market_analysis", "market_analysis", 8.5, task_market_analysis),
        ("auto_trade", "auto_trade", 9 + 20 / 60, task_auto_trade),
        ("eod_update", "eod_update", 15.0, task_eod_update),
        ("daily_learning", "daily_learning", 16.0, task_daily_learning),
    ]
    for task_name, state_key, cutoff_hour, task_fn in task_schedule:
        if already_ran_today(state_key) or current_hour < cutoff_hour:
            continue
        if task_name == "auto_trade" and current_hour > 11.0:
            log.info("  auto_trade missed and too late after 11:00; marking skipped")
            mark_ran_today(state_key)
            continue
        missed.append((task_name, task_fn))

    if not missed:
        log.info("No missed tasks - all up to date")
        return

    log.info("Missed tasks to catch up: %s", [item[0] for item in missed])
    for task_name, task_fn in missed:
        log.info("  Running catch-up: %s", task_name)
        try:
            task_fn()
        except Exception as exc:
            log.error("  Catch-up %s failed: %s", task_name, exc)


def main():
    import sys

    log.info("Autonomous Trading Scheduler starting...")
    log.info("Time: %s", ict_now().strftime("%Y-%m-%d %H:%M %Z"))
    if len(sys.argv) > 1:
        run_now(sys.argv[1])
        return

    acquire_single_instance_lock()
    catch_up_missed_tasks()
    setup_schedule()
    start_intraday_monitor()
    log.info("Intraday monitor running in background")
    log.info("Scheduler running. Press Ctrl+C to stop.")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
