"""
Backtester Pro using the backtesting.py library.

Falls back to the legacy backtester when backtesting.py is unavailable.
"""

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")
os.makedirs(RESULTS_DIR, exist_ok=True)

try:
    from backtesting import Backtest, Strategy
    from backtesting.lib import TrailingStrategy, crossover

    BACKTESTING_AVAILABLE = True
    BACKTESTING_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only when dependency is missing
    Backtest = None
    Strategy = object
    TrailingStrategy = object
    crossover = None
    BACKTESTING_AVAILABLE = False
    BACKTESTING_IMPORT_ERROR = exc


def load_backtest_config_file():
    """Load backtest_config.json with a safe default fallback."""
    config_path = os.path.join(BASE_DIR, "backtest_config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "positive_ev_tickers": ["MBB", "ACB", "VCB", "TCB"],
            "optimal_config": {"atr_stop_mult": 1.0, "atr_target_mult": 2.0, "max_hold_days": 15},
        }


def _to_numeric_frame(df):
    df = df.copy()
    for col in df.columns:
        if col != "time":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _rolling_rsi(close, period=14):
    series = pd.Series(np.asarray(close, dtype=float))
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rsi = 100 - 100 / (1 + gain / (loss + 1e-9))
    return rsi.to_numpy()


def _macd_hist(close):
    series = pd.Series(np.asarray(close, dtype=float))
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return (macd - signal).to_numpy()


def _sma(close, period):
    return pd.Series(np.asarray(close, dtype=float)).rolling(period).mean().to_numpy()


