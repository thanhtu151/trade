"""VN exchange calendar implemented with Python's standard library only.

Closures come from three tiers (a date is closed if any tier closes it, unless
market data proves the exchange traded that day):

1. verified: the hard-coded table below plus closures observed in market data
   (weekdays without a VN-Index session), written by calendar_sync.py;
2. announced: official holiday lists shipped with vnstock, written by
   calendar_sync.py into trading_calendar.json;
3. provisional: statutory holidays computed from the Labour Code (fixed solar
   dates plus Lunar New Year and Hung Kings day from the Vietnamese lunar
   calendar), used only for years with no verified/announced list.

Unlike the earlier fail-closed behaviour, a year without an official list runs
on the provisional rules instead of halting every task on 1 January; the
calendar reports such years as "provisional" so the scheduler can alert.
"""

import json
import math
import os
from datetime import date, timedelta
from pathlib import Path


VN_EXCHANGE_HOLIDAYS = {
    # HOSE notice 2294/TB-SGDHCM (09/12/2025), plus 24/11 Vietnam Culture Day: a
    # statutory paid holiday from 2026 under Resolution 28/2026/QH16 (effective
    # 01/07/2026), adopted after that notice was published.
    2026: {
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-04-27", "2026-04-30", "2026-05-01",
        "2026-08-31", "2026-09-01", "2026-09-02",
        "2026-11-24",
    },
}

CALENDAR_FILE = Path(os.getenv("TRADING_CALENDAR_FILE", Path(__file__).resolve().parent / "trading_calendar.json"))
_cache = {"mtime": None, "data": None}


# --- Vietnamese lunar calendar (Ho Ngoc Duc's algorithm, UTC+7) --------------

