import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.engine import Config, Costs, Panel, simulate  # noqa: E402
from research.strategies import Momentum, UniverseRule, month_end_sessions, universe  # noqa: E402


def synthetic(n_symbols=30, n_days=400, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    rows = []
    for k in range(n_symbols):
        drift = (k - n_symbols / 2) * 0.0004  # higher k trends up faster
        close = 20 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n_days)))
        for d, c in zip(dates, close):
            rows.append({"time": d, "symbol": f"S{k:02d}", "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": 1_000_000 + 10_000 * k})
    return pd.DataFrame(rows)


def test_universe_uses_only_past_data():
    prices = synthetic()
    panel = Panel(prices)
    rule = UniverseRule(top_n=10, min_history=100, price_quantile_floor=0)
    strat = Momentum(panel, rule=rule, lookback=100, skip=10, hold=5)
    before = universe(strat.pre, rule, 250)
    # Changing everything after session 250 must not change the universe at 250.
    later = prices["time"] > panel.dates[250]
    shocked = prices.copy()
    shocked.loc[later, "volume"] = shocked.loc[later, "volume"][::-1].to_numpy()
    shocked.loc[later, "close"] = shocked.loc[later, "close"] * 5
    strat2 = Momentum(Panel(shocked), rule=rule, lookback=100, skip=10, hold=5)
    assert universe(strat2.pre, rule, 250) == before
    assert strat2.ranked(250) == strat.ranked(250)


def test_universe_requires_history_and_liquidity_rank():
    prices = synthetic()
    panel = Panel(prices)
    pre = Momentum(panel, rule=UniverseRule(top_n=5, min_history=100, price_quantile_floor=0)).pre
    assert universe(pre, UniverseRule(top_n=5, min_history=100, price_quantile_floor=0), 50) == []
    top = universe(pre, UniverseRule(top_n=5, min_history=100, price_quantile_floor=0), 200)
    value = (prices["close"] * prices["volume"]).groupby(prices["symbol"]).apply(
        lambda s: s.iloc[141:201].median())
    assert top == list(value.sort_values(ascending=False).index[:5])  # highest traded value first


def test_momentum_rebalances_monthly_and_prefers_winners():
    panel = Panel(synthetic())
    strat = Momentum(panel, rule=UniverseRule(top_n=30, min_history=100, price_quantile_floor=0), lookback=100, skip=10,
                     hold=5, keep_rank=10)
    out = simulate(panel, strat, Config(costs=Costs(0, 0, 0, 0), stop_loss=None))
    rebalance_dates = {d for d, _ in strat.log}
    assert rebalance_dates <= {panel.dates[i] for i in month_end_sessions(panel.dates)}
    held = [s for _, names in strat.log for s in names]
    assert np.mean([int(s[1:]) for s in held]) > 20  # strong-drift names dominate
    assert out["curve"]["equity"].iloc[-1] > 0


def test_rank_buffer_keeps_existing_holding():
    panel = Panel(synthetic())
    strat = Momentum(panel, rule=UniverseRule(top_n=30, min_history=100, min_price=0, price_quantile_floor=0), lookback=100, skip=10,
                     hold=3, keep_rank=30)
    i = max(month_end_sessions(panel.dates))
    target = strat(panel, i, {"S00": 1000})
    assert target["S00"] is None  # kept untouched: still within keep_rank
    assert sum(1 for v in target.values() if v is not None) == 2


def test_regime_filter_moves_to_cash():
    prices = synthetic()
    idx = prices[prices["symbol"] == "S00"].copy()
    idx["symbol"] = "VNINDEX"
    idx["close"] = np.linspace(100, 50, len(idx))  # persistent downtrend
    idx["open"] = idx["high"] = idx["low"] = idx["close"]
    panel = Panel(pd.concat([prices, idx]))
    strat = Momentum(panel, rule=UniverseRule(top_n=30, min_history=100, price_quantile_floor=0), lookback=100, skip=10,
                     hold=5, regime_symbol="VNINDEX", regime_ma=50, regime_exposure=0.0)
    i = max(month_end_sessions(panel.dates))
    assert strat(panel, i, {"S29": 1000}) == {}


def test_relative_price_floor_drops_cheapest_liquid_names():
    prices = synthetic()
    panel = Panel(prices)
    rule = UniverseRule(top_n=30, candidates=30, min_history=100, price_quantile_floor=0.2)
    pre = Momentum(panel, rule=rule).pre
    names = universe(pre, rule, 300)
    close = pre.close.iloc[300]
    assert len(names) == 24  # 30 candidates minus the cheapest 20%
    assert close[names].min() > close.drop(labels=names).max()
