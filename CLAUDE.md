# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Vietnamese stock market (VN Stock) algorithmic trading platform** built with Python. It combines a Streamlit dashboard, LLM-powered analysis, ML ensemble models, paper trading, backtesting, and autonomous scheduling — all targeting HoSE/HNX-listed tickers.

Python interpreter: `C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe`

## Running the System

**Dashboard (main UI):**
```powershell
# Launch headlessly (preferred, idempotent — won't double-start)
.\start_dashboard_vn.ps1
# Or directly:
python -m streamlit run dashboard_vn.py --server.port 8501
```

**Autonomous scheduler (background worker):**
```bat
start_scheduler.bat
# Or directly:
python scheduler.py          # full mode
python scheduler.py prep     # morning prep only
python scheduler.py analysis # market analysis only
```

**Daily health check:**
```bat
cd E:\Trade && python health_check.py
```

**Paper trader (standalone Streamlit app, separate port):**
```bat
start_auto_trader.bat
```

## Tests

```bat
# All tests (unit + integration + performance + visual)
run_tests.bat

# Specific test classes
python -m pytest tests/test_dashboard.py::TestDataFiles -v
python -m pytest tests/test_dashboard.py::TestCoreLogic -v
python -m pytest tests/test_dashboard.py::TestRegression -v
python -m pytest tests/test_dashboard.py::TestPerformance -v

# Visual tests require the dashboard running on port 8501
python -m pytest tests/test_dashboard.py::TestVisual -v --headed
```

Required env vars for tests: `PYTHONUTF8=1`, `TF_CPP_MIN_LOG_LEVEL=3`, `CUDA_VISIBLE_DEVICES=` (set in `tests/conftest.py`).

## ML Model Training

```bat
# Train ensemble (XGBoost + LightGBM + RandomForest) for specific tickers
python train_ensemble.py

# Train LSTM models
python train_lstm.py VCB,MBB,ACB
# (No args → uses WATCHLIST = ["VNM", "VIC", "HPG", "VHM", "MWG"])
```

Trained models are saved under `lstm_models/` as `<TICKER>_xgb.pkl`, `<TICKER>_lgbm.pkl`, `<TICKER>_rf.pkl`, and `<TICKER>_lstm.h5`.

## Architecture

### Core Data Flow

```
[VN Market / Yahoo Finance / Macrodata]
          ↓ data_fetcher.py (cache: data_cache/)
          ↓
[dashboard_vn.py] ←→ [auto_trader.py] ←→ [paper_portfolio.json]
          ↓                  ↓
    [debate_agents.py]   [scheduler.py]  ← drives all automation
          ↓
    [llm_router.py]      [train_ensemble.py / train_lstm.py]
          ↓                       ↓ saves to lstm_models/
    [learning_engine.py] ←────────┘
          ↓
    [reflection_manager.py]  (prediction_history.json)
          ↓
    [backtester.py / backtester_pro.py] → backtest_results/
```

### Key Modules

