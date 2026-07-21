import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from vnstock.api.quote import Quote
from llm_router import call_llm, call_llm_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("auto_trader")
PORTFOLIO_FILE = os.path.join(BASE_DIR, "paper_portfolio.json")
PORTFOLIO_LOCK_FILE = PORTFOLIO_FILE + ".lock"
TRADES_FILE = os.path.join(BASE_DIR, "paper_trades.json")
AI_FUND_CONFIG_FILE = os.path.join(BASE_DIR, "paper_ai_fund_config.json")
AI_FUND_EQUITY_FILE = os.path.join(BASE_DIR, "paper_ai_fund_equity.json")
BACKTEST_CONFIG_FILE = os.path.join(BASE_DIR, "backtest_config.json")
VNSTOCK_FETCH_WORKER = os.path.join(BASE_DIR, "vnstock_fetch_worker.py")
INITIAL_CASH = 100_000_000.0
MAX_POSITION_PCT = 0.20
STOP_LOSS_PCT = 0.05
TAKE_PROFIT_PCT = 0.15
OLLAMA_TIMEOUT_SECONDS = 8
VNSTOCK_FETCH_TIMEOUT_SECONDS = 30
VNSTOCK_MIN_DELAY_SECONDS = 1.05
DEFAULT_SYMBOLS = [
    "VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "STB", "HDB", "VIB",
    "SSI", "VND", "HCM", "VCI", "HPG", "VIC", "VHM", "FPT", "MWG", "VNM",
]