def _jd_from_date(d):
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def _date_from_jd(jd):
    a = jd + 32044
    b = (4 * a + 3) // 146097
    c = a - (b * 146097) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    return date(b * 100 + d - 4800 + m // 10, m + 3 - 12 * (m // 10), e - (153 * m + 2) // 5 + 1)


def _new_moon(k):
    t = k / 1236.85
    t2, t3 = t * t, t * t * t
    dr = math.pi / 180
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * t2 - 0.000000155 * t3
    jd1 += 0.00033 * math.sin((166.56 + 132.87 * t - 0.009173 * t2) * dr)
    m = 359.2242 + 29.10535608 * k - 0.0000333 * t2 - 0.00000347 * t3
    mpr = 306.0253 + 385.81691806 * k + 0.0107306 * t2 + 0.00001236 * t3
    f = 21.2964 + 390.67050646 * k - 0.0016528 * t2 - 0.00000239 * t3
    c1 = (0.1734 - 0.000393 * t) * math.sin(m * dr) + 0.0021 * math.sin(2 * dr * m)
    c1 = c1 - 0.4068 * math.sin(mpr * dr) + 0.0161 * math.sin(dr * 2 * mpr)
    c1 = c1 - 0.0004 * math.sin(dr * 3 * mpr)
    c1 = c1 + 0.0104 * math.sin(dr * 2 * f) - 0.0051 * math.sin(dr * (m + mpr))
    c1 = c1 - 0.0074 * math.sin(dr * (m - mpr)) + 0.0004 * math.sin(dr * (2 * f + m))
    c1 = c1 - 0.0004 * math.sin(dr * (2 * f - m)) - 0.0006 * math.sin(dr * (2 * f + mpr))
    c1 = c1 + 0.0010 * math.sin(dr * (2 * f - mpr)) + 0.0005 * math.sin(dr * (2 * mpr + m))
    if t < -11:
        delta = 0.001 + 0.000839 * t + 0.0002261 * t2 - 0.00000845 * t3 - 0.000000081 * t * t3
    else:
        delta = -0.000278 + 0.000265 * t + 0.000262 * t2
    return jd1 + c1 - delta


def _new_moon_day(k, tz=7.0):
    return int(_new_moon(k) + 0.5 + tz / 24)


def _sun_longitude(jdn, tz=7.0):
    t = (jdn - 2451545.5 - tz / 24) / 36525
    t2 = t * t
    dr = math.pi / 180
    m = 357.52910 + 35999.05030 * t - 0.0001559 * t2 - 0.00000048 * t * t2
    l0 = 280.46645 + 36000.76983 * t + 0.0003032 * t2
    dl = (1.914600 - 0.004817 * t - 0.000014 * t2) * math.sin(dr * m)
    dl += (0.019993 - 0.000101 * t) * math.sin(dr * 2 * m) + 0.000290 * math.sin(dr * 3 * m)
    lon = (l0 + dl) * dr
    lon -= math.pi * 2 * int(lon / (math.pi * 2))
    return int(lon / math.pi * 6)


def _lunar_month11(year, tz=7.0):
    off = _jd_from_date(date(year, 12, 31)) - 2415021
    k = int(off / 29.530588853)
    nm = _new_moon_day(k, tz)
    if _sun_longitude(nm, tz) >= 9:
        nm = _new_moon_day(k - 1, tz)
    return nm


def _leap_month_offset(a11, tz=7.0):
    k = int((a11 - 2415021.076998695) / 29.530588853 + 0.5)
    i = 1
    arc = _sun_longitude(_new_moon_day(k + i, tz), tz)
    while True:
        last = arc
        i += 1
        arc = _sun_longitude(_new_moon_day(k + i, tz), tz)
        if arc == last or i >= 14:
            break
    return i - 1


def lunar_to_solar(lunar_year, lunar_month, lunar_day, leap=False, tz=7.0):
    """Solar date of a Vietnamese lunar date."""
    if lunar_month < 11:
        a11, b11 = _lunar_month11(lunar_year - 1, tz), _lunar_month11(lunar_year, tz)
    else:
        a11, b11 = _lunar_month11(lunar_year, tz), _lunar_month11(lunar_year + 1, tz)
    k = int(0.5 + (a11 - 2415021.076998695) / 29.530588853)
    off = lunar_month - 11
    if off < 0:
        off += 12
    if b11 - a11 > 365:
        leap_off = _leap_month_offset(a11, tz)
        leap_month = leap_off - 2
        if leap_month < 0:
            leap_month += 12
        if leap and lunar_month != leap_month:
            raise ValueError("not a leap month")
        if leap or off >= leap_off:
            off += 1
    month_start = _new_moon_day(k + off, tz)
    return _date_from_jd(month_start + lunar_day - 1)


# --- provisional statutory holidays -------------------------------------------

def _weekdays(days):
    return {d.isoformat() for d in days if d.weekday() < 5}


def provisional_holidays(year):
    """Statutory closures that are certain from the Labour Code alone.

    Deliberately conservative: only days that are always holidays, so an
    unannounced year may trade on a compensation day the government adds later
    rather than skip a real session. Lunar New Year: the last day of the lunar
    year through the 4th day of the new year (5 statutory days).
    """
    tet = lunar_to_solar(year, 1, 1)
    tet_days = [tet + timedelta(days=offset) for offset in range(-1, 4)]
    fixed = [date(year, 1, 1), date(year, 4, 30), date(year, 5, 1), date(year, 9, 2),
             lunar_to_solar(year, 3, 10)]
    if year >= 2026:  # Vietnam Culture Day, Resolution 28/2026/QH16
        fixed.append(date(year, 11, 24))
    return _weekdays(tet_days + fixed)


# --- lookup -------------------------------------------------------------------

def _load_file():
    try:
        mtime = CALENDAR_FILE.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            _cache["data"] = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _cache["data"] = {}
        _cache["mtime"] = mtime
    return _cache["data"] or {}


def year_status(year):
    """'verified', 'announced' or 'provisional' for the closure list used for year."""
    if year in VN_EXCHANGE_HOLIDAYS:
        return "verified"
    entry = (_load_file().get("years") or {}).get(str(year))
    if entry and entry.get("status") in ("verified", "announced"):
        return entry["status"]
    return "provisional"


def closures(year):
    data = _load_file()
    entry = (data.get("years") or {}).get(str(year)) or {}
    closed = set(VN_EXCHANGE_HOLIDAYS.get(year, set())) | set(entry.get("closed") or [])
    if year_status(year) == "provisional":
        closed |= provisional_holidays(year)
    return closed


def is_trading_day(day=None):
    current = day or date.today()
    if current.weekday() >= 5:
        return False
    iso = current.isoformat()
    observed_open = set((_load_file().get("observed") or {}).get("open") or [])
    if iso in observed_open:
        return True
    return iso not in closures(current.year)
