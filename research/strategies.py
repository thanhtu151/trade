"""Point-in-time universe and cross-sectional strategies for the research engine.

Every rule only reads data up to and including the signal session.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

INDEX_SYMBOLS = {"VNINDEX", "VN30", "HNXINDEX", "E1VFVN30"}


@dataclass
class UniverseRule:
    top_n: int = 100             # by median 60-session traded value
    value_window: int = 60
    min_price: float = 10.0      # thousand VND (adjusted price, see README)
    min_history: int = 252       # sessions listed before eligibility
    min_traded_ratio: float = 0.9


class Precomputed:
    """Rolling quantities shared by universe and signals, computed once per panel."""

    def __init__(self, panel, rule):
        close = panel.frame("last_close")
        traded = pd.DataFrame(panel.traded, index=panel.dates, columns=panel.symbols)
        value = pd.DataFrame(np.where(panel.traded, panel.close * panel.volume, 0.0),
                             index=panel.dates, columns=panel.symbols)
        self.close = close
        self.median_value = value.rolling(rule.value_window, min_periods=rule.value_window // 2).median()
        self.traded_ratio = traded.rolling(rule.value_window, min_periods=1).mean()
        self.history = traded.cumsum()
        self.is_stock = np.array([s not in INDEX_SYMBOLS for s in panel.symbols])


def universe(pre, rule, i):
    """Symbols eligible at the close of session i."""
    close = pre.close.iloc[i].to_numpy()
    ok = (
        pre.is_stock
        & np.isfinite(close) & (close >= rule.min_price)
        & (pre.history.iloc[i].to_numpy() >= rule.min_history)
        & (pre.traded_ratio.iloc[i].to_numpy() >= rule.min_traded_ratio)
    )
    value = pre.median_value.iloc[i].to_numpy()
    value = np.where(ok & np.isfinite(value), value, -np.inf)
    order = np.argsort(-value)[: rule.top_n]
    return [pre.close.columns[k] for k in order if np.isfinite(value[k])]


def month_end_sessions(dates):
    """Indices of the last session of each calendar month."""
    s = pd.Series(np.arange(len(dates)), index=dates)
    return set(s.groupby([dates.year, dates.month]).max().tolist())


class Momentum:
    """Long-only cross-sectional momentum with a rank buffer.

    Score = return from `lookback` to `skip` sessions ago (6-1 month: 126/21).
    Buys the top `hold` names equally weighted; an existing holding is kept
    unchanged while it ranks within `keep_rank`, which limits turnover.
    Optional regime filter: when the index closes below its `regime_ma`
    session average, target exposure becomes `regime_exposure`.
    """

    def __init__(self, panel, rule=None, lookback=126, skip=21, hold=10, keep_rank=25,
                 regime_symbol=None, regime_ma=200, regime_exposure=0.0, picker=None):
        self.rule = rule or UniverseRule()
        self.pre = Precomputed(panel, self.rule)
        self.lookback, self.skip = lookback, skip
        self.hold, self.keep_rank = hold, keep_rank
        self.rebalance_days = month_end_sessions(panel.dates)
        self.picker = picker  # optional override of the ranking (random-selection test)
        self.regime = None
        if regime_symbol:
            idx = self.pre.close[regime_symbol]
            self.regime = (idx >= idx.rolling(regime_ma, min_periods=regime_ma).mean()).to_numpy()
            self.regime_exposure = regime_exposure
        self.log = []

    def ranked(self, i):
        names = universe(self.pre, self.rule, i)
        if i < self.lookback or not names:
            return []
        close = self.pre.close
        past = close.iloc[i - self.lookback][names]
        recent = close.iloc[i - self.skip][names]
        score = (recent / past - 1).replace([np.inf, -np.inf], np.nan).dropna()
        return list(score.sort_values(ascending=False).index)

    def __call__(self, panel, i, holdings):
        if i not in self.rebalance_days:
            return None
        ranked = self.picker(self, i) if self.picker else self.ranked(i)
        if not ranked:
            return None
        exposure = 1.0
        if self.regime is not None and not self.regime[i]:
            exposure = self.regime_exposure
        if exposure <= 0:
            self.log.append((panel.dates[i], []))
            return {}
        position = {s: r for r, s in enumerate(ranked)}
        keep = [s for s in holdings if position.get(s, 10**9) < self.keep_rank]
        keep = sorted(keep, key=position.get)[: self.hold]
        new = [s for s in ranked if s not in keep][: self.hold - len(keep)]
        weight = exposure / self.hold
        target = {s: weight for s in new}
        # Keepers stay as they are unless the regime filter cut exposure.
        target.update({s: (None if exposure >= 1.0 else weight) for s in keep})
        self.log.append((panel.dates[i], keep + new))
        return target


def random_picker(seed):
    """Picker that ranks the eligible universe randomly (placebo test)."""
    rng = np.random.default_rng(seed)

    def pick(strategy, i):
        names = universe(strategy.pre, strategy.rule, i)
        return list(rng.permutation(names)) if names else []

    return pick


class BuyAndHold:
    """Hold one symbol fully invested (benchmark such as the VN30 ETF)."""

    def __init__(self, symbol):
        self.symbol = symbol

    def __call__(self, panel, i, holdings):
        # Keep asking until the position exists (the symbol may not trade yet).
        return None if holdings.get(self.symbol) else {self.symbol: 0.99}
