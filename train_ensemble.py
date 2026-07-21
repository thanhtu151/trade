"""
Ensemble direction models: XGBoost + LightGBM + Random Forest + optional LSTM.

Models are saved under lstm_models/:
- <TICKER>_xgb.pkl
- <TICKER>_lgbm.pkl
- <TICKER>_rf.pkl
"""

import json
import os
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "lstm_models")
os.makedirs(MODEL_DIR, exist_ok=True)
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


def build_features(df):
    """
    Build tabular features for 5-6 session direction classification.
    Input df must include time, open, high, low, close, volume.
    """
    df = df.copy().sort_values("time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
        df[col] = df[col].astype(float)

    df["return_1d"] = df["close"].pct_change()
    df["return_3d"] = df["close"].pct_change(3)
    df["return_5d"] = df["close"].pct_change(5)
    df["return_10d"] = df["close"].pct_change(10)
    df["return_20d"] = df["close"].pct_change(20)

    df["high_low_range"] = (df["high"] - df["low"]) / df["close"]
    df["gap_open"] = (df["open"] - df["close"].shift(1)) / df["close"].shift(1)
    df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
    df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]
    df["body_size"] = abs(df["close"] - df["open"]) / df["close"]

    df["sma5"] = df["close"].rolling(5).mean()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma5_ratio"] = df["close"] / df["sma5"] - 1
    df["sma20_ratio"] = df["close"] / df["sma20"] - 1
    df["sma50_ratio"] = df["close"] / df["sma50"] - 1
    df["sma5_20_cross"] = df["sma5"] / df["sma20"] - 1

    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma20"]
    df["volume_ratio_5"] = df["volume"] / df["volume"].rolling(5).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df["rsi"] = (100 - 100 / (1 + rs)) / 100
    df["rsi_3d_change"] = df["rsi"].diff(3)

    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_position"] = (df["close"] - (bb_mid - 2 * bb_std)) / (4 * bb_std + 1e-9)
    df["bb_position"] = df["bb_position"].clip(0, 1)
    df["bb_width"] = (4 * bb_std) / bb_mid

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd_norm"] = (ema12 - ema26) / df["close"]
    df["macd_signal_norm"] = df["macd_norm"].ewm(span=9).mean()
    df["macd_hist"] = df["macd_norm"] - df["macd_signal_norm"]

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr_norm"] = tr.rolling(14).mean() / df["close"]

    df["momentum_5"] = df["close"] / df["close"].shift(5) - 1
    df["momentum_10"] = df["close"] / df["close"].shift(10) - 1
    df["target"] = _build_horizon_target(df["close"])

    feature_cols = [
        "return_1d",
        "return_3d",
        "return_5d",
        "return_10d",
        "return_20d",
        "high_low_range",
        "gap_open",
        "upper_shadow",
        "lower_shadow",
        "body_size",
        "sma5_ratio",
        "sma20_ratio",
        "sma50_ratio",
        "sma5_20_cross",
        "volume_ratio",
        "volume_ratio_5",
        "rsi",
        "rsi_3d_change",
        "bb_position",
        "bb_width",
        "macd_norm",
        "macd_signal_norm",
        "macd_hist",
        "atr_norm",
        "momentum_5",
        "momentum_10",
    ]

    df = df.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    return df, feature_cols


