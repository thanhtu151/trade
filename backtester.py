"""
Backtesting engine: replay strategy on historical data.

Measures win rate, EV/trade, monthly return, max drawdown, and Sharpe ratio.
"""

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def load_backtest_config_file():
    """Load backtest_config.json."""
    config_path = os.path.join(BASE_DIR, "backtest_config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "positive_ev_tickers": ["MBB", "ACB", "VCB", "TCB"],
            "optimal_config": {"atr_stop_mult": 1.0, "atr_target_mult": 2.0, "max_hold_days": 15},
        }


def generate_signals(df, ticker):
    """
    Create rule-based BUY/SELL signals from technical indicators.
    Does not call LLM because historical replay needs to be fast.
    """
    df = df.copy().sort_values("time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / (loss + 1e-9))

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()

    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_upper"] = bb_mid + 2 * bb_std

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma20"]

    signals = pd.DataFrame(index=df.index)
    signals["rsi_oversold"] = (df["rsi"] < 35).astype(int)
    signals["macd_bullish"] = (df["macd"] > df["macd_signal"]).astype(int)
    signals["sma_bullish"] = (df["sma20"] > df["sma50"]).astype(int)
    signals["bb_oversold"] = (df["close"] < df["bb_lower"] * 1.02).astype(int)
    signals["volume_confirm"] = ((df["volume_ratio"] > 1.2) & (df["close"] > df["close"].shift(1))).astype(int)
    signals["momentum"] = (df["close"] > df["close"].shift(5)).astype(int)

    df["confluence"] = signals.sum(axis=1)
    df["signal"] = 0
    df.loc[df["confluence"] >= 4, "signal"] = 1
    df.loc[df["confluence"] <= 1, "signal"] = -1
    return df


def apply_ensemble_filter(df, ticker):
    """
    Apply ensemble filter using only rows strictly before each simulated day.
    """
    df = df.copy()
    try:
        import joblib

        from train_ensemble import MODEL_DIR, build_features_extended

        ticker = str(ticker).upper()
        models = {}
        for name, suffix in (("xgb", "_xgb.pkl"), ("lgbm", "_lgbm.pkl"), ("rf", "_rf.pkl")):
            path = os.path.join(MODEL_DIR, f"{ticker}{suffix}")
            if not os.path.exists(path):
                continue
            payload = joblib.load(path)
            models[name] = payload
        if not models:
            raise FileNotFoundError(f"No ensemble pickle models found for {ticker}")

        window = 252
        ensemble_signals = [None] * len(df)
        high_confidence = [False] * len(df)

        for i in range(len(df)):
            if i < window:
                continue

            try:
                history_df = df.iloc[max(0, i - window) : i].copy()
                feature_df, feature_cols = build_features_extended(history_df, ticker=None)
                if feature_df is None or len(feature_df) == 0:
                    ensemble_signals[i] = 0
                    continue
                probs = []
                latest = feature_df.tail(1).copy()
                for payload in models.values():
                    model = payload.get("model")
                    model_cols = payload.get("feature_cols") or feature_cols
                    if model is None or not model_cols:
                        continue
                    for col in model_cols:
                        if col not in latest.columns:
                            latest[col] = 0.0
                    X_latest = latest[model_cols]
                    if not np.isfinite(X_latest.values).all():
                        continue
                    if model.__class__.__name__ == "RandomForestClassifier":
                        X_latest = X_latest.values
                    probs.append(float(model.predict_proba(X_latest)[0][1]))
                if not probs:
                    ensemble_signals[i] = 0
                    continue
                avg_prob = float(np.mean(probs))
                if avg_prob > 0.60:
                    ensemble_signals[i] = 1
                    high_confidence[i] = True
                elif avg_prob < 0.40:
                    ensemble_signals[i] = -1
                    high_confidence[i] = True
                else:
                    ensemble_signals[i] = 0
            except Exception:
                ensemble_signals[i] = 0

        df["ensemble_direction"] = ensemble_signals
        df["ensemble_high_confidence"] = high_confidence
        df["signal_filtered"] = df.get("signal", 0).copy()
        mask_block = (df["signal"] == 1) & (
            df["ensemble_direction"].isin([-1, 0]) | ~df["ensemble_high_confidence"].astype(bool)
        )
        df.loc[mask_block, "signal_filtered"] = 0

    except Exception as exc:
        print(f"  Ensemble filter unavailable: {exc}")
        df["signal_filtered"] = df.get("signal", 0)
        df["ensemble_direction"] = None
        df["ensemble_high_confidence"] = False

    return df


def simulate_trades(
    df,
    ticker,
    atr_stop_mult=1.5,
    atr_target_mult=3.0,
    max_hold_days=15,
    use_ensemble_filter=True,
):
    """
    Simulate long-only trades from signals.
    Entry is at close, stop checks daily low, target checks daily high.
    """
    signal_col = "signal_filtered" if use_ensemble_filter and "signal_filtered" in df.columns else "signal"

    trades = []
    in_trade = False
    entry_price = 0.0
    entry_date = None
    stop_loss = 0.0
    target = 0.0
    entry_atr = 0.0
    hold_days = 0

    for _, row in df.iterrows():
        if in_trade:
            hold_days += 1

            if row["low"] <= stop_loss:
                pnl_pct = (stop_loss - entry_price) / entry_price
                trades.append(
                    {
                        "ticker": ticker,
                        "entry_date": entry_date,
                        "exit_date": row["time"],
                        "entry_price": entry_price,
                        "exit_price": stop_loss,
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "exit_reason": "stop_loss",
                        "atr": entry_atr,
                    }
                )
                in_trade = False
                continue

            if row["high"] >= target:
                pnl_pct = (target - entry_price) / entry_price
                trades.append(
                    {
                        "ticker": ticker,
                        "entry_date": entry_date,
                        "exit_date": row["time"],
                        "entry_price": entry_price,
                        "exit_price": target,
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "exit_reason": "target",
                        "atr": entry_atr,
                    }
                )
                in_trade = False
                continue

            if hold_days >= max_hold_days:
                pnl_pct = (row["close"] - entry_price) / entry_price
                trades.append(
                    {
                        "ticker": ticker,
                        "entry_date": entry_date,
                        "exit_date": row["time"],
                        "entry_price": entry_price,
                        "exit_price": row["close"],
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "exit_reason": "timeout",
                        "atr": entry_atr,
                    }
                )
                in_trade = False

        if not in_trade and row.get(signal_col) == 1 and not pd.isna(row["atr"]):
            in_trade = True
            entry_price = float(row["close"])
            entry_date = row["time"]
            entry_atr = float(row["atr"])
            stop_loss = entry_price - atr_stop_mult * entry_atr
            target = entry_price + atr_target_mult * entry_atr
            hold_days = 0

    return trades


def calculate_metrics(trades, initial_capital=100_000_000):
    """Calculate performance metrics from simulated trades."""
    if not trades:
        return {
            "total_trades": 0,
            "win_trades": 0,
            "loss_trades": 0,
            "win_rate": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "ev_per_trade_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "total_return_pct": 0.0,
            "monthly_returns": {},
            "exit_reasons": {},
            "avg_hold_days": 0.0,
        }

    df = pd.DataFrame(trades)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]

    win_rate = len(wins) / len(df)
    avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    ev = win_rate * avg_win + (1 - win_rate) * avg_loss

    equity = initial_capital
    equity_curve = [equity]
    for _, trade in df.iterrows():
        equity = equity * (1 + trade["pnl_pct"] * 0.2)
        equity_curve.append(equity)

    peak = initial_capital
    max_dd = 0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)

    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["month"] = df["exit_date"].dt.to_period("M")
    monthly = df.groupby("month")["pnl_pct"].sum()

    trade_returns = df["pnl_pct"] * 0.2
    sharpe = (trade_returns.mean() / (trade_returns.std() + 1e-9)) * np.sqrt(252)
    gross_win = wins["pnl_pct"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl_pct"].sum()) if len(losses) > 0 else 0

    return {
        "total_trades": int(len(df)),
        "win_trades": int(len(wins)),
        "loss_trades": int(len(losses)),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win * 100, 2),
        "avg_loss_pct": round(avg_loss * 100, 2),
        "ev_per_trade_pct": round(ev * 100, 3),
        "profit_factor": round(gross_win / gross_loss if gross_loss else 0, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "total_return_pct": round((equity_curve[-1] / initial_capital - 1) * 100, 2),
        "monthly_returns": {str(k): round(v * 100, 2) for k, v in monthly.items()},
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
        "avg_hold_days": round(df["hold_days"].mean(), 1),
    }


def run_backtest(ticker, years=2, use_ensemble=True, atr_stop=1.5, atr_target=3.0):
    """Run full backtest for one ticker."""
    ticker = str(ticker).upper()
    print(f"\n{'=' * 50}")
    print(f"Backtesting {ticker} ({years} years)")
    print(f"Stop: {atr_stop}ATR | Target: {atr_target}ATR | Ensemble: {use_ensemble}")
    print("=" * 50)

    from data_fetcher import get_stock_data_cached

    df = get_stock_data_cached(ticker, years=years)
    df = generate_signals(df, ticker)

    if use_ensemble:
        print("  Applying ensemble filter...")
        df = apply_ensemble_filter(df, ticker)

    trades_raw = simulate_trades(
        df,
        ticker,
        atr_stop_mult=atr_stop,
        atr_target_mult=atr_target,
        use_ensemble_filter=False,
    )
    trades_filtered = simulate_trades(
        df,
        ticker,
        atr_stop_mult=atr_stop,
        atr_target_mult=atr_target,
        use_ensemble_filter=use_ensemble,
    )

    metrics_raw = calculate_metrics(trades_raw)
    metrics_filtered = calculate_metrics(trades_filtered)

    result = {
        "ticker": ticker,
        "years": years,
        "config": {
            "atr_stop": atr_stop,
            "atr_target": atr_target,
            "position_size_pct": 20,
            "transaction_costs_included": False,
        },
        "without_ensemble": metrics_raw,
        "with_ensemble": metrics_filtered,
        "ensemble_improvement": {
            "ev_change": round(metrics_filtered.get("ev_per_trade_pct", 0) - metrics_raw.get("ev_per_trade_pct", 0), 3),
            "win_rate_change": round(metrics_filtered.get("win_rate", 0) - metrics_raw.get("win_rate", 0), 4),
            "trades_filtered_out": metrics_raw.get("total_trades", 0) - metrics_filtered.get("total_trades", 0),
        },
        "backtested_at": datetime.now().isoformat(),
    }

    print("\nWITHOUT Ensemble Filter:")
    print(
        f"  Trades: {metrics_raw.get('total_trades')} | "
        f"Win rate: {metrics_raw.get('win_rate', 0):.1%} | "
        f"EV/trade: {metrics_raw.get('ev_per_trade_pct', 0):+.2f}% | "
        f"Max DD: {metrics_raw.get('max_drawdown_pct', 0):.1f}%"
    )

    print("\nWITH Ensemble Filter:")
    print(
        f"  Trades: {metrics_filtered.get('total_trades')} | "
        f"Win rate: {metrics_filtered.get('win_rate', 0):.1%} | "
        f"EV/trade: {metrics_filtered.get('ev_per_trade_pct', 0):+.2f}% | "
        f"Max DD: {metrics_filtered.get('max_drawdown_pct', 0):.1f}%"
    )

    print("\nEnsemble impact:")
    print(f"  EV change: {result['ensemble_improvement']['ev_change']:+.3f}%")
    print(f"  Trades filtered out: {result['ensemble_improvement']['trades_filtered_out']}")

    path = os.path.join(RESULTS_DIR, f"{ticker}_backtest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {path}")
    return result


def update_backtest_config(results, atr_stop=1.0, atr_target=2.0):
    """Update backtest_config.json after a portfolio backtest."""
    config_path = os.path.join(BASE_DIR, "backtest_config.json")
    positive_ev = []
    negative_ev = []
    ev_data = {}

    for ticker, result in results.items():
        ticker = str(ticker).upper()
        if "error" in result:
            continue
        metrics = result.get("without_ensemble", {})
        ev = metrics.get("ev_per_trade_pct", 0)
        win_rate = metrics.get("win_rate", 0)
        trades = metrics.get("total_trades", 0)
        ev_data[ticker] = {
            "ev": round(float(ev), 3),
            "win_rate": round(float(win_rate), 3),
            "trades": int(trades),
        }
        if ev > 0 and trades >= 5:
            positive_ev.append(ticker)
        else:
            negative_ev.append(ticker)

    positive_ev.sort(key=lambda item: ev_data[item]["ev"], reverse=True)
    negative_ev.sort(key=lambda item: ev_data[item]["ev"])

    config = {
        "optimal_config": {
            "atr_stop_mult": atr_stop,
            "atr_target_mult": atr_target,
            "max_hold_days": 15,
            "min_confluence": 4,
        },
        "positive_ev_tickers": positive_ev,
        "negative_ev_tickers": negative_ev,
        "ev_data": ev_data,
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print("\nUpdated backtest_config.json")
    print(f"  Positive EV: {positive_ev}")
    print(f"  Negative EV: {negative_ev}")
    return config


def run_portfolio_backtest(tickers, years=2, atr_stop=1.0, atr_target=2.0):
    """Backtest a list of tickers and auto-update config."""
    results = {}
    for ticker in tickers:
        try:
            results[str(ticker).upper()] = run_backtest(
                ticker,
                years=years,
                use_ensemble=False,
                atr_stop=atr_stop,
                atr_target=atr_target,
            )
        except Exception as exc:
            print(f"{ticker} ERROR: {exc}")
            results[str(ticker).upper()] = {"error": str(exc)}

    update_backtest_config(results, atr_stop=atr_stop, atr_target=atr_target)
    profitable = [
        ticker
        for ticker, result in results.items()
        if result.get("without_ensemble", {}).get("ev_per_trade_pct", -999) > 0
    ]
    print(f"\n{'=' * 50}")
    print("PORTFOLIO SUMMARY")
    print(f"  Profitable tickers: {profitable}")
    print(f"  Total tested: {len(results)}")
    return results
