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
from datetime import date, datetime, timedelta

import schedule


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
INSTANCE_LOCK_FILE = os.path.join(BASE_DIR, "scheduler.pid.lock")
_instance_lock_handle = None


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
VN_HOLIDAYS_2026 = {
    "2026-01-01",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-02-21",
    "2026-04-30",
    "2026-05-01",
    "2026-09-02",
}


def is_trading_day():
    today = date.today()
    if today.weekday() >= 5:
        return False
    if today.isoformat() in VN_HOLIDAYS_2026:
        return False
    return True


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def already_ran_today(task_name):
    state = load_state()
    return state.get(task_name) == date.today().isoformat()


def mark_ran_today(task_name):
    state = load_state()
    state[task_name] = date.today().isoformat()
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


def task_morning_prep():
    """08:00 - clear cache, refresh external data, train missing EV-positive models."""
    if already_ran_today("morning_prep"):
        log.info("morning_prep already ran today, skipping")
        return

    log.info("=" * 50)
    log.info("TASK: Morning Prep")
    log.info("=" * 50)

    try:
        cache_file = os.path.join(BASE_DIR, "llm_key_cache.json")
        if os.path.exists(cache_file):
            os.remove(cache_file)
            log.info("LLM key cache cleared")
    except Exception as exc:
        log.warning("Clear LLM cache failed: %s", exc)

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
    except Exception as exc:
        log.warning("Missing ensemble training failed: %s", exc)

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
        stage2_results = []
        tradeable = []

    tradeable_tickers = [row.get("ticker") for row in tradeable if row.get("ticker")]

    with open(ANALYSIS_RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "date": date.today().isoformat(),
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
    """09:00 - choose top candidates and execute paper trades when supported."""
    if not is_trading_day():
        log.info("Not a trading day, skipping auto trade")
        mark_ran_today("auto_trade")
        return
    if already_ran_today("auto_trade"):
        log.info("auto_trade already ran today, skipping")
        return

    log.info("=" * 50)
    log.info("TASK: Auto Trade")
    log.info("=" * 50)

    try:
        if not os.path.exists(ANALYSIS_RESULTS_FILE):
            log.warning("No analysis_results.json found, skipping trade")
            return
        with open(ANALYSIS_RESULTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != date.today().isoformat():
            log.warning("Analysis results are not from today, skipping")
            return

        tradeable = data.get("tradeable") or []
        if not tradeable:
            log.info("No buy candidates today")
            mark_ran_today("auto_trade")
            return

        from auto_trader import execute_paper_trade

        for trade in tradeable:
            try:
                log.info("  Executing paper BUY: %s @ %s", trade["ticker"], trade.get("price"))
                confidence = int(float((trade.get("llm") or {}).get("confidence", 50) if trade.get("llm") else 50))
                execute_paper_trade(
                    ticker=trade["ticker"],
                    action="BUY",
                    price=trade.get("price"),
                    confidence=confidence,
                    source="two_stage_scheduler",
                )
            except Exception as exc:
                log.warning("  %s paper trade failed: %s", trade["ticker"], exc)
    except Exception as exc:
        log.error("Auto trade failed: %s", exc)

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


def _close_position_direct(portfolio, ticker, price, reason):
    from auto_trader import log_trade, save_trades

    positions = portfolio.get("positions", {}) or {}
    pos = positions.get(ticker)
    if not pos:
        return False

    qty = int(pos.get("qty", 0))
    avg_price = float(pos.get("avg_price", price))
    proceeds = qty * float(price)
    pnl = (float(price) - avg_price) * qty

    portfolio["cash"] = float(portfolio.get("cash", 0)) + proceeds
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

    log_trade(trades, ticker, "SELL", qty, price, reason, pnl=pnl)
    save_trades(trades)
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

    now = datetime.now()
    hour = now.hour + now.minute / 60.0
    if not (9.0 <= hour <= 14.85):
        return

    log.info("Intraday monitor check...")

    try:
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
                stop_loss = float(pos.get("stop_loss", 0) or 0)
                target = float(pos.get("target_price", 0) or 0)
                atr = float(pos.get("atr", 0) or (entry_price * 0.02 if entry_price > 0 else 0))
                qty = int(pos.get("qty", 0) or 0)

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
                    _close_position_direct(portfolio, ticker, current_price, "stop_loss_intraday")
                    alerts.append({"time": now.isoformat(), "message": f"🔴 {ticker}: STOP LOSS @ {current_price:,.1f}"})
                    updated = True
                    continue

                if target > 0 and current_price >= target:
                    log.info("  TARGET HIT: %s @ %.1f (target=%.1f)", ticker, current_price, target)
                    _close_position_direct(portfolio, ticker, current_price, "target_intraday")
                    alerts.append({"time": now.isoformat(), "message": f"🟢 {ticker}: TARGET @ {current_price:,.1f}"})
                    updated = True
                    continue

                if atr > 0:
                    if current_price >= entry_price + atr:
                        new_stop = max(stop_loss, entry_price)
                        if new_stop > stop_loss:
                            pos["stop_loss"] = round(new_stop, 1)
                            stop_loss = new_stop
                            updated = True
                            log.info("  Trailing stop %s -> break-even %.1f", ticker, new_stop)

                    if current_price >= entry_price + 2 * atr:
                        new_stop = max(stop_loss, entry_price + atr)
                        if new_stop > stop_loss:
                            pos["stop_loss"] = round(new_stop, 1)
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
            portfolio["updated_at"] = datetime.now().isoformat()
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


def task_eod_update():
    """15:00 - update trailing stops, exit stopped positions, and refresh PnL."""
    if not is_trading_day():
        log.info("Not a trading day, skipping EOD update")
        mark_ran_today("eod_update")
        return
    if already_ran_today("eod_update"):
        log.info("eod_update already ran today, skipping")
        return

    log.info("=" * 50)
    log.info("TASK: EOD Update")
    log.info("=" * 50)
    try:
        from data_fetcher import get_stock_data_cached

        portfolio = load_portfolio_direct()
        positions = portfolio.get("positions", {}) or {}
        updated = False
        closed = 0

        for ticker, pos in list(positions.items()):
            try:
                df = get_stock_data_cached(ticker, years=0.1)
                if df is None or len(df) < 2:
                    continue

                current_price = float(df["close"].iloc[-1])
                entry_price = float(pos.get("avg_price", current_price))
                stop_loss = float(pos.get("stop_loss", 0) or (entry_price * 0.95))
                target = float(pos.get("target_price", 0) or (entry_price * 1.10))
                atr = float(pos.get("atr", 0) or 0)
                qty = int(pos.get("qty", 0))

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

                if atr > 0 and current_price >= entry_price + atr:
                    new_stop = max(stop_loss, entry_price)
                    if new_stop > stop_loss:
                        pos["stop_loss"] = round(new_stop, 1)
                        stop_loss = new_stop
                        updated = True
                        log.info("  %s: trailing stop -> break-even %.0f", ticker, new_stop)

                if atr > 0 and current_price >= entry_price + 2 * atr:
                    new_stop = max(stop_loss, entry_price + atr)
                    if new_stop > stop_loss:
                        pos["stop_loss"] = round(new_stop, 1)
                        stop_loss = new_stop
                        updated = True
                        log.info("  %s: trailing stop -> +1ATR %.0f", ticker, new_stop)

                if current_price <= stop_loss:
                    if _close_position_direct(portfolio, ticker, current_price, "stop_loss"):
                        closed += 1
                        updated = True
                        continue

                if current_price >= target:
                    if _close_position_direct(portfolio, ticker, current_price, "target"):
                        closed += 1
                        updated = True
                        continue

                if int(pos.get("hold_days", 0)) >= 15:
                    if _close_position_direct(portfolio, ticker, current_price, "timeout"):
                        closed += 1
                        updated = True
                        continue
            except Exception as exc:
                log.warning("  EOD %s: %s", ticker, exc)

        if updated:
            portfolio["updated_at"] = datetime.now().isoformat()
            _save_portfolio_direct(portfolio)
        log.info("Closed %s positions", closed)
    except Exception as exc:
        log.warning("EOD update failed: %s", exc)

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
                cutoff = datetime.now().date() - timedelta(days=3)
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
    except Exception as exc:
        log.error("Daily learning failed: %s", exc)

    mark_ran_today("daily_learning")
    log.info("Daily learning DONE")


def task_weekly_rebacktest():
    """Monday 07:00 - rebacktest training watchlist and update backtest_config.json."""
    today = date.today()
    if today.weekday() != 0:
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
            runner_kwargs = {"years": 2, "optimize": True}
        except Exception as exc:
            log.warning("backtester_pro unavailable, falling back to legacy backtester: %s", exc)
            from backtester import run_portfolio_backtest

            runner = run_portfolio_backtest
            runner_kwargs = {"years": 2, "atr_stop": 1.0, "atr_target": 2.0}

        watchlist_path = os.path.join(BASE_DIR, "training_watchlist.json")
        with open(watchlist_path, encoding="utf-8") as f:
            watchlist = json.load(f)
        if isinstance(watchlist, dict):
            watchlist = list(watchlist.keys())
        log.info("Re-backtesting %s tickers...", len(watchlist))
        runner(watchlist, **runner_kwargs)
        log.info("Weekly rebacktest DONE")
    except Exception as exc:
        log.error("Weekly rebacktest failed: %s", exc)

    state["weekly_rebacktest"] = today.isoformat()
    save_state(state)


def setup_schedule():
    schedule.every().day.at("08:00").do(task_morning_prep)
    schedule.every().day.at("08:30").do(task_market_analysis)
    schedule.every().day.at("09:00").do(task_auto_trade)
    schedule.every().day.at("15:00").do(task_eod_update)
    schedule.every().day.at("16:00").do(task_daily_learning)
    schedule.every().monday.at("07:00").do(task_weekly_rebacktest)

    log.info("Schedule registered:")
    log.info("  08:00 Morning prep")
    log.info("  08:30 Market analysis")
    log.info("  09:00 Auto trade")
    log.info("  15:00 EOD update")
    log.info("  16:00 Daily learning")
    log.info("  Mon 07:00 Weekly rebacktest")


def run_now(task_name=None):
    tasks = {
        "prep": task_morning_prep,
        "analysis": task_market_analysis,
        "trade": task_auto_trade,
        "eod": task_eod_update,
        "learning": task_daily_learning,
        "rebacktest": task_weekly_rebacktest,
    }
    if task_name in tasks:
        log.info("Running %s NOW...", task_name)
        state = load_state()
        state_key = {
            "prep": "morning_prep",
            "analysis": "market_analysis",
            "trade": "auto_trade",
            "eod": "eod_update",
            "learning": "daily_learning",
        }.get(task_name)
        if state_key:
            state.pop(state_key, None)
            save_state(state)
        tasks[task_name]()
    else:
        log.info("Available tasks: %s", list(tasks.keys()))


def catch_up_missed_tasks():
    now = datetime.now()
    today = date.today()
    current_hour = now.hour + now.minute / 60.0
    if not is_trading_day():
        log.info("Not a trading day - no catch-up needed")
        return

    log.info("Checking for missed tasks...")
    missed = []
    task_schedule = [
        ("morning_prep", "morning_prep", 8.0, task_morning_prep),
        ("market_analysis", "market_analysis", 8.5, task_market_analysis),
        ("auto_trade", "auto_trade", 9.0, task_auto_trade),
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
    log.info("Time: %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
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
