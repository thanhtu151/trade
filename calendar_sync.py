"""Refresh trading_calendar.json from market data and vnstock's holiday list.

Run weekly by the scheduler (morning prep) and on demand:

    python calendar_sync.py

For the previous, current and next year it records:
- observed: weekdays with a VN-Index session (open) and without one (closed),
  over the last two years of data. Observed facts override every list;
- announced: vnstock's MARKET_EVENTS holidays, used only for years without a
  verified list. That list has had errors (e.g. 2018-01-22..24 and 2021-06-01
  are marked as holidays but traded), so it never overrides observed data or
  the verified table;
- status per year: verified / announced / provisional (statutory rules only).

It alerts on Discord when the next year is still provisional from October on,
and when sources disagree about a future date.
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import trading_calendar as tc

log = logging.getLogger("calendar_sync")
ICT = timezone(timedelta(hours=7))


def announced_holidays():
    """Weekday holidays from the installed vnstock, grouped by year ({} if unavailable)."""
    try:
        from market_data_adapter import market_events

        MARKET_EVENTS = market_events()
    except Exception as exc:  # vnstock missing or moved the table
        log.warning("vnstock holiday list unavailable: %s", exc)
        return {}
    by_year = {}
    for iso, event in MARKET_EVENTS.items():
        try:
            day = date.fromisoformat(iso)
        except ValueError:
            continue
        if day.weekday() < 5 and str(event.get("type", "")).lower() in ("holiday", "compensation"):
            by_year.setdefault(day.year, set()).add(iso)
    return by_year


def observed_sessions(bars, today):
    """(open, closed) weekday ISO dates between the first bar and yesterday."""
    traded = {str(t)[:10] for t in bars["time"]}
    if not traded:
        return set(), set()
    start = date.fromisoformat(min(traded))
    open_days, closed_days = set(), set()
    day = start
    while day < today:
        if day.weekday() < 5:
            (open_days if day.isoformat() in traded else closed_days).add(day.isoformat())
        day += timedelta(days=1)
    return open_days, closed_days


def build(today, bars, announced):
    open_days, closed_days = observed_sessions(bars, today) if bars is not None else (set(), set())
    years, discrepancies = {}, []
    for year in (today.year - 1, today.year, today.year + 1):
        observed_closed = {d for d in closed_days if d.startswith(str(year))}
        listed = announced.get(year, set())
        if year in tc.VN_EXCHANGE_HOLIDAYS:
            status, closed = "verified", set(observed_closed)
            verified = tc.VN_EXCHANGE_HOLIDAYS[year]
            for d in sorted(listed - verified):
                if d >= today.isoformat():
                    discrepancies.append(f"{d}: vnstock lists a holiday, verified calendar says trading day")
        elif len(listed) >= 3:
            status, closed = "announced", (listed - open_days) | observed_closed
        else:
            status, closed = "provisional", set(observed_closed)
        for d in sorted(listed & open_days):
            discrepancies.append(f"{d}: vnstock lists a holiday but the market traded")
        known = closed | tc.VN_EXCHANGE_HOLIDAYS.get(year, set()) | listed | tc.provisional_holidays(year)
        for d in sorted(observed_closed - known):
            discrepancies.append(f"{d}: market closed on a day no list marks as a holiday")
        years[str(year)] = {"status": status, "closed": sorted(closed)}
    return {
        "generated_at": datetime.now(ICT).isoformat(timespec="seconds"),
        "sources": {"vnstock_years": sorted(announced), "observed_range": [min(open_days | closed_days, default=None),
                                                                              max(open_days | closed_days, default=None)]},
        "years": years,
        "observed": {"open": sorted(open_days)},
        "discrepancies": discrepancies,
    }


def write(data, path=None):
    path = Path(path or tc.CALENDAR_FILE)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def alerts(data, today):
    messages = []
    next_year = str(today.year + 1)
    if today.month >= 10 and data["years"][next_year]["status"] == "provisional":
        messages.append(f"Lịch nghỉ {next_year} chưa có danh sách chính thức; hệ thống sẽ dùng lịch tạm tính "
                        f"(Tết, Giỗ Tổ, ngày lễ cố định). Cập nhật vnstock hoặc VN_EXCHANGE_HOLIDAYS khi HOSE công bố.")
    upcoming = [d for d in data["discrepancies"] if d[:10] >= today.isoformat()]
    if upcoming:
        messages.append("Nguồn lịch không khớp: " + "; ".join(upcoming[:5]))
    return messages


def sync(today=None, fetch=None, announced=None, path=None, notify=True):
    today = today or datetime.now(ICT).date()
    if fetch is None:
        from data_fetcher import get_stock_data_cached

        def fetch():
            return get_stock_data_cached("VNINDEX", years=2, force_refresh=True)
    try:
        bars = fetch()
    except Exception as exc:
        log.warning("VN-Index history unavailable, syncing lists only: %s", exc)
        bars = None
    data = build(today, bars, announced if announced is not None else announced_holidays())
    write(data, path)
    messages = alerts(data, today)
    for message in messages:
        log.warning("calendar: %s", message)
    if notify and messages:
        try:
            from notify import send_once

            week = today.isocalendar()
            digest = hashlib.sha256("\n".join(messages).encode("utf-8")).hexdigest()[:8]
            send_once(f"calendar:{week[0]}-W{week[1]}:{digest}",
                      "Lịch giao dịch cần kiểm tra", "\n".join(messages), "warning")
        except Exception:
            pass
    return {"years": {y: v["status"] for y, v in data["years"].items()}, "alerts": messages}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(sync(notify=False), ensure_ascii=False, indent=1))