def build_features_extended(df, ticker=None, use_external=True):
    """
    Build features with external data: foreign trading, USD/VND, VIX, and RS vs VNIndex.
    Falls back to base OHLCV features if external sources are unavailable.
    """
    df, feature_cols = build_features(df)
    if not use_external:
        return df, feature_cols

    try:
        from data_fetcher import fetch_foreign_trading, fetch_usdvnd, fetch_vix, fetch_vnindex
    except Exception as exc:
        print(f"  External feature imports failed: {exc}")
        return df, feature_cols

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"]).dt.date.astype(str)

    def add_feature_cols(cols):
        for col in cols:
            if col in df.columns and col not in feature_cols:
                feature_cols.append(col)

    try:
        usdvnd_df = fetch_usdvnd(years=6)
        if usdvnd_df is not None and not usdvnd_df.empty:
            usdvnd_df = usdvnd_df.copy()
            usdvnd_df["time"] = pd.to_datetime(usdvnd_df["time"]).dt.date.astype(str)
            df = df.merge(
                usdvnd_df[["time", "usdvnd_change", "usdvnd_deviation"]],
                on="time",
                how="left",
            )
            df["usdvnd_change"] = pd.to_numeric(df["usdvnd_change"], errors="coerce").fillna(0)
            df["usdvnd_deviation"] = pd.to_numeric(df["usdvnd_deviation"], errors="coerce").fillna(0)
            add_feature_cols(["usdvnd_change", "usdvnd_deviation"])
    except Exception as exc:
        print(f"  USD/VND merge failed: {exc}")

    try:
        vix_df = fetch_vix(years=6)
        if vix_df is not None and not vix_df.empty:
            vix_df = vix_df.copy()
            vix_df["time"] = pd.to_datetime(vix_df["time"]).dt.date.astype(str)
            df = df.merge(vix_df[["time", "vix_change", "vix_regime"]], on="time", how="left")
            df["vix_change"] = pd.to_numeric(df["vix_change"], errors="coerce").fillna(0)
            df["vix_regime"] = pd.to_numeric(df["vix_regime"], errors="coerce").fillna(0)
            add_feature_cols(["vix_change", "vix_regime"])
    except Exception as exc:
        print(f"  VIX merge failed: {exc}")

    try:
        vni_df = fetch_vnindex(years=6)
        if vni_df is not None and not vni_df.empty:
            vni_df = vni_df.copy()
            vni_df["time"] = pd.to_datetime(vni_df["time"]).dt.date.astype(str)
            df = df.merge(vni_df[["time", "vni_return", "vni_trend"]], on="time", how="left")
            df["vni_return"] = pd.to_numeric(df["vni_return"], errors="coerce").fillna(0)
            df["vni_trend"] = pd.to_numeric(df["vni_trend"], errors="coerce").fillna(0)
            df["rs_vs_vni"] = df["return_1d"] - df["vni_return"]
            add_feature_cols(["rs_vs_vni", "vni_trend"])
    except Exception as exc:
        print(f"  VNIndex merge failed: {exc}")

    if ticker:
        try:
            foreign_df = fetch_foreign_trading(ticker, years=6)
            if foreign_df is not None and not foreign_df.empty:
                foreign_df = foreign_df.copy()
                foreign_df["time"] = pd.to_datetime(foreign_df["time"]).dt.date.astype(str)
                df = df.merge(
                    foreign_df[["time", "foreign_net_norm", "foreign_buying", "foreign_net_ma5"]],
                    on="time",
                    how="left",
                )
                df["foreign_net_norm"] = pd.to_numeric(df["foreign_net_norm"], errors="coerce").fillna(0)
                df["foreign_buying"] = pd.to_numeric(df["foreign_buying"], errors="coerce").fillna(0)
                df["foreign_net_ma5"] = pd.to_numeric(df["foreign_net_ma5"], errors="coerce").fillna(0)
                add_feature_cols(["foreign_net_norm", "foreign_buying", "foreign_net_ma5"])
        except Exception as exc:
            print(f"  Foreign trading merge failed: {exc}")

    for col in feature_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
    df = df.dropna(subset=["target"]).reset_index(drop=True)
    return df, feature_cols


def _ensure_training_data(ticker, df=None):
    if df is not None:
        return df.copy()
    from train_lstm import get_data

    return get_data(str(ticker).upper(), years=6)


def _safe_auc(y_true, y_prob):
    try:
        if len(set(np.asarray(y_true).astype(int))) < 2:
            return 0.5
        return float(roc_auc_score(y_true, y_prob))
    except ValueError:
        return 0.5


def _fit_frame(ticker, df=None):
    ticker = str(ticker).upper()
    print(f"  Building extended features for {ticker}...")
    df, feature_cols = build_features_extended(_ensure_training_data(ticker, df), ticker=ticker)
    if len(df) < 100:
        raise ValueError(f"Not enough feature rows for {ticker}: {len(df)}")
    X = df[feature_cols].values
    y = df["target"].values.astype(int)
    return ticker, feature_cols, X, y


