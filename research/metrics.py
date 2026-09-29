"""Performance statistics that account for non-normal returns and multiple testing.

Sharpe ratios here are per period (not annualised) unless the name says
otherwise. References:
  Bailey & Lopez de Prado (2012) "The Sharpe ratio efficient frontier" (PSR, MinTRL)
  Bailey & Lopez de Prado (2014) "The deflated Sharpe ratio" (DSR)
"""

import math

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def sharpe(returns):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1))


def _moments(returns):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    return len(r), float(stats.skew(r)), float(stats.kurtosis(r, fisher=False))


def _sr_std(sr, skew, kurt):
    return math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))


def probabilistic_sharpe(sr, sr_benchmark, n_obs, skew=0.0, kurt=3.0):
    """P(true Sharpe > sr_benchmark) given an observed per-period Sharpe."""
    if n_obs < 2:
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / _sr_std(sr, skew, kurt)
    return float(stats.norm.cdf(z))


def min_track_record_length(sr, sr_benchmark, skew=0.0, kurt=3.0, alpha=0.05):
    """Observations needed before sr is significantly above sr_benchmark."""
    if sr <= sr_benchmark:
        return float("inf")
    z = stats.norm.ppf(1 - alpha)
    return float(1 + _sr_std(sr, skew, kurt) ** 2 * (z / (sr - sr_benchmark)) ** 2)


def expected_max_sharpe(n_trials, sr_variance):
    """Expected best per-period Sharpe among n_trials skill-less strategies."""
    if n_trials <= 1:
        return 0.0
    return math.sqrt(sr_variance) * (
        (1 - EULER_GAMMA) * stats.norm.ppf(1 - 1 / n_trials)
        + EULER_GAMMA * stats.norm.ppf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(sr, n_obs, n_trials, sr_variance, skew=0.0, kurt=3.0):
    """PSR against the Sharpe a lucky pick among n_trials would show by chance."""
    return probabilistic_sharpe(sr, expected_max_sharpe(n_trials, sr_variance), n_obs, skew, kurt)


def block_bootstrap(returns, statistic, n_boot=10_000, block=6, seed=0):
    """Distribution of statistic(returns) under a circular block bootstrap."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n == 0:
        return np.array([])
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    samples = r[idx.reshape(n_boot, -1)[:, :n]]
    return np.array([statistic(s) for s in samples])


def max_drawdown(equity):
    equity = np.asarray(equity, dtype=float)
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1).min())


def cagr(equity, periods_per_year):
    equity = np.asarray(equity, dtype=float)
    if len(equity) < 2 or equity[0] <= 0:
        return 0.0
    years = (len(equity) - 1) / periods_per_year
    return float((equity[-1] / equity[0]) ** (1 / years) - 1) if years > 0 else 0.0


def summarize(daily_returns, n_trials=1, sr_variance=None, benchmark_returns=None):
    """Headline report for a daily return series (pd.Series indexed by date)."""
    daily = pd.Series(daily_returns).dropna()
    equity = (1 + daily).cumprod()
    monthly = (1 + daily).resample("ME").prod() - 1
    n, skew, kurt = _moments(monthly)
    sr_m = sharpe(monthly)
    # Without a measured spread across trials, assume annual Sharpe variance 0.5.
    var_m = sr_variance if sr_variance is not None else 0.5 / 12
    out = {
        "start": str(daily.index.min().date()) if len(daily) else None,
        "end": str(daily.index.max().date()) if len(daily) else None,
        "months": n,
        "cagr": cagr(equity.values, 252),
        "vol_annual": float(daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 else 0.0,
        "sharpe_annual": sharpe(daily) * math.sqrt(252),
        "max_drawdown": max_drawdown(equity.values),
        "psr_vs_0": probabilistic_sharpe(sr_m, 0.0, n, skew, kurt),
        "min_trl_months_vs_0": min_track_record_length(sr_m, 0.0, skew, kurt),
        "n_trials": n_trials,
        "dsr": deflated_sharpe(sr_m, n, n_trials, var_m, skew, kurt),
    }
    boot = block_bootstrap(monthly.values, lambda s: sharpe(s) * math.sqrt(12), n_boot=2000, block=6)
    out["sharpe_annual_p05"] = float(np.percentile(boot, 5)) if len(boot) else float("nan")
    if benchmark_returns is not None:
        bench = pd.Series(benchmark_returns).reindex(daily.index).fillna(0.0)
        excess_m = monthly - ((1 + bench).resample("ME").prod() - 1)
        out["excess_annual"] = float(excess_m.mean() * 12)
        out["excess_tstat"] = float(excess_m.mean() / (excess_m.std(ddof=1) / math.sqrt(len(excess_m)))) if len(excess_m) > 1 else 0.0
        out["benchmark_cagr"] = cagr((1 + bench).cumprod().values, 252)
        out["benchmark_sharpe_annual"] = sharpe(bench) * math.sqrt(252)
        out["benchmark_max_drawdown"] = max_drawdown((1 + bench).cumprod().values)
    return out


def probability_of_backtest_overfitting(returns_matrix, n_splits=16):
    """PBO via combinatorially symmetric cross-validation (Bailey et al.).

    returns_matrix: T x N array of per-period returns for N configurations.
    For every way to pick half of the n_splits blocks as in-sample, take the
    best in-sample configuration and record whether it lands in the bottom
    half out-of-sample. Returns the share of such splits (lambda <= 0).
    """
    from itertools import combinations

    m = np.asarray(returns_matrix, dtype=float)
    t, n = m.shape
    if n < 2 or t < n_splits:
        return float("nan")
    blocks = np.array_split(np.arange(t), n_splits)
    logits = []
    for chosen in combinations(range(n_splits), n_splits // 2):
        is_idx = np.concatenate([blocks[k] for k in chosen])
        oos_idx = np.concatenate([blocks[k] for k in range(n_splits) if k not in chosen])
        is_sr = np.array([sharpe(m[is_idx, j]) for j in range(n)])
        oos_sr = np.array([sharpe(m[oos_idx, j]) for j in range(n)])
        best = int(np.argmax(is_sr))
        rank = (oos_sr < oos_sr[best]).sum() + 0.5 * ((oos_sr == oos_sr[best]).sum() - 1)
        omega = (rank + 0.5) / n  # relative rank in (0, 1)
        logits.append(np.log(omega / (1 - omega)))
    return float(np.mean(np.array(logits) <= 0))