@contextmanager
def _portfolio_file_lock():
    with open(PORTFOLIO_LOCK_FILE, "a+b") as lock_f:
        try:
            if os.name == "nt":
                import msvcrt

                lock_f.seek(0)
                msvcrt.locking(lock_f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_f.seek(0)
                    msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def apply_auto_trader_style():
    st.markdown(
        """
    <style>
    .stApp { background:#07111f; color:#f8fafc; }
    .block-container { padding-top:1.2rem; max-width:1500px; }
    h1, h2, h3, p, span, div, label { color:#f8fafc; }
    .metric-card {
        background:#0f1b2d;
        border:1px solid #334155;
        border-radius:8px;
        padding:0.9rem;
        min-height:5.6rem;
    }
    .muted { color:#cbd5e1; font-size:0.86rem; }
    .buy { color:#22c55e; font-weight:800; }
    .sell { color:#ef4444; font-weight:800; }
    .hold { color:#f59e0b; font-weight:800; }
    div[data-testid="stDataFrame"] { background:#f8fafc; color:#0f172a; }
    </style>
    """,
        unsafe_allow_html=True,
    )


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if data is not None else default
        except (json.JSONDecodeError, OSError):
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _safe_read_portfolio():
    """Read portfolio JSON with retries while another process is replacing it."""
    default = {"cash": 0.0, "positions": {}, "updated_at": "unknown"}
    for attempt in range(5):
        try:
            with _portfolio_file_lock():
                with open(PORTFOLIO_FILE, encoding="utf-8") as f:
                    content = f.read()
            if not content.strip():
                raise ValueError("Empty portfolio file")
            data = json.loads(content)
            return data if isinstance(data, dict) else default
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == 4:
                log.error("portfolio.json corrupted or empty: %s", exc)
                return default
            time.sleep(0.1)
        except FileNotFoundError:
            return default_portfolio()
        except Exception as exc:
            log.warning("Read portfolio attempt %s failed: %s", attempt + 1, exc)
            time.sleep(0.1)
    return default


def _safe_write_portfolio(portfolio):
    """Atomically write portfolio JSON via temp file + os.replace."""
    tmp_path = PORTFOLIO_FILE + ".tmp"
    try:
        content = json.dumps(portfolio, ensure_ascii=False, indent=2)
        with _portfolio_file_lock():
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, PORTFOLIO_FILE)
    except Exception as exc:
        log.error("Write portfolio failed: %s", exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise


def load_backtest_config():
    """Load backtest results to know which tickers have positive EV."""
    try:
        with open(BACKTEST_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_tradeable_tickers(watchlist):
    """
    Keep only positive-EV tickers from the current watchlist.
    This is a universe filter, not a settlement-cycle or execution-timing rule.
    Falls back to full watchlist when config is missing, empty, or too restrictive.
    """
    watchlist = [str(t).upper() for t in (watchlist or []) if str(t).strip()]
    config = load_backtest_config()
    if config is None:
        return watchlist, "No backtest config - using full watchlist"

    positive_ev = [str(t).upper() for t in config.get("positive_ev_tickers", [])]
    if not positive_ev:
        return watchlist, "No positive EV tickers found - using full watchlist"

    tradeable = [ticker for ticker in watchlist if ticker in positive_ev]
    if len(tradeable) < 3:
        return watchlist, "Too few positive EV tickers - using full watchlist"

    return tradeable, f"Filtered to {len(tradeable)} positive-EV tickers: {tradeable}"


def get_optimal_atr_config():
    """Return ATR config from backtest results."""
    fallback = {"atr_stop_mult": 1.0, "atr_target_mult": 2.0, "max_hold_days": 15}
    config = load_backtest_config()
    if not config:
        return fallback
    optimal = config.get("optimal_config") or {}
    fallback.update({k: optimal[k] for k in fallback if k in optimal})
    return fallback


def get_ticker_ev(ticker):
    """Return EV info from backtest config."""
    config = load_backtest_config()
    if config:
        return (config.get("ev_data") or {}).get(str(ticker).upper(), {})
    return {}


def apply_ev_score_adjustment(ticker, score):
    ev_info = get_ticker_ev(ticker)
    ev = float(ev_info.get("ev", 0) or 0)
    if ev > 0.5:
        score += 20
    elif ev > 0:
        score += 10
    elif ev < 0:
        score -= 15
    return round(max(0, min(100, score)), 1)


def stage1_quick_scan(tickers, top_n=10):
    """
    Stage 1: quick rule-based scan without LLM or ensemble.
    Returns the top_n tickers by weighted score.
    """
    import pandas as pd

    from data_fetcher import get_news_sentiment_fast, get_stock_data_cached, get_weekly_trend
    from learning_engine import get_signal_weight

    tickers = [str(t).upper() for t in (tickers or []) if str(t).strip()]
    results = []

    for ticker in tickers:
        try:
            df = get_stock_data_cached(ticker, years=0.5)
            if df is None or len(df) < 50:
                continue

            close = df["close"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            volume = df["volume"].astype(float)

            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi_series = 100 - 100 / (1 + gain / (loss + 1e-9))
            rsi = float(rsi_series.iloc[-1])

            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd = ema12 - ema26
            macd_signal = macd.ewm(span=9).mean()
            macd_val = float(macd.iloc[-1])
            macd_sig_val = float(macd_signal.iloc[-1])

            sma20 = float(close.rolling(20).mean().iloc[-1])
            sma50 = float(close.rolling(50).mean().iloc[-1])

            vol_ma20 = volume.rolling(20).mean()
            vol_ratio = float((volume / (vol_ma20 + 1e-9)).iloc[-1])

            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            last_close = float(close.iloc[-1])
            atr_pct = (atr / last_close * 100) if last_close else 0.0

            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_pos = float(((close - (bb_mid - 2 * bb_std)) / (4 * bb_std + 1e-9)).iloc[-1])

            momentum_5 = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0

            weekly = get_weekly_trend(ticker)
            weekly_trend = int(weekly.get("trend", 0)) if isinstance(weekly, dict) else 0
            weekly_rsi = float(weekly.get("rsi_weekly", 50)) if isinstance(weekly, dict) else 50.0
            news_score = float(get_news_sentiment_fast(ticker))

            score = 0.0
            signals = {}

            if rsi < 35:
                score += 1.5
                signals["rsi"] = "oversold"
            elif rsi < 45:
                score += 0.5
                signals["rsi"] = "low"

            if macd_val > macd_sig_val:
                score += 1.0
                signals["macd"] = "bullish"

            if sma20 > sma50:
                score += 1.0
                signals["sma"] = "golden_cross"

            if vol_ratio > 1.5 and len(close) >= 2 and close.iloc[-1] > close.iloc[-2]:
                score += 1.5
                signals["volume"] = "surge"
            elif vol_ratio > 1.2:
                score += 0.5
                signals["volume"] = "above_avg"

            if bb_pos < 0.2:
                score += 1.0
                signals["bb"] = "oversold"

            if momentum_5 > 0.01:
                score += 0.5
                signals["momentum"] = "positive"

            if weekly_trend == -1:
                score -= 1.5
                signals["weekly"] = "downtrend"
            elif weekly_trend == 1:
                score += 0.5
                signals["weekly"] = "uptrend"

            if news_score > 0.3:
                score += 1.0
                signals["news"] = "positive"
            elif news_score < -0.3:
                score -= 1.0
                signals["news"] = "negative"

            score = max(0.0, score)
            weight = 1.0
            try:
                weight = float(get_signal_weight(ticker))
            except Exception:
                weight = 1.0

            weighted_score = score * weight

            results.append(
                {
                    "ticker": ticker,
                    "score": round(score, 2),
                    "weighted_score": round(weighted_score, 2),
                    "weight": round(weight, 2),
                    "rsi": round(rsi, 1),
                    "macd_bull": macd_val > macd_sig_val,
                    "sma_bull": sma20 > sma50,
                    "vol_ratio": round(vol_ratio, 2),
                    "atr_pct": round(atr_pct, 2),
                    "bb_pos": round(bb_pos, 2),
                    "weekly_trend": weekly_trend,
                    "weekly_rsi": round(weekly_rsi, 1),
                    "news_sentiment": round(news_score, 2),
                    "signals": signals,
                    "price": round(last_close, 1),
                }
            )
        except Exception as exc:
            log.warning("  Stage1 %s: %s", ticker, exc)

    results.sort(key=lambda item: item["weighted_score"], reverse=True)
    top = results[: max(0, int(top_n))]
    log.info(
        "Stage1: Scanned %s/%s tickers - Top %s: %s",
        len(results),
        len(tickers),
        len(top),
        [row["ticker"] for row in top],
    )
    return top


def _get_market_regime_simple():
    """Get market regime từ cached analysis_results nếu có."""
    try:
        import json
        import os

        analysis_path = os.path.join(BASE_DIR, "analysis_results.json")
        if os.path.exists(analysis_path):
            with open(analysis_path, encoding="utf-8") as f:
                analysis = json.load(f)
            if isinstance(analysis, dict):
                return analysis.get("market_regime", "UNKNOWN")
    except Exception:
        pass
    return "UNKNOWN"


def stage2_deep_analysis(stage1_results, use_llm=True, use_ensemble=True, use_debate=True):
    """
    Stage 2: deep analysis for Stage 1 top candidates only.
    """
    from data_fetcher import get_news_sentiment_fast, get_stock_data_cached, get_weekly_trend
    from learning_engine import build_llm_context, log_prediction

    call_llm_json = None
    if use_llm:
        try:
            from llm_router import call_llm_json as _call_llm_json

            call_llm_json = _call_llm_json
        except Exception as exc:
            log.warning("  Stage2 LLM unavailable: %s", exc)
            use_llm = False

    try:
        from train_ensemble import ensemble_predict
    except Exception:
        ensemble_predict = None

    results = []
    for item in stage1_results or []:
        ticker = str(item.get("ticker", "")).upper()
        if not ticker:
            continue
        log.info("  Stage2 analyzing %s (score=%s)...", ticker, item.get("weighted_score"))

        try:
            df = get_stock_data_cached(ticker, years=1)
            if df is None or len(df) < 60:
                continue

            weekly = get_weekly_trend(ticker)
            weekly_trend = int(weekly.get("trend", 0)) if isinstance(weekly, dict) else 0
            weekly_rsi = weekly.get("rsi_weekly", "N/A") if isinstance(weekly, dict) else "N/A"
            weekly_str = {1: "uptrend", -1: "downtrend", 0: "sideways"}.get(weekly_trend, "unknown")
            news_sentiment = get_news_sentiment_fast(ticker)

            ensemble_result = {
                "direction": 0,
                "signal": "N/A",
                "high_confidence": False,
                "consensus": False,
                "confidence": 0,
            }
            if use_ensemble and ensemble_predict is not None:
                try:
                    ensemble_result = ensemble_predict(ticker, df) or ensemble_result
                except Exception as exc:
                    log.warning("  Stage2 %s ensemble failed: %s", ticker, exc)

            llm_result = None
            debate_result = None
            bull_case = None
            bear_case = None
            if use_llm and float(item.get("score", 0)) >= 3 and int(ensemble_result.get("direction", 0)) != -1:
                llm_context = build_llm_context(ticker)
                try:
                    market_data = {
                        "price": item.get("price", 0),
                        "rsi": item.get("rsi", 50),
                        "macd_bull": item.get("macd_bull", False),
                        "vol_ratio": item.get("vol_ratio", 1.0),
                        "score": item.get("score", 0),
                        "ensemble_signal": ensemble_result.get("signal", "N/A"),
                        "weekly_trend": weekly_str,
                        "news_sentiment": news_sentiment,
                        "market_regime": _get_market_regime_simple(),
                        "cash_available": load_cash(),
                    }
                    if use_debate:
                        try:
                            from debate_agents import run_debate

                            debate_result = run_debate(ticker, market_data)
                            bull_case = debate_result.get("bull_case") or {}
                            bear_case = debate_result.get("bear_case") or {}
                            final_decision = debate_result.get("final_decision") or {}
                            llm_result = {
                                "action": final_decision.get("action", "GIỮ"),
                                "confidence": final_decision.get("confidence", 30),
                                "target": final_decision.get("target"),
                                "stoploss": final_decision.get("stoploss"),
                                "reason": final_decision.get("key_reason", ""),
                                "risk_reward": final_decision.get("risk_reward"),
                                "bull_summary": bull_case.get("summary"),
                                "bear_summary": bear_case.get("summary"),
                                "agreed_with": final_decision.get("agreed_with"),
                            }
                        except Exception as exc:
                            log.warning("  Stage2 %s debate failed, fallback to single LLM: %s", ticker, exc)
                            use_debate = False

                    if not use_debate:
                        prompt = f"""{llm_context}
Phan tich co phieu {ticker}:
- Gia: {item.get('price', 0):,.0f} VND
- RSI: {item.get('rsi', 0)}
- MACD: {'bullish' if item.get('macd_bull') else 'bearish'}
- SMA cross: {'golden' if item.get('sma_bull') else 'death'}
- Volume ratio: {item.get('vol_ratio', 0)}x | ATR: {item.get('atr_pct', 0)}%
- Bollinger position: {float(item.get('bb_pos', 0)):.1%}
- Weekly trend: {weekly_str} (RSI weekly={weekly_rsi})
- Confluence score: {item.get('score', 0)}/7
- Ensemble: {ensemble_result.get('signal', 'N/A')} (confidence={ensemble_result.get('confidence', 0):.0f}%)
- Signals: {item.get('signals', {})}

Tra ve JSON:
{{"action": "MUA/BAN/GIU", "confidence": 0-100, "target_pct": <ti le tang>, "stoploss_pct": <ti le cat lo>, "reason": "<1 cau>"}}"""
                        llm_result = call_llm_json(
                            prompt=prompt,
                            system="Ban la chuyen gia phan tich co phieu VN. Chi tra ve JSON.",
                            max_tokens=200,
                        )
                except Exception as exc:
                    log.warning("  Stage2 %s LLM failed: %s", ticker, exc)

            final_score = float(item.get("weighted_score", 0))
            if ensemble_result.get("high_confidence"):
                final_score += 1.0
            if ensemble_result.get("consensus"):
                final_score += 0.5
            if isinstance(llm_result, dict) and str(llm_result.get("action", "")).upper() == "MUA":
                final_score += float(llm_result.get("confidence", 50) or 50) / 50.0

            tradeable = (
                float(item.get("score", 0)) >= 3
                and int(ensemble_result.get("direction", 0)) != -1
                and (not isinstance(llm_result, dict) or str(llm_result.get("action", "GIU")).upper() not in {"BAN", "SELL"})
            )

            result = {
                **item,
                "ensemble": ensemble_result,
                "llm": llm_result,
                "debate": bool(debate_result),
                "bull_case": bull_case,
                "bear_case": bear_case,
                "final_score": round(final_score, 2),
                "tradeable": tradeable,
                "weekly_trend": weekly_trend,
                "news_sentiment": round(news_sentiment, 2),
            }

            if int(ensemble_result.get("direction", 0)) != 0:
                try:
                    log_prediction(
                        ticker=ticker,
                        predicted_direction=int(ensemble_result.get("direction", 0)),
                        predicted_price=float(item.get("price", 0) or 0),
                        confidence=float(ensemble_result.get("confidence", 50) or 50),
                    source="two_stage_debate" if debate_result else "two_stage",
                    notes=f"stage1_score={item.get('score', 0)} llm={llm_result.get('action') if isinstance(llm_result, dict) else 'skip'}",
                )
                except Exception as exc:
                    log.warning("  Stage2 %s prediction log failed: %s", ticker, exc)

            results.append(result)
            log.info(
                "    %s: final_score=%.1f tradeable=%s ensemble=%s llm=%s",
                ticker,
                final_score,
                tradeable,
                ensemble_result.get("signal"),
                llm_result.get("action") if isinstance(llm_result, dict) else "skip",
            )
        except Exception as exc:
            log.warning("  Stage2 %s failed: %s", ticker, exc)

    results.sort(key=lambda item: item["final_score"], reverse=True)
    return results


def two_stage_scan(watchlist, top_n_stage1=10, top_n_final=3, use_llm=True, use_ensemble=True):
    """
    Full two-stage pipeline.
    Returns (stage2_results, tradeable_results).
    """
    import time

    start = time.time()
    watchlist = [str(t).upper() for t in (watchlist or []) if str(t).strip()]
    try:
        log.info(
            "Two-Stage Scan: %s tickers - S1 top%s - S2 top%s",
            len(watchlist),
            top_n_stage1,
            top_n_final,
        )
        stage1 = stage1_quick_scan(watchlist, top_n=top_n_stage1)
        if not stage1:
            log.warning("Stage 1 returned no results")
            return [], []

        stage2 = stage2_deep_analysis(stage1, use_llm=use_llm, use_ensemble=use_ensemble, use_debate=True)
        tradeable = [row for row in stage2 if row.get("tradeable")][: max(0, int(top_n_final))]

        elapsed = time.time() - start
        log.info(
            "Two-Stage done in %.1fs: %s tradeable - %s",
            elapsed,
            len(tradeable),
            [row["ticker"] for row in tradeable],
        )
        return stage2, tradeable
    except Exception as exc:
        log.error("Two-Stage scan failed: %s", exc)
        return [], []


def default_portfolio(initial_cash=INITIAL_CASH):
    return {
        "initial_cash": float(initial_cash),
        "cash": float(initial_cash),
        "positions": {},
        "updated_at": now_text(),
    }


def load_portfolio():
    portfolio = _safe_read_portfolio()
    if not portfolio:
        portfolio = default_portfolio()
    portfolio.setdefault("initial_cash", INITIAL_CASH)
    portfolio.setdefault("cash", float(portfolio.get("initial_cash", INITIAL_CASH)))
    portfolio.setdefault("positions", {})
    portfolio.setdefault("updated_at", "")
    return portfolio


def load_cash():
    return float(load_portfolio().get("cash", INITIAL_CASH))


def save_portfolio(portfolio):
    portfolio["updated_at"] = now_text()
    _safe_write_portfolio(portfolio)


def load_trades():
    trades = load_json(TRADES_FILE, [])
    return trades if isinstance(trades, list) else []


def save_trades(trades):
    save_json(TRADES_FILE, trades)


def reset_state(initial_cash=INITIAL_CASH):
    save_portfolio(default_portfolio(initial_cash))
    save_trades([])


def default_ai_fund_config():
    return {
        "capital": INITIAL_CASH,
        "top_n": 5,
        "use_ollama": True,
        "last_run_at": "",
        "last_universe": [],
    }


def load_ai_fund_config():
    config = load_json(AI_FUND_CONFIG_FILE, default_ai_fund_config())
    base = default_ai_fund_config()
    base.update(config if isinstance(config, dict) else {})
    base["capital"] = float(base.get("capital") or INITIAL_CASH)
    base["top_n"] = int(base.get("top_n") or 5)
    base["top_n"] = max(1, min(10, base["top_n"]))
    base["use_ollama"] = bool(base.get("use_ollama"))
    return base


def save_ai_fund_config(config):
    save_json(AI_FUND_CONFIG_FILE, config)


def load_equity_history():
    data = load_json(AI_FUND_EQUITY_FILE, [])
    return data if isinstance(data, list) else []


def save_equity_history(rows):
    save_json(AI_FUND_EQUITY_FILE, rows)


def _portfolio_snapshot_path():
    return os.path.join(BASE_DIR, "portfolio_snapshots.json")


def load_intraday_alerts(max_age_hours=2, limit=5, active_symbols=None):
    alerts_path = os.path.join(BASE_DIR, "intraday_alerts.json")
    if not os.path.exists(alerts_path):
        return []

    try:
        with open(alerts_path, encoding="utf-8") as f:
            alerts = json.load(f)
        if not isinstance(alerts, list):
            return []

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        active_set = {str(sym).upper() for sym in (active_symbols or []) if str(sym).strip()}
        recent = []
        for alert in alerts:
            try:
                alert_time = datetime.fromisoformat(str(alert.get("time", "")))
            except Exception:
                continue
            if alert_time >= cutoff:
                message = str(alert.get("message", ""))
                if active_set:
                    matched = next((sym for sym in active_set if sym in message.upper()), None)
                    if not matched:
                        continue
                recent.append(alert)
        return recent[-limit:]
    except Exception:
        return []


def prune_intraday_alerts(symbol):
    alerts_path = os.path.join(BASE_DIR, "intraday_alerts.json")
    if not os.path.exists(alerts_path):
        return

    try:
        with open(alerts_path, encoding="utf-8") as f:
            alerts = json.load(f)
        if not isinstance(alerts, list):
            return

        symbol = str(symbol).upper().strip()
        if not symbol:
            return

        kept = [alert for alert in alerts if symbol not in str(alert.get("message", "")).upper()]
        if len(kept) != len(alerts):
            save_json(alerts_path, kept)
    except Exception:
        return


def auto_snapshot_if_needed():
    """Write one snapshot per day for the unified portfolio chart."""
    today = datetime.now().date().isoformat()
    snapshot_path = _portfolio_snapshot_path()
    snapshots = load_json(snapshot_path, [])
    if not isinstance(snapshots, list):
        snapshots = []

    if snapshots and snapshots[-1].get("date") == today:
        return snapshots[-1]

    portfolio = load_portfolio()
    cash = float(portfolio.get("cash", 0))
    market_value = 0.0
    unrealized_pnl = 0.0
    positions = list((portfolio.get("positions") or {}).keys())

    for symbol, pos in (portfolio.get("positions") or {}).items():
        price = current_price(symbol)
        if price is None:
            continue
        qty = int(pos.get("qty", 0))
        avg = float(pos.get("avg_price", 0))
        market_value += qty * price
        unrealized_pnl += (price - avg) * qty

    initial_cash = float(portfolio.get("initial_cash", INITIAL_CASH))
    equity = cash + market_value
    snapshot = {
        "date": today,
        "time": datetime.now().isoformat(),
        "cash": cash,
        "market_value": market_value,
        "equity": equity,
        "unrealized_pnl": unrealized_pnl,
        "pnl_pct": ((equity - initial_cash) / initial_cash * 100) if initial_cash else 0.0,
        "positions": positions,
        "n_positions": len(positions),
    }
    snapshots.append(snapshot)
    snapshots = snapshots[-365:]
    save_json(snapshot_path, snapshots)
    return snapshot


@st.cache_data(ttl=60)
def fetch_history(symbol, days=180):
    return _fetch_history_uncached(symbol, days)


def _fetch_history_uncached(symbol, days=180):
    try:
        from data_fetcher import get_stock_data_cached

        years = max(0.1, float(days) / 365)
        df = get_stock_data_cached(symbol, years=years)
        cutoff = datetime.now() - timedelta(days=int(days))
        return df[df["time"] >= cutoff].sort_values("time").reset_index(drop=True)
    except Exception:
        pass

    from data_fetcher import _call_with_timeout

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    quote = Quote(symbol=symbol, source="VCI")
    df = _call_with_timeout(quote.history, start=start, end=end, interval="1D")
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def fetch_history_for_scan(symbol, days=180):
    try:
        return _fetch_history_uncached(symbol, days)
    except Exception:
        pass

    fd, output_path = tempfile.mkstemp(prefix=f"vnstock_{symbol}_", suffix=".json")
    os.close(fd)
    try:
        result = subprocess.run(
            [sys.executable, VNSTOCK_FETCH_WORKER, symbol, str(days), output_path],
            cwd=os.path.dirname(VNSTOCK_FETCH_WORKER),
            timeout=VNSTOCK_FETCH_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise RuntimeError(detail[-1] if detail else f"VNStock worker loi {result.returncode}")
        with open(output_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("status") == "error":
            raise RuntimeError(payload.get("error") or "VNStock worker loi")
        rows = payload.get("rows") or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        return df.sort_values("time").reset_index(drop=True)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Qua {VNSTOCK_FETCH_TIMEOUT_SECONDS}s khong lay duoc du lieu VNStock") from exc
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass


def current_price(symbol):
    df = fetch_history(symbol, days=20)
    if df.empty:
        return None
    return float(df.iloc[-1]["close"])


def close_negative_ev_positions():
    """
    Exit positions whose ticker has negative EV in the backtest config.
    """
    try:
        from backtester import load_backtest_config_file
    except Exception:
        load_backtest_config_file = None

    config = load_backtest_config_file() if load_backtest_config_file else {}
    negative_ev = set(
        str(t).upper()
        for t in (config.get("negative_ev_tickers") or ["VIC", "VHM", "VRE", "FPT", "HPG", "MWG"])
    )
    portfolio = load_portfolio()
    closed = []

    for ticker in list((portfolio.get("positions") or {}).keys()):
        ticker = str(ticker).upper()
        if ticker not in negative_ev:
            continue
        try:
            price = current_price(ticker)
            if price is None:
                raise ValueError("no current price")
            ok, msg = sell_position(ticker, reason="ev_negative_exit")
            if ok:
                closed.append(ticker)
                print(f"  Closed {ticker} (EV negative): {price:,.0f}")
            else:
                print(f"  Close {ticker} skipped: {msg}")
        except Exception as exc:
            print(f"  Close {ticker} failed: {exc}")

    return closed


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    value = 100 - (100 / (1 + gain / loss))
    return value.fillna(50)


def calculate_atr(ticker_data, period=14):
    if ticker_data is None or len(ticker_data) < period + 1:
        return 0.0
    high = ticker_data["high"].astype(float) if "high" in ticker_data else ticker_data["close"].astype(float)
    low = ticker_data["low"].astype(float) if "low" in ticker_data else ticker_data["close"].astype(float)
    close = ticker_data["close"].astype(float)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not pd.isna(atr) else 0.0


def calculate_position_size(ticker_data, portfolio_value, max_risk_pct=0.02):
    atr = calculate_atr(ticker_data, period=14)
    price = float(ticker_data["close"].iloc[-1])
    if atr <= 0 or price <= 0 or portfolio_value <= 0:
        return {
            "position_value": 0,
            "shares": 0,
            "stop_loss_price": None,
            "risk_pct": 0,
            "atr": atr,
        }

    atr_config = get_optimal_atr_config()
    stop_loss_distance = float(atr_config.get("atr_stop_mult", 1.0)) * atr
    max_loss_vnd = float(portfolio_value) * float(max_risk_pct)
    max_shares = int(max_loss_vnd / stop_loss_distance)
    position_value = min(max_shares * price, float(portfolio_value) * MAX_POSITION_PCT)
    shares = int(position_value / price / 100) * 100
    position_value = shares * price
    risk_pct = (stop_loss_distance * shares) / float(portfolio_value) if portfolio_value else 0
    return {
        "position_value": round(position_value, 2),
        "shares": int(shares),
        "stop_loss_price": round(price - stop_loss_distance, 1),
        "risk_pct": round(risk_pct, 4),
        "atr": round(atr, 4),
    }


def ollama_vote(symbol, price, sma20, sma50, rsi_value):
    prompt = f"""Bạn là bộ lọc giao dịch chứng khoán Việt Nam cho paper trading.
Mã: {symbol}
Giá: {price:,.2f}
SMA20: {sma20:,.2f}
SMA50: {sma50:,.2f}
RSI14: {rsi_value:.1f}

    Chỉ trả lời một từ: BUY, SELL hoặc HOLD."""
    try:
        try:
            from learning_engine import build_llm_context

            ctx = build_llm_context(symbol)
            if ctx:
                prompt = ctx + "\n" + prompt
        except Exception:
            pass
        # OLD: direct Ollama HTTP call
        result = call_llm(
            prompt=prompt,
            system="Bạn là bộ lọc giao dịch. Chỉ trả lời BUY, SELL hoặc HOLD.",
            max_tokens=8,
        )
        text = str(result.get("content") or "").upper() if result.get("success") else ""
        if "BUY" in text or "MUA" in text:
            return "BUY"
        if "SELL" in text or "BÁN" in text or "BAN" in text:
            return "SELL"
        return "HOLD"
    except Exception:
        return "HOLD"

def score_signal(action, signals_agree, votes_total, sma20, sma50, rsi_value):
    base = {"BUY": 70, "HOLD": 35, "SELL": 0}.get(str(action).upper(), 0)
    agreement = (float(signals_agree or 0) / max(1, float(votes_total or 1))) * 20
    trend = 0
    if sma50:
        trend = max(-15, min(15, ((float(sma20) / float(sma50)) - 1) * 300))
    rsi_bonus = 0
    rsi_num = float(rsi_value)
    if 40 <= rsi_num <= 65:
        rsi_bonus = 10
    elif rsi_num < 35:
        rsi_bonus = 6
    elif rsi_num > 70:
        rsi_bonus = -20
    return round(max(0, min(100, base + agreement + trend + rsi_bonus)), 1)


def build_trade_plan(row):
    price = float(row.get("price") or 0)
    sma20 = float(row.get("sma20") or price)
    score = float(row.get("score") or 0)
    if price <= 0:
        return {
            "entry_window": "-",
            "target_price": None,
            "stop_loss": None,
            "hold_days": "-",
            "sell_rule": "Khong co gia hop le",
            "plan_summary": "Khong lap duoc ke hoach vi thieu gia.",
        }

    atr = float(row.get("atr") or 0)
    atr_config = get_optimal_atr_config()
    atr_stop_mult = float(atr_config.get("atr_stop_mult", 1.0))
    atr_target_mult = float(atr_config.get("atr_target_mult", 2.0))
    max_hold_days = int(atr_config.get("max_hold_days", 15))
    atr_stop = price - atr_stop_mult * atr if atr > 0 else None
    stop_pct = 0.05 if score < 85 else 0.06
    static_stop = min(price * (1 - stop_pct), sma20 * 0.98) if sma20 > 0 else price * (1 - stop_pct)
    stop_loss = max(0, atr_stop if atr_stop is not None else static_stop)
    target_price = price + atr_target_mult * atr if atr > 0 else price * (1.08 if score < 75 else 1.12)
    hold_days = f"toi da {max_hold_days} phien"
    entry_low = price * 0.99
    entry_high = price * 1.01
    sell_rule = (
        f"Ban khi cham target {target_price:,.2f}, cat lo duoi {stop_loss:,.2f}, "
        "hoac tin hieu roi khoi top AI fund."
    )
    return {
        "entry_window": f"{entry_low:,.2f} - {entry_high:,.2f}",
        "target_price": round(target_price, 2),
        "stop_loss": round(stop_loss, 2),
        "initial_stop_loss": round(stop_loss, 2),
        "atr": round(atr, 4) if atr > 0 else None,
        "trailing_stop_active": False,
        "hold_days": hold_days,
        "sell_rule": sell_rule,
        "plan_summary": (
            f"Mua quanh {entry_low:,.2f}-{entry_high:,.2f}; "
            f"target {target_price:,.2f}; stop {stop_loss:,.2f}; giu {hold_days}."
        ),
    }


def scan_symbol(symbol, use_ollama=False):
    try:
        df = fetch_history_for_scan(symbol)
    except Exception as exc:
        return {
            "symbol": symbol,
            "price": None,
            "action": "ERROR",
            "score": 0,
            "signals_agree": 0,
            "reason": f"Loi tai du lieu: {exc}",
        }

    if df.empty or len(df) < 55:
        return {
            "symbol": symbol,
            "price": None,
            "action": "NO_DATA",
            "score": 0,
            "signals_agree": 0,
            "reason": "KhÃ´ng Ä‘á»§ dá»¯ liá»‡u",
        }

    close = df["close"].astype(float)
    df["sma20"] = close.rolling(20).mean()
    df["sma50"] = close.rolling(50).mean()
    df["rsi14"] = rsi(close)
    atr_value = calculate_atr(df)

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(latest["close"])
    sma20 = float(latest["sma20"])
    sma50 = float(latest["sma50"])
    rsi_value = float(latest["rsi14"])

    ma_signal = "HOLD"
    if prev["sma20"] <= prev["sma50"] and latest["sma20"] > latest["sma50"]:
        ma_signal = "BUY"
    elif prev["sma20"] >= prev["sma50"] and latest["sma20"] < latest["sma50"]:
        ma_signal = "SELL"
    elif latest["sma20"] > latest["sma50"]:
        ma_signal = "BUY"
    elif latest["sma20"] < latest["sma50"]:
        ma_signal = "SELL"

    rsi_signal = "HOLD"
    if rsi_value < 35:
        rsi_signal = "BUY"
    elif rsi_value > 70:
        rsi_signal = "SELL"

    votes = [ma_signal, rsi_signal]
    ollama_signal = "OFF"
    if use_ollama:
        ollama_signal = ollama_vote(symbol, price, sma20, sma50, rsi_value)
        if ollama_signal in {"BUY", "SELL", "HOLD"}:
            votes.append(ollama_signal)

    buy_votes = votes.count("BUY")
    sell_votes = votes.count("SELL")
    hold_votes = votes.count("HOLD")
    min_votes = 2

    action = "HOLD"
    agree = hold_votes
    if buy_votes >= min_votes:
        action = "BUY"
        agree = buy_votes
    elif sell_votes >= min_votes:
        action = "SELL"
        agree = sell_votes

    score = score_signal(action, agree, len(votes), sma20, sma50, rsi_value)
    ev_info = get_ticker_ev(symbol)
    try:
        from learning_engine import get_signal_weight

        weight = float(get_signal_weight(symbol))
    except Exception:
        weight = 1.0
    score = score * weight
    score = apply_ev_score_adjustment(symbol, score)
    ollama_reason = "TIMEOUT" if ollama_signal == "TIMEOUT" else ollama_signal
    result = {
        "symbol": symbol,
        "price": price,
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": rsi_value,
        "atr": atr_value,
        "ma_signal": ma_signal,
        "rsi_signal": rsi_signal,
        "ollama_signal": ollama_signal,
        "signals_agree": agree,
        "votes_total": len(votes),
        "action": action,
        "score": round(score, 1),
        "signal_weight": round(weight, 2),
        "ev": ev_info.get("ev"),
        "ev_win_rate": ev_info.get("win_rate"),
        "ev_trades": ev_info.get("trades"),
        "reason": f"MA={ma_signal}, RSI={rsi_signal}, Ollama={ollama_reason}",
    }
    try:
        result["position_sizing"] = calculate_position_size(df, portfolio_equity(load_portfolio()))
    except Exception:
        result["position_sizing"] = {}
    result.update(build_trade_plan(result))
    return result


def position_market_value(position, price):
    return float(position.get("qty", 0)) * price


def portfolio_equity(portfolio):
    equity = float(portfolio.get("cash", 0))
    for symbol, pos in portfolio.get("positions", {}).items():
        price = current_price(symbol)
        if price is not None:
            equity += position_market_value(pos, price)
    return equity


def get_unified_portfolio_summary():
    """Single source of truth for dashboard fund and portfolio summary."""
    try:
        portfolio = load_portfolio()
        cash = float(portfolio.get("cash", 0) or 0)
        positions = portfolio.get("positions", {}) or {}
        total_mv = 0.0
        total_pnl = 0.0
        for symbol, pos in positions.items():
            qty = float(pos.get("qty", 0) or 0)
            price = current_price(symbol)
            if price is None:
                price = float(pos.get("current_price") or pos.get("avg_price") or 0)
            avg = float(pos.get("avg_price") or price or 0)
            market_value = qty * float(price or 0)
            total_mv += market_value
            total_pnl += (float(price or 0) - avg) * qty
        return {
            "cash": cash,
            "market_value": total_mv,
            "equity": cash + total_mv,
            "unrealized_pnl": total_pnl,
            "n_positions": len(positions),
            "positions": positions,
        }
    except Exception as exc:
        log.warning("Unified portfolio summary failed: %s", exc)
        return {"cash": 0.0, "market_value": 0.0, "equity": 0.0, "unrealized_pnl": 0.0, "n_positions": 0, "positions": {}}


def log_trade(trades, symbol, side, qty, price, reason, pnl=None, plan=None):
    trades.append({
        "time": now_text(),
        "symbol": symbol,
        "side": side,
        "qty": int(qty),
        "price": round(float(price), 2),
        "value": round(float(qty) * float(price), 2),
        "reason": reason,
        "pnl": None if pnl is None else round(float(pnl), 2),
        "plan": plan or {},
    })


def buy_position(symbol, reason="Manual BUY", target_value=None, max_position_pct=MAX_POSITION_PCT, plan=None):
    portfolio = load_portfolio()
    trades = load_trades()
    price = current_price(symbol)
    if price is None or price <= 0:
        return False, f"KhÃ´ng láº¥y Ä‘Æ°á»£c giÃ¡ hiá»‡n táº¡i cho {symbol}"

    equity = portfolio_equity(portfolio)
    max_value = equity * float(max_position_pct)
    existing = portfolio["positions"].get(symbol)
    existing_value = 0
    if existing:
        existing_value = float(existing.get("qty", 0)) * price
    desired_value = max_value if target_value is None else min(float(target_value), max_value)
    available_value = min(float(portfolio["cash"]), max(0, desired_value - existing_value))
    qty = int(available_value // price)
    if target_value is not None:
        qty = int(qty / 100) * 100
    if qty <= 0:
        return False, f"KhÃ´ng Ä‘á»§ tiá»n hoáº·c vá»‹ tháº¿ {symbol} Ä‘Ã£ Ä‘áº¡t giá»›i háº¡n 20%"

    cost = qty * price
    portfolio["cash"] = float(portfolio["cash"]) - cost
    if existing:
        old_qty = int(existing.get("qty", 0))
        old_avg = float(existing.get("avg_price", 0))
        new_qty = old_qty + qty
        existing["avg_price"] = ((old_qty * old_avg) + cost) / new_qty
        existing["qty"] = new_qty
        if plan:
            existing["plan"] = plan
    else:
        portfolio["positions"][symbol] = {
            "qty": qty,
            "avg_price": price,
            "opened_at": now_text(),
            "plan": plan or {},
        }
    log_trade(trades, symbol, "BUY", qty, price, reason, plan=plan)
    save_portfolio(portfolio)
    save_trades(trades)
    return True, f"BUY {qty:,} {symbol} @ {price:,.2f}"


def sell_position(symbol, reason="Manual SELL", qty=None):
    portfolio = load_portfolio()
    trades = load_trades()
    position = portfolio.get("positions", {}).get(symbol)
    if not position:
        return False, f"KhÃ´ng cÃ³ vá»‹ tháº¿ {symbol}"
    price = current_price(symbol)
    if price is None or price <= 0:
        return False, f"KhÃ´ng láº¥y Ä‘Æ°á»£c giÃ¡ hiá»‡n táº¡i cho {symbol}"

    owned_qty = int(position.get("qty", 0))
    sell_qty = owned_qty if qty is None else min(int(qty), owned_qty)
    if sell_qty <= 0:
        return False, "Sá»‘ lÆ°á»£ng bÃ¡n khÃ´ng há»£p lá»‡"

    avg_price = float(position.get("avg_price", 0))
    proceeds = sell_qty * price
    pnl = (price - avg_price) * sell_qty
    portfolio["cash"] = float(portfolio["cash"]) + proceeds

    remaining = owned_qty - sell_qty
    if remaining > 0:
        position["qty"] = remaining
    else:
        portfolio["positions"].pop(symbol, None)

    log_trade(trades, symbol, "SELL", sell_qty, price, reason, pnl=pnl)
    save_portfolio(portfolio)
    save_trades(trades)
    if remaining <= 0:
        prune_intraday_alerts(symbol)
    return True, f"SELL {sell_qty:,} {symbol} @ {price:,.2f} | PnL {pnl:,.0f}"


def execute_paper_trade(ticker, action, price=None, confidence=50, source="scheduler", reason=None):
    """
    Scheduler-facing paper trade entry point.
    BUY uses Kelly-based sizing; SELL uses existing portfolio helper.
    """
    import math

    ticker = str(ticker).upper()
    action = str(action).upper()
    reason = reason or f"{source}: {action}"
    price = float(price or 0)

    portfolio = load_portfolio()
    cash = float(portfolio.get("cash", 0))
    positions = portfolio.get("positions", {}) or {}
    equity = cash
    for sym, pos in positions.items():
        try:
            pos_price = float(pos.get("market_value", 0))
            if pos_price > 0:
                equity += pos_price
            else:
                qty = float(pos.get("qty", 0))
                avg = float(pos.get("avg_price", 0))
                equity += qty * avg
        except Exception:
            continue

    def _resolve_live_price():
        """Always prefer the live market price for BUY sizing and entry planning."""
        try:
            live_price = float(current_price(ticker) or 0)
        except Exception:
            live_price = 0.0
        provided_price = float(price or 0)
        if live_price > 0:
            if provided_price > 0:
                ratio = max(live_price / provided_price, provided_price / live_price) if live_price and provided_price else 1
                if ratio >= 20:
                    log.warning(
                        "%s: provided price %.2f differs from live price %.2f, using live price",
                        ticker,
                        provided_price,
                        live_price,
                    )
            return live_price
        return provided_price

    def _build_buy_plan(avg_price, current_price, shares, value, kelly_fraction):
        from data_fetcher import get_stock_data_cached

        df = get_stock_data_cached(ticker, years=0.1)
        atr = calculate_atr(df) if df is not None and len(df) >= 15 else 0.0
        base_price = float(current_price or avg_price or 0)
        if atr <= 0 and base_price > 0:
            atr = base_price * 0.02
        stop_loss = round(base_price - 1.0 * atr, 1)
        target = round(base_price + 2.0 * atr, 1)
        return {
            "qty": int(shares),
            "avg_price": round(avg_price, 2),
            "current_price": round(base_price, 2),
            "market_value": round(value, 2),
            "unrealized_pnl": round((base_price - avg_price) * shares, 2),
            "pnl_pct": round(((base_price / avg_price) - 1) * 100, 4) if avg_price > 0 else 0.0,
            "target_price": target,
            "stop_loss": stop_loss,
            "atr": round(float(atr), 2),
            "kelly_fraction": round(float(kelly_fraction), 3),
            "entry_date": now_text(),
            "hold_days": 0,
            "source": source,
            "plan": {
                "target_price": target,
                "stop_loss": stop_loss,
                "atr": round(float(atr), 2),
                "hold_days": "toi da 15 phien",
                "sell_rule": f"Ban khi cham target {target:,.2f}, cat lo duoi {stop_loss:,.2f}.",
                "trailing_stop_active": False,
                "kelly_fraction": round(float(kelly_fraction), 3),
            },
        }

    if action == "BUY":
        if ticker in positions:
            log.warning("%s: already in portfolio, skip", ticker)
            return False, f"{ticker} already in portfolio"
        if cash < 1_000_000:
            log.warning("Insufficient cash: %,.0f", cash)
            return False, f"Insufficient cash: {cash:,.0f}"
        # Circuit breaker: portfolio state (cash/equity) should never realistically
        # drift far from INITIAL_CASH for this paper fund. A bad price tick or a
        # data-corruption bug elsewhere must not get to size a real trade off of it.
        if equity > INITIAL_CASH * 50:
            log.error(
                "%s: refusing BUY, equity %,.0f looks corrupted (>50x initial cash %,.0f)",
                ticker, equity, INITIAL_CASH,
            )
            return False, f"Equity {equity:,.0f} looks corrupted, refusing to size trade"
        if len(positions) >= 5:
            log.warning("Max positions reached (%s)", len(positions))
            return False, f"Max positions reached ({len(positions)})"
        price = _resolve_live_price()
        if price <= 0:
            return False, f"Khong lay duoc gia hien tai cho {ticker}"

        sizing = get_kelly_position_size(ticker, equity, price)
        max_spend = cash * 0.95
        value = min(float(sizing.get("value", 0)), max_spend)
        shares = int(value / price / 100) * 100 if price > 0 else 0
        value = shares * price
        kelly_fraction = float(sizing.get("kelly_fraction", 0.25))

        if value < price * 100:
            log.warning("%s: position too small (%s), skip", ticker, f"{value:,.0f}")
            return False, f"{ticker}: position too small ({value:,.0f})"
        if value > cash:
            log.error("%s: cost %,.0f > cash %,.0f, abort", ticker, value, cash)
            return False, f"{ticker}: cost {value:,.0f} > cash {cash:,.0f}"

        positions[ticker] = _build_buy_plan(price, price, shares, value, kelly_fraction)

        portfolio["cash"] = cash - value
        portfolio["positions"] = positions
        portfolio["updated_at"] = now_text()
        save_portfolio(portfolio)

        trades = load_trades()
        log_trade(trades, ticker, "BUY", shares, price, reason, plan=positions[ticker].get("plan"))
        save_trades(trades)
        log.info(
            "BUY %s: %s cp @ %,.0f Kelly=%.1f%% (%.1f%% portfolio)",
            ticker,
            shares,
            price,
            kelly_fraction * 100,
            float(sizing.get("pct_portfolio", 0)),
        )
        return True, f"BUY {shares:,} {ticker} @ {price:,.2f}"
    if action == "SELL":
        return sell_position(ticker, reason=reason)
    return False, f"Skip {ticker}: action={action}"


def get_kelly_position_size(ticker, portfolio_value, price):
    """
    Size position from backtesting Kelly fraction.
    Falls back to conservative 25%.
    """
    result_path = os.path.join(BASE_DIR, "backtest_results", f"{str(ticker).upper()}_pro_backtest.json")
    kelly_fraction = 0.25
    try:
        if os.path.exists(result_path):
            with open(result_path, encoding="utf-8") as f:
                bt = json.load(f)
            kelly = float(bt.get("kelly", 0) or 0)
            if kelly > 0:
                kelly_fraction = min(kelly * 0.5, 0.30)
    except Exception as exc:
        log.warning("Kelly sizing fallback for %s: %s", ticker, exc)

    portfolio_value = float(portfolio_value or 0)
    price = float(price or 0)
    position_value = portfolio_value * kelly_fraction
    shares = int(position_value / price / 100) * 100 if portfolio_value > 0 and price > 0 else 0
    actual_value = shares * price
    pct_portfolio = (actual_value / portfolio_value * 100) if portfolio_value > 0 else 0

    return {
        "shares": shares,
        "value": actual_value,
        "kelly_fraction": round(kelly_fraction, 3),
        "pct_portfolio": round(pct_portfolio, 1),
    }


def run_risk_checks():
    portfolio = load_portfolio()
    messages = []
    for symbol, pos in list(portfolio.get("positions", {}).items()):
        price = current_price(symbol)
        if price is None:
            continue
        avg = float(pos.get("avg_price", 0))
        if avg <= 0:
            continue
        plan = pos.get("plan") or {}
        planned_stop = float(plan.get("stop_loss") or 0)
        planned_target = float(plan.get("target_price") or 0)
        atr = float(plan.get("atr") or 0)
        if atr > 0 and price >= avg + atr and planned_stop < avg:
            plan["stop_loss"] = round(avg, 2)
            plan["trailing_stop_active"] = True
            plan["sell_rule"] = (
                f"Trailing stop da keo len break-even {avg:,.2f}; "
                f"ban khi cham target {planned_target:,.2f} hoac roi khoi top AI fund."
            )
            pos["plan"] = plan
            planned_stop = float(plan["stop_loss"])
            save_portfolio(portfolio)
            messages.append(f"TRAILING STOP {symbol}: stop-loss len break-even {avg:,.2f}")
        if planned_stop > 0 and price <= planned_stop:
            ok, msg = sell_position(symbol, f"AUTO PLAN STOP {planned_stop:,.2f}")
            messages.append(msg)
            continue
        if planned_target > 0 and price >= planned_target:
            ok, msg = sell_position(symbol, f"AUTO PLAN TARGET {planned_target:,.2f}")
            messages.append(msg)
            continue
        change = (price - avg) / avg
        if change <= -STOP_LOSS_PCT:
            ok, msg = sell_position(symbol, "AUTO STOP-LOSS 5%")
            messages.append(msg)
        elif change >= TAKE_PROFIT_PCT:
            ok, msg = sell_position(symbol, "AUTO TAKE-PROFIT 15%")
            messages.append(msg)
    return messages


def check_and_close_positions():
    """
    EOD check for open paper positions.
    Closes positions when current price hits ATR-based stop or target.
    """
    try:
        from backtester import load_backtest_config_file
    except Exception:
        load_backtest_config_file = None

    if load_backtest_config_file:
        config = load_backtest_config_file()
    else:
        config = {"optimal_config": {"atr_stop_mult": 1.0, "atr_target_mult": 2.0}}
    atr_config = config.get("optimal_config", {})
    atr_stop = float(atr_config.get("atr_stop_mult", 1.0))
    atr_target = float(atr_config.get("atr_target_mult", 2.0))

    portfolio = load_portfolio()
    closed = 0
    for ticker, position in list(portfolio.get("positions", {}).items()):
        try:
            price = current_price(ticker)
            if price is None or price <= 0:
                continue

            entry_price = float(position.get("avg_price") or price)
            plan = position.get("plan") or {}
            atr = float(plan.get("atr") or 0)
            if atr <= 0:
                df = fetch_history(ticker, days=45)
                atr = calculate_atr(df) if not df.empty else price * 0.02
            if atr <= 0:
                atr = price * 0.02

            stop_loss = float(plan.get("stop_loss") or (entry_price - atr_stop * atr))
            target = float(plan.get("target_price") or (entry_price + atr_target * atr))

            if price <= stop_loss:
                ok, _ = sell_position(ticker, f"EOD STOP LOSS {stop_loss:,.2f}")
                if ok:
                    closed += 1
            elif price >= target:
                ok, _ = sell_position(ticker, f"EOD TARGET {target:,.2f}")
                if ok:
                    closed += 1
        except Exception:
            continue

    return closed


def run_auto_trades(scan_rows):
    messages = run_risk_checks()
    for row in scan_rows:
        symbol = row.get("symbol")
        action = row.get("action")
        if action == "BUY":
            ok, msg = buy_position(symbol, "AUTO BUY: min 2/3 signals agree")
            messages.append(msg)
        elif action == "SELL":
            if load_portfolio().get("positions", {}).get(symbol):
                ok, msg = sell_position(symbol, "AUTO SELL: min 2/3 signals agree")
                messages.append(msg)
    return messages


def select_ai_candidates(scan_rows, top_n):
    valid_rows = [
        row for row in scan_rows
        if row.get("action") == "BUY" and row.get("price") and float(row.get("score", 0)) >= 65
    ]
    return sorted(valid_rows, key=lambda row: (float(row.get("score", 0)), int(row.get("signals_agree", 0))), reverse=True)[:top_n]


def summarize_scan_rows(scan_rows):
    total = len(scan_rows or [])
    errors = sum(1 for row in scan_rows or [] if row.get("action") == "ERROR")
    no_data = sum(1 for row in scan_rows or [] if row.get("action") == "NO_DATA")
    buys = [row for row in scan_rows or [] if row.get("action") == "BUY" and row.get("price")]
    qualified = [row for row in buys if float(row.get("score", 0)) >= 65]
    best = max((float(row.get("score", 0)) for row in scan_rows or []), default=0)
    return (
        f"Tong {total} ma | loi du lieu {errors} | thieu du lieu {no_data} | "
        f"BUY {len(buys)} | BUY score >= 65 {len(qualified)} | diem cao nhat {best:.1f}"
    )


def record_equity_snapshot(label="snapshot"):
    portfolio = load_portfolio()
    equity = portfolio_equity(portfolio)
    initial = float(portfolio.get("initial_cash", INITIAL_CASH))
    pnl = equity - initial
    pnl_pct = (pnl / initial * 100) if initial else 0
    row = {
        "time": now_text(),
        "label": label,
        "cash": round(float(portfolio.get("cash", 0)), 2),
        "equity": round(equity, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "positions": sorted(list(portfolio.get("positions", {}).keys())),
    }
    history = load_equity_history()
    history.append(row)
    save_equity_history(history[-500:])
    return row


def rebalance_ai_fund(scan_rows, top_n):
    messages = run_risk_checks()
    candidates = select_ai_candidates(scan_rows, top_n)
    target_symbols = [row["symbol"] for row in candidates]

    portfolio = load_portfolio()
    for symbol in list(portfolio.get("positions", {}).keys()):
        row = next((item for item in scan_rows if item.get("symbol") == symbol), {})
        if symbol not in target_symbols or row.get("action") == "SELL":
            ok, msg = sell_position(symbol, "AI FUND EXIT: not in top candidates")
            messages.append(msg)

    portfolio = load_portfolio()
    equity = portfolio_equity(portfolio)
    if not candidates:
        messages.append(f"AI FUND: khong co ma BUY dat diem toi thieu, giu tien mat. {summarize_scan_rows(scan_rows)}.")
        record_equity_snapshot("rebalance_no_candidates")
        return candidates, messages

    for row in candidates:
        sizing = row.get("position_sizing") or {}
        target_value = float(sizing.get("position_value") or 0)
        max_position_pct = min(MAX_POSITION_PCT, target_value / equity) if equity and target_value > 0 else MAX_POSITION_PCT
        if target_value <= 0:
            target_value = equity * min(MAX_POSITION_PCT, 1 / max(1, top_n))
        ok, msg = buy_position(
            row["symbol"],
            f"AI FUND BUY: top {top_n}, score {row.get('score')}, ATR risk {sizing.get('risk_pct', 0)}",
            target_value=target_value,
            max_position_pct=max_position_pct,
            plan=build_trade_plan(row),
        )
        messages.append(msg)

    record_equity_snapshot("rebalance")
    return candidates, messages


def normalize_symbols(text):
    parts = re.split(r"[\s,;]+", text.upper().strip())
    return [p for p in parts if p]


def format_vnd(value):
    return f"{float(value):,.0f} VND"


def run_scan(symbols, use_ollama, session_key, title="Dang scan"):
    rows = []
    progress = st.progress(0)
    box = st.empty()
    started_at = datetime.now()
    for idx, symbol in enumerate(symbols):
        elapsed = (datetime.now() - started_at).total_seconds()
        box.info(
            f"{title} {idx + 1}/{len(symbols)}: {symbol} | "
            f"lay du lieu, timeout {VNSTOCK_FETCH_TIMEOUT_SECONDS}s/ma | tong {elapsed:.0f}s"
        )
        symbol_started = datetime.now()
        row = scan_symbol(symbol, use_ollama=use_ollama)
        row["scan_seconds"] = round((datetime.now() - symbol_started).total_seconds(), 1)
        rows.append(row)
        box.info(
            f"{title} {idx + 1}/{len(symbols)} xong: {symbol} | "
            f"Ollama={row.get('ollama_signal')} | {row['scan_seconds']}s"
        )
        progress.progress((idx + 1) / len(symbols))
        if idx < len(symbols) - 1:
            time.sleep(VNSTOCK_MIN_DELAY_SECONDS)
    box.success(f"Da scan xong {len(symbols)} ma. {summarize_scan_rows(rows)}.")
    st.session_state[session_key] = rows
    return rows


def apply_ollama_votes(rows, max_items, title="Dang hoi Ollama"):
    if not rows:
        return rows
    ranked = sorted(
        [
            row for row in rows
            if row.get("price") and row.get("action") in {"BUY", "HOLD"}
        ],
        key=lambda row: float(row.get("score", 0)),
        reverse=True,
    )[:max_items]
    if not ranked:
        return rows

    by_symbol = {row["symbol"]: row for row in rows}
    progress = st.progress(0)
    box = st.empty()
    for idx, row in enumerate(ranked):
        symbol = row["symbol"]
        box.info(f"{title} {idx + 1}/{len(ranked)}: {symbol}")
        started = datetime.now()
        ollama_signal = ollama_vote(
            symbol,
            float(row["price"]),
            float(row["sma20"]),
            float(row["sma50"]),
            float(row["rsi14"]),
        )
        votes = [row.get("ma_signal"), row.get("rsi_signal")]
        if ollama_signal in {"BUY", "SELL", "HOLD"}:
            votes.append(ollama_signal)
        buy_votes = votes.count("BUY")
        sell_votes = votes.count("SELL")
        hold_votes = votes.count("HOLD")
        action = "HOLD"
        agree = hold_votes
        if buy_votes >= 2:
            action = "BUY"
            agree = buy_votes
        elif sell_votes >= 2:
            action = "SELL"
            agree = sell_votes
        row["ollama_signal"] = ollama_signal
        row["signals_agree"] = agree
        row["votes_total"] = len(votes)
        row["action"] = action
        row["score"] = score_signal(action, agree, len(votes), row["sma20"], row["sma50"], row["rsi14"])
        row["score"] = apply_ev_score_adjustment(symbol, row["score"])
        ev_info = get_ticker_ev(symbol)
        row["ev"] = ev_info.get("ev")
        row["ev_win_rate"] = ev_info.get("win_rate")
        row["ev_trades"] = ev_info.get("trades")
        row["reason"] = f"MA={row.get('ma_signal')}, RSI={row.get('rsi_signal')}, Ollama={ollama_signal}"
        row.update(build_trade_plan(row))
        row["ollama_seconds"] = round((datetime.now() - started).total_seconds(), 1)
        by_symbol[symbol] = row
        box.info(f"{title} {idx + 1}/{len(ranked)} xong: {symbol} | Ollama={ollama_signal} | {row['ollama_seconds']}s")
        progress.progress((idx + 1) / len(ranked))
    return [by_symbol.get(row["symbol"], row) for row in rows]


def run_ai_fund_scan(symbols, use_ollama, top_n):
    rows = run_scan(symbols, False, "ai_fund_scan", title="Dang scan ky thuat")
    if use_ollama:
        ollama_limit = max(10, min(20, int(top_n) * 2))
        rows = apply_ollama_votes(rows, ollama_limit, title="Dang hoi Ollama top ung vien")
        st.session_state["ai_fund_scan"] = rows
    return rows


def style_scan_table(df):
    view = df.copy()
    rename_map = {
        "symbol": "MÃ£",
        "price": "GiÃ¡ hiá»‡n táº¡i",
        "sma20": "SMA20",
        "sma50": "SMA50",
        "rsi14": "RSI14",
        "ma_signal": "MA",
        "rsi_signal": "RSI",
        "ollama_signal": "Ollama",
        "signals_agree": "Äá»“ng Ã½",
        "votes_total": "Tá»•ng phiáº¿u",
        "action": "TÃ­n hiá»‡u",
        "ev": "EV %",
        "ev_win_rate": "EV WR",
        "ev_trades": "EV trades",
        "reason": "LÃ½ do",
    }
    view = view.rename(columns=rename_map)

    if "GiÃ¡ hiá»‡n táº¡i" in view.columns:
        view["GiÃ¡ hiá»‡n táº¡i"] = view["GiÃ¡ hiá»‡n táº¡i"].apply(
            lambda x: "-" if pd.isna(x) or x is None else f"{float(x):,.2f}"
        )
    for col in ["SMA20", "SMA50", "RSI14"]:
        if col in view.columns:
            view[col] = view[col].apply(
                lambda x: "-" if pd.isna(x) or x is None else f"{float(x):,.2f}" if col != "RSI14" else f"{float(x):,.1f}"
            )
    if "EV %" in view.columns:
        view["EV %"] = view["EV %"].apply(lambda x: "-" if pd.isna(x) or x is None else f"{float(x):+.2f}")
    if "EV WR" in view.columns:
        view["EV WR"] = view["EV WR"].apply(lambda x: "-" if pd.isna(x) or x is None else f"{float(x):.1%}")

    def color_action(val):
        text = str(val).upper()
        if text == "BUY":
            return "color:#22c55e;font-weight:900;"
        if text == "SELL":
            return "color:#ef4444;font-weight:900;"
        if text in {"HOLD", "NO_DATA", "ERROR"}:
            return "color:#f59e0b;font-weight:800;"
        return "color:#cbd5e1;"

    def color_price(row):
        action = str(row.get("TÃ­n hiá»‡u", "")).upper()
        if action == "SELL":
            return ["background-color: rgba(239,68,68,0.12);"] * len(row)
        if action == "BUY":
            return ["background-color: rgba(34,197,94,0.10);"] * len(row)
        if action in {"NO_DATA", "ERROR"}:
            return ["background-color: rgba(245,158,11,0.08);"] * len(row)
        return [""] * len(row)

    styler = view.style.apply(color_price, axis=1)
    if "TÃ­n hiá»‡u" in view.columns:
        styler = styler.applymap(color_action, subset=["TÃ­n hiá»‡u"])
    return styler


def render_header():
    st.title("VN Paper Auto Trader")
    st.caption("Paper trading only. KhÃ´ng Ä‘áº·t lá»‡nh tháº­t. Initial cash: 100M VND.")


def render_scanner(symbols, use_ollama, auto_trade):
    st.subheader("Signal scanner")
    if not symbols:
        st.warning("Chua co ma de quet.")
        return
    symbols, filter_reason = get_tradeable_tickers(symbols)
    st.caption(f"Universe filter: {filter_reason}")

    scan_signature = {
        "symbols": symbols,
        "use_ollama": bool(use_ollama),
    }
    if st.session_state.get("last_scan_signature") != scan_signature:
        st.session_state.pop("last_scan", None)
        st.session_state["last_scan_signature"] = scan_signature

    if auto_trade:
        messages = run_risk_checks()
        for msg in messages:
            st.warning(msg)

    scan_estimate = len(symbols) * VNSTOCK_MIN_DELAY_SECONDS
    if use_ollama:
        st.warning(
            f"Ollama vote dang bat: moi ma co the doi toi da {OLLAMA_TIMEOUT_SECONDS} giay. "
            "Tat Ollama vote neu can quet nhanh."
        )
        scan_estimate += len(symbols) * OLLAMA_TIMEOUT_SECONDS
    st.caption(f"Du kien toi thieu khoang {scan_estimate:.0f} giay cho {len(symbols)} ma.")

    if st.button("QuÃ©t tÃ­n hiá»‡u", type="primary", width='stretch'):
        rows = []
        progress = st.progress(0)
        status_box = st.empty()
        started_at = datetime.now()
        for idx, symbol in enumerate(symbols):
            elapsed = (datetime.now() - started_at).total_seconds()
            status_box.info(f"Dang quet {idx + 1}/{len(symbols)}: {symbol} | da chay {elapsed:.0f}s")
            rows.append(scan_symbol(symbol, use_ollama=use_ollama))
            progress.progress((idx + 1) / len(symbols))
            if idx < len(symbols) - 1:
                time.sleep(VNSTOCK_MIN_DELAY_SECONDS)
        elapsed = (datetime.now() - started_at).total_seconds()
        status_box.success(f"Quet xong {len(symbols)} ma trong {elapsed:.0f}s.")
        st.session_state["last_scan"] = rows
        st.session_state["last_scan_signature"] = scan_signature
        if auto_trade:
            for msg in run_auto_trades(rows):
                st.info(msg)

    rows = st.session_state.get("last_scan", [])
    if not rows:
        st.info("Báº¥m QuÃ©t tÃ­n hiá»‡u Ä‘á»ƒ báº¯t Ä‘áº§u.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        style_scan_table(df),
        width='stretch',
        hide_index=True,
    )

    st.markdown("#### Manual trade")
    trade_cols = st.columns([2, 1, 1])
    with trade_cols[0]:
        selected = st.selectbox("MÃ£", symbols, key="manual_symbol")
    with trade_cols[1]:
        if st.button("Manual BUY", width='stretch'):
            ok, msg = buy_position(selected)
            st.success(msg) if ok else st.error(msg)
    with trade_cols[2]:
        if st.button("Manual SELL", width='stretch'):
            ok, msg = sell_position(selected)
            st.success(msg) if ok else st.error(msg)


def render_ai_fund(symbols):
    st.subheader("AI Managed Fund")
    st.caption("Paper fund only. AI chon top ma, rebalance tren cung mot portfolio voi tab Portfolio & PnL.")

    if not symbols:
        st.warning("Chua co universe de scan.")
        return
    symbols, filter_reason = get_tradeable_tickers(symbols)
    st.caption(f"Universe filter: {filter_reason}")
    atr_config = get_optimal_atr_config()
    st.caption(
        f"ATR config tu backtest: stop {atr_config.get('atr_stop_mult')}ATR, "
        f"target {atr_config.get('atr_target_mult')}ATR, hold toi da {atr_config.get('max_hold_days')} phien."
    )

    config = load_ai_fund_config()
    portfolio = load_portfolio()
    current_initial = float(portfolio.get("initial_cash", INITIAL_CASH))
    portfolio_summary = get_unified_portfolio_summary()

    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        capital_m = st.number_input(
            "Von AI fund (trieu VND)",
            min_value=1.0,
            max_value=10_000.0,
            value=float(config.get("capital", current_initial)) / 1_000_000,
            step=10.0,
            key="ai_fund_capital_m",
        )
        st.caption(
            f"Portfolio thật: equity {portfolio_summary['equity']:,.0f} | "
            f"cash {portfolio_summary['cash']:,.0f} | positions {portfolio_summary['n_positions']}"
        )
    with c2:
        top_n = st.slider("So ma nam giu", 5, 10, int(config.get("top_n", 5)), key="ai_fund_top_n")
    with c3:
        use_llm = st.checkbox("Dùng LLM vote (Groq/Gemini)", value=bool(config.get("use_ollama", False)), key="ai_fund_ollama")
        st.caption("LLM phân tích qua Groq / Gemini / Ollama tự động")

    config.update({
        "capital": float(capital_m) * 1_000_000,
        "top_n": int(top_n),
        "use_ollama": bool(use_llm),
        "last_universe": symbols,
    })
    save_ai_fund_config(config)

    scan_signature = {
        "symbols": symbols,
        "use_ollama": bool(use_llm),
    }
    if st.session_state.get("ai_fund_scan_signature") != scan_signature:
        st.session_state.pop("ai_fund_scan", None)
        st.session_state["ai_fund_scan_signature"] = scan_signature

    if abs(portfolio_summary["equity"] - config["capital"]) > 1:
        st.warning("Von cai dat khac voi portfolio hien tai. Portfolio/PnL dang dung paper_portfolio.json lam nguon chinh.")

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        if st.button("Reset AI fund", width='stretch'):
            reset_state(config["capital"])
            save_equity_history([])
            record_equity_snapshot("reset")
            st.success("Da reset AI fund theo von moi.")
    with a2:
        if st.button("Scan top candidates", width='stretch'):
            run_ai_fund_scan(symbols, use_llm, top_n)
            st.session_state["ai_fund_scan_signature"] = scan_signature
    with a3:
        st.session_state.setdefault("confirm_trade", False)
        regime_info = None
        bear_trend = False
        try:
            from dashboard_vn import detect_market_regime

            regime_info = detect_market_regime()
            bear_trend = bool((regime_info or {}).get("regime") == "BEAR_TREND")
        except Exception:
            regime_info = None
            bear_trend = False

        if bear_trend:
            st.button(
                "💹 Auto trade top",
                width='stretch',
                key="btn_trade_v2",
                disabled=True,
                help="Disabled: Market đang BEAR_TREND",
            )
            st.caption("🔴 BEAR_TREND - Không trade mới")
        else:
            if st.button("💹 Auto trade top", width='stretch', type="primary", key="btn_trade_v2"):
                st.session_state["confirm_trade"] = True

        if st.session_state.get("confirm_trade") and not bear_trend:
            st.warning("Xác nhận đặt lệnh paper trade?")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Xác nhận", key="btn_confirm_yes"):
                    rows = st.session_state.get("ai_fund_scan")
                    if not rows:
                        rows = run_ai_fund_scan(symbols, use_llm, top_n)
                        st.session_state["ai_fund_scan_signature"] = scan_signature
                    candidates, messages = rebalance_ai_fund(rows, top_n)
                    config["last_run_at"] = now_text()
                    save_ai_fund_config(config)
                    st.session_state["ai_fund_candidates"] = candidates
                    for msg in messages:
                        st.info(msg)
                    st.success("Đã đặt lệnh thành công")
                    st.session_state["confirm_trade"] = False
            with col_no:
                if st.button("Huỷ", key="btn_confirm_no"):
                    st.session_state["confirm_trade"] = False

    st.divider()
    st.caption("🤖 Scheduler tự động chạy lúc 9:00 sáng mỗi ngày")
    state_path = os.path.join(BASE_DIR, "scheduler_state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                sched = json.load(f)
            last_trade = sched.get("auto_trade", "Chưa có")
            st.caption(f"Lần trade cuối: {last_trade}")
        except Exception:
            st.caption("Không đọc được scheduler state")

    with st.expander("Portfolio hien tai"):
        portfolio = load_portfolio()
        cash = float(portfolio.get("cash", 0))
        positions = portfolio.get("positions", {}) or {}
        market_value = 0.0
        unrealized = 0.0
        rows_positions = []
        for symbol, pos in positions.items():
            price = current_price(symbol)
            qty = int(pos.get("qty", 0))
            avg = float(pos.get("avg_price", 0))
            value = qty * price if price else 0
            pnl = (price - avg) * qty if price else 0
            pnl_pct = ((price - avg) / avg * 100) if price and avg else 0
            market_value += value
            unrealized += pnl
            rows_positions.append({
                "symbol": symbol,
                "qty": qty,
                "avg_price": avg,
                "current_price": price,
                "market_value": value,
                "unrealized_pnl": pnl,
                "pnl_pct": pnl_pct,
                "ev": get_ticker_ev(symbol).get("ev"),
            })

        equity = cash + market_value
        c1, c2, c3 = st.columns(3)
        c1.metric("Cash", format_vnd(cash))
        c2.metric("Equity", format_vnd(equity))
        c3.metric("Unrealized PnL", format_vnd(unrealized))
        if rows_positions:
            st.dataframe(pd.DataFrame(rows_positions), width='stretch', hide_index=True)
        else:
            st.info("Chua co vi the nao.")

    c_close = st.container()
    with c_close:
        if st.button("Thoat EV am", width='stretch'):
            with st.spinner("Dong vi the EV am..."):
                closed = close_negative_ev_positions()
            if closed:
                st.success(f"Da thoat: {closed}")
            else:
                st.info("Khong co vi the EV am")

    rows = st.session_state.get("ai_fund_scan", [])
    if rows:
        df = pd.DataFrame(rows).sort_values("score", ascending=False)
        st.markdown("#### Bang xep hang tin hieu")
        st.dataframe(style_scan_table(df), width='stretch', hide_index=True)
        candidates = select_ai_candidates(rows, top_n)
        if candidates:
            st.markdown("#### Top se trade")
            plan_df = pd.DataFrame(candidates)
            plan_cols = [
                "symbol", "score", "ev", "ev_win_rate", "ev_trades", "price", "entry_window", "target_price",
                "stop_loss", "hold_days", "sell_rule", "reason"
            ]
            plan_cols = [col for col in plan_cols if col in plan_df.columns]
            st.dataframe(plan_df[plan_cols], width='stretch', hide_index=True)
        else:
            st.warning("Chua co ma nao dat dieu kien BUY score >= 65.")
    else:
        st.info("Bam Scan top candidates hoac Auto trade top de tao bang xep hang moi theo cau hinh hien tai.")
    st.info("Portfolio va PnL duoc theo doi chung trong tab Portfolio & PnL. AI fund chi hien luong quyet dinh va vi the hien tai.")


def render_portfolio():
    st.subheader("Portfolio & PnL")
    refresh_cols = st.columns([0.78, 0.22])
    with refresh_cols[1]:
        if st.button("Refresh", width='stretch'):
            st.rerun()
    auto_snapshot_if_needed()
    portfolio = load_portfolio()
    cash = float(portfolio.get("cash", 0))
    rows = []
    market_value = 0.0
    unrealized = 0.0
    for symbol, pos in portfolio.get("positions", {}).items():
        price = current_price(symbol)
        qty = int(pos.get("qty", 0))
        avg = float(pos.get("avg_price", 0))
        plan = pos.get("plan") or {}
        value = qty * price if price else 0
        pnl = (price - avg) * qty if price else 0
        pnl_pct = ((price - avg) / avg * 100) if price and avg else 0
        market_value += value
        unrealized += pnl
        stop = plan.get("stop_loss") or avg * (1 - STOP_LOSS_PCT)
        target = plan.get("target_price") or avg * (1 + TAKE_PROFIT_PCT)

        # Distance to stop/target as % of current price
        dist_stop_pct  = ((price - stop)   / price * 100) if price and stop   else None
        dist_tgt_pct   = ((target - price) / price * 100) if price and target else None

        # Status label
        if price and price <= stop:
            status = "🔴 CẮT LỖ NGAY"
        elif price and price >= target:
            status = "🟢 CHỐT LỜI NGAY"
        elif dist_stop_pct is not None and dist_stop_pct <= 3:
            status = f"🟠 Gần stop ({dist_stop_pct:.1f}%)"
        elif dist_tgt_pct is not None and dist_tgt_pct <= 3:
            status = f"🎯 Gần target ({dist_tgt_pct:.1f}%)"
        else:
            status = f"⏳ Giữ ({pnl_pct:+.1f}%)"

        rows.append({
            "Mã": symbol,
            "SL": qty,
            "Giá vào": avg,
            "Giá hiện tại": price,
            "Stoploss": round(stop, 1),
            "Target": round(target, 1),
            "Còn đến stop": f"{dist_stop_pct:.1f}%" if dist_stop_pct is not None else "-",
            "Còn đến target": f"{dist_tgt_pct:.1f}%" if dist_tgt_pct is not None else "-",
            "PnL%": f"{pnl_pct:+.1f}%",
            "PnL (VND)": round(pnl),
            "Trạng thái": status,
            "Ngày giữ": plan.get("hold_days", ""),
        })

    equity = cash + market_value
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash", format_vnd(cash))
    c2.metric("Market value", format_vnd(market_value))
    c3.metric("Equity", format_vnd(equity))
    c4.metric("Unrealized PnL", format_vnd(unrealized))

    if rows:
        # Highlight urgent rows
        urgent = [r for r in rows if r["Trạng thái"].startswith(("🔴", "🟢", "🟠", "🎯"))]
        if urgent:
            st.markdown("#### ⚠️ Cần hành động")
            for r in urgent:
                color = "#7f1d1d" if "🔴" in r["Trạng thái"] else "#14532d" if "🟢" in r["Trạng thái"] else "#78350f"
                st.markdown(
                    f'<div style="background:{color};border-radius:8px;padding:0.6rem 1rem;margin-bottom:0.4rem;">'
                    f'<b>{r["Mã"]}</b> — {r["Trạng thái"]} &nbsp;|&nbsp; '
                    f'Giá: <b>{r["Giá hiện tại"]:,.1f}</b> &nbsp;|&nbsp; '
                    f'Stop: <b>{r["Stoploss"]:,.1f}</b> &nbsp;|&nbsp; '
                    f'Target: <b>{r["Target"]:,.1f}</b>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        recent_alerts = load_intraday_alerts(active_symbols=[r["Mã"] for r in rows])
        if recent_alerts:
            st.markdown("#### 🚨 Intraday alerts")
            st.caption("Chỉ hiện alert của mã đang còn trong portfolio.")
            for alert in reversed(recent_alerts):
                msg = str(alert.get("message", ""))
                time_str = ""
                try:
                    time_str = datetime.fromisoformat(str(alert.get("time", ""))).strftime("%H:%M")
                except Exception:
                    pass
                line = f"{time_str} {msg}".strip()
                if "STOP LOSS" in msg or "🔴" in msg:
                    st.error(line)
                elif "TARGET" in msg or "🟢" in msg:
                    st.success(line)
                elif "gần stop" in msg or "🟠" in msg:
                    st.warning(line)
                else:
                    st.info(line)
        st.markdown("#### Hành động nhanh")
        st.caption("Nút `SELL` bên dưới sẽ bán toàn bộ vị thế của mã tương ứng.")
        for r in rows:
            action_cols = st.columns([1.2, 0.9, 0.9, 0.9, 0.8])
            action_cols[0].markdown(f"**{r['Mã']}**")
            action_cols[1].caption(f"SL: {r['SL']}")
            action_cols[2].caption(f"PnL: {r['PnL%']}")
            action_cols[3].caption(f"Stop: {r['Stoploss']:.1f}")
            if action_cols[4].button("SELL", key=f"portfolio_sell_{r['Mã']}", width='stretch'):
                ok, msg = sell_position(r["Mã"])
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
        st.markdown("#### Tất cả vị thế")
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.info("Chưa có vị thế.")

    st.markdown("#### Equity History")
    history = load_json(_portfolio_snapshot_path(), [])
    if isinstance(history, list) and len(history) >= 2:
        hist_df = pd.DataFrame(history)
        hist_df["date"] = pd.to_datetime(hist_df["date"])
        hist_df = hist_df.set_index("date")
        st.line_chart(hist_df[["equity"]], height=220)
        first_equity = float(history[0].get("equity", equity))
        last_equity = float(history[-1].get("equity", equity))
        total_return = ((last_equity - first_equity) / first_equity * 100) if first_equity else 0.0
        days = (pd.to_datetime(history[-1]["date"]) - pd.to_datetime(history[0]["date"])).days
        c5, c6, c7 = st.columns(3)
        c5.metric("Total Return", f"{total_return:+.2f}%")
        c6.metric("Days Tracked", days)
        c7.metric("Snapshots", len(history))
    else:
        st.caption("Can it nhat 2 ngay de hien thi equity chart.")

    if st.button("Cháº¡y kiá»ƒm tra stop-loss / take-profit", width='stretch'):
        messages = run_risk_checks()
        if messages:
            for msg in messages:
                st.warning(msg)
        else:
            st.success("KhÃ´ng cÃ³ vá»‹ tháº¿ nÃ o cháº¡m stop-loss/take-profit.")


def render_history():
    st.subheader("Trade history")
    trades = load_trades()
    if not trades:
        st.info("ChÆ°a cÃ³ giao dá»‹ch.")
        return

    df = pd.DataFrame(trades)
    sells = df[df["side"] == "SELL"].copy()
    closed = sells[sells["pnl"].notna()]
    if len(closed):
        wins = int((closed["pnl"] > 0).sum())
        win_rate = wins / len(closed) * 100
        realized = float(closed["pnl"].sum())
    else:
        wins = 0
        win_rate = 0
        realized = 0

    h1, h2, h3 = st.columns(3)
    h1.metric("Closed trades", len(closed))
    h2.metric("Win rate", f"{win_rate:.1f}%")
    h3.metric("Realized PnL", format_vnd(realized))
    st.dataframe(df.sort_values("time", ascending=False), width='stretch', hide_index=True)


def main():
    st.set_page_config(page_title="VN Paper Auto Trader", layout="wide", page_icon="ðŸ“ˆ")
    apply_auto_trader_style()
    render_header()

    with st.sidebar:
        st.markdown("### Settings")
        symbol_text = st.text_area(
            "Universe",
            value=", ".join(DEFAULT_SYMBOLS),
            height=140,
        )
        symbols = normalize_symbols(symbol_text) or DEFAULT_SYMBOLS
        use_ollama = st.checkbox("LLM vote", value=True)
        st.caption("LLM phân tích qua Groq / Gemini / Ollama")
        auto_trade = False
        if st.button("Reset paper account", width='stretch'):
            reset_state()
            st.success("ÄÃ£ reset vá» 100M VND.")

    tabs = st.tabs(["AI fund", "Signal scanner", "Portfolio & PnL", "Trade history"])
    with tabs[0]:
        render_ai_fund(symbols)
    with tabs[1]:
        render_scanner(symbols, use_ollama, auto_trade)
    with tabs[2]:
        render_portfolio()
    with tabs[3]:
        render_history()


if __name__ == "__main__":
    main()