def train_xgboost(ticker, df=None):
    ticker, feature_cols, X, y = _fit_frame(ticker, df)
    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        eval_metric="logloss",
        verbosity=0,
    )
    model.fit(X, y)

    path = os.path.join(MODEL_DIR, f"{ticker}_xgb.pkl")
    joblib.dump({"model": model, "feature_cols": feature_cols}, path)
    print(f"XGBoost saved: {path} ({len(feature_cols)} features)")
    return model, feature_cols


def train_lightgbm(ticker, df=None):
    ticker, feature_cols, X, y = _fit_frame(ticker, df)
    model = LGBMClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=-1,
    )
    model.fit(X, y)

    path = os.path.join(MODEL_DIR, f"{ticker}_lgbm.pkl")
    joblib.dump({"model": model, "feature_cols": feature_cols}, path)
    print(f"LightGBM saved: {path} ({len(feature_cols)} features)")
    return model, feature_cols


def train_random_forest(ticker, df=None):
    ticker, feature_cols, X, y = _fit_frame(ticker, df)
    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)

    path = os.path.join(MODEL_DIR, f"{ticker}_rf.pkl")
    joblib.dump({"model": model, "feature_cols": feature_cols}, path)
    print(f"Random Forest saved: {path} ({len(feature_cols)} features)")
    return model, feature_cols