| File | Role |
|---|---|
| `dashboard_vn.py` | Main Streamlit UI (~250KB). Lazy-loads `auto_trader` via `_LazyModuleProxy` to avoid circular imports. Uses `@st.cache_data` / `@st.cache_resource` extensively. |
| `auto_trader.py` | Paper trading engine with file-locking (`msvcrt` on Windows, `fcntl` on POSIX). Manages `paper_portfolio.json`, `paper_trades.json`, and `paper_ai_fund_config.json`. Hard limits: 20% max position size, 5% stop-loss, 15% take-profit. |
| `llm_router.py` | Multi-provider LLM fallback chain: Groq → Cerebras → Cloudflare Workers AI → Gateway keys → Gemini direct → DeepSeek → Ollama. Caches working keys to `llm_key_cache.json` (30 min TTL). Exposes `call_llm()` and `call_llm_json()`. If no `.env` keys are set, auto-fetches free keys from a public GitHub repo. |
| `debate_agents.py` | Bull vs Bear debate framework: two LLM agents argue opposing cases, then a judge synthesizes a final BUY/HOLD/SELL with confidence score. Inspired by TradingAgents. |
| `scheduler.py` | Autonomous daily scheduler using the `schedule` library. Tasks: `morning_prep` (08:00, clears LLM cache, refreshes macro data, trains missing models), `market_analysis` (09:15, runs full debate + signal generation), `auto_trade` (09:30, executes paper trades). Skips weekends and Vietnamese public holidays. State persisted in `scheduler_state.json`. |
| `data_fetcher.py` | Caching wrapper around `vnstock`, `yfinance`, and macro endpoints (USD/VND, VIX, VNIndex). Cache TTL is adaptive: 4h during market hours (08–16), 12h otherwise. |
| `learning_engine.py` | Tracks predictions vs actual outcomes in `prediction_log.json` and `prediction_history.json`. Computes accuracy, generates feedback context for LLM prompts. |
| `reflection_manager.py` | Reads `prediction_history.json` to build compact recent-performance summaries injected into LLM prompts so the AI "remembers" its past decisions. |
| `train_ensemble.py` | Builds tabular features from OHLCV data (returns, RSI, MACD, Bollinger Bands, ATR, volume ratios) and trains a 3-model ensemble for next-3-day direction classification with `TimeSeriesSplit` CV. |
| `train_lstm.py` | Sequence-based LSTM for price direction using a sliding window of 20 days. Uses `MinMaxScaler` per ticker. |
| `backtester.py` | Rule-based signal replay (no LLM) measuring win rate, EV/trade, Sharpe, and max drawdown. Config driven by `backtest_config.json` (stores `positive_ev_tickers` — tickers where backtesting found positive expected value). |
| `backtester_pro.py` | Extended backtester with walk-forward optimization over ATR stop/target multipliers. |
| `vnstock_fetch_worker.py` | Thin subprocess wrapper around `vnstock` API calls — spawned by `auto_trader.py` to isolate rate-limited fetches with a 30s timeout and 1.05s minimum inter-request delay. |

### State Files (runtime, gitignored)

- `paper_portfolio.json` — current holdings and cash; protected by `paper_portfolio.json.lock`
- `paper_trades.json` — trade history
- `analysis_results.json` — latest market analysis output
- `prediction_history.json` — full LLM prediction log with outcomes
- `scheduler_state.json` — per-task last-run dates (deduplication guard)
- `llm_key_cache.json` — working LLM keys, 30-min TTL
- `debate_log.json` — last 100 debate sessions
- `learning_memory.json` — aggregated accuracy stats per ticker

### Environment Variables (`.env`)

| Variable | Purpose |
|---|---|
| `GROQ_KEY` | Groq API key (fastest provider) |
| `CEREBRAS_KEY` | Cerebras API key |
| `CLOUDFLARE_KEY` / `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Workers AI |
| `GATEWAY_KEYS` | Comma-separated OpenAI-compatible gateway keys |
| `GATEWAY_URL` | Gateway base URL (default: OpenRouter) |
| `GATEWAY_MODELS` | Comma-separated model names to try on the gateway |
| `GEMINI_KEY1..3` | Direct Gemini keys |
| `DEEPSEEK_KEY` | DeepSeek direct key |
| `OLLAMA_MODEL` | Local Ollama model name (default: `qwen2.5:7b`) |
| `OLLAMA_URL` | Local Ollama endpoint |

### Default Watchlist (20 tickers)

`VCB BID CTG TCB MBB ACB VPB STB HDB VIB SSI VND HCM VCI HPG VIC VHM FPT MWG VNM`

### Important Constraints

- VNstock API has rate limits — `auto_trader.py` enforces a 1.05s minimum delay between requests and uses a subprocess worker with a 30s timeout to avoid hanging the UI.
- `paper_portfolio.json` uses Windows `msvcrt.locking` for file-level locking (cross-process safe on Windows).
- The LSTM/TensorFlow import is suppressed at startup in `dashboard_vn.py` to keep load time fast; TF env vars must be set before import.
- `backtest_config.json` drives which tickers get ensemble models trained in morning prep — only tickers with positive historical EV are included.
