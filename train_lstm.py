import json
import os
import sys
from datetime import datetime, timedelta

import joblib
import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
from tensorflow.keras.models import Model, load_model
from vnstock.api.quote import Quote


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "lstm_models")
os.makedirs(MODELS_DIR, exist_ok=True)

DEFAULT_WATCHLIST_FILE = os.path.join(BASE_DIR, "training_watchlist.json")
STATE_FILE = os.path.join(BASE_DIR, "lstm_training_state.json")
SEQUENCE_LEN = 20
TARGET_HORIZON_MIN = 5
TARGET_HORIZON_MAX = 6


def _build_horizon_target(close_series):
    future_min = close_series.shift(-TARGET_HORIZON_MIN)
    future_max = close_series.shift(-TARGET_HORIZON_MAX)
    future_avg = (future_min + future_max) / 2
    target = pd.Series(np.nan, index=close_series.index, dtype=float)
    valid_mask = future_avg.notna()
    target.loc[valid_mask] = (future_avg.loc[valid_mask] > close_series.loc[valid_mask]).astype(int)
    return target


def save_state(state):
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_symbols_from_args():
    if len(sys.argv) > 1:
        symbols = []
        for arg in sys.argv[1:]:
            symbols.extend([s.strip().upper() for s in arg.split(",") if s.strip()])
        if symbols:
            return list(dict.fromkeys(symbols))
    if os.path.exists(DEFAULT_WATCHLIST_FILE):
        try:
            with open(DEFAULT_WATCHLIST_FILE, "r", encoding="utf-8") as f:
                symbols = json.load(f)
            symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if symbols:
                return list(dict.fromkeys(symbols))
        except Exception:
            pass
    return ["VNM", "VIC", "HPG", "VHM", "MWG"]


def get_data(symbol, years=6):
    try:
        from data_fetcher import get_stock_data_cached

        return get_stock_data_cached(symbol, years=years)
    except ImportError:
        pass

    print(f"Lấy data {symbol}...")
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    stock = Quote(symbol=symbol, source="VCI")
    df = stock.history(start=start, end=end, interval="1D")
    if df is None or len(df) == 0:
        raise ValueError(f"No data returned for {symbol}")
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def build_features(df):
    """
    Builds normalized features for 5-6 session direction classification.
    Input df must have open, high, low, close, volume.
    """
    df = df.copy()
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
        df[col] = df[col].astype(float)

    df["return_1d"] = df["close"].pct_change()
    df["return_3d"] = df["close"].pct_change(3)
    df["return_5d"] = df["close"].pct_change(5)

    df["high_low_range"] = (df["high"] - df["low"]) / df["close"]
    df["gap_open"] = (df["open"] - df["close"].shift(1)) / df["close"].shift(1)
    df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
    df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]

    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma_ratio"] = df["sma20"] / df["sma50"] - 1

    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma20"]

    df["rsi"] = compute_rsi(df["close"], 14) / 100

    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_position"] = (df["close"] - (bb_mid - 2 * bb_std)) / (4 * bb_std)
    df["bb_position"] = df["bb_position"].clip(0, 1)

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd_norm"] = (ema12 - ema26) / df["close"]

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr_norm"] = tr.rolling(14).mean() / df["close"]

    df["target"] = _build_horizon_target(df["close"])

    feature_cols = [
        "return_1d",
        "return_3d",
        "return_5d",
        "high_low_range",
        "gap_open",
        "upper_shadow",
        "lower_shadow",
        "sma_ratio",
        "volume_ratio",
        "rsi",
        "bb_position",
        "macd_norm",
        "atr_norm",
    ]

    df = df.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    return df, feature_cols


def build_model(input_shape):
    inputs = Input(shape=input_shape)
    x = LSTM(64, return_sequences=True)(inputs)
    x = Dropout(0.3)(x)
    x = LSTM(32)(x)
    x = Dropout(0.3)(x)
    x = Dense(16, activation="relu")(x)
    outputs = Dense(1, activation="sigmoid")(x)

    model = Model(inputs, outputs)
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def make_sequences(features, targets, seq_len=SEQUENCE_LEN):
    X, y = [], []
    for i in range(seq_len, len(features)):
        X.append(features[i - seq_len:i])
        y.append(targets[i])
    return np.array(X), np.array(y).astype(int)


def safe_auc(y_true, y_prob):
    try:
        if len(set(np.asarray(y_true).astype(int))) < 2:
            return 0.5
        return float(roc_auc_score(y_true, y_prob))
    except ValueError:
        return 0.5


def baseline_bullish_accuracy(y_test):
    baseline_pred = np.ones_like(y_test, dtype=int)
    return float(accuracy_score(y_test, baseline_pred))