def _atr(high, low, close, period=14):
    high_s = pd.Series(np.asarray(high, dtype=float))
    low_s = pd.Series(np.asarray(low, dtype=float))
    close_s = pd.Series(np.asarray(close, dtype=float))
    tr = pd.concat(
        [
            high_s - low_s,
            (high_s - close_s.shift()).abs(),
            (low_s - close_s.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean().to_numpy()


def _volume_ratio(volume, period=20):
    series = pd.Series(np.asarray(volume, dtype=float))
    vol_ma = series.rolling(period).mean()
    return (series / vol_ma).to_numpy()


def _load_ensemble_signal_array(length):
    signal_file = os.path.join(BASE_DIR, "ensemble_signals_cache.json")
    if not os.path.exists(signal_file):
        return np.zeros(length, dtype=float)

    try:
        with open(signal_file, encoding="utf-8") as f:
            signals = json.load(f)
        arr = np.zeros(length, dtype=float)
        tail = signals[-length:]
        for idx, sig in enumerate(tail):
            arr[idx] = float(sig)
        return arr
    except Exception:
        return np.zeros(length, dtype=float)


def prepare_data(ticker, years=2):
    """
    Load cached VNStock data and convert it to backtesting.py format.
    Requires Open, High, Low, Close, Volume with a DatetimeIndex.
    """
    from data_fetcher import get_stock_data_cached

    df = get_stock_data_cached(ticker, years=years)
    if df is None or df.empty:
        raise ValueError(f"No data available for {ticker}")

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    if "time" not in df.columns:
        for candidate in ("date", "trading_date", "datetime"):
            if candidate in df.columns:
                df = df.rename(columns={candidate: "time"})
                break

    required = ["open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{ticker} missing columns: {missing}")

    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").set_index("time")
    df = _to_numeric_frame(df)
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    df.index.name = None
    return df


class VNConfluenceStrategy(Strategy):
    """
    ATR-based confluence strategy.
    Buy when confluence >= confluence_min.
    """

    atr_period = 14
    atr_stop = 1.0
    atr_target = 2.0
    rsi_period = 14
    rsi_oversold = 35
    confluence_min = 4

    def init(self):
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        volume = self.data.Volume

        self.rsi = self.I(_rolling_rsi, close, self.rsi_period)
        self.macd_hist = self.I(_macd_hist, close)
        self.sma20 = self.I(_sma, close, 20)
        self.sma50 = self.I(_sma, close, 50)
        self.atr = self.I(_atr, high, low, close, self.atr_period)
        self.vol_ratio = self.I(_volume_ratio, volume, 20)

    def next(self):
        confluence = 0
        if self.rsi[-1] < self.rsi_oversold:
            confluence += 1
        if self.macd_hist[-1] > 0:
            confluence += 1
        if self.sma20[-1] > self.sma50[-1]:
            confluence += 1
        if self.vol_ratio[-1] > 1.2 and self.data.Close[-1] > self.data.Close[-2]:
            confluence += 1
        if len(self.data.Close) > 5 and self.data.Close[-1] > self.data.Close[-6]:
            confluence += 1

        price = float(self.data.Close[-1])
        atr = float(self.atr[-1]) if not np.isnan(self.atr[-1]) else 0.0
        if not self.position and confluence >= self.confluence_min and atr > 0:
            sl = price - self.atr_stop * atr
            tp = price + self.atr_target * atr
            self.buy(sl=sl, tp=tp)


class VNEnsembleStrategy(Strategy):
    """
    Confluence strategy with a precomputed ensemble direction filter.
    """

    atr_stop = 1.0
    atr_target = 2.0
    confluence_min = 3

    def init(self):
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        volume = self.data.Volume

        self.rsi = self.I(_rolling_rsi, close, 14)
        self.macd_hist = self.I(_macd_hist, close)
        self.sma20 = self.I(_sma, close, 20)
        self.sma50 = self.I(_sma, close, 50)
        self.atr = self.I(_atr, high, low, close, 14)
        self.vol_ratio = self.I(_volume_ratio, volume, 20)
        self.ensemble_bull = self.I(lambda: _load_ensemble_signal_array(len(self.data.Close)))

    def next(self):
        confluence = 0
        if self.rsi[-1] < 35:
            confluence += 1
        if self.macd_hist[-1] > 0:
            confluence += 1
        if self.sma20[-1] > self.sma50[-1]:
            confluence += 1
        if self.vol_ratio[-1] > 1.2:
            confluence += 1

        ensemble_ok = self.ensemble_bull[-1] >= 0
        price = float(self.data.Close[-1])
        atr = float(self.atr[-1]) if not np.isnan(self.atr[-1]) else 0.0

        if not self.position and confluence >= self.confluence_min and ensemble_ok and atr > 0:
            sl = price - self.atr_stop * atr
            tp = price + self.atr_target * atr
            self.buy(sl=sl, tp=tp)


def _stats_value(stats, key, default=0.0):
    try:
        value = stats.get(key, default)
        if value is None:
            return default
        return value
    except Exception:
        return default


def _build_result_from_stats(ticker, strategy_name, years, commission, stats):
    return {
        "ticker": ticker,
        "strategy": strategy_name,
        "years": years,
        "commission": commission,
        "return_pct": round(float(_stats_value(stats, "Return [%]")), 2),
        "buy_hold_pct": round(float(_stats_value(stats, "Buy & Hold Return [%]")), 2),
        "win_rate": round(float(_stats_value(stats, "Win Rate [%]")) / 100, 4),
        "profit_factor": round(float(_stats_value(stats, "Profit Factor")), 3),
        "sharpe": round(float(_stats_value(stats, "Sharpe Ratio")), 3),
        "sortino": round(float(_stats_value(stats, "Sortino Ratio")), 3),
        "calmar": round(float(_stats_value(stats, "Calmar Ratio")), 3),
        "max_drawdown_pct": round(float(_stats_value(stats, "Max. Drawdown [%]")), 2),
        "trades": int(_stats_value(stats, "# Trades", 0)),
        "expectancy_pct": round(float(_stats_value(stats, "Expectancy [%]")), 3),
        "kelly": round(float(_stats_value(stats, "Kelly Criterion")), 4),
        "backtested_at": datetime.now().isoformat(),
    }


def _legacy_fallback_backtest(ticker, years, atr_stop, atr_target):
    from backtester import run_backtest

    legacy = run_backtest(
        ticker,
        years=years,
        use_ensemble=True,
        atr_stop=atr_stop,
        atr_target=atr_target,
    )
    legacy_metrics = legacy.get("with_ensemble", {}) or {}
    result = {
        "ticker": ticker,
        "strategy": "LegacyFallback",
        "years": years,
        "commission": 0.0015,
        "return_pct": float(legacy_metrics.get("total_return_pct", 0.0)),
        "buy_hold_pct": float(legacy_metrics.get("buy_hold_pct", 0.0)),
        "win_rate": float(legacy_metrics.get("win_rate", 0.0)),
        "profit_factor": float(legacy_metrics.get("profit_factor", 0.0)),
        "sharpe": float(legacy_metrics.get("sharpe_ratio", 0.0)),
        "sortino": float(legacy_metrics.get("sortino_ratio", 0.0)),
        "calmar": float(legacy_metrics.get("calmar_ratio", 0.0)),
        "max_drawdown_pct": float(legacy_metrics.get("max_drawdown_pct", 0.0)),
        "trades": int(legacy_metrics.get("total_trades", 0)),
        "expectancy_pct": float(legacy_metrics.get("ev_per_trade_pct", 0.0)),
        "kelly": 0.0,
        "backtested_at": datetime.now().isoformat(),
    }
    stats = {
        "Return [%]": result["return_pct"],
        "Buy & Hold Return [%]": result["buy_hold_pct"],
        "Win Rate [%]": result["win_rate"] * 100,
        "Profit Factor": result["profit_factor"],
        "Sharpe Ratio": result["sharpe"],
        "Sortino Ratio": result["sortino"],
        "Calmar Ratio": result["calmar"],
        "Max. Drawdown [%]": result["max_drawdown_pct"],
        "# Trades": result["trades"],
        "Expectancy [%]": result["expectancy_pct"],
        "Kelly Criterion": result["kelly"],
    }
    return result, stats, None


def run_backtest_pro(
    ticker,
    years=2,
    strategy_class=VNConfluenceStrategy,
    cash=100_000_000,
    commission=0.0015,
    save_html=True,
    **strategy_params,
):
    """
    Run a backtest with backtesting.py and return:
    (result_dict, stats_object, backtest_object)
    """
    ticker = str(ticker).upper()

    if not BACKTESTING_AVAILABLE:
        print(f"[backtesting.py unavailable] falling back to legacy backtester: {BACKTESTING_IMPORT_ERROR}")
        return _legacy_fallback_backtest(
            ticker,
            years=years,
            atr_stop=float(strategy_params.get("atr_stop", 1.0)),
            atr_target=float(strategy_params.get("atr_target", 2.0)),
        )

    print(f"\n{'=' * 55}")
    print(f"[backtesting.py] {ticker} - {strategy_class.__name__}")
    print(f"  Years: {years} | Commission: {commission:.2%} | Cash: {cash:,.0f}")
    print("=" * 55)

    df = prepare_data(ticker, years=years)
    print(f"  Data: {len(df)} rows ({df.index[0].date()} -> {df.index[-1].date()})")

    bt = Backtest(
        df,
        strategy_class,
        cash=cash,
        commission=commission,
        exclusive_orders=True,
        trade_on_close=True,
    )

    stats = bt.run(**strategy_params) if strategy_params else bt.run()

    print("\nResults:")
    print(f"  Return:        {stats['Return [%]']:+.2f}%")
    print(f"  Buy&Hold:      {stats['Buy & Hold Return [%]']:+.2f}%")
    print(f"  Win Rate:      {stats['Win Rate [%]']:.1f}%")
    print(f"  Profit Factor: {stats['Profit Factor']:.2f}")
    print(f"  Sharpe:        {stats['Sharpe Ratio']:.3f}")
    print(f"  Sortino:       {stats['Sortino Ratio']:.3f}")
    print(f"  Calmar:        {_stats_value(stats, 'Calmar Ratio'):.3f}")
    print(f"  Max Drawdown:  {stats['Max. Drawdown [%]']:.1f}%")
    print(f"  # Trades:      {stats['# Trades']}")
    print(f"  Expectancy:    {stats['Expectancy [%]']:.2f}%")
    print(f"  Kelly:         {stats['Kelly Criterion']:.3f}")

    chart_path = os.path.join(RESULTS_DIR, f"{ticker}_pro_chart.html")
    if save_html:
        try:
            bt.plot(filename=chart_path, open_browser=False)
            print(f"\n  Chart saved: {chart_path}")
        except Exception as exc:
            print(f"\n  Chart save failed: {exc}")

    result = _build_result_from_stats(ticker, strategy_class.__name__, years, commission, stats)
    json_path = os.path.join(RESULTS_DIR, f"{ticker}_pro_backtest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result, stats, bt


def optimize_strategy(ticker, years=2):
    """
    Search for better ATR stop/target/confluence parameters.
    Uses bt.optimize() when available, otherwise falls back to a coarse grid search.
    """
    ticker = str(ticker).upper()
    if BACKTESTING_AVAILABLE:
        print(f"\nOptimizing {ticker} with backtesting.py...")
        df = prepare_data(ticker, years=years)
        bt = Backtest(
            df,
            VNConfluenceStrategy,
            cash=100_000_000,
            commission=0.0015,
            exclusive_orders=True,
            trade_on_close=True,
        )

        stats = bt.optimize(
            atr_stop=[0.8, 1.0, 1.2, 1.5],
            atr_target=[1.5, 2.0, 2.5, 3.0],
            confluence_min=[3, 4, 5],
            maximize="Expectancy [%]",
            constraint=lambda p: p.atr_target > p.atr_stop,
        )

        best = {
            "ticker": ticker,
            "atr_stop": float(stats._strategy.atr_stop),
            "atr_target": float(stats._strategy.atr_target),
            "confluence_min": int(stats._strategy.confluence_min),
            "expectancy": round(float(stats["Expectancy [%]"]), 3),
            "win_rate": round(float(stats["Win Rate [%]"]) / 100, 4),
            "sharpe": round(float(stats["Sharpe Ratio"]), 3),
            "trades": int(stats["# Trades"]),
        }
    else:
        print(f"\nOptimizing {ticker} with legacy fallback...")
        from backtester import run_backtest

        candidates = []
        for atr_stop in (0.8, 1.0, 1.2, 1.5):
            for atr_target in (1.5, 2.0, 2.5, 3.0):
                if atr_target <= atr_stop:
                    continue
                for confluence_min in (3, 4, 5):
                    try:
                        res = run_backtest(
                            ticker,
                            years=years,
                            use_ensemble=True,
                            atr_stop=atr_stop,
                            atr_target=atr_target,
                        )
                        metrics = res.get("with_ensemble", {}) or {}
                        expectancy = float(metrics.get("ev_per_trade_pct", 0.0))
                        trades = int(metrics.get("total_trades", 0))
                        candidates.append(
                            {
                                "ticker": ticker,
                                "atr_stop": atr_stop,
                                "atr_target": atr_target,
                                "confluence_min": confluence_min,
                                "expectancy": expectancy,
                                "win_rate": float(metrics.get("win_rate", 0.0)),
                                "sharpe": float(metrics.get("sharpe_ratio", 0.0)),
                                "trades": trades,
                            }
                        )
                    except Exception:
                        continue
        best = max(candidates, key=lambda item: item["expectancy"], default=None)
        if best is None:
            raise RuntimeError(f"Unable to optimize {ticker}")

    opt_path = os.path.join(RESULTS_DIR, f"{ticker}_optimal_params.json")
    with open(opt_path, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2)

    print(
        f"  Best params: stop={best['atr_stop']} target={best['atr_target']} "
        f"confluence={best['confluence_min']}"
    )
    print(
        f"  Expectancy: {best['expectancy']}% | Win rate: {best['win_rate']:.1%} "
        f"| Sharpe: {best['sharpe']}"
    )
    return best


def run_portfolio_backtest_pro(tickers, years=2, optimize=False):
    """
    Backtest a portfolio and update backtest_config.json.
    """
    results = {}
    optimal_params = {}

    for ticker in tickers:
        ticker = str(ticker).upper()
        try:
            if optimize:
                params = optimize_strategy(ticker, years=years)
                optimal_params[ticker] = params
                result, _, _ = run_backtest_pro(
                    ticker,
                    years=years,
                    atr_stop=params["atr_stop"],
                    atr_target=params["atr_target"],
                    confluence_min=params["confluence_min"],
                )
            else:
                result, _, _ = run_backtest_pro(ticker, years=years)
            results[ticker] = result
        except Exception as exc:
            print(f"{ticker} ERROR: {exc}")
            results[ticker] = {"error": str(exc)}

    positive_ev = [
        ticker
        for ticker, result in results.items()
        if result.get("expectancy_pct", -999) > 0 and result.get("trades", 0) >= 5
    ]
    positive_ev.sort(key=lambda ticker: results[ticker].get("expectancy_pct", 0), reverse=True)

    config_path = os.path.join(BASE_DIR, "backtest_config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = {}

    config["positive_ev_tickers"] = positive_ev
    config["ev_data"] = {
        ticker: {
            "ev": result.get("expectancy_pct", 0),
            "win_rate": result.get("win_rate", 0),
            "trades": result.get("trades", 0),
            "sharpe": result.get("sharpe", 0),
            "profit_factor": result.get("profit_factor", 0),
        }
        for ticker, result in results.items()
        if "error" not in result
    }
    if optimize and optimal_params:
        config["optimal_params_per_ticker"] = optimal_params
    config["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    config["backtester"] = "backtesting.py"

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print("\nUpdated backtest_config.json")
    print(f"  Positive EV tickers: {positive_ev}")
    return results

