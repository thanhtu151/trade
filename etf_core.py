"""Paper-trading core: VN30 ETF with a 10-month trend filter.

Rule (research/run_trend.py, trial "trend_ma10m"): at the last session of each
month, hold the ETF if the VN30 index closes at or above the average of its
last 10 month-end closes (this one included); otherwise hold cash. Orders fill
at the next session's open with brokerage, exchange fee, 0.1% sell tax and
slippage, in board lots of 100 — the same frictions as the backtest.

This sleeve is independent of the LLM paper portfolio (paper_portfolio.json)
and places no real orders. It processes every completed session found in the
data after the last one it handled, so delayed or repeated runs replay missed
sessions in order and never double-count.

    python etf_core.py            # process new sessions and print the state
"""

import json
import logging
import os
import tempfile
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger("etf_core")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = "etf_core_state.json"
ICT = timezone(timedelta(hours=7))

ETF = "E1VFVN30"          # most liquid VN30 ETF (~14 bn VND/day in 2026)
SIGNAL = "VN30"
MONTHS = 10
INITIAL_CASH = 100_000_000.0
LOT = 100
BROKERAGE = 0.0015
EXCHANGE_FEE = 0.0003
SELL_TAX = 0.0010
SLIPPAGE = 0.0015
SESSION_CLOSE_READY = dtime(14, 50)  # today's bar counts as complete after ATC


def _now():
    return datetime.now(ICT)


def default_state(today=None):
    return {
        "version": 1, "etf": ETF, "signal_symbol": SIGNAL, "months": MONTHS,
        "initial_cash": INITIAL_CASH, "cash": INITIAL_CASH, "units": 0,
        "started": (today or _now().date()).isoformat(),
        "last_session": None, "pending": None, "signal": None,
        "trades": [], "equity": [], "signals": [],
    }


def load_state(base_dir=BASE_DIR):
    path = Path(base_dir) / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and "cash" in state:
            return state
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return default_state()


def save_state(state, base_dir=BASE_DIR):
    path = Path(base_dir) / STATE_FILE
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _bars(df):
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"]).dt.normalize()
    return df.drop_duplicates("time", keep="last").set_index("time").sort_index()


def is_month_end(session, sessions, is_trading_day):
    """True if no later trading session exists in the same month."""
    later = [s for s in sessions if s > session]
    if later:
        return later[0].month != session.month
    day = session.date() + timedelta(days=1)
    while day.month == session.month:
        if is_trading_day(day):
            return False
        day += timedelta(days=1)
    return True


def trend_on(signal_close, session):
    """Month-end close >= mean of the last MONTHS month-end closes (inclusive)."""
    closes = signal_close[signal_close.index <= session]
    month_ends = closes.groupby([closes.index.year, closes.index.month]).tail(1)
    if len(month_ends) < MONTHS:
        return None
    window = month_ends.iloc[-MONTHS:]
    return bool(window.iloc[-1] >= window.mean()), float(window.iloc[-1]), float(window.mean())