def walk_forward_validate_ensemble(ticker, n_splits=5):
    ticker = str(ticker).upper()
    print(f"Validating extended ensemble for {ticker}...")
    df, feature_cols = build_features_extended(_ensure_training_data(ticker, None), ticker=ticker)
    print(f"  Features: {len(feature_cols)} ({', '.join(feature_cols[:5])}...)")
    if len(df) < n_splits + 100:
        raise ValueError(f"Not enough data for walk-forward validation: {len(df)} rows")

    X = df[feature_cols].values
    y = df["target"].values.astype(int)
    tscv = TimeSeriesSplit(n_splits=n_splits)

    xgb_results = []
    lgbm_results = []
    ensemble_results = []

    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X), 1):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        baseline_pred = np.ones(len(y_test), dtype=int) * int(y_train.mean() > 0.5)
        baseline_acc = accuracy_score(y_test, baseline_pred)

        xgb = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            eval_metric="logloss",
            verbosity=0,
        )
        xgb.fit(X_train, y_train)
        xgb_prob = xgb.predict_proba(X_test)[:, 1]
        xgb_acc = accuracy_score(y_test, (xgb_prob > 0.5).astype(int))
        xgb_auc = _safe_auc(y_test, xgb_prob)

        lgbm = LGBMClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            verbosity=-1,
        )
        lgbm.fit(X_train, y_train)
        lgbm_prob = lgbm.predict_proba(X_test)[:, 1]
        lgbm_acc = accuracy_score(y_test, (lgbm_prob > 0.5).astype(int))
        lgbm_auc = _safe_auc(y_test, lgbm_prob)

        rf = RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=10,
            random_state=42,
            n_jobs=-1,
        )
        rf.fit(X_train, y_train)
        rf_prob = rf.predict_proba(X_test)[:, 1]
        rf_acc = accuracy_score(y_test, (rf_prob > 0.5).astype(int))
        rf_auc = _safe_auc(y_test, rf_prob)

        ensemble_prob = (xgb_prob + lgbm_prob + rf_prob) / 3
        ensemble_pred = (ensemble_prob > 0.5).astype(int)
        ensemble_acc = accuracy_score(y_test, ensemble_pred)
        high_conf_mask = (ensemble_prob > 0.60) | (ensemble_prob < 0.40)
        if int(high_conf_mask.sum()) > 10:
            filtered_acc = accuracy_score(y_test[high_conf_mask], ensemble_pred[high_conf_mask])
            filtered_rate = float(high_conf_mask.mean())
        else:
            filtered_acc = 0.0
            filtered_rate = 0.0

        xgb_results.append({"fold": fold_idx, "accuracy": xgb_acc, "auc": xgb_auc, "baseline": baseline_acc})
        lgbm_results.append({"fold": fold_idx, "accuracy": lgbm_acc, "auc": lgbm_auc, "baseline": baseline_acc})
        ensemble_results.append(
            {
                "fold": fold_idx,
                "accuracy": ensemble_acc,
                "filtered_accuracy": filtered_acc,
                "filtered_rate": filtered_rate,
                "rf_acc": rf_acc,
                "rf_auc": rf_auc,
                "baseline": baseline_acc,
            }
        )

        print(
            f"  Fold {fold_idx}: XGB={xgb_acc:.1%} LGBM={lgbm_acc:.1%} "
            f"RF={rf_acc:.1%} Ensemble={ensemble_acc:.1%} | "
            f"HighConf={filtered_acc:.1%} ({filtered_rate:.0%} ngày có signal) | "
            f"Base={baseline_acc:.1%}"
        )

    avg_xgb = float(np.mean([r["accuracy"] for r in xgb_results]))
    avg_lgbm = float(np.mean([r["accuracy"] for r in lgbm_results]))
    avg_rf = float(np.mean([r["rf_acc"] for r in ensemble_results]))
    avg_ensemble = float(np.mean([r["accuracy"] for r in ensemble_results]))
    avg_filtered = float(np.mean([r["filtered_accuracy"] for r in ensemble_results]))
    signal_rate = float(np.mean([r["filtered_rate"] for r in ensemble_results]))
    avg_baseline = float(np.mean([r["baseline"] for r in xgb_results]))

    result = {
        "ticker": ticker,
        "model_type": "xgb_lgbm_rf_ensemble",
        "target_horizon_sessions_min": TARGET_HORIZON_MIN,
        "target_horizon_sessions_max": TARGET_HORIZON_MAX,
        "features": feature_cols,
        "xgb_accuracy": round(avg_xgb, 4),
        "lgbm_accuracy": round(avg_lgbm, 4),
        "rf_accuracy": round(avg_rf, 4),
        "ensemble_accuracy": round(avg_ensemble, 4),
        "filtered_accuracy": round(avg_filtered, 4),
        "signal_rate": round(signal_rate, 4),
        "baseline_accuracy": round(avg_baseline, 4),
        "improvement": round(avg_ensemble - avg_baseline, 4),
        "is_reliable": bool(avg_filtered > 0.57 and signal_rate > 0.20),
        "folds": n_splits,
        "last_validated": datetime.now().strftime("%Y-%m-%d"),
        "xgb_results": xgb_results,
        "lgbm_results": lgbm_results,
        "ensemble_results": ensemble_results,
    }

    val_path = os.path.join(MODEL_DIR, f"{ticker}_ensemble_validation.json")
    with open(val_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nEnsemble validation saved: {val_path}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def _predict_pickle_model(path, feature_frame):
    data = joblib.load(path)
    feature_model = data["model"]
    model_feature_cols = data.get("feature_cols") or []
    if not model_feature_cols:
        raise ValueError("Missing feature_cols in model pickle")
    missing = [col for col in model_feature_cols if col not in feature_frame.columns]
    for col in missing:
        feature_frame[col] = 0.0
    X_latest = feature_frame[model_feature_cols].tail(1)
    if X_latest.empty:
        raise ValueError("No latest feature row")
    if not np.isfinite(X_latest.values).all():
        raise ValueError("NaN/Inf in features")
    if feature_model.__class__.__name__ == "RandomForestClassifier":
        X_latest = X_latest.values
    prob = float(feature_model.predict_proba(X_latest)[0][1])
    return prob


def ensemble_predict(ticker, current_df):
    ticker = str(ticker).upper()
    try:
        df, feature_cols = build_features_extended(current_df, ticker=ticker)
    except Exception:
        df, feature_cols = build_features(current_df)
    if df is None or len(df) == 0:
        return {
            "reliable": False,
            "direction": 0,
            "probability": 0.5,
            "high_confidence": False,
            "votes": {},
            "message": "No feature rows",
            "sentiment_score": 0.0,
        }

    votes = {}
    probs = []

    model_paths = {
        "xgb": os.path.join(MODEL_DIR, f"{ticker}_xgb.pkl"),
        "lgbm": os.path.join(MODEL_DIR, f"{ticker}_lgbm.pkl"),
        "rf": os.path.join(MODEL_DIR, f"{ticker}_rf.pkl"),
    }
    for name, path in model_paths.items():
        if not os.path.exists(path):
            continue
        try:
            data = joblib.load(path)
            model = data.get("model")
            model_feature_cols = data.get("feature_cols") or []
            if model is None or not model_feature_cols:
                votes[f"{name}_error"] = "missing model/feature_cols"
                continue
            missing = [col for col in model_feature_cols if col not in df.columns]
            if missing:
                print(f"  {ticker} {name}: missing features {missing[:3]}..., filling with 0")
                for col in missing:
                    df[col] = 0.0
            X_latest = df[model_feature_cols].tail(1)
            if X_latest.empty:
                votes[f"{name}_error"] = "empty feature row"
                continue
            if not np.isfinite(X_latest.values).all():
                votes[f"{name}_error"] = "NaN/Inf features"
                continue
            if model.__class__.__name__ == "RandomForestClassifier":
                X_latest = X_latest.values
            prob = float(model.predict_proba(X_latest)[0][1])
            votes[name] = 1 if prob > 0.5 else -1
            probs.append(prob)
        except Exception as exc:
            votes[f"{name}_error"] = str(exc)[:80]

    try:
        from train_lstm import lstm_predict

        lstm_result = lstm_predict(ticker, current_df)
        if lstm_result.get("reliable"):
            lstm_prob = float(lstm_result.get("probability", 0.5))
            votes["lstm"] = 1 if int(lstm_result.get("direction", 0)) > 0 else -1
            probs.append(lstm_prob)
    except Exception:
        pass

    numeric_votes = {k: v for k, v in votes.items() if isinstance(v, int)}
    if not probs:
        return {
            "reliable": False,
            "direction": 0,
            "probability": 0.5,
            "high_confidence": False,
            "votes": numeric_votes,
            "message": "No ensemble models available",
            "sentiment_score": 0.0,
        }

    avg_prob = float(np.mean(probs))
    high_conf = 0.60
    low_conf = 0.40
    if avg_prob > high_conf:
        direction = 1
        signal = "tăng mạnh"
    elif avg_prob < low_conf:
        direction = -1
        signal = "giảm mạnh"
    else:
        direction = 0
        signal = "không rõ"
    confidence = round(abs(avg_prob - 0.5) * 200, 1)
    bullish_votes = sum(1 for v in numeric_votes.values() if v == 1)
    bearish_votes = sum(1 for v in numeric_votes.values() if v == -1)

    return {
        "reliable": True,
        "direction": direction,
        "probability": round(avg_prob, 3),
        "confidence": confidence,
        "signal": signal,
        "high_confidence": bool(avg_prob > high_conf or avg_prob < low_conf),
        "votes": numeric_votes,
        "bullish_votes": bullish_votes,
        "bearish_votes": bearish_votes,
        "total_models": len(numeric_votes),
        "consensus": bool(numeric_votes) and (bullish_votes == len(numeric_votes) or bearish_votes == len(numeric_votes)),
        "sentiment_score": 0.0,
    }


def train_all(ticker, df=None):
    from train_lstm import get_data
    import time

    ticker = str(ticker).upper()
    start = time.time()
    if df is None:
        df = get_data(ticker, years=6)

    print(f"Training XGBoost + LightGBM + Random Forest for {ticker}...")
    for suffix in ["_xgb.pkl", "_lgbm.pkl", "_rf.pkl"]:
        path = os.path.join(MODEL_DIR, f"{ticker}{suffix}")
        if os.path.exists(path):
            os.remove(path)
    train_xgboost(ticker, df)
    train_lightgbm(ticker, df)
    train_random_forest(ticker, df)

    elapsed = time.time() - start
    print(f"Done in {elapsed:.1f}s")
    return elapsed
