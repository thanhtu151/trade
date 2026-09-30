"""Entry-halt limits (daily loss, drawdown) and the VN30 market-trend gate for new BUY orders."""

import logging

log = logging.getLogger(__name__)

DEFAULT_LIMITS = {
    "max_daily_loss_pct": 2.0,     # halt new entries when NAV is down >= this % vs the previous session's NAV
    "max_drawdown_pct": 10.0,      # halt new entries when NAV is down >= this % vs the NAV peak
    "require_market_uptrend": True,  # BUY only while VN30 >= its 10-month average
}


def limits_from_config(config):
    """Merge the ai-fund config over the defaults; bad values fall back to the default."""
    limits = dict(DEFAULT_LIMITS)
    for key in ("max_daily_loss_pct", "max_drawdown_pct"):
        try:
            value = float((config or {}).get(key, limits[key]))
            if value > 0:
                limits[key] = value
        except (TypeError, ValueError):
            pass
    if "require_market_uptrend" in (config or {}):
        limits["require_market_uptrend"] = bool(config["require_market_uptrend"])
    return limits


def entry_halt_reason(equity, history, initial_cash, today, limits=None):
    """Reason string when new entries must stop, else None. `today` is a date; `history` rows have time/equity."""
    limits = limits or DEFAULT_LIMITS
    equity = float(equity)
    peak = max([float(initial_cash or 0), equity] + [_row_equity(r) for r in history or []])
    if peak > 0:
        drawdown = (peak - equity) / peak * 100
        if drawdown >= limits["max_drawdown_pct"]:
            return f"drawdown {drawdown:.1f}% from NAV peak {peak:,.0f} >= {limits['max_drawdown_pct']:g}%"
    start = None
    for row in history or []:
        if str(row.get("time", ""))[:10] < today.isoformat() and _row_equity(row) > 0:
            start = _row_equity(row)   # rows are chronological: keep the last one before today
    if start:
        loss = (start - equity) / start * 100
        if loss >= limits["max_daily_loss_pct"]:
            return f"daily loss {loss:.1f}% vs previous NAV {start:,.0f} >= {limits['max_daily_loss_pct']:g}%"
    return None


def _row_equity(row):
    try:
        return float(row.get("equity", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def market_uptrend_reason(signal_bars=None, session=None):
    """None when VN30 closes >= its 10-month-end average (etf_core.trend_on); else why BUYs are blocked.

    Fails closed: missing data or under 10 months of history blocks entries.
    """
    import pandas as pd

    import etf_core

    try:
        if signal_bars is None:
            from data_fetcher import get_stock_data_cached

            signal_bars = get_stock_data_cached(etf_core.SIGNAL, years=1.5)
        if signal_bars is None or len(signal_bars) == 0:
            return "VN30 data unavailable; market gate fails closed"
        bars = etf_core._bars(signal_bars)
        session = pd.Timestamp(session) if session is not None else bars.index[-1]
        decision = etf_core.trend_on(bars["close"], session)
    except Exception as exc:
        return f"VN30 trend check failed ({exc}); market gate fails closed"
    if decision is None:
        return f"VN30 has fewer than {etf_core.MONTHS} months of data; market gate fails closed"
    on, close, average = decision
    if on:
        return None
    return f"VN30 {close:,.2f} is below its {etf_core.MONTHS}-month average {average:,.2f}"


def alert_entry_halt(reason, base_dir=None):
    """Discord warning (once per reason per day); silent when no webhook is configured."""
    try:
        import hashlib

        from notify import ict_today, send_once

        fingerprint = hashlib.sha256(f"entry_halt|{reason}|{ict_today()}".encode()).hexdigest()
        send_once(fingerprint, "New entries halted", reason, "warning", base_dir=base_dir)
    except Exception as exc:
        log.warning("entry-halt alert failed: %s", exc)