def walk_forward_validate(ticker, n_splits=5):
    ticker = str(ticker).upper()
    df, feature_cols = build_features(get_data(ticker, years=6))
    features = df[feature_cols].values
    targets = df["target"].values.astype(int)

    min_fold_rows = SEQUENCE_LEN + 80
    if len(df) < min_fold_rows:
        raise ValueError(f"Not enough data for walk-forward validation: {len(df)} rows")

    fold_results = []
    start_end = int(len(df) * 0.55)
    fold_ends = np.linspace(start_end, len(df), n_splits + 1, dtype=int)[1:]
    for fold_idx, end_idx in enumerate(fold_ends, start=1):
        fold_features = features[:end_idx]
        fold_targets = targets[:end_idx]
        if len(fold_features) < min_fold_rows:
            continue

        train_end = int(len(fold_features) * 0.8)
        train_features = fold_features[:train_end]
        train_targets = fold_targets[:train_end]
        eval_features = fold_features[train_end - SEQUENCE_LEN:]
        eval_targets = fold_targets[train_end - SEQUENCE_LEN:]

        scaler = MinMaxScaler()
        train_scaled = scaler.fit_transform(train_features)
        eval_scaled = scaler.transform(eval_features)

        X_train, y_train = make_sequences(train_scaled, train_targets)
        X_test, y_test = make_sequences(eval_scaled, eval_targets)
        if len(X_train) == 0 or len(X_test) == 0:
            continue

        tf.keras.backend.clear_session()
        model = build_model((SEQUENCE_LEN, len(feature_cols)))
        callbacks = [EarlyStopping(patience=5, restore_best_weights=True)]
        model.fit(
            X_train,
            y_train,
            epochs=25,
            batch_size=32,
            validation_split=0.1,
            callbacks=callbacks,
            verbose=0,
        )

        y_pred_prob = model.predict(X_test, verbose=0).flatten()
        y_pred_class = (y_pred_prob > 0.5).astype(int)
        fold_results.append({
            "fold": fold_idx,
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "directional_accuracy": round(float(accuracy_score(y_test, y_pred_class)), 4),
            "auc": round(safe_auc(y_test, y_pred_prob), 4),
            "baseline_directional_accuracy": round(baseline_bullish_accuracy(y_test), 4),
            "positive_rate": round(float(np.mean(y_test)), 4),
        })

    if not fold_results:
        raise ValueError("No valid folds produced")

    avg_acc = float(np.mean([f["directional_accuracy"] for f in fold_results]))
    avg_auc = float(np.mean([f["auc"] for f in fold_results]))
    avg_baseline = float(np.mean([f["baseline_directional_accuracy"] for f in fold_results]))
    improvement = avg_acc - avg_baseline
    result = {
        "ticker": ticker,
        "model_type": "direction_classification",
        "sequence_len": SEQUENCE_LEN,
        "target_horizon_sessions_min": TARGET_HORIZON_MIN,
        "target_horizon_sessions_max": TARGET_HORIZON_MAX,
        "features": feature_cols,
        "directional_accuracy": round(avg_acc, 4),
        "auc": round(avg_auc, 4),
        "baseline_directional_accuracy": round(avg_baseline, 4),
        "improvement_over_baseline": round(improvement, 4),
        "is_reliable": bool(avg_acc > 0.55 and improvement > 0.03),
        "folds": len(fold_results),
        "fold_results": fold_results,
        "last_validated": datetime.now().strftime("%Y-%m-%d"),
    }
    out_path = os.path.join(MODELS_DIR, f"{ticker}_validation.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Validation saved: {out_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def train_direction_model(symbol):
    symbol = str(symbol).upper()
    print(f"\n{'=' * 40}")
    print(f"Training direction model {symbol}...")

    df, feature_cols = build_features(get_data(symbol, years=6))
    features = df[feature_cols].values
    targets = df["target"].values.astype(int)

    split = int(len(df) * 0.8)
    train_features = features[:split]
    train_targets = targets[:split]
    test_features = features[split - SEQUENCE_LEN:]
    test_targets = targets[split - SEQUENCE_LEN:]

    scaler = MinMaxScaler()
    train_scaled = scaler.fit_transform(train_features)
    test_scaled = scaler.transform(test_features)

    X_train, y_train = make_sequences(train_scaled, train_targets)
    X_test, y_test = make_sequences(test_scaled, test_targets)
    if len(X_train) == 0 or len(X_test) == 0:
        raise ValueError(f"Not enough sequence data for {symbol}")

    print(f"Train: {len(X_train)} samples | Test: {len(X_test)} samples")
    tf.keras.backend.clear_session()
    model = build_model((SEQUENCE_LEN, len(feature_cols)))
    model_path = os.path.join(MODELS_DIR, f"{symbol}_direction_model.h5")
    scaler_path = os.path.join(MODELS_DIR, f"{symbol}_direction_scaler.pkl")

    callbacks = [
        EarlyStopping(patience=5, restore_best_weights=True),
        ModelCheckpoint(model_path, save_best_only=True),
    ]
    model.fit(
        X_train,
        y_train,
        epochs=20,
        batch_size=32,
        validation_split=0.1,
        callbacks=callbacks,
        verbose=1,
    )

    y_prob = model.predict(X_test, verbose=0).flatten()
    y_pred = (y_prob > 0.5).astype(int)
    acc = float(accuracy_score(y_test, y_pred))
    auc = safe_auc(y_test, y_prob)
    baseline = baseline_bullish_accuracy(y_test)

    joblib.dump({
        "scaler": scaler,
        "feature_cols": feature_cols,
        "sequence_len": SEQUENCE_LEN,
        "target_horizon_sessions_min": TARGET_HORIZON_MIN,
        "target_horizon_sessions_max": TARGET_HORIZON_MAX,
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }, scaler_path)

    metrics = {
        "directional_accuracy": round(acc, 4),
        "auc": round(auc, 4),
        "baseline_directional_accuracy": round(baseline, 4),
        "improvement_over_baseline": round(acc - baseline, 4),
    }
    print(f"{symbol} direction accuracy={acc:.2%} | AUC={auc:.3f} | baseline={baseline:.2%}")
    return metrics


def train_symbol(symbol):
    return train_direction_model(symbol)


def lstm_predict(ticker, current_data=None):
    ticker = str(ticker).upper()
    model_path = os.path.join(MODELS_DIR, f"{ticker}_direction_model.h5")
    scaler_path = os.path.join(MODELS_DIR, f"{ticker}_direction_scaler.pkl")
    validation_path = os.path.join(MODELS_DIR, f"{ticker}_validation.json")

    if os.path.exists(validation_path):
        with open(validation_path, "r", encoding="utf-8") as f:
            val = json.load(f)
        if not val.get("is_reliable", False):
            return {
                "reliable": False,
                "direction": None,
                "probability": None,
                "message": f"Model chưa đạt ngưỡng tin cậy (acc={val.get('directional_accuracy', 0):.1%})",
            }

    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        return {
            "reliable": False,
            "direction": None,
            "probability": None,
            "message": "Chưa có direction model/scaler",
        }

    if current_data is None:
        current_data = get_data(ticker, years=1)

    payload = joblib.load(scaler_path)
    scaler = payload["scaler"]
    feature_cols = payload["feature_cols"]
    seq_len = int(payload.get("sequence_len", SEQUENCE_LEN))

    df, _ = build_features(current_data)
    if len(df) < seq_len:
        return {
            "reliable": False,
            "direction": None,
            "probability": None,
            "message": "Không đủ dữ liệu để predict direction",
        }

    X = scaler.transform(df[feature_cols].tail(seq_len))
    X = X.reshape(1, seq_len, len(feature_cols))
    model = load_model(model_path)
    prob = float(model.predict(X, verbose=0)[0][0])
    direction = 1 if prob > 0.5 else -1
    return {
        "reliable": True,
        "direction": direction,
        "probability": round(prob, 3),
        "signal": "tăng" if direction == 1 else "giảm",
        "confidence": round(abs(prob - 0.5) * 200, 1),
    }


if __name__ == "__main__":
    print("Starting direction LSTM training...")
    watchlist = load_symbols_from_args()
    results = {}
    completed = []
    failed = {}
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state({
        "status": "running",
        "symbols": watchlist,
        "completed": completed,
        "failed": failed,
        "current": "",
        "started_at": started_at,
        "finished_at": "",
        "message": "Direction LSTM training started",
    })

    for symbol in watchlist:
        save_state({
            "status": "running",
            "symbols": watchlist,
            "completed": completed,
            "failed": failed,
            "current": symbol,
            "started_at": started_at,
            "finished_at": "",
            "message": f"Training direction model {symbol}",
        })
        try:
            metrics = train_direction_model(symbol)
            results[symbol] = metrics
            completed.append(symbol)
        except Exception as e:
            print(f"Error {symbol}: {e}")
            failed[symbol] = str(e)
        save_state({
            "status": "running",
            "symbols": watchlist,
            "completed": completed,
            "failed": failed,
            "current": "",
            "started_at": started_at,
            "finished_at": "",
            "message": f"Completed {len(completed)}/{len(watchlist)}",
        })

    save_state({
        "status": "completed",
        "symbols": watchlist,
        "completed": completed,
        "failed": failed,
        "current": "",
        "started_at": started_at,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": f"Completed {len(completed)}/{len(watchlist)}",
        "results": results,
    })
