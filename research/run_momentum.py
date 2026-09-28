"""Pre-registered momentum experiment on the point-in-time VN universe.

Trials are fixed before looking at results (see TRIALS). Every run is appended
to research/trials.jsonl so the trial count used by the deflated Sharpe ratio
is honest. The holdout period is evaluated once; a lock file records that and
later runs refuse to repeat it unless --force-holdout is given (and logged).

    python -m research.run_momentum dev          # development period only
    python -m research.run_momentum holdout      # one-shot holdout evaluation
"""

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research import metrics
from research.data import latest_snapshot, load_prices
from research.engine import Config, Costs, Panel, simulate
from research.strategies import BuyAndHold, Momentum, random_picker

ROOT = Path(__file__).resolve().parent
TRIAL_LOG = ROOT / "trials.jsonl"
HOLDOUT_LOCK = ROOT / "holdout_used.json"
REPORT_DIR = ROOT / "reports"

DEV = ("2011-01-01", "2023-12-31")
HOLDOUT = ("2024-01-01", None)
PRIMARY = "mom_6_1"
TRIALS = {
    "mom_6_1": dict(lookback=126, skip=21),
    "mom_12_1": dict(lookback=252, skip=21),
    "mom_6_1_regime0": dict(lookback=126, skip=21, regime_symbol="VNINDEX", regime_exposure=0.0),
    "mom_6_1_regime50": dict(lookback=126, skip=21, regime_symbol="VNINDEX", regime_exposure=0.5),
}
# Drops symbols whose 60-session median traded value never reaches this level
# (thousand VND, i.e. 0.3 bn VND/day). They can never make a top-100 liquidity
# universe, so removing them only saves memory.
MIN_EVER_VALUE = 3e5
PLACEBO_RUNS = 200


def load_panel(snapshot=None):
    prices = load_prices(snapshot)
    prices = prices.dropna(subset=["close"])
    value = (prices["close"] * prices["volume"]).groupby(prices["symbol"]).transform(
        lambda s: s.rolling(60, min_periods=30).median())
    peak = value.groupby(prices["symbol"]).max()
    keep = set(peak[peak >= MIN_EVER_VALUE].index) | {"VNINDEX", "VN30", "E1VFVN30"}
    return Panel(prices[prices["symbol"].isin(keep)].copy())


def index_returns(panel, symbol, start, end):
    close = panel.frame("last_close")[symbol]
    r = close.pct_change().fillna(0.0)
    return r.loc[start:end] if end else r.loc[start:]


def turnover(trades, curve):
    if trades.empty:
        return 0.0
    traded = (trades["qty"] * trades["price"]).sum()
    years = len(curve) / 252
    return float(traded / 2 / curve["equity"].mean() / years) if years > 0 else 0.0


def by_year(returns):
    return {str(k): float(v) for k, v in ((1 + returns).groupby(returns.index.year).prod() - 1).items()}


def run_trial(panel, name, params, start, end, costs=None):
    strat = Momentum(panel, **params)
    out = simulate(panel, strat, Config(costs=costs or Costs()), start=start, end=end)
    out["strategy"] = strat
    return out


def evaluate(panel, period, label):
    start, end = period
    bench = index_returns(panel, "VNINDEX", start, end)
    vn30 = index_returns(panel, "VN30", start, end)
    results, monthly_sr, monthly_returns = {}, {}, {}
    for name, params in TRIALS.items():
        t0 = time.time()
        net = run_trial(panel, name, params, start, end)
        gross = run_trial(panel, name, params, start, end, costs=Costs(0, 0, 0, 0))
        r = net["curve"]["return"]
        monthly = (1 + r).resample("ME").prod() - 1
        monthly_sr[name] = metrics.sharpe(monthly)
        monthly_returns[name] = monthly
        gross_total = gross["curve"]["equity"].iloc[-1] / gross["curve"]["equity"].iloc[0] - 1
        net_total = net["curve"]["equity"].iloc[-1] / net["curve"]["equity"].iloc[0] - 1
        trades = net["trades"]
        sells = trades[trades["side"] == "SELL"] if not trades.empty else trades
        years = by_year(r)
        best_year = max(years, key=years.get) if years else None
        without_best = r[r.index.year != int(best_year)] if best_year else r
        results[name] = {
            "summary": None,
            "gross_total_return": float(gross_total),
            "net_total_return": float(net_total),
            "cost_share_of_gross": float((gross_total - net_total) / gross_total) if gross_total > 0 else None,
            "turnover_per_year": turnover(trades, net["curve"]),
            "trades": int(len(trades)),
            "win_rate_sells": float((sells["pnl"] > 0).mean()) if len(sells) else None,
            "stops": int((sells["reason"] == "stop").sum()) if len(sells) else 0,
            "delisted_writeoffs": int((sells["reason"] == "delisted").sum()) if len(sells) else 0,
            "by_year": years,
            "cagr_without_best_year": metrics.cagr((1 + without_best).cumprod().values, 252),
            "vs_vn30": metrics.summarize(r, benchmark_returns=vn30),
            "seconds": round(time.time() - t0, 1),
            "_returns": r,
        }
    # DSR uses the spread of monthly Sharpe across every trial run in this period.
    sr_var = float(np.var(list(monthly_sr.values()), ddof=1)) if len(monthly_sr) > 1 else None
    n_trials = max(len(TRIALS), count_logged_trials())
    for name, res in results.items():
        res["summary"] = metrics.summarize(res.pop("_returns"), n_trials=n_trials,
                                           sr_variance=sr_var if sr_var and sr_var > 0 else None,
                                           benchmark_returns=bench)
    for name in TRIALS:
        log_trial(label, name, TRIALS[name], period, results[name]["summary"])
    matrix = pd.DataFrame(monthly_returns).dropna().to_numpy()
    pbo = metrics.probability_of_backtest_overfitting(matrix, n_splits=8) if len(matrix) >= 16 else None
    return results, {"VNINDEX": metrics.summarize(bench), "VN30": metrics.summarize(vn30)}, pbo


