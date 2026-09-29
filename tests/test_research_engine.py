import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.engine import Config, Costs, Panel, simulate  # noqa: E402
from research import metrics  # noqa: E402

ZERO = Costs(brokerage=0, exchange_fee=0, sell_tax=0, slippage=0)


def make_prices(bars, symbol="AAA", volume=1_000_000):
    """bars: list of (open, high, low, close) in thousand VND."""
    dates = pd.bdate_range("2024-01-01", periods=len(bars))
    return pd.DataFrame([
        {"time": d, "symbol": symbol, "open": o, "high": h, "low": low, "close": c, "volume": volume}
        for d, (o, h, low, c) in zip(dates, bars)
    ])


def flat(n, price=10.0):
    return [(price, price, price, price)] * n


class Script:
    """Strategy returning scripted weights on given session indices."""

    def __init__(self, plan):
        self.plan = plan

    def __call__(self, panel, i, holdings):
        return self.plan.get(i)


def run(bars, plan, **cfg):
    panel = Panel(make_prices(bars))
    config = Config(initial_cash=cfg.pop("cash", 10_000_000), costs=cfg.pop("costs", ZERO), **cfg)
    return simulate(panel, Script(plan), config)


def test_orders_fill_next_open_in_board_lots():
    bars = flat(30)
    bars[25] = (11.0, 11.2, 10.9, 11.0)  # gap up but trades both ways, so not a lock
    out = run(bars, {24: {"AAA": 0.5}}, stop_loss=None)
    buy = out["trades"].iloc[0]
    assert buy["date"] == pd.bdate_range("2024-01-01", periods=30)[25]
    assert buy["price"] == 11_000 and buy["qty"] % 100 == 0
    assert buy["qty"] == 500  # sized at the signal close: 0.5 * 10m / 10k


def test_costs_and_sell_tax_reduce_cash():
    costs = Costs(brokerage=0.0015, exchange_fee=0.0003, sell_tax=0.001, slippage=0.0)
    out = run(flat(40), {24: {"AAA": 0.5}, 30: {}}, costs=costs, stop_loss=None)
    trades = out["trades"]
    buy_fee, sell_fee = trades["fee"].tolist()
    value = trades["qty"].iloc[0] * 10_000
    assert buy_fee == pytest.approx(value * 0.0018)
    assert sell_fee == pytest.approx(value * 0.0028)
    assert out["curve"]["equity"].iloc[-1] == pytest.approx(10_000_000 - buy_fee - sell_fee)


def test_t_plus_2_blocks_early_sell():
    out = run(flat(40), {24: {"AAA": 0.5}, 25: {}}, stop_loss=None)
    trades = out["trades"]
    buy_session = 25
    sell_date = trades[trades["side"] == "SELL"]["date"].iloc[0]
    dates = pd.bdate_range("2024-01-01", periods=40)
    assert dates.get_loc(sell_date) == buy_session + 3


def test_no_buy_when_open_locked_at_ceiling():
    bars = flat(30)
    bars[25] = (10.7, 10.7, 10.7, 10.7)  # opens +7% and never trades lower
    out = run(bars, {24: {"AAA": 0.5}}, stop_loss=None)
    assert out["trades"]["date"].iloc[0] == pd.bdate_range("2024-01-01", periods=30)[26]


def test_no_sell_when_open_locked_at_floor():
    bars = flat(40)
    bars[32] = (9.3, 9.3, 9.3, 9.3)
    out = run(bars, {24: {"AAA": 0.5}, 31: {}}, stop_loss=None)
    sell = out["trades"][out["trades"]["side"] == "SELL"].iloc[0]
    assert pd.bdate_range("2024-01-01", periods=40).get_loc(sell["date"]) == 33


def test_order_capped_by_adv_and_carried_over():
    prices = make_prices(flat(40), volume=2_000)  # 5% of ADV = 100 shares/session
    panel = Panel(prices)
    out = simulate(panel, Script({24: {"AAA": 0.5}}), Config(initial_cash=10_000_000, costs=ZERO, stop_loss=None))
    buys = out["trades"][out["trades"]["side"] == "BUY"]
    assert (buys["qty"] == 100).all() and len(buys) == 5


def test_stop_fills_at_gapped_open():
    bars = flat(40)
    bars[30] = (9.0, 9.0, 7.5, 7.6)   # close below 20% stop
    bars[31] = (7.0, 7.2, 6.9, 7.0)   # gaps lower the next session
    out = run(bars, {24: {"AAA": 0.5}}, stop_loss=0.20)
    sell = out["trades"][out["trades"]["side"] == "SELL"].iloc[0]
    assert sell["reason"] == "stop" and sell["price"] == 7_000


def test_delisted_position_written_off_with_haircut():
    bars = flat(30) + [(np.nan,) * 4] * 25
    out = run(bars, {24: {"AAA": 0.5}}, stop_loss=None, delist_after=20, delist_haircut=0.3)
    last = out["trades"].iloc[-1]
    assert last["reason"] == "delisted" and last["price"] == pytest.approx(7_000)


def test_metrics_match_published_examples():
    # Bailey & Lopez de Prado (2012): SR 2 vs 1 annual, normal daily returns -> ~689 obs.
    assert math.ceil(metrics.min_track_record_length(2 / math.sqrt(252), 1 / math.sqrt(252))) in (688, 689)
    # Bailey & Lopez de Prado (2014): DSR ~0.90 for SR 2.5, T=1250, N=100, V=0.5, skew -3, kurt 10.
    dsr = metrics.deflated_sharpe(2.5 / math.sqrt(252), 1250, 100, 0.5 / 252, -3, 10)
    assert dsr == pytest.approx(0.90, abs=0.005)


def test_cash_goes_to_orders_in_strategy_rank_order():
    # Cash covers only one name: the first-ranked symbol must win, independent of hash order.
    prices = pd.concat([make_prices(flat(40), "ZZZ"), make_prices(flat(40), "AAA")])
    panel = Panel(prices)
    plan = {24: {"ZZZ": 0.9, "AAA": 0.9}}
    out = simulate(panel, Script(plan), Config(initial_cash=10_000_000, costs=ZERO, stop_loss=None))
    buys = out["trades"][out["trades"]["side"] == "BUY"]
    assert buys["symbol"].iloc[0] == "ZZZ" and buys.groupby("symbol")["qty"].sum()["ZZZ"] == 900
