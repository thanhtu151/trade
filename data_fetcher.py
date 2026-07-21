"""
Fetch and cache external data sources for feature engineering.
"""

import concurrent.futures
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


log = logging.getLogger("data_fetcher")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
VNSTOCK_CACHE_DIR = os.path.join(CACHE_DIR, "vnstock")
os.makedirs(VNSTOCK_CACHE_DIR, exist_ok=True)

VNSTOCK_CALL_TIMEOUT_SECONDS = 25


def _call_with_timeout(fn, *args, timeout=VNSTOCK_CALL_TIMEOUT_SECONDS, **kwargs):
    """
    Run a blocking call (e.g. vnstock's Quote.history()) in a worker thread and
    give up after `timeout` seconds. vnstock/requests calls in this module carry
    no built-in timeout, so a slow/unresponsive API can otherwise hang the caller
    (dashboard UI or scheduler) indefinitely. The worker thread itself cannot be
    killed once started, but the caller is freed to move on.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        executor.shutdown(wait=False)


def _cache_path(key):
    safe_key = str(key).replace("/", "_").replace("\\", "_").replace(":", "_")
    return os.path.join(CACHE_DIR, f"{safe_key}.json")


def _load_cache(key, max_age_hours=6):
    path = _cache_path(key)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(data["cached_at"])).total_seconds() / 3600
        if age < max_age_hours:
            return data["value"]
    except Exception:
        pass
    return None


def _save_cache(key, value):
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "value": value}, f, ensure_ascii=False)
    except Exception:
        pass


def _flatten_yfinance_columns(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
    return df


def _prepare_yfinance_cache(yf):
    try:
        yf.set_tz_cache_location(os.path.join(CACHE_DIR, "yfinance"))
    except Exception:
        pass


def _records_to_frame(cached):
    return pd.DataFrame(cached) if cached else pd.DataFrame()


def _vnstock_cache_path(ticker, years):
    safe_ticker = str(ticker).upper().replace("/", "_").replace("\\", "_")
    years_key = ("%s" % years).replace(".", "p")
    return os.path.join(VNSTOCK_CACHE_DIR, f"{safe_ticker}_{years_key}y.json")


def _ttl_hours_for_vnstock():
    now_hour = datetime.now().hour
    return 4 if 8 <= now_hour <= 16 else 12


def fetch_with_fallback(ticker: str, start: str, end: str, interval: str = "1D"):
    """Fetch OHLCV trying VCI → TCBS → MSN. Returns (DataFrame, source_used)."""
    import source_manager
    from vnstock.api.quote import Quote

    current = source_manager.get_source()
    ordered = [current] + [s for s in source_manager.SOURCES if s != current]

    last_exc = None
    for source in ordered:
        try:
            df = _call_with_timeout(
                Quote(symbol=ticker, source=source).history, start=start, end=end, interval=interval
            )
            if df is None or df.empty:
                raise ValueError(f"empty response from {source}")
            df = df.copy()
            df["time"] = pd.to_datetime(df["time"])
            source_manager.report_success(source)
            log.info("  %s: fetched %d rows via %s", ticker, len(df), source)
            return df, source
        except Exception as exc:
            log.warning("  %s: source %s failed: %s", ticker, source, exc)
            source_manager.report_failure(source)
            last_exc = exc

    raise last_exc or RuntimeError(f"All sources failed for {ticker}")


def _serialize_frame_records(df):
    records = df.to_dict("records")
    for row in records:
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
    return records


def get_stock_data_cached(ticker, years=1, force_refresh=False):
    """
    Fetch OHLCV data with a file cache to avoid VNStock rate limits.
    TTL is 4 hours during trading hours and 12 hours outside trading hours.
    """
    ticker = str(ticker).upper()
    cache_path = _vnstock_cache_path(ticker, years)

    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            cached_at = datetime.fromisoformat(cached["cached_at"])
            age_hours = (datetime.now() - cached_at).total_seconds() / 3600
            if age_hours < _ttl_hours_for_vnstock():
                df = pd.DataFrame(cached["data"])
                df["time"] = pd.to_datetime(df["time"])
                log.info("  %s: using cached data (%.1fh old, %s rows)", ticker, age_hours, len(df))
                return df.sort_values("time").reset_index(drop=True)
        except Exception as exc:
            log.warning("  Cache read failed for %s: %s", ticker, exc)

    log.info("  %s: fetching from vnstock API (with source fallback)...", ticker)
    try:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=int(float(years) * 365))).strftime("%Y-%m-%d")
        df, _source = fetch_with_fallback(ticker, start, end)
        df = df.sort_values("time").reset_index(drop=True)

        cache_data = {
            "ticker": ticker,
            "years": years,
            "cached_at": datetime.now().isoformat(),
            "rows": len(df),
            "data": _serialize_frame_records(df),
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)
        time.sleep(1)
        return df
    except Exception as exc:
        log.error("  %s: all sources failed: %s", ticker, exc)
        if os.path.exists(cache_path):
            log.warning("  %s: using stale cache as last resort", ticker)
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            df = pd.DataFrame(cached["data"])
            df["time"] = pd.to_datetime(df["time"])
            return df.sort_values("time").reset_index(drop=True)
        raise


def invalidate_stock_cache(ticker=None):
    """Clear one ticker cache or all VNStock cache files."""
    if ticker:
        ticker = str(ticker).upper()
        for path in Path(VNSTOCK_CACHE_DIR).glob(f"{ticker}_*.json"):
            path.unlink()
            log.info("Cache invalidated: %s", path.name)
    else:
        for path in Path(VNSTOCK_CACHE_DIR).glob("*.json"):
            path.unlink()
        log.info("All vnstock cache cleared")


def get_cache_status():
    """Return current VNStock cache status."""
    status = []
    for path in sorted(Path(VNSTOCK_CACHE_DIR).glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            cached_at = datetime.fromisoformat(data["cached_at"])
            age_min = (datetime.now() - cached_at).total_seconds() / 60
            status.append(
                {
                    "ticker": data.get("ticker"),
                    "rows": data.get("rows"),
                    "age_min": round(age_min, 1),
                    "file": path.name,
                }
            )
        except Exception:
            pass
    return status


def fetch_usdvnd(years=6):
    """Fetch USD/VND exchange rate from yfinance."""
    cache_key = f"usdvnd_{years}y"
    cached = _load_cache(cache_key, max_age_hours=6)
    if cached is not None:
        return _records_to_frame(cached)

    try:
        import yfinance as yf

        _prepare_yfinance_cache(yf)
        end = datetime.now()
        start = end - timedelta(days=years * 365)
        df = yf.download("USDVND=X", start=start, end=end, progress=False, auto_adjust=False)
        if df.empty:
            df = yf.download("VND=X", start=start, end=end, progress=False, auto_adjust=False)
        if df.empty:
            return pd.DataFrame()

        df = _flatten_yfinance_columns(df)
        df = df[["Close"]].rename(columns={"Close": "usdvnd"})
        df.index = pd.to_datetime(df.index)
        df["usdvnd_change"] = df["usdvnd"].pct_change()
        df["usdvnd_ma20"] = df["usdvnd"].rolling(20).mean()
        df["usdvnd_deviation"] = df["usdvnd"] / df["usdvnd_ma20"] - 1
        result = df.reset_index().rename(columns={"Date": "time"})
        result["time"] = result["time"].astype(str)
        _save_cache(cache_key, result.to_dict("records"))
        return result
    except Exception as exc:
        log.warning("USD/VND fetch failed: %s", exc)
        return pd.DataFrame()


def fetch_vix(years=6):
    """Fetch VIX fear index from yfinance."""
    cache_key = f"vix_{years}y"
    cached = _load_cache(cache_key, max_age_hours=6)
    if cached is not None:
        return _records_to_frame(cached)

    try:
        import yfinance as yf

        _prepare_yfinance_cache(yf)
        end = datetime.now()
        start = end - timedelta(days=years * 365)
        df = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)
        if df.empty:
            return pd.DataFrame()

        df = _flatten_yfinance_columns(df)
        df = df[["Close"]].rename(columns={"Close": "vix"})
        df.index = pd.to_datetime(df.index)
        df["vix_change"] = df["vix"].pct_change()
        df["vix_ma10"] = df["vix"].rolling(10).mean()
        df["vix_regime"] = (df["vix"] > 20).astype(int)
        result = df.reset_index().rename(columns={"Date": "time"})
        result["time"] = result["time"].astype(str)
        _save_cache(cache_key, result.to_dict("records"))
        return result
    except Exception as exc:
        log.warning("VIX fetch failed: %s", exc)
        return pd.DataFrame()


def _normalize_time_column(df):
    df = df.copy()
    if "time" not in df.columns:
        for col in ("date", "trading_date", "tradingdate"):
            if col in df.columns:
                df = df.rename(columns={col: "time"})
                break
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"]).dt.date.astype(str)
    return df


def fetch_foreign_trading(ticker, years=6):
    """Fetch foreign net trading data from vnstock when available."""
    ticker = str(ticker).upper()
    cache_key = f"foreign_{ticker}_{years}y"
    cached = _load_cache(cache_key, max_age_hours=6)
    if cached is not None:
        return _records_to_frame(cached)

    try:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
        df = pd.DataFrame()

        try:
            from vnstock.api.trading import Trading

            trading = Trading(source="vci", symbol=ticker, show_log=False)
            df = _call_with_timeout(trading.foreign_trade, start=start, end=end)
        except Exception:
            try:
                from vnstock import Vnstock

                stock = Vnstock().stock(symbol=ticker, source="VCI")
                df = _call_with_timeout(stock.trading.foreign_trading, start=start, end=end)
            except Exception:
                df = pd.DataFrame()
        finally:
            time.sleep(2)

        if df is None or df.empty:
            _save_cache(cache_key, [])
            return pd.DataFrame()

        df = df.copy()
        df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
        df = _normalize_time_column(df)
        if "time" not in df.columns:
            _save_cache(cache_key, [])
            return pd.DataFrame()

        net_col = None
        preferred = ("foreign_net", "net_value", "net_buy_value", "net_volume", "net_buy_volume")
        for col in preferred:
            if col in df.columns:
                net_col = col
                break
        if net_col is None:
            for col in df.columns:
                if "net" in col and ("buy" in col or "value" in col or "vol" in col):
                    net_col = col
                    break
        if net_col is None:
            _save_cache(cache_key, [])
            return pd.DataFrame()

        df = df.rename(columns={net_col: "foreign_net"})
        df["foreign_net"] = pd.to_numeric(df["foreign_net"], errors="coerce").fillna(0)
        df = df.sort_values("time").reset_index(drop=True)
        df["foreign_net_ma5"] = df["foreign_net"].rolling(5).mean()
        df["foreign_net_ma20"] = df["foreign_net"].rolling(20).mean()
        df["foreign_buying"] = (df["foreign_net"] > 0).astype(int)
        df["foreign_net_norm"] = df["foreign_net"] / (df["foreign_net"].abs().rolling(20).mean() + 1e-9)

        result = df[
            ["time", "foreign_net", "foreign_net_ma5", "foreign_net_ma20", "foreign_buying", "foreign_net_norm"]
        ].copy()
        result["time"] = result["time"].astype(str)
        _save_cache(cache_key, result.to_dict("records"))
        return result
    except Exception as exc:
        log.warning("Foreign trading fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()


def fetch_vnindex(years=6):
    """Fetch VNIndex to calculate relative strength."""
    cache_key = f"vnindex_{years}y"
    cached = _load_cache(cache_key, max_age_hours=6)
    if cached is not None:
        return _records_to_frame(cached)

    try:
        from vnstock.api.quote import Quote

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
        stock = Quote(symbol="VNINDEX", source="VCI")
        try:
            df = _call_with_timeout(stock.history, start=start, end=end, interval="1D")
        finally:
            time.sleep(2)

        if df is None or df.empty:
            return pd.DataFrame()

        df = _normalize_time_column(df)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.sort_values("time").reset_index(drop=True)
        df["vni_return"] = df["close"].pct_change()
        df["vni_ma20"] = df["close"].rolling(20).mean()
        df["vni_trend"] = (df["close"] > df["vni_ma20"]).astype(int)
        result = df[["time", "close", "vni_return", "vni_ma20", "vni_trend"]].copy()
        result = result.rename(columns={"close": "vnindex_close"})
        result["time"] = result["time"].astype(str)
        _save_cache(cache_key, result.to_dict("records"))
        return result
    except Exception as exc:
        log.warning("VNIndex fetch failed: %s", exc)
        return pd.DataFrame()


def get_weekly_trend(ticker):
    """
    Get weekly trend proxy for a ticker.
    Returns: dict with trend (1=uptrend, -1=downtrend, 0=sideways).
    """
    ticker = str(ticker).upper()
    cache_key = f"weekly_{ticker}"
    cached = _load_cache(cache_key, max_age_hours=24)
    if cached is not None:
        return cached

    try:
        df = get_stock_data_cached(ticker, years=1)
        if df is None or len(df) < 20:
            result = {"trend": 0, "score": 0}
            _save_cache(cache_key, result)
            return result

        close = df["close"].astype(float)

        sma5w = close.rolling(25).mean().iloc[-1]
        sma10w = close.rolling(50).mean().iloc[-1]

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(21).mean()
        loss = (-delta.clip(upper=0)).rolling(21).mean()
        rsi_weekly = (100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1]

        mom_4w = (close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0

        score = 0
        if sma5w > sma10w:
            score += 1
        if rsi_weekly > 50:
            score += 1
        if mom_4w > 0.02:
            score += 1

        if score >= 2:
            trend = 1
        elif score <= 0:
            trend = -1
        else:
            trend = 0

        result = {
            "trend": trend,
            "sma5w": round(float(sma5w), 1),
            "sma10w": round(float(sma10w), 1),
            "rsi_weekly": round(float(rsi_weekly), 1),
            "mom_4w_pct": round(float(mom_4w) * 100, 2),
            "score": int(score),
        }
        _save_cache(cache_key, result)
        return result
    except Exception as exc:
        log.warning("Weekly trend %s: %s", ticker, exc)
        return {"trend": 0, "score": 0}


def get_news_sentiment_fast(ticker, max_news=5):
    """
    Fast keyword-based news sentiment. No LLM.
    Returns float in [-1.0, 1.0].
    """
    ticker = str(ticker).upper()
    cache_key = f"news_sentiment_{ticker}"
    cached = _load_cache(cache_key, max_age_hours=2)
    if cached is not None:
        try:
            return float(cached)
        except Exception:
            return 0.0

    positive_keywords = [
        "tăng trưởng", "lợi nhuận tăng", "kỷ lục", "vượt kế hoạch",
        "cổ tức", "mua vào", "khuyến nghị mua", "tích cực", "triển vọng",
        "tăng vốn", "mở rộng", "hợp đồng lớn", "doanh thu tăng",
    ]
    negative_keywords = [
        "lỗ", "giảm mạnh", "thua lỗ", "bán ra", "khuyến nghị bán",
        "rủi ro", "vi phạm", "xử phạt", "nợ xấu", "thoái vốn",
        "doanh thu giảm", "lợi nhuận giảm", "khó khăn",
    ]

    try:
        import feedparser

        feeds = [
            "https://cafef.vn/thi-truong-chung-khoan.rss",
            "https://vietstock.vn/rss/co-phieu.rss",
        ]

        all_text = ""
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:max_news]:
                    title = str(entry.get("title", "")).lower()
                    summary = str(entry.get("summary", "")).lower()
                    if ticker.lower() in title or ticker.lower() in summary:
                        all_text += f" {title} {summary}"
            except Exception:
                continue

        if not all_text:
            _save_cache(cache_key, 0.0)
            return 0.0

        pos_count = sum(1 for kw in positive_keywords if kw in all_text)
        neg_count = sum(1 for kw in negative_keywords if kw in all_text)
        total = pos_count + neg_count
        if total == 0:
            score = 0.0
        else:
            score = (pos_count - neg_count) / total
            score = max(-1.0, min(1.0, score))

        _save_cache(cache_key, score)
        return float(score)
    except Exception as exc:
        log.warning("News sentiment %s: %s", ticker, exc)
        return 0.0


def get_news_sentiment_score(ticker, news_list):
    """
    Score Vietnamese stock news sentiment with the configured LLM router.
    Returns a float from -1.0 to 1.0.
    """
    if not news_list:
        return 0.0

    try:
        from llm_router import call_llm_json

        news_text = "\n".join([f"- {item}" for item in news_list[:5] if item])
        if not news_text.strip():
            return 0.0
        result = call_llm_json(
            prompt=f"""Phân tích sentiment các tin tức sau về cổ phiếu {ticker}:
{news_text}

Trả về JSON với format:
{{"score": <float từ -1.0 đến 1.0>, "reason": "<1 câu ngắn>"}}

Trong đó: 1.0 = rất tích cực, 0 = trung tính, -1.0 = rất tiêu cực""",
            system="Bạn là chuyên gia phân tích tin tức tài chính VN. Chỉ trả về JSON.",
            max_tokens=100,
        )
        if result and "score" in result:
            return max(-1.0, min(1.0, float(result["score"])))
    except Exception:
        pass
    return 0.0
