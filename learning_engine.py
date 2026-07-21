"""
Self-learning engine for prediction tracking, LLM memory, and retraining.
"""

import json
import os
from datetime import date, datetime

import numpy as np


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "learning_memory.json")
PREDICTION_LOG = os.path.join(BASE_DIR, "prediction_log.json")
PREDICTION_HISTORY = os.path.join(BASE_DIR, "prediction_history.json")
PERFORMANCE_REPORT = os.path.join(BASE_DIR, "performance_report.json")


def _load_predictions():
    try:
        with open(PREDICTION_LOG, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_predictions(data):
    with open(PREDICTION_LOG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_prediction_history():
    try:
        with open(PREDICTION_HISTORY, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_prediction_history(history):
    with open(PREDICTION_HISTORY, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _load_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_memory(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _latest_close(ticker):
    try:
        from data_fetcher import get_stock_data_cached

        df = get_stock_data_cached(ticker, years=0.1)
        if df is not None and len(df) > 0:
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    return None


def log_prediction(ticker, predicted_direction, predicted_price, confidence, source="ensemble", notes=""):
    """
    Store a daily prediction before the actual result is known.
    """
    ticker = str(ticker).upper()
    predictions = _load_predictions()
    key = f"{ticker}_{date.today().isoformat()}"

    entry = {
        "id": key,
        "ticker": ticker,
        "date": date.today().isoformat(),
        "predicted_direction": int(predicted_direction or 0),
        "predicted_price": float(predicted_price or 0),
        "confidence": float(confidence or 0),
        "source": str(source),
        "notes": str(notes),
        "entry_price": None,
        "actual_price_3d": None,
        "actual_direction": None,
        "correct": None,
        "pnl_pct": None,
        "logged_at": datetime.now().isoformat(),
        "resolved": False,
    }

    entry["entry_price"] = _latest_close(ticker)
    predictions[key] = entry
    _save_predictions(predictions)

    try:
        if int(predicted_direction or 0) != 0:
            _sync_to_prediction_history(ticker, int(predicted_direction or 0), predicted_price, confidence, entry)
    except Exception:
        pass

    return entry


def _sync_to_prediction_history(ticker, direction, price, confidence, log_entry):
    """
    Mirror non-neutral learning-engine predictions into dashboard history.
    Dashboard history uses symbol/prediction/correct, so keep those fields and
    add ticker/action/source for richer display and future compatibility.
    """
    ticker = str(ticker).upper()
    today = date.today().isoformat()
    history = _load_prediction_history()

    for row in history:
        row_ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        row_date = str(row.get("date") or "")[:10]
        if row_ticker == ticker and row_date == today:
            return

    action_map = {1: "MUA", -1: "BÁN"}
    action = action_map.get(int(direction), "GIỮ")
    entry_price = float(log_entry.get("entry_price") or price or 0)
    atr_estimate = entry_price * 0.02

    if int(direction) == 1:
        target = round(entry_price + 2.0 * atr_estimate, 1)
        stoploss = round(entry_price - 1.0 * atr_estimate, 1)
    elif int(direction) == -1:
        target = round(entry_price - 2.0 * atr_estimate, 1)
        stoploss = round(entry_price + 1.0 * atr_estimate, 1)
    else:
        target = 0
        stoploss = 0

    history.append({
        "date": today,
        "symbol": ticker,
        "ticker": ticker,
        "price_at_prediction": entry_price,
        "prediction": action,
        "action": action,
        "target": target,
        "stoploss": stoploss,
        "timeframe": "3 phiên",
        "confidence": float(confidence or 0),
        "source": log_entry.get("source", "ensemble"),
        "notes": log_entry.get("notes", ""),
        "actual_price": None,
        "correct": None,
        "outcome": None,
        "resolved": False,
        "synced_from": "prediction_log",
    })
    _save_prediction_history(history)


def _update_history_outcome(ticker, pred_date, correct, pnl_pct):
    ticker = str(ticker).upper()
    history = _load_prediction_history()
    changed = False

    for row in history:
        row_ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        row_date = str(row.get("date") or "")[:10]
        if row_ticker == ticker and row_date == str(pred_date)[:10]:
            row["outcome"] = "correct" if correct else "incorrect"
            row["pnl_pct"] = pnl_pct
            row["correct"] = bool(correct)
            row["resolved"] = True
            changed = True

    if changed:
        _save_prediction_history(history)


def resolve_predictions():
    """
    Resolve predictions that are at least 3 days old.
    """
    predictions = _load_predictions()
    resolved_count = 0
    today = date.today()

    for pred_id, pred in predictions.items():
        if pred.get("resolved"):
            continue
        try:
            pred_date = date.fromisoformat(pred["date"])
        except Exception:
            continue

        if (today - pred_date).days < 3:
            continue
        if pred.get("entry_price") is None:
            continue

        now_hour = datetime.now().hour
        if now_hour < 15:
            continue

        current_price = _latest_close(pred["ticker"])
        if current_price is None:
            continue

        entry_price = float(pred["entry_price"])
        actual_direction = 1 if current_price > entry_price else -1
        pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price else 0.0
        correct = actual_direction == int(pred.get("predicted_direction", 0))

        pred["actual_price_3d"] = float(current_price)
        pred["actual_direction"] = int(actual_direction)
        pred["correct"] = bool(correct)
        pred["pnl_pct"] = round(float(pnl_pct), 3)
        pred["resolved"] = True
        pred["resolved_at"] = datetime.now().isoformat()
        _update_history_outcome(pred["ticker"], pred["date"], pred["correct"], pred["pnl_pct"])
        resolved_count += 1

    _save_predictions(predictions)
    return resolved_count


def calculate_accuracy_stats():
    """
    Calculate stats per ticker and store them in memory.
    """
    predictions = _load_predictions()
    resolved = [p for p in predictions.values() if p.get("resolved")]
    if not resolved:
        return {}

    resolved = sorted(resolved, key=lambda p: (p.get("ticker", ""), p.get("date", "")))
    stats = {}

    tickers = sorted({str(p["ticker"]).upper() for p in resolved})
    for ticker in tickers:
        ticker_preds = [p for p in resolved if str(p.get("ticker", "")).upper() == ticker]
        if len(ticker_preds) < 3:
            continue

        correct = sum(1 for p in ticker_preds if p.get("correct"))
        total = len(ticker_preds)
        pnl_values = [float(p.get("pnl_pct") or 0.0) for p in ticker_preds]
        recent_5 = ticker_preds[-5:]
        recent_acc = sum(1 for p in recent_5 if p.get("correct")) / max(1, len(recent_5))

        stats[ticker] = {
            "total_predictions": total,
            "accuracy": round(correct / total, 3),
            "recent_5_accuracy": round(float(recent_acc), 3),
            "avg_pnl_pct": round(float(np.mean(pnl_values)), 3),
            "trend": "improving" if recent_acc > (correct / total) else "declining",
            "last_5": [
                {"date": p.get("date"), "correct": bool(p.get("correct")), "pnl": p.get("pnl_pct")}
                for p in recent_5
            ],
        }

    overall_correct = sum(1 for p in resolved if p.get("correct"))
    stats["_overall"] = {
        "total": len(resolved),
        "accuracy": round(overall_correct / len(resolved), 3),
        "avg_pnl": round(float(np.mean([float(p.get("pnl_pct") or 0.0) for p in resolved])), 3),
    }

    memory = _load_memory()
    memory["accuracy_stats"] = stats
    memory["updated_at"] = datetime.now().isoformat()
    _save_memory(memory)
    return stats


def build_llm_context(ticker):
    """
    Return a short memory summary for prompt injection.
    """
    ticker = str(ticker).upper()
    memory = _load_memory()
    stats = memory.get("accuracy_stats") or {}
    ticker_stats = stats.get(ticker) or {}
    if not ticker_stats:
        return ""

    acc = float(ticker_stats.get("accuracy", 0))
    recent_acc = float(ticker_stats.get("recent_5_accuracy", 0))
    avg_pnl = float(ticker_stats.get("avg_pnl_pct", 0))
    total = int(ticker_stats.get("total_predictions", 0))
    trend = ticker_stats.get("trend", "unknown")
    last_5 = ticker_stats.get("last_5", [])
    last_5_str = " ".join([f"{'OK' if p.get('correct') else 'ERR'}({float(p.get('pnl') or 0):+.1f}%)" for p in last_5])

    context = (
        f"LENH SU DU DOAN {ticker} ({total} lan):\n"
        f"- Accuracy tong: {acc:.0%} | Gan day (5 lan): {recent_acc:.0%}\n"
        f"- PnL trung binh: {avg_pnl:+.2f}%/prediction | XU huong: {trend}\n"
        f"- 5 lan gan nhat: {last_5_str}\n"
    )
    if recent_acc < 0.4:
        context += f"Model dang sai nhieu cho {ticker}, giam confidence va uu tien HOLD.\n"
    elif recent_acc > 0.65:
        context += f"Model dang tot cho {ticker}, co the tin signal hon.\n"
    return context


def get_signal_weight(ticker):
    """
    Map historical accuracy to a signal weight.
    """
    ticker = str(ticker).upper()
    memory = _load_memory()
    stats = memory.get("accuracy_stats") or {}
    ticker_stats = stats.get(ticker) or {}

    if not ticker_stats or int(ticker_stats.get("total_predictions", 0)) < 5:
        return 1.0

    recent_acc = float(ticker_stats.get("recent_5_accuracy", 0.5))
    if recent_acc >= 0.70:
        return 1.5
    if recent_acc >= 0.55:
        return 1.2
    if recent_acc >= 0.45:
        return 1.0
    if recent_acc >= 0.35:
        return 0.7
    return 0.5


def should_retrain(ticker):
    """
    Trigger retraining when recent accuracy is low or model is stale.
    """
    ticker = str(ticker).upper()
    memory = _load_memory()
    stats = memory.get("accuracy_stats") or {}
    ticker_stats = stats.get(ticker) or {}
    recent_acc = float(ticker_stats.get("recent_5_accuracy", 0.5))

    if recent_acc < 0.45 and int(ticker_stats.get("total_predictions", 0)) >= 5:
        return True

    train_log = memory.get("last_trained") or {}
    last_train = train_log.get(ticker)
    if not last_train:
        return True

    try:
        days_since = (date.today() - date.fromisoformat(last_train)).days
        return days_since >= 7
    except Exception:
        return True


def retrain_if_needed(tickers):
    """
    Retrain selected tickers when needed.
    """
    from train_ensemble import train_all
    from train_lstm import get_data

    retrained = []
    memory = _load_memory()
    train_log = memory.setdefault("last_trained", {})

    for ticker in tickers:
        ticker = str(ticker).upper()
        if not should_retrain(ticker):
            continue
        try:
            df = get_data(ticker, years=6)
            train_all(ticker, df)
            train_log[ticker] = date.today().isoformat()
            retrained.append(ticker)
        except Exception as exc:
            print(f"  {ticker} retrain failed: {exc}")

    _save_memory(memory)
    return retrained


def should_retrain_lstm(ticker):
    """
    Same staleness/accuracy trigger as should_retrain(), but tracked against
    the LSTM model's own last-trained date (lstm_models/<ticker>_direction_model.h5
    is trained separately from the XGB/LGBM/RF ensemble and on a slower cadence).
    """
    ticker = str(ticker).upper()
    memory = _load_memory()
    stats = memory.get("accuracy_stats") or {}
    ticker_stats = stats.get(ticker) or {}
    recent_acc = float(ticker_stats.get("recent_5_accuracy", 0.5))

    if recent_acc < 0.45 and int(ticker_stats.get("total_predictions", 0)) >= 5:
        return True

    train_log = memory.get("last_trained_lstm") or {}
    last_train = train_log.get(ticker)
    if not last_train:
        return True

    try:
        days_since = (date.today() - date.fromisoformat(last_train)).days
        return days_since >= 7
    except Exception:
        return True


def retrain_lstm_if_needed(tickers, max_per_day=3):
    """
    Retrain LSTM direction models for at most `max_per_day` tickers per call,
    worst-accuracy-first. LSTM training (walk-forward validation + Keras fit)
    is much slower than the ensemble, so it's deliberately throttled instead of
    retraining every eligible ticker in one run.
    """
    from train_lstm import train_symbol

    memory = _load_memory()
    stats = memory.get("accuracy_stats") or {}
    train_log = memory.setdefault("last_trained_lstm", {})

    candidates = [str(t).upper() for t in tickers if should_retrain_lstm(str(t).upper())]
    candidates.sort(key=lambda t: float((stats.get(t) or {}).get("recent_5_accuracy", 0.5)))

    retrained = []
    for ticker in candidates[:max_per_day]:
        try:
            train_symbol(ticker)
            train_log[ticker] = date.today().isoformat()
            retrained.append(ticker)
        except Exception as exc:
            print(f"  {ticker} LSTM retrain failed: {exc}")

    _save_memory(memory)
    return retrained


def generate_performance_report():
    """
    Generate and persist a performance summary.
    """
    stats = calculate_accuracy_stats()
    overall = stats.get("_overall", {"total": 0, "accuracy": 0.0, "avg_pnl": 0.0})
    report = {
        "date": date.today().isoformat(),
        "overall": overall,
        "by_ticker": {k: v for k, v in stats.items() if not str(k).startswith("_")},
        "generated_at": datetime.now().isoformat(),
    }
    with open(PERFORMANCE_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report
