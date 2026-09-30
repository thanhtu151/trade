import json
from datetime import date, timedelta

import pandas as pd
import pytest

from broker import Order, PaperBroker, TCBSBroker
from broker.paper import tick_size
from signals import REGISTRY, E1Ma10Month

MON, TUE, WED = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)


def weekday_add(day, n):
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def make(cash=100_000_000, **kw):
    return PaperBroker(cash, add_trading_days=weekday_add, **kw)


def order(side="BUY", qty=1000, day=MON, ref=30000, mkt=30000, **kw):
    return Order("E1VFVN30", side, qty, day, ref, mkt, client_id=kw.pop("client_id", f"{side}-{day}"), **kw)


def test_buy_fills_with_slippage_fee_and_lot():
    b = make()
    f = b.place_order(order())
    assert f.status == "FILLED" and f.qty == 1000
    assert f.price == 30030 and f.price % tick_size("E1VFVN30", 30000) == 0
    assert f.slippage_pct == pytest.approx(0.1, abs=0.01)
    assert f.fee == pytest.approx(f.price * 1000 * 0.00175, abs=0.01)
    assert b.get_cash() == pytest.approx(100_000_000 - f.price * 1000 - f.fee)


def test_odd_lot_rejected():
    b = make()
    f = b.place_order(order(qty=150))
    assert f.status == "REJECTED" and "multiple_of_100" in f.reason
    assert b.get_cash() == 100_000_000


def test_price_outside_band_rejected():
    b = make()
    assert b.place_order(order(mkt=32200, client_id="a")).status == "REJECTED"   # > +7% (32100)
    assert b.place_order(order(mkt=27800, client_id="b")).status == "REJECTED"   # < -7% (27900)
    assert b.place_order(order(mkt=32100, client_id="c")).status == "FILLED"   # at the ceiling
    f = b.place_order(order(limit_price=32200, client_id="d"))
    assert f.status == "REJECTED" and "limit_price_outside_band" in f.reason


def test_off_tick_rejected():
    assert make().place_order(order(mkt=30005)).reason.startswith("market_price_off_tick")


def test_t_plus_2_blocks_early_sell_then_allows():
    b = make()
    b.place_order(order())
    assert b.get_positions(TUE)["E1VFVN30"]["sellable_qty"] == 0
    early = b.place_order(order("SELL", day=TUE))
    assert early.status == "REJECTED" and "T+2" in early.reason
    assert b.get_positions(WED)["E1VFVN30"]["sellable_qty"] == 1000
    assert b.place_order(order("SELL", day=WED)).status == "FILLED"


def test_switch_to_cash_sells_everything_and_journals(tmp_path):
    journal = tmp_path / "orders.jsonl"
    b = make(journal_path=journal)
    b.place_order(order())
    f = b.place_order(order("SELL", day=WED, ref=30500, mkt=30500))
    assert f.status == "FILLED" and b.get_positions() == {}
    assert b.get_cash() > 100_000_000 * 0.99
    rows = [json.loads(x) for x in journal.read_text().splitlines()]
    assert [r["status"] for r in rows] == ["FILLED", "FILLED"]
    assert rows[1]["side"] == "SELL" and rows[1]["ref_price"] == 30500
    assert "slippage_pct" in rows[1] and rows[1]["broker"] == "paper"


def test_insufficient_cash_and_duplicate_client_id():
    b = make(cash=1_000_000)
    assert b.place_order(order()).reason == "insufficient_cash"
    b2 = make()
    b2.place_order(order(client_id="x"))
    assert b2.place_order(order(client_id="x")).status == "DUPLICATE"
    assert b2.get_positions()["E1VFVN30"]["qty"] == 1000


def test_tcbs_stub_never_connects():
    with pytest.raises(NotImplementedError):
        TCBSBroker()
    for name in ("get_cash", "get_positions", "place_order", "cancel", "authenticate"):
        with pytest.raises(NotImplementedError):
            getattr(TCBSBroker, name)(object.__new__(TCBSBroker), *([None] if name != "get_cash" else []))


def _monthly(values):
    idx = pd.to_datetime([f"2025-{m:02d}-28" for m in range(1, 13)][: len(values)])
    return pd.Series(values, index=idx)


def test_e1_on_off_and_insufficient_history():
    sig = E1Ma10Month()
    assert "E1_ma10m" in REGISTRY
    short = _monthly([100] * 9)
    assert sig.target("2025-09-28", {"VN30": short}).weight == 0.0
    up = _monthly([100] * 9 + [110, 120])
    assert sig.target("2025-11-28", {"VN30": up}).weight == 1.0
    down = _monthly([100] * 9 + [110, 90])
    t = sig.target("2025-11-28", {"VN30": down})
    assert t.weight == 0.0 and t.reason == "close<ma10m"


def test_e1_uses_last_close_of_each_month_and_ignores_future():
    days = pd.bdate_range("2025-01-01", "2025-12-31")
    s = pd.Series(range(len(days)), index=days, dtype=float)
    t = E1Ma10Month().target("2025-06-30", {"VN30": s})
    assert t.reason == "insufficient_history"           # only 6 month-ends up to June
    assert E1Ma10Month().target("2025-12-31", {"VN30": s}).weight == 1.0   # rising series