def placebo(panel, period, n_runs=PLACEBO_RUNS):
    start, end = period
    sharpes = []
    for seed in range(n_runs):
        strat = Momentum(panel, **TRIALS[PRIMARY], picker=random_picker(seed))
        out = simulate(panel, strat, Config(), start=start, end=end)
        sharpes.append(metrics.sharpe(out["curve"]["return"]) * math.sqrt(252))
    return np.array(sharpes)


def gate1(res, benchmarks, pbo):
    """Backtest -> paper criteria from the research brief, checked on the primary trial."""
    s = res["summary"]
    checks = {
        "sharpe_ge_0.8": s["sharpe_annual"] >= 0.8,
        "sharpe_beats_vn30": s["sharpe_annual"] > benchmarks["VN30"]["sharpe_annual"],
        "dsr_ge_0.95": (s["dsr"] or 0) >= 0.95,
        "pbo_lt_0.2": pbo is not None and pbo < 0.2,
        "excess_tstat_vs_vn30_ge_2": (res["vs_vn30"].get("excess_tstat") or 0) >= 2,
        "max_drawdown_le_30pct": s["max_drawdown"] >= -0.30,
        "costs_le_30pct_of_gross": res["cost_share_of_gross"] is not None and res["cost_share_of_gross"] <= 0.30,
        "bootstrap_sharpe_p05_gt_0": s["sharpe_annual_p05"] > 0,
        "positive_without_best_year": res["cagr_without_best_year"] > 0,
    }
    return {"passed": all(checks.values()), "checks": checks}


def count_logged_trials():
    if not TRIAL_LOG.exists():
        return 0
    names = set()
    for line in TRIAL_LOG.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("period_label") == "dev":
            names.add(json.dumps(row["params"], sort_keys=True))
    return len(names)


def log_trial(label, name, params, period, summary):
    row = {"at": datetime.now().isoformat(timespec="seconds"), "period_label": label, "name": name,
           "params": params, "period": list(period), "snapshot": latest_snapshot().name,
           "sharpe_annual": summary["sharpe_annual"], "cagr": summary["cagr"], "dsr": summary["dsr"]}
    with TRIAL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not math.isfinite(float(obj)) else round(float(obj), 6)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("period", choices=("dev", "holdout"))
    parser.add_argument("--force-holdout", action="store_true")
    parser.add_argument("--placebo", type=int, default=PLACEBO_RUNS)
    args = parser.parse_args()

    if args.period == "holdout" and HOLDOUT_LOCK.exists() and not args.force_holdout:
        raise SystemExit(f"holdout already evaluated: {HOLDOUT_LOCK.read_text()}")
    period = DEV if args.period == "dev" else HOLDOUT
    panel = load_panel()
    print(f"panel: {len(panel.symbols)} symbols, {len(panel.dates)} sessions "
          f"{panel.dates[0].date()}..{panel.dates[-1].date()}")
    results, benchmarks, pbo = evaluate(panel, period, args.period)
    # Cost sensitivity for the primary trial (same rules, cheaper execution): not a new trial.
    low_cost = run_trial(panel, PRIMARY, TRIALS[PRIMARY], *period,
                         costs=Costs(brokerage=0.0, exchange_fee=0.0003, sell_tax=0.001, slippage=0.001))
    low_cost_summary = metrics.summarize(low_cost["curve"]["return"])
    placebo_sharpes = placebo(panel, period, args.placebo) if args.placebo else np.array([])
    primary_sharpe = results[PRIMARY]["summary"]["sharpe_annual"]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot": latest_snapshot().name,
        "period": args.period, "range": list(period),
        "universe_symbols_in_panel": len(panel.symbols),
        "trials": results, "benchmarks": benchmarks, "pbo": pbo,
        "primary_low_cost": low_cost_summary,
        "gate1": gate1(results[PRIMARY], benchmarks, pbo),
        "placebo": {
            "runs": int(len(placebo_sharpes)),
            "sharpe_p50": float(np.median(placebo_sharpes)) if len(placebo_sharpes) else None,
            "sharpe_p95": float(np.percentile(placebo_sharpes, 95)) if len(placebo_sharpes) else None,
            "primary_percentile": float((placebo_sharpes < primary_sharpe).mean()) if len(placebo_sharpes) else None,
        },
    }
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"momentum_{args.period}.json"
    path.write_text(json.dumps(_clean(report), ensure_ascii=False, indent=1), encoding="utf-8")
    if args.period == "holdout":
        HOLDOUT_LOCK.write_text(json.dumps({"evaluated_at": report["generated_at"], "snapshot": report["snapshot"],
                                            "forced": args.force_holdout}))
    print(path)


if __name__ == "__main__":
    main()
