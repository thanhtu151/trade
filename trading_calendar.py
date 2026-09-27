"""Verified VN exchange calendar implemented with Python's standard library only."""

from datetime import date


VN_EXCHANGE_HOLIDAYS = {
    2026: {
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-04-27", "2026-04-30", "2026-05-01",
        "2026-08-31", "2026-09-01", "2026-09-02",
    },
}


def is_trading_day(day=None):
    current = day or date.today()
    if current.weekday() >= 5:
        return False
    holidays = VN_EXCHANGE_HOLIDAYS.get(current.year)
    if holidays is None:
        return False
    return current.isoformat() not in holidays