def _fill(state, side, session, open_price):
    if side == "BUY":
        px = open_price * (1 + SLIPPAGE)
        units = int(state["cash"] / (px * (1 + BROKERAGE + EXCHANGE_FEE)) // LOT * LOT)
        if units <= 0:
            return None
        value = units * px
        fee = value * (BROKERAGE + EXCHANGE_FEE)
        state["cash"] -= value + fee
        state["units"] += units
    else:
        units = state["units"]
        if units <= 0:
            return None
        px = open_price * (1 - SLIPPAGE)
        value = units * px
        fee = value * (BROKERAGE + EXCHANGE_FEE + SELL_TAX)
        state["cash"] += value - fee
        state["units"] = 0
    trade = {"session": session.date().isoformat(), "side": side, "units": units, "open": open_price,
             "price": round(px, 2), "fee": round(fee, 0), "cash_after": round(state["cash"], 0)}
    state["trades"].append(trade)
    return trade


def process(state, etf_bars, signal_bars, now, is_trading_day):
    """Advance state through every completed session after state['last_session'].

    Returns a list of events (fills and month-end signals) for notification.
    """
    etf, sig = _bars(etf_bars), _bars(signal_bars)
    sessions = sorted(set(etf.index) & set(sig.index))
    today = pd.Timestamp(now.date())
    if now.time() < SESSION_CLOSE_READY:
        sessions = [s for s in sessions if s < today]
    last = pd.Timestamp(state["last_session"]) if state.get("last_session") else None
    if last is None:
        # First run: start from the latest completed session; no back-filling of history.
        sessions = sessions[-1:]
    else:
        sessions = [s for s in sessions if s > last]
    events = []
    for session in sessions:
        pending = state.get("pending")
        if pending and pd.Timestamp(pending["signal_session"]) < session:
            trade = _fill(state, pending["side"], session, float(etf.loc[session, "open"]))
            state["pending"] = None
            if trade:
                events.append({"type": "fill", **trade})
        close = float(etf.loc[session, "close"])
        equity = state["cash"] + state["units"] * close
        state["equity"].append({"session": session.date().isoformat(), "close": close,
                                "units": state["units"], "cash": round(state["cash"], 0), "equity": round(equity, 0)})
        if is_month_end(session, sessions_all(etf, sig), is_trading_day):
            decision = trend_on(sig["close"], session)
            if decision is not None:
                on, month_close, average = decision
                state["signal"] = "ON" if on else "OFF"
                record = {"session": session.date().isoformat(), "signal": state["signal"],
                          "vn30_close": month_close, "ma10m": round(average, 2)}
                state["signals"].append(record)
                side = "BUY" if on and state["units"] == 0 else "SELL" if not on and state["units"] > 0 else None
                state["pending"] = {"side": side, "signal_session": record["session"]} if side else None
                events.append({"type": "signal", "order": side, **record, "equity": round(equity, 0)})
        state["last_session"] = session.date().isoformat()
    return events


def sessions_all(etf, sig):
    return sorted(set(etf.index) & set(sig.index))


def summary(state):
    last = state["equity"][-1] if state["equity"] else None
    equity = last["equity"] if last else state["cash"]
    return {
        "signal": state.get("signal"), "units": state["units"], "cash": round(state["cash"], 0),
        "equity": equity, "return_pct": round((equity / state["initial_cash"] - 1) * 100, 2),
        "last_session": state.get("last_session"), "pending": state.get("pending"),
        "trades": len(state["trades"]),
    }


def run_daily(base_dir=BASE_DIR, now=None, fetch=None, is_trading_day=None, notify=True):
    """Fetch fresh bars, process new sessions, persist, and notify. Returns the summary."""
    if fetch is None:
        from data_fetcher import get_stock_data_cached

        def fetch(symbol, years):
            return get_stock_data_cached(symbol, years=years, force_refresh=True)
    if is_trading_day is None:
        from trading_calendar import is_trading_day
    now = now or _now()
    state = load_state(base_dir)
    etf_bars = fetch(ETF, 0.5)
    signal_bars = fetch(SIGNAL, 1.5)
    if etf_bars is None or signal_bars is None or len(etf_bars) == 0 or len(signal_bars) == 0:
        raise RuntimeError("etf_core: missing ETF or VN30 data")
    events = process(state, etf_bars, signal_bars, now, is_trading_day)
    save_state(state, base_dir)
    info = summary(state)
    log.info("ETF core: %s", info)
    if notify:
        _notify(events, info, base_dir)
    return info


def _notify(events, info, base_dir):
    try:
        from notify import send_once
    except Exception:
        return
    for event in events:
        if event["type"] == "signal":
            order = {"BUY": "MUA ETF phiên tới", "SELL": "BÁN ETF phiên tới"}.get(event["order"], "giữ nguyên")
            send_once(f"etf_core:signal:{event['session']}", "ETF core — tín hiệu cuối tháng",
                      f"VN30 {event['vn30_close']:,.2f} vs MA10 tháng {event['ma10m']:,.2f} → **{event['signal']}** "
                      f"({order}). Tài sản {event['equity']:,.0f} VND.", "info", base_dir=base_dir)
        elif event["type"] == "fill":
            send_once(f"etf_core:fill:{event['session']}:{event['side']}", "ETF core — khớp lệnh (paper)",
                      f"{event['side']} {event['units']:,} {ETF} @ {event['price']:,.0f} "
                      f"(mở cửa {event['open']:,.0f}), phí {event['fee']:,.0f}. "
                      f"Lợi nhuận từ đầu: {info['return_pct']:+.2f}%.", "info", base_dir=base_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run_daily(notify=False), ensure_ascii=False, indent=1))
