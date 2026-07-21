import streamlit as st
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["CUDA_VISIBLE_DEVICES"] = ""

st.set_page_config(page_title="VN Stock Dashboard", layout="wide", page_icon="VN")

# --- Cloud bootstrap: bring Streamlit secrets into env (for llm_router) and pull
# runtime state from the `state` branch. No-op locally. Must run before the
# llm_router import below so LLM keys are visible. ---
try:
    import cloud_bootstrap

    cloud_bootstrap.bridge_secrets()
    cloud_bootstrap.sync_state()
except Exception:
    pass

import plotly.graph_objects as go
import requests
import time
import feedparser
import json
import html
import io
import re
import subprocess
import sys
import contextlib
import numpy as np
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
from reflection_manager import ReflectionManager
from llm_router import call_llm, call_llm_json, get_router_status
import source_manager


class _LazyModuleProxy:
    def __init__(self, module_name):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = __import__(self._module_name, fromlist=["*"])
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)


paper_trader = _LazyModuleProxy("auto_trader")


@st.cache_resource(show_spinner=False)
def get_vnstock_client(symbol, source="VCI"):
    from vnstock.api.quote import Quote

    return Quote(symbol=symbol, source=source)


@st.cache_data(ttl=300, show_spinner=False)
def get_vnstock_status_cached():
    from vnstock import check_status

    return check_status()


@st.cache_resource(show_spinner=False)
def get_finance_client(symbol, source="VCI"):
    from vnstock.api.financial import Finance

    return Finance(symbol=symbol, source=source)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_stock_history(symbol, start, end, interval="1D"):
    from data_fetcher import fetch_with_fallback
    df, _ = fetch_with_fallback(symbol, start, end, interval)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_market_regime_cached():
    return detect_market_regime()


@st.cache_data(ttl=300, show_spinner=False)
def get_portfolio_summary_cached():
    from auto_trader import _safe_read_portfolio

    p = _safe_read_portfolio()
    cash = float(p.get("cash", 0) or 0)
    positions = p.get("positions", {}) or {}
    total_mv = sum(float(pos.get("market_value", 0) or 0) for pos in positions.values())
    total_pnl = sum(float(pos.get("unrealized_pnl", 0) or 0) for pos in positions.values())
    return {
        "cash": cash,
        "equity": cash + total_mv,
        "pnl": total_pnl,
        "positions": positions,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_backtest_config_cached():
    try:
        with open("backtest_config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_ensemble_predictor():
    try:
        from train_ensemble import ensemble_predict

        return ensemble_predict
    except Exception:
        return None


def get_ensemble_trainer():
    try:
        from train_ensemble import train_all

        return train_all
    except Exception:
        return None


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

YFINANCE_CACHE_DIR = str(BASE_DIR / ".yfinance_cache")
os.makedirs(YFINANCE_CACHE_DIR, exist_ok=True)
yf.set_tz_cache_location(YFINANCE_CACHE_DIR)

# ===================== CUSTOM CSS =====================
st.markdown("""
<style>
    :root {
        --bg: #020617;
        --panel: #0f172a;
        --panel-2: #1e293b;
        --panel-3: #334155;
        --line: rgba(148, 163, 184, 0.18);
        --text: #f8fafc;
        --muted: #94a3b8;
        --muted-2: #64748b;
        --accent: #38bdf8;
        --accent-2: #22c55e;
        --warn: #f59e0b;
        --danger: #f43f5e;
    }

    .stApp {
        background:
            radial-gradient(circle at top left, rgba(56, 189, 248, 0.12), transparent 26rem),
            linear-gradient(180deg, #020617 0%, #050816 45%, #020617 100%);
        color: var(--text);
    }
    .main .block-container {
        max-width: 1480px;
        padding: 1.25rem 1.6rem 2rem;
    }
    body, p, span, div, .stMarkdown, .stText { color: var(--text); }

    .app-header {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        align-items: flex-end;
        padding: 1.1rem 1.2rem;
        margin-bottom: 1rem;
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.92), rgba(14, 165, 233, 0.08));
        border: 1px solid var(--line);
        border-radius: 8px;
        box-shadow: 0 18px 45px rgba(0, 0, 0, 0.22);
    }
    .app-kicker {
        color: var(--accent);
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .app-title {
        color: #f8fafc;
        font-size: 1.9rem;
        font-weight: 900;
        line-height: 1.15;
        margin-top: 0.25rem;
    }
    .app-subtitle {
        color: var(--muted);
        font-size: 0.86rem;
        margin-top: 0.35rem;
    }
    .header-pill {
        background: rgba(15, 23, 42, 0.75);
        border: 1px solid var(--line);
        border-radius: 999px;
        color: var(--muted);
        font-size: 0.78rem;
        padding: 0.35rem 0.7rem;
        white-space: nowrap;
    }

    .card {
        background: linear-gradient(180deg, rgba(18, 27, 46, 0.96), rgba(15, 22, 37, 0.96));
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.8rem;
        box-shadow: 0 14px 32px rgba(0, 0, 0, 0.18);
    }
    .card-glow { box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.12), 0 18px 42px rgba(0, 0, 0, 0.24); }

    .metric-up { color: var(--accent-2); font-weight: 700; }
    .metric-down { color: var(--danger); font-weight: 700; }
    .metric-neutral { color: var(--warn); font-weight: 700; }
    .header-grad { color: #f8fafc; font-weight: 900; }
    .sub-header {
        color: var(--muted);
        font-size: 0.78rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    .badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 3.1rem;
        padding: 0.12rem 0.5rem;
        border-radius: 999px;
        font-size: 0.67rem;
        font-weight: 700;
        margin-left: 0.35rem;
    }
    .badge-up { background: rgba(34,197,94,0.13); color: #4ade80; }
    .badge-down { background: rgba(244,63,94,0.14); color: #fb7185; }
    .badge-neutral { background: rgba(245,158,11,0.14); color: #fbbf24; }

    .divider-custom {
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(148,163,184,0.22), transparent);
        margin: 1rem 0;
    }

    .section-title {
        color: #f8fafc;
        font-weight: 800;
        font-size: 0.92rem;
        margin-bottom: 0.55rem;
        letter-spacing: 0.01em;
    }
    .section-caption {
        color: var(--muted-2);
        font-size: 0.74rem;
        margin-top: -0.35rem;
        margin-bottom: 0.6rem;
    }

    .wl-panel {
        background: rgba(15, 23, 42, 0.62);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.35rem;
    }
    .wl-item {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 0.5rem;
        align-items: center;
        padding: 0.55rem 0.6rem;
        border-radius: 6px;
        border: 1px solid transparent;
        transition: background 0.15s ease, border-color 0.15s ease;
    }
    .wl-item + .wl-item { margin-top: 0.18rem; }
    .wl-item:hover {
        background: rgba(56, 189, 248, 0.08);
        border-color: rgba(56, 189, 248, 0.18);
    }
    .wl-symbol { font-weight: 800; font-size: 0.88rem; color: #f8fafc; }
    .wl-price { display: block; font-size: 0.78rem; color: var(--muted); margin-top: 0.12rem; }
    .wl-change { font-size: 0.78rem; font-weight: 800; text-align: right; min-width: 5rem; }
    .wl-meta { display: block; color: var(--muted-2); font-size: 0.68rem; margin-top: 0.16rem; font-weight: 600; }
    .status-dot {
        display: inline-block;
        width: 0.45rem;
        height: 0.45rem;
        border-radius: 999px;
        margin-right: 0.35rem;
        vertical-align: 0.06rem;
    }
    .dot-up { background: var(--accent-2); box-shadow: 0 0 0 3px rgba(34,197,94,0.12); }
    .dot-down { background: var(--danger); box-shadow: 0 0 0 3px rgba(244,63,94,0.12); }
    .dot-neutral { background: var(--warn); box-shadow: 0 0 0 3px rgba(245,158,11,0.12); }

    .stSelectbox label, .stSlider label, .stTextInput label { color: var(--muted) !important; font-weight: 600; }
    .stSelectbox div[data-baseweb="select"] > div,
    textarea, input {
        background: rgba(15, 23, 42, 0.92) !important;
        border-color: rgba(148,163,184,0.20) !important;
        color: var(--text) !important;
        border-radius: 7px !important;
    }
    .stSelectbox div[data-baseweb="select"] span,
    .stSelectbox div[data-baseweb="select"] input {
        color: #f8fafc !important;
        -webkit-text-fill-color: #f8fafc !important;
        font-weight: 800 !important;
    }
    div[data-baseweb="popover"] ul,
    div[data-baseweb="menu"] {
        background: #f8fafc !important;
        border: 1px solid rgba(15, 23, 42, 0.18) !important;
        box-shadow: 0 18px 38px rgba(0,0,0,0.28) !important;
    }
    div[data-baseweb="popover"] li,
    div[role="option"] {
        background: #f8fafc !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        font-weight: 800 !important;
    }
    div[data-baseweb="popover"] li:hover,
    div[role="option"]:hover,
    div[aria-selected="true"][role="option"] {
        background: #dbeafe !important;
        color: #082f49 !important;
        -webkit-text-fill-color: #082f49 !important;
    }
    [data-testid="stMetricValue"] { color: var(--text); font-weight: 800; }
    [data-testid="stMetricDelta"] { font-weight: 700; }
    [data-testid="stMetricLabel"] { color: var(--muted); }

    .stTabs [data-baseweb="tab-list"] {
        gap: 0.35rem;
        border-bottom: 1px solid var(--line);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 7px 7px 0 0;
        padding: 0.55rem 1rem;
        font-size: 0.85rem;
        color: var(--muted);
    }
    .stTabs [aria-selected="true"] {
        background: rgba(15, 23, 42, 0.95) !important;
        border: 1px solid var(--line) !important;
        border-bottom-color: transparent !important;
        color: #f8fafc !important;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #080d17 0%, #0c1220 100%);
        border-right: 1px solid var(--line);
    }
    section[data-testid="stSidebar"] .block-container { padding: 1.2rem 0.8rem 1.5rem; }
    section[data-testid="stSidebar"] .stMarkdown p { color: var(--text); }
    section[data-testid="stSidebar"] label { color: var(--muted) !important; }
    section[data-testid="stSidebar"] .stAlert { background: rgba(15, 23, 42, 0.78); border-radius: 7px; color: var(--text); }
    section[data-testid="stSidebar"] .stAlert p { color: inherit; }

    .metric-box {
        min-height: 6rem;
        background: linear-gradient(180deg, rgba(15, 23, 42, 0.96), rgba(15, 23, 42, 0.88));
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.82rem;
        text-align: left;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }
    .metric-box .label {
        color: var(--muted);
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-weight: 700;
    }
    .metric-box .value {
        font-size: 1.35rem;
        font-weight: 830;
        color: #f8fafc;
        margin: 0.32rem 0 0.2rem;
        line-height: 1.15;
    }
    .metric-box .delta { font-size: 0.8rem; color: var(--muted); }

    .info-box {
        background: linear-gradient(180deg, rgba(15, 23, 42, 0.96), rgba(8, 13, 23, 0.96));
        border: 1px solid rgba(56, 189, 248, 0.20);
        border-radius: 8px;
        padding: 1rem;
        color: var(--text);
    }
    .stButton button {
        border-radius: 7px;
        font-weight: 800;
        border: 1px solid rgba(148,163,184,0.22);
        background: rgba(15, 23, 42, 0.92);
        color: var(--text);
        transition: all 0.15s ease;
    }
    .stButton button:hover {
        border-color: rgba(56, 189, 248, 0.45);
        color: #f8fafc;
        transform: translateY(-1px);
        box-shadow: 0 10px 22px rgba(0,0,0,0.22);
    }
    div[data-testid="stDataFrame"], table {
        border-radius: 8px;
        overflow: hidden;
    }
    div[data-testid="stDataFrame"] {
        background: #0f172a;
        color: #f8fafc;
    }

    /* High-contrast overrides for readability on the dark dashboard. */
    /* Fix #2 – all 9 nav tabs stay on one scrollable row, never wrap */
    div[data-baseweb="button-group"] {
        gap: 0.45rem !important;
        margin: 0.35rem 0 1rem !important;
        border-bottom: 1px solid rgba(226, 232, 240, 0.24);
        padding-bottom: 0.15rem;
        flex-wrap: nowrap !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        scrollbar-width: thin;
        scrollbar-color: rgba(148,163,184,0.22) transparent;
    }
    div[data-baseweb="button-group"]::-webkit-scrollbar { height: 3px; }
    div[data-baseweb="button-group"]::-webkit-scrollbar-thumb { background: rgba(148,163,184,0.28); border-radius: 3px; }
    button[data-testid="stBaseButton-pills"],
    button[data-testid="stBaseButton-pillsActive"] {
        min-height: 2.45rem !important;
        border-radius: 8px 8px 0 0 !important;
        border: 1px solid rgba(148, 163, 184, 0.28) !important;
        background: #101827 !important;
        color: #f8fafc !important;
        font-size: 0.92rem !important;
        font-weight: 900 !important;
        text-shadow: none !important;
        opacity: 1 !important;
    }
    button[data-testid="stBaseButton-pills"] p,
    button[data-testid="stBaseButton-pillsActive"] p,
    button[data-testid="stBaseButton-pills"] span,
    button[data-testid="stBaseButton-pillsActive"] span {
        color: #f8fafc !important;
        -webkit-text-fill-color: #f8fafc !important;
        opacity: 1 !important;
        font-weight: 900 !important;
    }
    button[data-testid="stBaseButton-pillsActive"] {
        background: #182236 !important;
        border-color: rgba(56, 189, 248, 0.65) !important;
        box-shadow: inset 0 -3px 0 #38bdf8 !important;
    }
    button[data-testid="stBaseButton-pills"]:hover {
        background: #1e293b !important;
        border-color: #cbd5e1 !important;
    }
    .section-title,
    section[data-testid="stSidebar"] .section-title {
        color: #ffffff !important;
        font-weight: 900 !important;
    }
    .section-caption,
    .metric-box .label,
    .metric-box .delta,
    .app-subtitle,
    .header-pill,
    .wl-price,
    .wl-meta {
        color: #cbd5e1 !important;
        opacity: 1 !important;
    }
    .metric-box .value,
    .wl-symbol,
    .app-title {
        color: #ffffff !important;
        opacity: 1 !important;
    }
    .ticker-strip {
        display: grid;
        grid-template-columns: repeat(6, minmax(0, 1fr));
        gap: 0.5rem;
        padding: 0.45rem 0.65rem;
        margin: 0 0 0.85rem;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(15, 23, 42, 0.84);
        align-items: center;
    }
    .ticker-strip-item {
        min-width: 0;
        line-height: 1.1;
    }
    .ticker-strip-label {
        font-size: 0.68rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .ticker-strip-value {
        font-size: 0.86rem;
        font-weight: 780;
        color: var(--text);
        margin-top: 0.15rem;
        white-space: nowrap;
    }
    .ticker-strip-value.up { color: var(--accent-2); }
    .ticker-strip-value.down { color: var(--danger); }
    .ticker-strip-value.neutral { color: var(--muted); }
    .cockpit-primary {
        display: grid;
        grid-template-columns: 2fr 1.2fr 1.2fr 1.5fr;
        gap: 0.65rem;
    }
    .cockpit-secondary {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.65rem;
        margin-top: 0.5rem;
    }
    .signal-summary {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.65rem;
        margin: 0.35rem 0 0.9rem;
    }
    .signal-chip {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(15, 23, 42, 0.84);
        padding: 0.65rem 0.75rem;
    }
    .signal-chip .k {
        font-size: 0.68rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .signal-chip .v {
        margin-top: 0.18rem;
        font-size: 0.92rem;
        font-weight: 800;
        color: var(--text);
    }
    .signal-chip .v.up { color: var(--accent-2); }
    .signal-chip .v.down { color: var(--danger); }
    .signal-chip .v.warn { color: var(--warn); }
    .rank-table-wrap{width:100%;overflow-x:auto;border-radius:8px;border:1px solid #334155;}
    .rank-table{width:100%;border-collapse:collapse;font-size:0.86rem;background:#0f172a;color:#f8fafc;}
    .rank-table th{background:#1e293b;color:#94a3b8!important;padding:0.68rem 0.6rem;text-align:left;font-weight:900;border-bottom:1px solid #334155;white-space:nowrap;text-transform:uppercase;letter-spacing:0.04em;font-size:0.72rem;}
    .rank-table td{padding:0.62rem 0.6rem;border-bottom:1px solid #1e293b;color:#e2e8f0!important;font-weight:760;white-space:nowrap;}
    .rank-table tr.rank-normal td{background:#0f172a;color:#e2e8f0!important;}
    .rank-table tr.rank-best td{background:rgba(34,197,94,0.12)!important;color:#ffffff!important;font-weight:900;border-left:3px solid #22c55e;}
    .rank-table tr.rank-worst td{background:rgba(244,63,94,0.12)!important;color:#ffffff!important;font-weight:900;border-left:3px solid #f43f5e;}
    .rank-table tr.rank-normal td.symbol-cell{font-weight:920;color:#f8fafc!important;}
    .rank-table tr.rank-normal td.verdict-buy,.rank-table tr.rank-normal td.score-up{color:#4ade80!important;font-weight:920;}
    .rank-table tr.rank-normal td.verdict-sell,.rank-table tr.rank-normal td.score-down{color:#fb7185!important;font-weight:920;}
    .rank-table tr.rank-normal td.verdict-hold,.rank-table tr.rank-normal td.score-flat{color:#fbbf24!important;font-weight:920;}
    .rank-table tr.rank-best td.verdict-buy,.rank-table tr.rank-best td.verdict-sell,.rank-table tr.rank-best td.verdict-hold,.rank-table tr.rank-best td.score-up,.rank-table tr.rank-best td.score-down,.rank-table tr.rank-best td.score-flat,
    .rank-table tr.rank-worst td.verdict-buy,.rank-table tr.rank-worst td.verdict-sell,.rank-table tr.rank-worst td.verdict-hold,.rank-table tr.rank-worst td.score-up,.rank-table tr.rank-worst td.score-down,.rank-table tr.rank-worst td.score-flat{color:#ffffff!important;font-weight:950;}
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] div {
        color: #e5edf7;
    }
</style>
""", unsafe_allow_html=True)

# ===================== CONFIG =====================
HISTORY_FILE = str(BASE_DIR / "prediction_history.json")
MODELS_DIR = str(BASE_DIR / "lstm_models")
WATCHLIST_FILE = str(BASE_DIR / "watchlist.json")
TRACKED_POSITIONS_FILE = str(BASE_DIR / "tracked_positions.json")
TRAINING_WATCHLIST_FILE = str(BASE_DIR / "training_watchlist.json")
TRAINING_AI_STATE_FILE = str(BASE_DIR / "training_ai_state.json")
LSTM_TRAINING_STATE_FILE = str(BASE_DIR / "lstm_training_state.json")
AUTO_ANALYSIS_STATE_FILE = str(BASE_DIR / "auto_analysis_state.json")
SYSTEM_STATUS_FILE = str(BASE_DIR / "system_status.json")
AUTO_ANALYSIS_HOUR = 9
AUTO_ANALYSIS_MINUTE = 30
OLLAMA_TIMEOUT_SECONDS = 30
REFLECTION = ReflectionManager(HISTORY_FILE)

DEFAULT_TRAINING_WATCHLIST = [
    "VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "STB", "HDB", "VIB",
    "SSI", "VND", "HCM", "VCI", "SHS",
    "HPG", "HSG", "NKG",
    "VIC", "VHM", "VRE", "NVL", "KDH", "DXG",
    "FPT", "CMG", "MWG", "FRT", "DGW",
    "VNM", "MSN", "SAB", "GAS", "PLX", "POW", "PVD", "PVS",
    "GVR", "DGC", "DPM", "DCM", "BMP",
    "GMD", "VSC", "HAH", "VJC", "HVN",
    "REE", "PC1", "KBC"
]

# ===================== WATCHLIST MANAGEMENT =====================
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r') as f:
            return json.load(f)
    return ["VNM", "VIC", "HPG", "VHM", "MWG"]

def save_watchlist(wl):
    with open(WATCHLIST_FILE, 'w') as f:
        json.dump(wl, f)

def load_tracked_positions():
    if os.path.exists(TRACKED_POSITIONS_FILE):
        try:
            with open(TRACKED_POSITIONS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = []
    else:
        raw = []

    if isinstance(raw, dict):
        raw = [
            {"symbol": sym, **(pos or {})}
            for sym, pos in raw.items()
            if sym
        ]

    if not isinstance(raw, list):
        raw = []

    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "qty": int(safe_float(item.get("qty"), 0) or 0),
                "buy_price": safe_float(item.get("buy_price") or item.get("avg_price"), 0),
                "stop_loss": safe_float(item.get("stop_loss"), 0),
                "take_profit": safe_float(item.get("take_profit"), 0),
                "note": str(item.get("note", "") or ""),
                "source": str(item.get("source", "manual") or "manual"),
            }
        )

    return rows


def save_tracked_positions(rows):
    cleaned = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        cleaned.append(
            {
                "symbol": symbol,
                "qty": int(safe_float(item.get("qty"), 0) or 0),
                "buy_price": safe_float(item.get("buy_price"), 0),
                "stop_loss": safe_float(item.get("stop_loss"), 0),
                "take_profit": safe_float(item.get("take_profit"), 0),
                "note": str(item.get("note", "") or ""),
                "source": str(item.get("source", "manual") or "manual"),
            }
        )
    with open(TRACKED_POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)


def normalize_symbol_list(symbols):
    clean = []
    for sym in symbols:
        item = str(sym).strip().upper()
        if item and item not in clean:
            clean.append(item)
    return clean


@st.cache_data(ttl=300, show_spinner=False)
def get_today_tradeable_symbols():
    """
    Return symbols marked tradeable in today's two-stage analysis output.
    The scheduler now tags these as session-horizon candidates.
    Falls back to an empty list when today's analysis has not run yet.
    """
    analysis_path = BASE_DIR / "analysis_results.json"
    if not analysis_path.exists():
        return [], ""

    try:
        with open(analysis_path, encoding="utf-8") as f:
            analysis = json.load(f) or {}
    except Exception:
        return [], ""

    today_key = date.today().isoformat()
    analysis_date = str(analysis.get("date") or analysis.get("today") or "")
    if analysis_date != today_key:
        return [], analysis_date

    tradeable = analysis.get("tradeable_tickers") or analysis.get("eligible_tickers") or []
    if not tradeable:
        tradeable = [row.get("ticker") for row in (analysis.get("tradeable") or []) if row.get("ticker")]
    return normalize_symbol_list(tradeable), analysis_date


def _analysis_horizon_label(analysis, default_sessions=4):
    min_sessions = analysis.get("prediction_horizon_sessions_min")
    max_sessions = analysis.get("prediction_horizon_sessions_max")
    try:
        min_sessions = int(min_sessions) if min_sessions is not None else None
    except Exception:
        min_sessions = None
    try:
        max_sessions = int(max_sessions) if max_sessions is not None else None
    except Exception:
        max_sessions = None

    if min_sessions and max_sessions and max_sessions >= min_sessions:
        if min_sessions == max_sessions:
            return max_sessions, f"{max_sessions} phiên"
        return max_sessions, f"{min_sessions}-{max_sessions} phiên"

    sessions = analysis.get("prediction_horizon_sessions")
    if sessions is None:
        days = analysis.get("prediction_horizon_days")
        if days is not None:
            try:
                sessions = int(days) * 2
            except Exception:
                sessions = None
    try:
        sessions = int(sessions)
    except Exception:
        sessions = default_sessions
    if sessions <= 0:
        sessions = default_sessions
    return sessions, f"{sessions} phiên"


def get_ev_badge(ticker):
    """Return EV badge text and Streamlit message type from backtest config."""
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_config.json")
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        ev_data = config.get("ev_data", {})
        ticker = str(ticker).upper()
        if ticker in ev_data:
            ev = float(ev_data[ticker].get("ev", 0))
            win_rate = float(ev_data[ticker].get("win_rate", 0))
            if ev > 0.5:
                return f"EV +{ev:.2f}%/trade (WR {win_rate:.0%})", "success"
            if ev > 0:
                return f"EV +{ev:.2f}%/trade (WR {win_rate:.0%})", "warning"
            return f"EV {ev:.2f}%/trade (WR {win_rate:.0%})", "error"
        return "Chưa backtest", "info"
    except Exception:
        return "Chưa backtest", "info"


@st.cache_data(ttl=300, show_spinner=False)
def get_stock_data_st(ticker, years=1):
    """Streamlit-cached wrapper for stock data."""
    try:
        from data_fetcher import get_stock_data_cached

        return get_stock_data_cached(ticker, years=years)
    except Exception:
        st.warning("VNStock đang rate limit hoặc chưa trả dữ liệu. Chờ một lát rồi refresh lại.")
        return None


@st.cache_data(ttl=600, show_spinner=False)
def get_vnindex_data_st(years=1):
    """Streamlit-cached wrapper for VNIndex."""
    try:
        from data_fetcher import get_stock_data_cached

        return get_stock_data_cached("VNINDEX", years=years)
    except Exception:
        return None


def _scheduler_is_running():
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline"]):
            cmdline = proc.info.get("cmdline") or []
            if any("scheduler.py" in str(arg) for arg in cmdline):
                return True
    except Exception:
        pass
    return False


def _run_scheduler_task_bg(task_arg):
    """Spawn scheduler.py <task_arg> in a detached background process."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "scheduler.py"), task_arg],
        cwd=str(BASE_DIR),
        creationflags=flags,
    )


def render_scheduler_status():
    scheduler_state_path = BASE_DIR / "scheduler_state.json"
    st.markdown('<div class="section-title">Auto Scheduler</div>', unsafe_allow_html=True)

    # --- process status + start button ---
    running = _scheduler_is_running()
    proc_col, btn_col = st.columns([3, 2])
    with proc_col:
        if running:
            st.caption("🟢 Scheduler đang chạy")
        else:
            st.caption("🔴 Scheduler chưa chạy")
    with btn_col:
        if st.button("▶ Start Scheduler", key="btn_start_scheduler", width='stretch'):
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(
                [sys.executable, str(BASE_DIR / "scheduler.py")],
                cwd=str(BASE_DIR),
                creationflags=flags,
            )
            st.success("Scheduler đã khởi động — chờ 5s rồi tải lại trang.")

    if not scheduler_state_path.exists():
        st.caption("Chưa có state. Nhấn Start Scheduler hoặc Run Now từng task.")
        sched_state = {}
    else:
        try:
            with open(scheduler_state_path, encoding="utf-8") as f:
                sched_state = json.load(f)
        except Exception:
            st.caption("Không đọc được scheduler_state.json")
            return

    today = date.today().isoformat()
    # state_key → (label, run_now_arg)
    tasks = {
        "morning_prep":  ("Morning Prep", "prep"),
        "market_analysis": ("Analysis",    "analysis"),
        "auto_trade":    ("Auto Trade",   "trade"),
        "eod_update":    ("EOD Update",   "eod"),
    }
    for key, (label, arg) in tasks.items():
        ran = sched_state.get(key) == today
        row_left, row_right = st.columns([3, 2])
        with row_left:
            icon = "✅" if ran else "⏳"
            st.caption(f"{icon} {label}")
        with row_right:
            btn_label = "Re-run" if ran else "Run Now"
            if st.button(btn_label, key=f"btn_sched_{key}", width='stretch'):
                # clear today's state so the task won't be skipped
                try:
                    s = json.loads(scheduler_state_path.read_text(encoding="utf-8"))
                    s.pop(key, None)
                    scheduler_state_path.write_text(
                        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except Exception:
                    pass
                _run_scheduler_task_bg(arg)
                st.info(f"{label} đang chạy nền — xem logs/ để theo dõi tiến trình.")


def render_data_cache_status():
    st.markdown('<div class="section-title">Data Cache</div>', unsafe_allow_html=True)
    try:
        from data_fetcher import get_cache_status, invalidate_stock_cache

        cache_items = get_cache_status()
        if cache_items:
            df_cache = pd.DataFrame(cache_items)
            df_cache = df_cache.rename(
                columns={"ticker": "Mã", "rows": "Rows", "age_min": "Tuổi (phút)", "file": "File"}
            )
            st.dataframe(df_cache, width='stretch', hide_index=True)
            st.caption(f"Tổng: {len(cache_items)} mã được cache")
        else:
            st.caption("Chưa có cache")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Xóa all", key="btn_clear_all_cache"):
                invalidate_stock_cache()
                st.success("Đã xóa tất cả cache")
                st.rerun()
        with col2:
            ticker_to_clear = st.text_input("Xóa mã", key="cache_clear_ticker")
            if st.button("Xóa", key="btn_clear_ticker_cache") and ticker_to_clear:
                invalidate_stock_cache(ticker_to_clear.upper())
                st.success(f"Đã xóa cache {ticker_to_clear.upper()}")
                st.rerun()
    except Exception as exc:
        st.caption(f"Không đọc được cache status: {exc}")


def render_morning_briefing():
    st.markdown('<div class="section-title">Morning Briefing</div>', unsafe_allow_html=True)

    # --- collect data ---
    try:
        regime_data = get_market_regime_cached()
        regime = regime_data.get("regime", "UNKNOWN") if isinstance(regime_data, dict) else str(regime_data)
        regime_map = {"BULL_TREND": "🟢", "LOW_VOL_RANGING": "🟡", "HIGH_VOL_RANGING": "🟠", "BEAR_TREND": "🔴"}
        regime_icon = regime_map.get(regime, "")
        regime_label_short = regime.replace("_TREND", "").replace("_RANGING", " RNG")
        regime_val = f"{regime_icon} {regime_label_short}".strip()
        regime_color = "#22c55e" if regime == "BULL_TREND" else "#f43f5e" if regime == "BEAR_TREND" else "#f59e0b"
    except Exception:
        regime_val, regime_color = "N/A", "#94a3b8"

    try:
        portfolio = paper_trader.load_portfolio()
        cash = float(getattr(paper_trader, "load_cash", lambda: portfolio.get("cash", 0))())
        initial_cash = float(portfolio.get("initial_cash", 100_000_000))
        positions = portfolio.get("positions", {}) or {}
        total_mv, total_pnl = 0.0, 0.0
        for sym, pos in positions.items():
            price = paper_trader.current_price(sym)
            if price is None:
                continue
            qty = int(pos.get("qty", 0))
            avg = float(pos.get("avg_price", 0))
            total_mv += qty * price
            total_pnl += (price - avg) * qty
        equity = cash + total_mv
        pnl_pct = (total_pnl / initial_cash) * 100 if initial_cash else 0
        equity_val = f"{equity/1e6:.1f}M"
        equity_delta = f"{pnl_pct:+.1f}% · {len(positions)}p"
        equity_color = "#22c55e" if pnl_pct >= 0 else "#f43f5e"
    except Exception:
        equity_val, equity_delta, equity_color = "N/A", "", "#94a3b8"

    try:
        state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_state.json")
        with open(state_path, encoding="utf-8") as f:
            sched = json.load(f)
        today_str = date.today().isoformat()
        tasks = ["morning_prep", "market_analysis", "auto_trade", "eod_update"]
        ran = sum(1 for key in tasks if sched.get(key) == today_str)
        sched_val = f"{ran}/4"
        sched_color = "#22c55e" if ran == 4 else "#f59e0b" if ran >= 2 else "#f43f5e"
    except Exception:
        sched_val, sched_color = "N/A", "#94a3b8"

    try:
        analysis_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis_results.json")
        with open(analysis_path, encoding="utf-8") as f:
            analysis = json.load(f)
        if analysis.get("date") == date.today().isoformat():
            tradeable = analysis.get("tradeable_tickers", []) or []
            analysis_val = ", ".join(tradeable[:2]) + ("…" if len(tradeable) > 2 else "") if tradeable else "—"
            analysis_delta = f"{len(tradeable)} mã"
            analysis_color = "#22c55e" if tradeable else "#94a3b8"
        else:
            analysis_val, analysis_delta, analysis_color = "Chưa chạy", "", "#94a3b8"
    except Exception:
        analysis_val, analysis_delta, analysis_color = "N/A", "", "#94a3b8"

    # --- 2×2 compact card grid (no truncation) ---
    r1c1, r1c2 = st.columns(2)
    r2c1, r2c2 = st.columns(2)

    def _brief_card(col, label, value, delta, color):
        col.markdown(
            f'<div style="background:rgba(15,23,42,0.82);border:1px solid rgba(148,163,184,0.18);'
            f'border-radius:7px;padding:0.55rem 0.65rem;min-height:4.2rem;">'
            f'<div style="font-size:0.62rem;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;font-weight:700;">{label}</div>'
            f'<div style="font-size:0.92rem;font-weight:800;color:{color};margin-top:0.25rem;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{value}</div>'
            f'<div style="font-size:0.68rem;color:#64748b;margin-top:0.18rem;">{delta}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    _brief_card(r1c1, "Regime", regime_val, "", regime_color)
    _brief_card(r1c2, "Equity", equity_val, equity_delta, equity_color)
    _brief_card(r2c1, "Scheduler", sched_val, "tasks hôm nay", sched_color)
    _brief_card(r2c2, "Analysis", analysis_val, analysis_delta, analysis_color)


def render_learning_status():
    st.markdown('<div class="section-title">Learning Engine</div>', unsafe_allow_html=True)
    try:
        from learning_engine import get_signal_weight

        report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "performance_report.json")
        if os.path.exists(report_path):
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
            overall = report.get("overall", {})
            col1, col2, col3 = st.columns(3)
            col1.metric("Total", overall.get("total", 0))
            col2.metric("Accuracy", f"{overall.get('accuracy', 0):.0%}")
            col3.metric("Avg PnL", f"{overall.get('avg_pnl', 0):+.2f}%")

            by_ticker = report.get("by_ticker", {})
            if by_ticker:
                rows = []
                for ticker, stats in by_ticker.items():
                    rows.append(
                        {
                            "Mã": ticker,
                            "Accuracy": f"{stats.get('accuracy', 0):.0%}",
                            "Gần đây": f"{stats.get('recent_5_accuracy', 0):.0%}",
                            "Avg PnL": f"{stats.get('avg_pnl_pct', 0):+.2f}%",
                            "Weight": f"{get_signal_weight(ticker):.1f}x",
                            "Xu hướng": "Up" if stats.get("trend") == "improving" else "Down",
                        }
                    )
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        else:
            st.caption("Chưa có data learning. Hệ thống sẽ bắt đầu học sau vài ngày chạy.")
    except Exception as exc:
        st.caption(f"Không đọc được learning status: {exc}")


def load_training_watchlist():
    if os.path.exists(TRAINING_WATCHLIST_FILE):
        with open(TRAINING_WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            try:
                return normalize_symbol_list(json.load(f))
            except json.JSONDecodeError:
                pass
    save_training_watchlist(DEFAULT_TRAINING_WATCHLIST)
    return DEFAULT_TRAINING_WATCHLIST.copy()

def save_training_watchlist(wl):
    save_json_file(TRAINING_WATCHLIST_FILE, normalize_symbol_list(wl))

def default_training_ai_state():
    return {
        "status": "idle",
        "symbols": [],
        "completed": [],
        "failed": {},
        "current_index": 0,
        "stop_requested": False,
        "started_at": "",
        "updated_at": "",
        "message": ""
    }

def load_training_ai_state():
    state = load_json_file(TRAINING_AI_STATE_FILE, default_training_ai_state())
    base = default_training_ai_state()
    base.update(state if isinstance(state, dict) else {})
    base["symbols"] = normalize_symbol_list(base.get("symbols", []))
    base["completed"] = normalize_symbol_list(base.get("completed", []))
    base["failed"] = base.get("failed", {}) if isinstance(base.get("failed", {}), dict) else {}
    return base

def save_training_ai_state(state):
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json_file(TRAINING_AI_STATE_FILE, state)

def parse_state_datetime(value):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None

def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"

def training_eta_text(state, fallback_total):
    symbols = normalize_symbol_list(state.get("symbols") or [])
    total = len(symbols) or int(fallback_total or 0)
    completed = len(state.get("completed", []) or [])
    failed = len(state.get("failed", {}) or {})
    processed = max(completed + failed, int(state.get("current_index") or 0))
    remaining = max(0, total - processed)
    started_at = parse_state_datetime(state.get("started_at"))
    if not started_at or processed <= 0:
        return {
            "processed": processed,
            "total": total,
            "elapsed": "-",
            "avg": "-",
            "eta": "Dang tinh sau khi xong ma dau tien",
            "finish_at": "-"
        }
    elapsed_seconds = max(1, (datetime.now() - started_at).total_seconds())
    avg_seconds = elapsed_seconds / processed
    eta_seconds = avg_seconds * remaining
    finish_at = datetime.now() + timedelta(seconds=eta_seconds)
    return {
        "processed": processed,
        "total": total,
        "elapsed": format_duration(elapsed_seconds),
        "avg": format_duration(avg_seconds),
        "eta": format_duration(eta_seconds),
        "finish_at": finish_at.strftime("%H:%M:%S")
    }

def reset_training_ai_state():
    save_training_ai_state(default_training_ai_state())

def training_ai_state_is_stale(state, minutes=30):
    if state.get("status") not in ["running", "paused", "stopping"]:
        return False
    updated_at = parse_state_datetime(state.get("updated_at"))
    if not updated_at:
        return True
    return (datetime.now() - updated_at).total_seconds() > minutes * 60

def default_lstm_training_state():
    return {
        "status": "idle",
        "symbols": [],
        "completed": [],
        "failed": {},
        "current": "",
        "started_at": "",
        "finished_at": "",
        "updated_at": "",
        "message": ""
    }

def load_lstm_training_state():
    state = load_json_file(LSTM_TRAINING_STATE_FILE, default_lstm_training_state())
    base = default_lstm_training_state()
    base.update(state if isinstance(state, dict) else {})
    return base

def save_lstm_training_state(state):
    save_json_file(LSTM_TRAINING_STATE_FILE, state)

def lstm_model_ready(symbol):
    return (
        os.path.exists(f"{MODELS_DIR}/{symbol}_lstm.keras")
        and os.path.exists(f"{MODELS_DIR}/{symbol}_scaler.json")
    )

def missing_lstm_symbols(symbols):
    return [s for s in normalize_symbol_list(symbols) if not lstm_model_ready(s)]

def lstm_state_is_stale(state, minutes=30):
    if state.get("status") not in ["running", "starting"]:
        return False
    try:
        updated_at = datetime.strptime(state.get("updated_at", ""), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return True
    return (datetime.now() - updated_at).total_seconds() > minutes * 60

def start_lstm_training(symbols):
    symbols = normalize_symbol_list(symbols)
    if not symbols:
        return False, "Training watchlist dang trong"
    state = load_lstm_training_state()
    if state.get("status") in ["running", "starting"] and not lstm_state_is_stale(state):
        return False, "LSTM dang train"
    save_lstm_training_state({
        "status": "starting",
        "symbols": symbols,
        "completed": [],
        "failed": {},
        "current": "",
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": "Dang khoi dong train LSTM"
    })
    cmd = [sys.executable, "train_lstm.py", ",".join(symbols)]
    kwargs = {"cwd": str(BASE_DIR)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cmd, **kwargs)
    return True, f"Da bat dau train {len(symbols)} ma"

def _safe_read_json(filepath, default=None):
    """Read JSON với retry — tránh đọc file đang bị ghi dở."""
    if default is None:
        default = []
    max_retries = 5
    for i in range(max_retries):
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            if not content.strip():
                raise ValueError("Empty file")
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            if i < max_retries - 1:
                time.sleep(0.1)
            else:
                return default
        except FileNotFoundError:
            return default
        except Exception:
            time.sleep(0.1)
    return default


def _safe_write_json(filepath, data):
    """Write JSON atomically — dùng file .tmp rồi rename để tránh corrupt."""
    filepath = Path(filepath)
    tmp_path = filepath.with_suffix(".tmp")
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_json_file(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default
    return default

def save_json_file(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def set_system_status(status, message, detail=None):
    save_json_file(SYSTEM_STATUS_FILE, {
        "status": status,
        "message": message,
        "detail": detail or "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

def classify_health_error(message):
    text = str(message).lower()
    critical_terms = [
        "api key", "apikey", "unauthorized", "forbidden", "401", "403",
        "quota", "limit", "rate limit", "too many requests", "exhausted",
        "expired", "invalid token", "permission"
    ]
    network_terms = ["timeout", "connection", "network", "dns", "max retries", "failed to establish"]
    if any(term in text for term in critical_terms):
        return "critical"
    if any(term in text for term in network_terms):
        return "warning"
    return "warning"

def health_item(name, level, message, detail=""):
    return {
        "name": name,
        "level": level,
        "message": message,
        "detail": detail
    }

@st.cache_data(ttl=60, show_spinner=False)
def collect_system_health():
    items = []

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            api_status = get_vnstock_status_cached()
        if isinstance(api_status, dict) and api_status.get("has_api_key"):
            tier = api_status.get("tier") or "unknown"
            limits = api_status.get("limits") or {}
            limit_text = ""
            if isinstance(limits, dict) and limits:
                per_minute = limits.get("per_minute")
                per_hour = limits.get("per_hour")
                limit_text = f"{per_minute}/phut, {per_hour}/gio" if per_minute or per_hour else str(limits)
            items.append(health_item("VNStock API", "ok", f"API key OK - goi {tier}", limit_text))
        else:
            items.append(health_item("VNStock API", "warning", "Dung VNStock tier mien phi (VCI)", "Chua cau hinh API key tra phi - van lay du lieu VCI binh thuong, chi gioi han rate."))
    except Exception as exc:
        level = classify_health_error(exc)
        items.append(health_item("VNStock API", level, "Khong kiem tra duoc API key VNStock", str(exc)[:240]))

    vnstock_env_file = os.path.expanduser("~/.vnstock/id/environment.json")
    vnstock_env_dir = os.path.dirname(vnstock_env_file)
    if os.path.exists(vnstock_env_file) and not os.access(vnstock_env_file, os.R_OK | os.W_OK):
        items.append(health_item("VNStock config", "warning", "Khong doc/ghi duoc file cau hinh VNStock", vnstock_env_file))
    elif os.path.exists(vnstock_env_dir) and not os.access(vnstock_env_dir, os.W_OK):
        items.append(health_item("VNStock config", "warning", "Khong ghi duoc thu muc cau hinh VNStock", vnstock_env_dir))

    try:
        from data_fetcher import get_stock_data_cached
        df_probe = get_stock_data_cached("VNM", years=0.1)
        src_name, src_status, _ = source_manager.get_indicator()
        if df_probe is None or len(df_probe) == 0:
            items.append(health_item("Du lieu VCI", "critical", "VCI khong tra du lieu", "Thu lai API key hoac ket noi mang."))
        elif src_status == "all_failed":
            items.append(health_item("Du lieu VCI", "warning", f"Dung cache ({src_name})", "Tat ca source dang loi, dang dung cache cu"))
        elif src_status == "fallback":
            items.append(health_item("Du lieu VCI", "warning", f"Dang dung {src_name}", f"VCI loi, da chuyen sang {src_name}"))
        else:
            items.append(health_item("Du lieu VCI", "ok", f"Lay du lieu {src_name} OK", f"Probe VNM: {len(df_probe)} dong"))
    except Exception as exc:
        level = classify_health_error(exc)
        items.append(health_item("Du lieu VCI", level, "Loi lay du lieu VCI", str(exc)[:240]))

    try:
        router = get_router_status()
        active_providers = [p.get("provider") for p in router.get("providers", []) if p.get("has_key")]
        cloud_providers = [p for p in active_providers if p != "ollama"]
        if cloud_providers:
            items.append(health_item("LLM Router", "ok", "Cloud provider pool OK", ", ".join(cloud_providers)))
        elif any(p.get("provider") == "ollama" and p.get("has_key") for p in router.get("providers", [])):
            items.append(health_item("LLM Router", "warning", "Chi co fallback Ollama", "Them GROQ_KEY/CEREBRAS_KEY/CLOUDFLARE_KEY/GATEWAY_KEYS vao .env."))
        else:
            items.append(health_item("LLM Router", "critical", "Khong co provider LLM", "Can cau hinh key hoac chay Ollama."))
    except Exception as exc:
        items.append(health_item("LLM Router", "warning", "Khong doc duoc LLM Router status", str(exc)[:180]))

    required_files = [
        HISTORY_FILE,
        TRAINING_WATCHLIST_FILE,
        TRAINING_AI_STATE_FILE,
        AUTO_ANALYSIS_STATE_FILE,
        SYSTEM_STATUS_FILE
    ]
    missing = [path for path in required_files if not os.path.exists(path)]
    if missing:
        items.append(health_item("File he thong", "warning", f"Thieu {len(missing)} file trang thai", ", ".join(os.path.basename(p) for p in missing)))
    else:
        items.append(health_item("File he thong", "ok", "File trang thai OK", ""))

    level_rank = {"ok": 0, "warning": 1, "critical": 2}
    worst = max(items, key=lambda item: level_rank.get(item["level"], 0))["level"] if items else "ok"
    return {
        "level": worst,
        "items": items,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def render_sticky_alerts():
    """Alert nổi bật ở top main area khi có target/stoploss hit trong 30 phút gần nhất."""
    alerts_path = BASE_DIR / "intraday_alerts.json"
    if not alerts_path.exists():
        return
    try:
        with open(alerts_path, encoding="utf-8") as f:
            alerts = json.load(f)
        recent = [
            a for a in (alerts if isinstance(alerts, list) else [])
            if datetime.fromisoformat(str(a.get("time", ""))) > datetime.now() - timedelta(minutes=30)
            and ("STOP LOSS" in str(a.get("message", "")) or "TARGET" in str(a.get("message", "")))
        ]
        for alert in recent[-3:]:
            msg = str(alert.get("message", ""))
            try:
                time_str = datetime.fromisoformat(str(alert.get("time", ""))).strftime("%H:%M")
            except Exception:
                time_str = ""
            if "STOP LOSS" in msg or "🔴" in msg:
                st.error(f"🚨 {time_str} {msg}")
            else:
                st.success(f"✅ {time_str} {msg}")
    except Exception:
        pass


def render_health_alerts(location="main"):
    health = collect_system_health()
    issues = [item for item in health["items"] if item["level"] != "ok"]
    if not issues:
        if location == "sidebar":
            st.success("He thong OK")
        return

    critical = [item for item in issues if item["level"] == "critical"]
    title = "Loi he thong can xu ly" if critical else "Canh bao he thong"
    lines = [f"{item['name']}: {item['message']}" + (f" - {item['detail']}" if item.get("detail") else "") for item in issues]
    body = "\n".join(f"- {line}" for line in lines)
    if critical:
        st.error(f"{title}\n\n{body}")
    else:
        st.warning(f"{title}\n\n{body}")


def render_intraday_alerts():
    """Display recent intraday alerts from the scheduler monitor."""
    alerts_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intraday_alerts.json")
    if not os.path.exists(alerts_path):
        return

    try:
        with open(alerts_path, encoding="utf-8") as f:
            alerts = json.load(f)
        if not isinstance(alerts, list):
            return

        cutoff = datetime.now() - timedelta(hours=2)
        recent_alerts = []
        for alert in alerts:
            try:
                alert_time = datetime.fromisoformat(str(alert.get("time", "")))
                if alert_time > cutoff:
                    recent_alerts.append(alert)
            except Exception:
                continue

        if not recent_alerts:
            return

        st.markdown("---")
        st.markdown('<div class="section-title">🚨 Intraday Alerts</div>', unsafe_allow_html=True)
        for alert in reversed(recent_alerts[-5:]):
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
    except Exception:
        pass

def today_auto_completed_for_symbols(symbols):
    today_key = datetime.now().strftime("%Y-%m-%d")
    today_state = load_json_file(AUTO_ANALYSIS_STATE_FILE, {}).get(today_key, {})
    if today_state.get("status") != "completed":
        return False, today_state
    completed_symbols = normalize_symbol_list(today_state.get("symbols", []))
    target_symbols = normalize_symbol_list(symbols)
    return completed_symbols == target_symbols, today_state

def get_today_auto_state():
    state = load_json_file(AUTO_ANALYSIS_STATE_FILE, {})
    return state.get(datetime.now().strftime("%Y-%m-%d"), {})

def render_system_status(slot, refresh_seconds=None):
    status_data = load_json_file(SYSTEM_STATUS_FILE, {})
    auto_state = get_today_auto_state()
    auto_status = auto_state.get("status", "pending")
    health = collect_system_health()
    health_issues = [item for item in health["items"] if item["level"] != "ok"]
    try:
        status_updated = datetime.strptime(str(status_data.get("updated_at", "")), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        status_updated = None
    status_is_today = status_updated is not None and status_updated.date() == datetime.now().date()

    if any(item["level"] == "critical" for item in health_issues):
        first_issue = next(item for item in health_issues if item["level"] == "critical")
        message = f"Loi: {first_issue['name']}"
        detail = first_issue["message"]
        color = "#f43f5e"
    elif health_issues:
        first_issue = health_issues[0]
        message = f"Canh bao: {first_issue['name']}"
        detail = first_issue["message"]
        color = "#f59e0b"
    elif status_data.get("status") in ["auto_analysis", "manual_analysis", "updating_results", "waiting_for_ollama"]:
        message = status_data.get("message", "Đang xử lý")
        detail = status_data.get("detail", "")
        color = "#f59e0b"
    elif status_data.get("status") == "idle" and status_is_today and status_data.get("message"):
        message = status_data.get("message", "Đang chờ")
        detail = status_data.get("detail", "")
        color = "#22c55e"
    elif auto_status == "completed":
        completed_at = auto_state.get("completed_at", "")
        completed_list = auto_state.get("list_name", "watchlist")
        message = f"Đã phân tích {completed_list} hôm nay"
        detail = f"Hoàn tất: {completed_at}. Không có tác vụ AI nền."
        color = "#22c55e"
    elif auto_status == "waiting_for_ollama":
        message = "Đang chờ LLM Router để phân tích bù"
        detail = auto_state.get("last_checked", "")
        color = "#f59e0b"
    else:
        next_run = f"{AUTO_ANALYSIS_HOUR:02d}:{AUTO_ANALYSIS_MINUTE:02d}"
        message = "Đang chờ"
        detail = f"Tự phân tích lúc {next_run}. Bấm Lam moi du lieu để cập nhật màn hình."
        color = "#38bdf8"

    updated = health.get("updated_at") or status_data.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if health_issues:
        issue_details = []
        for item in health_issues[:3]:
            item_text = f"{item['name']}: {item['message']}"
            if item.get("detail"):
                item_text += f" - {item['detail']}"
            issue_details.append(item_text)
        detail = "<br>".join(html.escape(text) for text in issue_details)
    else:
        detail = html.escape(str(detail))

    slot.markdown(
        f'<div class="wl-panel">'
        f'<div style="font-weight:800;color:#f8fafc;margin-bottom:0.25rem;">Trạng thái hệ thống</div>'
        f'<div style="color:{color};font-weight:800;">{html.escape(str(message))}</div>'
        f'<div style="color:#92a1b6;font-size:0.74rem;margin-top:0.2rem;line-height:1.35;">{detail}</div>'
        f'<div style="color:#64748b;font-size:0.68rem;margin-top:0.35rem;">Cập nhật: {html.escape(str(updated))}</div>'
        f'</div>',
        unsafe_allow_html=True
    )

def get_watchlist_prices(watchlist):
    results = []
    for sym in watchlist:
        try:
            df = get_stock_data_st(sym, years=0.1)
            if df is not None and len(df) >= 2:
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                chg = ((latest["close"] - prev["close"]) / prev["close"]) * 100
                results.append({
                    "symbol": sym,
                    "price": latest["close"],
                    "change": chg,
                    "volume": latest["volume"],
                    "high": latest["high"],
                    "low": latest["low"]
                })
            else:
                results.append({"symbol": sym, "price": None, "change": None, "volume": None, "high": None, "low": None})
        except Exception:
            results.append({"symbol": sym, "price": None, "change": None, "volume": None, "high": None, "low": None})
    return results

# ===================== SIDEBAR =====================
with st.sidebar:
    st.markdown(
        '<div style="padding:0.2rem 0.25rem 0.7rem;">'
        '<div class="app-kicker">Vietnam market</div>'
        '<div class="header-grad" style="font-size:1.35rem;line-height:1.15;margin-top:0.2rem;">VN Stock</div>'
        '<div class="sub-header" style="margin-top:0.25rem;">Professional Dashboard</div>'
        '</div>',
        unsafe_allow_html=True
    )

    # ===== DATA SOURCE INDICATOR =====
    _src_name, _src_status, _src_color = source_manager.get_indicator()
    _src_dot = "●"
    _src_label = {"ok": "ok", "fallback": "fallback", "cache": "cached"}.get(_src_status, _src_status)
    st.markdown(
        f'<div style="padding:0 0.25rem 0.6rem;font-size:0.68rem;font-family:monospace;">'
        f'<span style="color:{_src_color};">{_src_dot} {_src_name}</span>'
        f'<span style="color:#8b949e;"> · data source · {_src_label}</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    # ===== MORNING BRIEFING (đặt đầu tiên) =====
    render_morning_briefing()
    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    # ===== WATCHLIST PANEL =====
    if "watchlist" not in st.session_state:
        st.session_state["watchlist"] = load_watchlist()
    watchlist = st.session_state["watchlist"]
    if "training_watchlist" not in st.session_state:
        st.session_state["training_watchlist"] = load_training_watchlist()
    training_watchlist = st.session_state["training_watchlist"]
    all_symbols = normalize_symbol_list(watchlist + training_watchlist)
    if "selected_symbol" not in st.session_state or st.session_state["selected_symbol"] not in all_symbols:
        st.session_state["selected_symbol"] = all_symbols[0] if all_symbols else "VNM"
    st.markdown('<div class="section-title">Watchlist</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="section-caption">{len(watchlist)} mã đang theo dõi</div>', unsafe_allow_html=True)
    with st.container():
        wl_data = get_watchlist_prices(watchlist)
        watchlist_html = ['<div class="wl-panel">']
        for w in wl_data:
            if w["price"] is not None:
                cls = "metric-up" if w["change"] > 0 else "metric-down" if w["change"] < 0 else "metric-neutral"
                badge = "badge-up" if w["change"] > 0 else "badge-down" if w["change"] < 0 else "badge-neutral"
                dot = "dot-up" if w["change"] > 0 else "dot-down" if w["change"] < 0 else "dot-neutral"
                arrow = "▲" if w["change"] > 0 else "▼" if w["change"] < 0 else "―"
                vol = f"{w['volume']/1e6:.1f}M" if w["volume"] else ""
                range_text = f"H {w['high']:,.1f} / L {w['low']:,.1f}" if w["high"] and w["low"] else ""
                watchlist_html.append(
                    f'<div class="wl-item">'
                    f'<div><span class="status-dot {dot}"></span><span class="wl-symbol">{w["symbol"]}</span>'
                    f'<span class="wl-price">{w["price"]:,.1f}</span></div>'
                    f'<div class="wl-change {cls}">{arrow} {w["change"]:+.2f}%'
                    f'<span class="wl-meta">{range_text}</span><span class="badge {badge}">{vol}</span></div>'
                    f'</div>'
                )
            else:
                watchlist_html.append(
                    f'<div class="wl-item"><div><span class="status-dot dot-neutral"></span>'
                    f'<span class="wl-symbol">{w["symbol"]}</span><span class="wl-price">Chưa có dữ liệu</span></div>'
                    f'<div class="wl-change metric-neutral">N/A</div></div>'
                )
        watchlist_html.append('</div>')
        st.markdown("".join(watchlist_html), unsafe_allow_html=True)

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    # ===== WATCHLIST MANAGEMENT =====
    st.markdown('<div class="section-title">⚙️ Quản lý</div>', unsafe_allow_html=True)
    new_symbol = st.text_input("Thêm mã mới", placeholder="VD: FPT", label_visibility="collapsed").upper()
    col_add, col_del = st.columns(2)
    if col_add.button("➕ Thêm", width='stretch'):
        if new_symbol and new_symbol not in watchlist:
            watchlist.append(new_symbol)
            st.session_state["watchlist"] = watchlist
            save_watchlist(watchlist)
            st.success(f"Đã thêm {new_symbol}")
    if col_del.button("➖ Xóa", width='stretch'):
        if new_symbol in watchlist:
            watchlist.remove(new_symbol)
            st.session_state["watchlist"] = watchlist
            save_watchlist(watchlist)
            st.success(f"Đã xóa {new_symbol}")

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    # ===== TRAINING WATCHLIST =====
    st.markdown('<div class="section-title">Training watchlist</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="section-caption">{len(training_watchlist)} mã dùng để AI tự học</div>',
        unsafe_allow_html=True
    )
    preview_training = ", ".join(training_watchlist[:12])
    more_training = f" +{len(training_watchlist) - 12}" if len(training_watchlist) > 12 else ""
    st.markdown(
        f'<div class="wl-panel" style="color:#cbd5e1;font-size:0.76rem;line-height:1.45;">{preview_training}{more_training}</div>',
        unsafe_allow_html=True
    )
    training_symbol = st.text_input("Training mã", placeholder="VD: VCB", label_visibility="collapsed", key="training_symbol_input").upper()
    col_tr_add, col_tr_del = st.columns(2)
    if col_tr_add.button("Thêm training", width='stretch'):
        if training_symbol and training_symbol not in training_watchlist:
            training_watchlist.append(training_symbol)
            training_watchlist = normalize_symbol_list(training_watchlist)
            st.session_state["training_watchlist"] = training_watchlist
            save_training_watchlist(training_watchlist)
            st.success(f"Đã thêm {training_symbol} vào training")
    if col_tr_del.button("Xóa training", width='stretch'):
        if training_symbol in training_watchlist:
            training_watchlist.remove(training_symbol)
            st.session_state["training_watchlist"] = training_watchlist
            save_training_watchlist(training_watchlist)
            st.success(f"Đã xóa {training_symbol} khỏi training")
    if st.button("Reset training 50 mã", width='stretch'):
        st.session_state["training_watchlist"] = DEFAULT_TRAINING_WATCHLIST.copy()
        save_training_watchlist(DEFAULT_TRAINING_WATCHLIST)
        st.success("Đã reset training watchlist")

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    # ===== SETTINGS =====
    st.markdown('<div class="section-title">🔧 Cài đặt</div>', unsafe_allow_html=True)
    all_symbols = normalize_symbol_list(watchlist + training_watchlist)
    if not all_symbols:
        all_symbols = ["VNM"]
    st.session_state.setdefault("selected_interval", "1M")
    st.session_state.setdefault("background_refresh", True)
    st.session_state.setdefault("refresh_sec", 30)

    @st.fragment
    def render_settings_controls(symbol_options):
        selected_symbol = st.session_state.get("selected_symbol", symbol_options[0])
        selected_index = symbol_options.index(selected_symbol) if selected_symbol in symbol_options else 0
        st.session_state["selected_symbol"] = st.selectbox(
            "Chọn mã",
            symbol_options,
            index=selected_index,
            label_visibility="collapsed",
            key="selected_symbol_control"
        )
        interval_options = ["1D", "1W", "1M", "3M"]
        interval_index = interval_options.index(st.session_state.get("selected_interval", "1M"))
        st.session_state["selected_interval"] = st.selectbox(
            "Khung thời gian",
            interval_options,
            index=interval_index,
            key="selected_interval_control"
        )
        st.session_state["background_refresh"] = st.toggle(
            "Cap nhat tu dong",
            value=bool(st.session_state.get("background_refresh", True)),
            key="background_refresh_control"
        )
        st.session_state["refresh_sec"] = st.slider(
            "Chu ky cap nhat (giay)",
            10,
            120,
            int(st.session_state.get("refresh_sec", 30)),
            disabled=not st.session_state["background_refresh"],
            key="refresh_sec_control"
        )

    render_settings_controls(all_symbols)
    symbol = st.session_state.get("selected_symbol", all_symbols[0])
    interval = st.session_state.get("selected_interval", "1M")
    background_refresh = bool(st.session_state.get("background_refresh", True))
    refresh_sec = int(st.session_state.get("refresh_sec", 30))

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    @st.fragment(run_every=f"{int(refresh_sec)}s" if background_refresh else None)
    def render_sidebar_status():
        render_system_status(st.empty(), refresh_sec if background_refresh else None)

    render_sidebar_status()
    render_intraday_alerts()

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)
    render_scheduler_status()

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    with st.expander("⚙️ System & Cache", expanded=False):
        render_data_cache_status()
        st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)
        render_learning_status()

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    # ===== LLM ROUTER =====
    # Fix #5 – show only summary; hide verbose per-provider stats in expander
    router_status = get_router_status()
    ollama_ok = any(p["has_key"] for p in router_status["providers"])
    keys_avail = router_status.get("keys_available", 0)
    fail_calls = router_status.get("fail_calls", 0)
    last_provider = router_status.get("last_provider") or "-"
    last_model = router_status.get("last_model") or "-"
    cache_state = "active" if router_status.get("cache_active") else "empty"
    router_health_color = "#22c55e" if keys_avail > 0 and fail_calls == 0 else "#f59e0b" if keys_avail > 0 else "#f43f5e"
    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.45rem;">'
        f'<div class="section-title" style="margin:0;">🔀 LLM Router</div>'
        f'<div style="font-size:0.72rem;font-weight:700;color:{router_health_color};">'
        f'{"✓" if keys_avail > 0 else "✗"} {keys_avail} keys · {fail_calls} err</div>'
        f'</div>',
        unsafe_allow_html=True
    )
    st.caption(f"Last: {last_provider} / {last_model[:22]}… | Cache: {cache_state}" if len(last_model) > 22 else f"Last: {last_provider} / {last_model} | Cache: {cache_state}")
    with st.expander("Chi tiết provider", expanded=False):
        st.caption(
            f"Groq: {router_status.get('groq_calls', 0)} | "
            f"Cerebras: {router_status.get('cerebras_calls', 0)} | "
            f"Cloudflare: {router_status.get('cloudflare_calls', 0)} | "
            f"Gateway: {router_status.get('gateway_calls', 0)} | "
            f"Gemini: {router_status.get('gemini_calls', 0)} | "
            f"DeepSeek: {router_status.get('deepseek_calls', 0)} | "
            f"Ollama: {router_status.get('ollama_calls', 0)}"
        )
        for provider_info in router_status["providers"]:
            icon = "✅" if provider_info["has_key"] else "⚪"
            last = " · last" if provider_info["is_last_used"] else ""
            st.caption(
                f"{icon} {provider_info['provider']}: "
                f"{provider_info['calls_today']} calls, {provider_info['errors_today']} errors{last}"
            )

    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

    # ===== BULK =====
    st.markdown('<div class="section-title">🔄 Hàng loạt</div>', unsafe_allow_html=True)
    training_ai_state = load_training_ai_state()
    completed_count = len(training_ai_state.get("completed", []))
    training_total = len(training_ai_state.get("symbols") or training_watchlist)
    auto_completed_today, auto_today_state = today_auto_completed_for_symbols(training_watchlist)
    stale_training_ai = training_ai_state_is_stale(training_ai_state)
    if auto_completed_today and stale_training_ai:
        training_ai_state["status"] = "completed"
        training_ai_state["symbols"] = training_watchlist
        training_ai_state["completed"] = training_watchlist
        training_ai_state["failed"] = {}
        training_ai_state["current_index"] = len(training_watchlist)
        training_ai_state["stop_requested"] = False
        training_ai_state["message"] = f"Synced from auto analysis completed at {auto_today_state.get('completed_at', '-')}"
        save_training_ai_state(training_ai_state)
        completed_count = len(training_ai_state.get("completed", []))
        training_total = len(training_watchlist)
    if auto_completed_today and training_ai_state.get("status") not in ["running", "paused", "stopping"]:
        st.caption(f"Hom nay da phan tich xong {len(training_watchlist)} ma luc {auto_today_state.get('completed_at', '-')}")
    if training_ai_state.get("status") in ["running", "paused", "stopping"]:
        eta = training_eta_text(training_ai_state, training_total)
        failed_for_eta = len(training_ai_state.get("failed", {}) or {})
        processed_for_eta = eta["processed"]
        total_for_eta = max(eta["total"], training_total, 1)
        status_label = training_ai_state.get("status")
        st.caption(
            f"Training AI: {status_label} | {processed_for_eta}/{total_for_eta} ma "
            f"(xong {completed_count}, loi {failed_for_eta})"
        )
        st.progress(min(1.0, processed_for_eta / total_for_eta))
        st.caption(
            f"Da chay: {eta['elapsed']} | TB/ma: {eta['avg']} | "
            f"Con lai: {eta['eta']} | Du kien xong: {eta['finish_at']}"
        )
    if st.button("Start / Resume training AI", width='stretch'):
        st.session_state["bulk_training_analyze"] = True
    if auto_completed_today and st.button("Phan tich lai hom nay", width='stretch'):
        st.session_state["force_bulk_training_analyze"] = True
    failed_count = len(training_ai_state.get("failed", {}) or {})
    if failed_count:
        st.caption(f"Ma loi: {failed_count}")
        if st.button("Chay lai ma loi", width='stretch'):
            st.session_state["retry_training_failed"] = True
    col_train_stop, col_train_reset = st.columns(2)
    if col_train_stop.button("Stop sau ma hien tai", width='stretch'):
        training_ai_state["status"] = "stopping"
        training_ai_state["stop_requested"] = True
        training_ai_state["message"] = "Stop requested"
        save_training_ai_state(training_ai_state)
        set_system_status("manual_analysis", "Training AI dang dung", "Se dung sau ma hien tai")
    if col_train_reset.button("Reset tien do", width='stretch'):
        reset_training_ai_state()

    st.markdown('<div class="section-caption" style="margin-top:0.55rem;">LSTM models</div>', unsafe_allow_html=True)
    @st.fragment(run_every="30s")
    def render_lstm_training_controls():
        lstm_state = load_lstm_training_state()
        lstm_symbols = normalize_symbol_list(st.session_state.get("training_watchlist", load_training_watchlist()))
        lstm_total = len(lstm_state.get("symbols") or lstm_symbols)
        lstm_completed = len(lstm_state.get("completed", []))
        lstm_failed = len(lstm_state.get("failed", {}) or {})
        missing_symbols = missing_lstm_symbols(lstm_symbols)
        ready_count = len(lstm_symbols) - len(missing_symbols)
        stale_lstm_state = lstm_state_is_stale(lstm_state)
        if lstm_state.get("status") in ["running", "starting"]:
            current = lstm_state.get("current") or lstm_state.get("message", "")
            st.caption(f"LSTM: {lstm_state.get('status')} | {lstm_completed}/{lstm_total} xong | dang train: {current or '-'}")
            if stale_lstm_state:
                st.caption("Trang thai LSTM cu bi ket. Co the chay tiep cac ma con thieu.")
            if lstm_total:
                st.progress(min(1.0, lstm_completed / lstm_total))
        elif lstm_state.get("status") == "completed":
            st.caption(f"LSTM: completed | {lstm_completed}/{lstm_total} xong | loi: {lstm_failed}")
        else:
            st.caption(f"LSTM da co model day du: {ready_count}/{len(lstm_symbols)} ma")

        disabled = lstm_state.get("status") in ["running", "starting"] and not stale_lstm_state
        col_lstm_all, col_lstm_resume = st.columns(2)
        if col_lstm_all.button("Train lai tat ca", width='stretch', disabled=disabled):
            ok, msg = start_lstm_training(lstm_symbols)
            if ok:
                st.success(msg)
            else:
                st.warning(msg)
        resume_disabled = disabled or not missing_symbols
        if col_lstm_resume.button("Chay tiep con thieu", width='stretch', disabled=resume_disabled):
            ok, msg = start_lstm_training(missing_symbols)
            if ok:
                st.success(f"{msg}: {', '.join(missing_symbols)}")
            else:
                st.warning(msg)
        if missing_symbols:
            st.caption(f"Con thieu LSTM: {', '.join(missing_symbols[:8])}{'...' if len(missing_symbols) > 8 else ''}")

    render_lstm_training_controls()

    @st.fragment
    def render_update_results_control():
        if st.button("🔄 Cập nhật kết quả", width='stretch'):
            updated = update_results(force=True)
            if updated:
                st.success(f"Đã cập nhật {updated} dự đoán")
            else:
                st.info("Không có dự đoán cần cập nhật")

    render_update_results_control()

render_health_alerts()
render_sticky_alerts()

# ===================== PERIOD CONFIG =====================
period_map = {"1D": 1, "1W": 7, "1M": 30, "3M": 90}
days = period_map[interval]
end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

# ===================== FUNCTIONS =====================
def get_news(symbol):
    try:
        articles = []
        feeds = [
            f"https://cafef.vn/tim-kiem.chn?keywords={symbol}&rss=1",
            "https://cafef.vn/thi-truong-chung-khoan.rss",
            "https://vnexpress.net/rss/kinh-doanh.rss",
            "https://tuoitre.vn/rss/kinh-te.rss",
            "https://thanhnien.vn/rss/tai-chinh-kinh-doanh.rss",
            "https://ndh.vn/rss/chung-khoan.rss",
        ]
        seen = set()
        for url in feeds:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    title = entry.get("title", "")
                    if title and title not in seen:
                        seen.add(title)
                        articles.append({
                            "title": title,
                            "summary": entry.get("summary", "")[:400],
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "source": feed.feed.get("title", "")
                        })
            except Exception:
                continue
        relevant = [a for a in articles if symbol.lower() in a["title"].lower() or symbol.lower() in a["summary"].lower()]
        other = [a for a in articles if a not in relevant]
        return (relevant + other)[:15]
    except Exception:
        return []

@st.cache_data(ttl=30)
def get_vnindex():
    try:
        df_vni = get_vnindex_data_st(years=0.1)
        if df_vni is not None and len(df_vni) >= 2:
            latest = df_vni.iloc[-1]["close"]
            prev = df_vni.iloc[-2]["close"]
            chg = ((latest - prev) / prev) * 100
            return latest, chg, float(df_vni.iloc[-1]["volume"])
        return None, None, None
    except Exception:
        return None, None, None

@st.cache_data(ttl=300)
def get_vnindex_history(days=90):
    try:
        df = get_vnindex_data_st(years=max(0.1, float(days) / 365))
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        cutoff = datetime.now() - timedelta(days=int(days))
        df = df[df["time"] >= cutoff]
        return df.sort_values("time").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

def calculate_adx(df, period=14):
    if df is None or len(df) < period + 2:
        return 0.0
    high = df["high"].astype(float) if "high" in df else df["close"].astype(float)
    low = df["low"].astype(float) if "low" in df else df["close"].astype(float)
    close = df["close"].astype(float)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.rolling(period).mean().iloc[-1]
    return float(adx) if not pd.isna(adx) else 0.0

def detect_market_regime(vnindex_data=None):
    df = vnindex_data if vnindex_data is not None else get_vnindex_history()
    if df is None or len(df) < 25:
        return {
            "regime": "UNKNOWN",
            "adx": 0.0,
            "return_20d": 0.0,
            "volatility_20d": 0.0,
            "recommendation": "Chưa đủ dữ liệu VN-Index để xác định regime",
        }
    close = df["close"].astype(float)
    adx = calculate_adx(df, period=14)
    ret_20d = (close.iloc[-1] / close.iloc[-20] - 1)
    vol_20d = close.pct_change().rolling(20).std().iloc[-1]
    if adx > 25 and ret_20d > 0.03:
        regime = "BULL_TREND"
    elif adx > 25 and ret_20d < -0.03:
        regime = "BEAR_TREND"
    elif vol_20d > 0.015:
        regime = "HIGH_VOL_RANGING"
    else:
        regime = "LOW_VOL_RANGING"
    recommendations = {
        "BULL_TREND": "Ưu tiên BUY, trailing stop 2.5 ATR",
        "BEAR_TREND": "Chỉ HOLD hoặc thoát, không mở mới",
        "HIGH_VOL_RANGING": "Giảm size 50%, stop loss chặt hơn",
        "LOW_VOL_RANGING": "Mean reversion: mua oversold, bán overbought",
    }
    return {
        "regime": regime,
        "adx": round(adx, 1),
        "return_20d": round(ret_20d * 100, 2),
        "volatility_20d": round(float(vol_20d) * 100, 2) if not pd.isna(vol_20d) else 0.0,
        "recommendation": recommendations[regime],
    }

def render_market_regime_card():
    regime = get_market_regime_cached()
    color = {
        "BULL_TREND": "#22c55e",
        "BEAR_TREND": "#f43f5e",
        "HIGH_VOL_RANGING": "#f59e0b",
        "LOW_VOL_RANGING": "#94a3b8",
    }.get(regime.get("regime"), "#94a3b8")
    st.markdown(
        f"""
        <div class="card" style="border-left:4px solid {color};padding:0.75rem 1rem;">
          <div style="display:flex;gap:0.8rem;align-items:center;justify-content:space-between;flex-wrap:wrap;">
            <div>
              <div class="sub-header">Market regime</div>
              <div style="color:{color};font-weight:900;font-size:1rem;">{regime.get('regime')}</div>
            </div>
            <div style="color:#cbd5e1;font-size:0.82rem;">
              ADX {regime.get('adx')} | 20D {regime.get('return_20d')}% | Vol {regime.get('volatility_20d')}%
            </div>
          </div>
          <div style="color:#92a1b6;font-size:0.8rem;margin-top:0.3rem;">{regime.get('recommendation')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_confluence_card(symbol):
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        df = fetch_symbol_history(symbol, start_date, end_date)
        if df is None or len(df) < 55:
            return
        lstm_result = ensemble_direction_predict(symbol, df)
        lstm_reliable = bool(lstm_result.get("reliable") and lstm_result.get("high_confidence"))
        indicators = build_indicator_snapshot(
            df,
            lstm_direction=lstm_result.get("direction") if lstm_reliable else 0,
            lstm_reliable=lstm_reliable,
        )
        confluence = calculate_confluence(indicators)
        score = int(confluence["confluence_score"])
        color = "#22c55e" if confluence["net_direction"] == "bullish" else "#f43f5e" if confluence["net_direction"] == "bearish" else "#f59e0b"
        breakdown = confluence["signal_breakdown"]
        st.markdown(
            f"""
            <div class="card" style="padding:0.75rem 1rem;">
              <div style="display:flex;justify-content:space-between;gap:1rem;align-items:center;">
                <div>
                  <div class="sub-header">Signal confluence</div>
                  <div style="font-weight:900;color:{color};">{symbol} {score}% · {confluence['net_direction']}</div>
                </div>
                <div style="color:#92a1b6;font-size:0.78rem;">Bull {breakdown['bullish']} | Bear {breakdown['bearish']} | Neutral {breakdown['neutral']}</div>
              </div>
              <div style="height:8px;background:#1f2937;border-radius:999px;overflow:hidden;margin-top:0.55rem;">
                <div style="height:8px;width:{score}%;background:{color};"></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        return

def parse_float_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d,\.\-]", "", str(value))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None

@st.cache_data(ttl=120)
def get_gold_price():
    try:
        resp = requests.get("https://api.metals.live/v1/spot/gold", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            latest_gold = data[-1]
            if isinstance(latest_gold, dict):
                price = latest_gold.get("price") or latest_gold.get("gold") or latest_gold.get("xau")
                return parse_float_value(price)
            if isinstance(latest_gold, list) and latest_gold:
                return parse_float_value(latest_gold[-1])
            return parse_float_value(latest_gold)
        if isinstance(data, dict):
            return parse_float_value(data.get("price") or data.get("gold") or data.get("xau"))
    except Exception:
        return None
    return None

@st.cache_data(ttl=300)
def get_vietcombank_usd_rate():
    try:
        resp = requests.get(
            "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.Trading.FX/fxrates.aspx",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            if not cells or not any(cell.upper().startswith("USD") for cell in cells[:2]):
                continue
            values = [parse_float_value(cell) for cell in cells]
            values = [v for v in values if v is not None]
            if not values:
                return None, None
            buy = values[1] if len(values) >= 3 else values[0]
            sell = values[-1]
            return buy, sell
    except Exception:
        return None, None
    return None, None

@st.cache_data(ttl=120)
def get_crypto_prices():
    ids = "bitcoin,ethereum,binancecoin,solana,ripple"
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        mapping = [
            ("BTC", "bitcoin"),
            ("ETH", "ethereum"),
            ("BNB", "binancecoin"),
            ("SOL", "solana"),
            ("XRP", "ripple"),
        ]
        rows = []
        for symbol_key, api_key in mapping:
            item = data.get(api_key, {})
            rows.append({
                "symbol": symbol_key,
                "price": parse_float_value(item.get("usd")),
                "change": parse_float_value(item.get("usd_24h_change"))
            })
        return rows
    except Exception:
        return []

def extract_close_series(df_market):
    if df_market is None or df_market.empty or "Close" not in df_market:
        return pd.Series(dtype=float)
    close = df_market["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.dropna().astype(float)

def calc_series_change(prices):
    clean = [float(p) for p in prices if p is not None and not pd.isna(p)]
    if len(clean) < 2 or clean[0] == 0:
        return None
    return (clean[-1] / clean[0] - 1) * 100

@st.cache_data(ttl=180)
def get_gold_market_data():
    try:
        ticker = yf.Ticker("GC=F")
        hist = ticker.history(period="7d", interval="1h")
        close = hist["Close"].dropna().astype(float)
        prices = close.tolist()
        current_price = prices[-1] if prices else None
        return {
            "symbol": "XAU/USD",
            "price": current_price,
            "change": ((prices[-1] - prices[0]) / prices[0] * 100) if len(prices) >= 2 and prices[0] else None,
            "series": prices
        }
    except Exception as e:
        print(f"[XAU/USD] yfinance GC=F failed: {type(e).__name__}: {e}")
        return {"symbol": "XAU/USD", "price": None, "change": None, "series": []}

@st.cache_data(ttl=300)
def get_usdvnd_market_data():
    try:
        df_fx = yf.download("USDVND=X", period="7d", interval="1d", progress=False, auto_adjust=False)
        prices = extract_close_series(df_fx).tolist()
        return {
            "symbol": "USD/VND",
            "price": prices[-1] if prices else None,
            "change": calc_series_change(prices),
            "series": prices
        }
    except Exception:
        return {"symbol": "USD/VND", "price": None, "change": None, "series": []}

@st.cache_data(ttl=180)
def get_crypto_market_chart(coin_id):
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=7",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        prices = [parse_float_value(item[1]) for item in data.get("prices", []) if len(item) >= 2]
        return [p for p in prices if p is not None]
    except Exception:
        return []

@st.cache_data(ttl=180)
def get_crypto_markets():
    mapping = [
        ("BTC", "bitcoin"),
        ("ETH", "ethereum"),
        ("BNB", "binancecoin"),
        ("SOL", "solana"),
        ("XRP", "ripple"),
    ]
    rows = []
    for symbol_key, coin_id in mapping:
        prices = get_crypto_market_chart(coin_id)
        rows.append({
            "symbol": symbol_key,
            "price": prices[-1] if prices else None,
            "change": calc_series_change(prices),
            "series": prices
        })
    return rows

def normalize_vn_gold_million(value):
    raw = str(value or "").strip()
    cleaned = re.sub(r"[^\d,\.\-]", "", raw)
    if cleaned.count(",") + cleaned.count(".") > 1:
        price = parse_float_value(re.sub(r"[,.]", "", cleaned))
    else:
        price = parse_float_value(cleaned)
    if price is None:
        return None
    if price > 1000000:
        return price / 1000000
    if price > 1000:
        return price / 1000
    return price

def find_gold_buy_sell_in_text(text):
    soup = BeautifulSoup(text or "", "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    if not rows:
        rows = [[line] for line in re.split(r"[\r\n]+", soup.get_text("\n", strip=True))]

    for cells in rows:
        joined = " ".join(cells)
        if "SJC" not in joined.upper():
            continue
        numbers = re.findall(r"\d[\d\.,]*", joined)
        values = [normalize_vn_gold_million(num) for num in numbers]
        values = [v for v in values if v is not None and v > 10]
        if len(values) >= 2:
            return values[-2], values[-1]
    return None, None

def find_hcm_sjc_buy_sell_in_table(text):
    soup = BeautifulSoup(text or "", "html.parser")
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        joined = " ".join(cells).upper()
        if ("HỒ CHÍ MINH" not in joined and "HO CHI MINH" not in joined and "TP.HCM" not in joined) or "SJC" not in joined:
            continue
        numbers = re.findall(r"\d[\d\.,]*", " ".join(cells))
        values = [normalize_vn_gold_million(num) for num in numbers]
        values = [v for v in values if v is not None and v > 10]
        if len(values) >= 2:
            return values[-2], values[-1]
    return None, None

def find_hcm_sjc_buy_sell_in_json(data):
    for node in iter_json_nodes(data):
        if not isinstance(node, dict):
            continue
        branch = str(node.get("BranchName", ""))
        type_name = str(node.get("TypeName", ""))
        branch_key = branch.upper()
        type_key = type_name.upper()
        if ("HỒ CHÍ MINH" not in branch_key and "HO CHI MINH" not in branch_key and "TP.HCM" not in branch_key):
            continue
        if "SJC" not in type_key or "1L" not in type_key:
            continue
        buy = normalize_vn_gold_million(node.get("BuyValue") or node.get("Buy"))
        sell = normalize_vn_gold_million(node.get("SellValue") or node.get("Sell"))
        if buy is not None and sell is not None:
            return buy, sell
    return None, None

def iter_json_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)

def find_btmc_sjc_buy_sell(data):
    if isinstance(data, dict) and "data_SJC" in data:
        sjc_data = data.get("data_SJC")
        if isinstance(sjc_data, str):
            buy, sell = find_gold_buy_sell_in_text(sjc_data)
            if buy is not None and sell is not None:
                return buy, sell
            numbers = re.findall(r"\d[\d\.,]*", sjc_data)
            values = [normalize_vn_gold_million(num) for num in numbers]
            values = [v for v in values if v is not None and v > 10]
            if len(values) >= 2:
                return values[-2], values[-1]
        if isinstance(sjc_data, (dict, list)):
            buy, sell = find_btmc_sjc_buy_sell(sjc_data)
            if buy is not None and sell is not None:
                return buy, sell

    buy_keys = ["buy", "buyprice", "mua", "giamua", "buy_price", "buyPrice"]
    sell_keys = ["sell", "sellprice", "ban", "giaban", "sell_price", "sellPrice"]
    for node in iter_json_nodes(data):
        text = " ".join(str(v) for v in node.values())
        if "SJC" not in text.upper() and not any(str(k).lower() == "data_sjc" for k in node.keys()):
            continue
        lowered = {str(k).lower(): v for k, v in node.items()}
        buy = next((normalize_vn_gold_million(lowered[k.lower()]) for k in buy_keys if k.lower() in lowered), None)
        sell = next((normalize_vn_gold_million(lowered[k.lower()]) for k in sell_keys if k.lower() in lowered), None)
        if buy is not None and sell is not None:
            return buy, sell
        numbers = re.findall(r"\d[\d\.,]*", text)
        values = [normalize_vn_gold_million(num) for num in numbers]
        values = [v for v in values if v is not None and v > 10]
        if len(values) >= 2:
            return values[-2], values[-1]
    return None, None

@st.cache_data(ttl=180)
def get_sjc_gold_market_data():
    buy = None
    sell = None
    source = ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        resp = requests.get("https://sjc.com.vn/bieu-do-gia-vang", headers=headers, timeout=10)
        resp.raise_for_status()
        buy, sell = find_hcm_sjc_buy_sell_in_table(resp.text)
        if buy is None or sell is None:
            service_headers = dict(headers)
            service_headers["Referer"] = "https://sjc.com.vn/bieu-do-gia-vang"
            service_headers["X-Requested-With"] = "XMLHttpRequest"
            today_vn = datetime.now().strftime("%d/%m/%Y")
            service_resp = requests.post(
                "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx",
                headers=service_headers,
                data={"method": "GetSJCGoldPriceByDate", "toDate": today_vn},
                timeout=10
            )
            service_resp.raise_for_status()
            buy, sell = find_hcm_sjc_buy_sell_in_json(service_resp.json())
        if buy is not None and sell is not None:
            source = "SJC"
        else:
            print("[SJC gold] SJC parsed no Ho Chi Minh SJC buy/sell")
    except Exception as e:
        print(f"[SJC gold] SJC failed: {type(e).__name__}: {e}")

    if buy is None or sell is None:
        try:
            resp = requests.get("https://www.24h.com.vn/gia-vang-hom-nay-c103.html", headers=headers, timeout=10)
            resp.raise_for_status()
            buy, sell = find_hcm_sjc_buy_sell_in_table(resp.text)
            if buy is None or sell is None:
                buy, sell = find_gold_buy_sell_in_text(resp.text)
            if buy is not None and sell is not None:
                source = "24h"
            else:
                print("[SJC gold] 24h parsed no SJC buy/sell")
        except Exception as e:
            print(f"[SJC gold] 24h failed: {type(e).__name__}: {e}")

    mid = (buy + sell) / 2 if buy is not None and sell is not None else None
    return {
        "symbol": "Vàng SJC",
        "price": mid,
        "buy": buy,
        "sell": sell,
        "change": None,
        "series": [],
        "source": source
    }

def format_market_value(symbol_key, price):
    if price is None:
        return "N/A"
    if symbol_key == "Vàng SJC":
        return f"{price:,.2f} triệu"
    if symbol_key == "USD/VND":
        return f"{price:,.0f}"
    if symbol_key == "XRP":
        return f"${price:,.4f}"
    return f"${price:,.2f}"

def render_sparkline(prices, change):
    clean = [float(p) for p in prices if p is not None and not pd.isna(p)]
    color = "#22c55e" if (change or 0) >= 0 else "#f43f5e"
    fig = go.Figure()
    if clean:
        fig.add_trace(go.Scatter(
            x=list(range(len(clean))),
            y=clean,
            mode="lines",
            line=dict(color=color, width=2.2),
            hoverinfo="skip"
        ))
    fig.update_layout(
        height=70,
        margin=dict(l=0, r=0, t=4, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=False
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

def render_world_market_card(asset):
    change = asset.get("change")
    cls = "metric-up" if change is not None and change >= 0 else "metric-down" if change is not None else "metric-neutral"
    arrow = "▲" if change is not None and change >= 0 else "▼" if change is not None else "•"
    change_text = f"{arrow} {change:+.2f}% 7 ngày" if change is not None else "Đang cập nhật"
    if asset.get("symbol") == "Vàng SJC":
        buy = asset.get("buy")
        sell = asset.get("sell")
        buy_text = f"{buy:,.2f}" if buy is not None else "N/A"
        sell_text = f"{sell:,.2f}" if sell is not None else "N/A"
        source_text = f"Nguồn: {asset.get('source')}" if asset.get("source") else "Fallback N/A nếu API lỗi"
        with st.container(border=True):
            st.markdown(
                f'<div class="label">Vàng SJC</div>'
                f'<div class="value" style="font-size:1.05rem;line-height:1.25;">'
                f'<span style="color:#22c55e;font-weight:900;">Mua {buy_text}</span>'
                f'<span style="color:#94a3b8;"> / </span>'
                f'<span style="color:#f43f5e;font-weight:900;">Bán {sell_text}</span>'
                f'</div>'
                f'<div class="delta metric-neutral">triệu VNĐ/lượng · {source_text}</div>',
                unsafe_allow_html=True
            )
            render_sparkline(asset.get("series", []), change)
        return
    with st.container(border=True):
        st.markdown(
            f'<div class="label">{asset["symbol"]}</div>'
            f'<div class="value">{format_market_value(asset["symbol"], asset.get("price"))}</div>'
            f'<div class="delta {cls}">{change_text}</div>',
            unsafe_allow_html=True
        )
        render_sparkline(asset.get("series", []), change)

def _render_strip_item(label, value, cls="neutral"):
    st.markdown(
        f'<div class="ticker-strip-item">'
        f'<div class="ticker-strip-label">{label}</div>'
        f'<div class="ticker-strip-value {cls}">{value}</div>'
        f'</div>',
        unsafe_allow_html=True
    )

def render_world_market_strip():
    now = datetime.now()
    is_open = now.weekday() < 5 and 9 <= now.hour < 15
    vni_price, vni_chg, _ = get_vnindex()
    gold = get_gold_market_data()
    usd = get_usdvnd_market_data()
    crypto = get_crypto_markets()
    btc = next((item for item in crypto if item.get("symbol") == "BTC"), {"price": None, "change": None})
    eth = next((item for item in crypto if item.get("symbol") == "ETH"), {"price": None, "change": None})

    cols = st.columns(6)
    with cols[0]:
        vni_text = "N/A" if vni_price is None else f"{vni_price:,.1f} {vni_chg:+.2f}%"
        _render_strip_item("VN-Index", vni_text, "up" if (vni_chg or 0) >= 0 else "down")
    with cols[1]:
        usd_price = usd.get("price")
        usd_change = usd.get("change")
        usd_text = "N/A" if usd_price is None else f"{usd_price:,.0f} {usd_change:+.2f}%" if usd_change is not None else f"{usd_price:,.0f}"
        _render_strip_item("USD/VND", usd_text, "up" if (usd_change or 0) >= 0 else "down")
    with cols[2]:
        gold_price = gold.get("price")
        gold_change = gold.get("change")
        gold_text = "N/A" if gold_price is None else f"{gold_price:,.2f} {gold_change:+.2f}%" if gold_change is not None else f"{gold_price:,.2f}"
        _render_strip_item("Gold", gold_text, "up" if (gold_change or 0) >= 0 else "neutral")
    with cols[3]:
        btc_price = btc.get("price")
        btc_change = btc.get("change")
        btc_text = "N/A" if btc_price is None else f"${btc_price:,.2f} {btc_change:+.2f}%" if btc_change is not None else f"${btc_price:,.2f}"
        _render_strip_item("BTC", btc_text, "up" if (btc_change or 0) >= 0 else "down")
    with cols[4]:
        eth_price = eth.get("price")
        eth_change = eth.get("change")
        eth_text = "N/A" if eth_price is None else f"${eth_price:,.2f} {eth_change:+.2f}%" if eth_change is not None else f"${eth_price:,.2f}"
        _render_strip_item("ETH", eth_text, "up" if (eth_change or 0) >= 0 else "down")
    with cols[5]:
        status_color = "#22c55e" if is_open else "#f43f5e"
        status_text = "OPEN" if is_open else "CLOSED"
        st.markdown(
            f'<div class="ticker-strip-item">'
            f'<div class="ticker-strip-label">Market</div>'
            f'<div class="ticker-strip-value" style="color:{status_color};">{status_text}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
def render_world_market_section():
    st.markdown('<div class="section-title" style="margin:0.2rem 0 0.5rem;">Thị trường thế giới</div>', unsafe_allow_html=True)
    markets = [get_gold_market_data(), get_sjc_gold_market_data(), get_usdvnd_market_data()] + get_crypto_markets()
    first_row = st.columns(4)
    for idx, asset in enumerate(markets[:4]):
        with first_row[idx]:
            render_world_market_card(asset)
    second_row = st.columns(4)
    for idx, asset in enumerate(markets[4:8]):
        with second_row[idx]:
            render_world_market_card(asset)
    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

def get_financials(symbol):
    try:
        fund = get_finance_client(symbol, source='VCI')
        income = fund.income_statement(period='year', lang='vi')
        if income is not None and len(income) > 0:
            year_cols = sorted([c for c in income.columns if str(c).isdigit()], reverse=True)
            year_col = year_cols[0]
            def get_item(item_id):
                row = income[income['item_id'] == item_id]
                return float(row[year_col].values[0]) if len(row) > 0 else None
            return {
                "year": year_col,
                "revenue": get_item('net_sales'),
                "profit": get_item('net_profit_loss_after_tax'),
                "gross_profit": get_item('gross_profit'),
                "eps": get_item('eps_basic_vnd'),
            }
        return {}
    except Exception:
        return {}

def ask_ollama(prompt, timeout=OLLAMA_TIMEOUT_SECONDS, num_predict=None):
    # OLD: direct Ollama HTTP call
    result = call_llm(prompt=prompt, max_tokens=int(num_predict or 1000))
    if result.get("success"):
        return result.get("content") or ""
    return f"Lỗi kết nối LLM Router: {result.get('error')}"

def parse_json_response(text, default):
    try:
        return json.loads(str(text or "").strip())
    except Exception:
        pass
    match = re.search(r"\{.*\}", str(text or ""), re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return default.copy() if isinstance(default, dict) else default

def load_history():
    return _safe_read_json(HISTORY_FILE, default=[])

def save_history(history):
    _safe_write_json(HISTORY_FILE, history)

def parse_history_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            pass
    return None

def normalize_prediction(value):
    text = str(value or "").upper()
    original = str(value or "")
    if "MUA" in text:
        return "MUA"
    if "BÁN" in text or "BAN" in text or "BÃ" in text:
        return "BAN"
    if "GIỮ" in text or "GIU" in text or "GIÁ" in text or "GIá" in original or "GIÃ" in text:
        return "GIU"
    return "N/A"

def latest_close_price(symbol, end_dt=None, lookback_days=10):
    end_dt = end_dt or datetime.now()
    try:
        from data_fetcher import get_stock_data_cached
        df_check = get_stock_data_cached(symbol, years=1)
    except Exception:
        return None
    if df_check is None or len(df_check) == 0:
        return None
    cutoff = end_dt - timedelta(days=lookback_days)
    df_check = df_check[df_check["time"] >= pd.to_datetime(cutoff)]
    if df_check.empty:
        return None
    return float(df_check.iloc[-1]["close"])

def evaluate_prediction(record, current):
    entry = float(record.get("price_at_prediction", 0) or 0)
    if entry <= 0 or current is None:
        return None
    # Guard against bad/mis-scaled source data (e.g. a fallback source returning
    # a price 1000x off VCI's "nghìn đồng" convention) poisoning the accuracy
    # stats with fake outlier returns. Horizons here span up to a few months, so
    # allow a wide but still sane band; leave unresolved (None) if it's outside.
    ratio = current / entry
    if not (0.2 <= ratio <= 5.0):
        return None
    pred = normalize_prediction(record.get("prediction"))
    pct_chg = ((current - entry) / entry) * 100
    if pred == "MUA":
        return current > entry
    if pred == "BAN":
        return current < entry
    if pred == "GIU":
        return abs(pct_chg) < 3
    return None

def update_results(force=False):
    now = datetime.now()
    if not force and now.hour < 15:
        return 0
    set_system_status("updating_results", "Đang cập nhật kết quả dự đoán", "So sánh dự đoán cũ với giá thực tế mới nhất")
    history = load_history()
    updated = 0
    today = now.date()
    price_cache = {}
    for record in history:
        if record.get("correct") is None:
            predicted_at = parse_history_date(record.get("date"))
            if not predicted_at:
                continue
            if not force and predicted_at.date() >= today:
                continue
            try:
                sym = record["symbol"]
                if sym not in price_cache:
                    price_cache[sym] = latest_close_price(sym, now)
                current = price_cache[sym]
                if current is not None:
                    record["actual_price"] = current
                    record["evaluated_at"] = now.strftime("%Y-%m-%d %H:%M")
                    record["correct"] = evaluate_prediction(record, current)
                    updated += 1
            except Exception:
                pass
    save_history(history)
    if updated:
        set_system_status("idle", "Đã cập nhật kết quả dự đoán", f"Đã chấm {updated} dự đoán")
    else:
        set_system_status("idle", "Không có dự đoán cần cập nhật", "Đang chờ tác vụ tiếp theo")
    return updated

def auto_update_results_after_15h():
    now = datetime.now()
    key = now.strftime("%Y-%m-%d")
    if st.session_state.get("history_auto_updated_v2") == key:
        return
    update_results()
    st.session_state["history_auto_updated_v2"] = key

def get_accuracy_stats():
    history = load_history()
    evaluated = [h for h in history if h.get("correct") is not None]
    if not evaluated:
        return None
    correct = sum(1 for h in evaluated if h.get("correct"))
    return {"total": len(evaluated), "correct": correct, "accuracy": correct / len(evaluated) * 100}

def get_accuracy_over_time():
    history = load_history()
    rows = []
    for h in history:
        if h.get("correct") is None:
            continue
        dt = parse_history_date(h.get("evaluated_at")) or parse_history_date(h.get("date"))
        if dt:
            rows.append({"date": dt.date(), "correct": bool(h.get("correct"))})
    if not rows:
        return pd.DataFrame()
    df_acc = pd.DataFrame(rows).sort_values("date")
    daily = df_acc.groupby("date").agg(total=("correct", "count"), correct=("correct", "sum")).reset_index()
    daily["accuracy"] = daily["correct"] / daily["total"] * 100
    daily["rolling_accuracy"] = daily["correct"].cumsum() / daily["total"].cumsum() * 100
    return daily

def summarize_learning_patterns(evaluated):
    wrong = [h for h in evaluated if h.get("correct") is False]
    if not wrong:
        return "20 du doan gan nhat chua co pattern sai ro rang."
    by_action = {}
    by_symbol = {}
    for h in wrong:
        action = normalize_prediction(h.get("prediction"))
        symbol_key = h.get("symbol", "N/A")
        by_action[action] = by_action.get(action, 0) + 1
        by_symbol[symbol_key] = by_symbol.get(symbol_key, 0) + 1
    action_notes = ", ".join([f"{k}: {v} lan sai" for k, v in sorted(by_action.items(), key=lambda x: x[1], reverse=True)])
    symbol_notes = ", ".join([f"{k}: {v} lan sai" for k, v in sorted(by_symbol.items(), key=lambda x: x[1], reverse=True)[:5]])
    return (
        "Pattern sai can tranh lap lai:\n"
        f"- Theo khuyen nghi: {action_notes}\n"
        f"- Theo ma co phieu: {symbol_notes}\n"
        "- Neu gap dieu kien tuong tu, hay ha do tu tin, doi them xac nhan tu RSI/MACD/volume va VN-Index."
    )

def build_prompt_tuning_context():
    history = load_history()
    dates = [parse_history_date(h.get("date")) for h in history]
    dates = [d for d in dates if d]
    stats = get_accuracy_stats()
    if not dates or not stats:
        return ""
    if (max(dates).date() - min(dates).date()).days < 30:
        return ""
    return (
        "\nPROMPT TU HOC SAU 30 NGAY:\n"
        f"- Do chinh xac lich su: {stats['accuracy']:.1f}% ({stats['correct']}/{stats['total']}).\n"
        "- Uu tien cac tin hieu da dung trong lich su, giam trong so cac pattern sai ben duoi.\n"
        "- Neu khong co loi the ro, chon GIU thay vi MUA/BAN.\n"
    )

def build_learning_context():
    history = load_history()
    evaluated = [h for h in history if h.get("correct") is not None][-20:]
    if not evaluated:
        return ""
    lines = []
    for h in evaluated:
        status = "DUNG" if h.get("correct") else "SAI"
        lines.append(f"- {h.get('date')} | {h.get('symbol')} | Gia: {h.get('price_at_prediction')} | Du doan: {h.get('prediction')} | Ket qua: {status}")
    return "\n".join(lines) + "\n\n" + summarize_learning_patterns(evaluated) + build_prompt_tuning_context()

def latest_ai_verdicts():
    verdicts = {}
    history = sorted(
        load_history(),
        key=lambda h: parse_history_date(h.get("date")) or datetime.min
    )
    for item in history:
        sym = str(item.get("symbol", "")).upper()
        if sym:
            verdicts[sym] = normalize_prediction(item.get("prediction"))
    return verdicts

def verdict_label(value):
    mapping = {"MUA": "MUA", "BAN": "BÁN", "GIU": "GIỮ"}
    return mapping.get(value, "N/A")

def safe_float(value, default=0.0):
    try:
        text = str(value or "").replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else default
    except Exception:
        return default

def safe_sma(series, window):
    """Return last SMA value or None when there is insufficient data."""
    if series is None:
        return None
    try:
        if len(series) < window:
            return None
        value = series.rolling(window).mean().iloc[-1]
        return None if pd.isna(value) else round(float(value), 2)
    except Exception:
        return None

def fmt_optional_number(value, digits=2, blank=""):
    if value is None:
        return blank
    try:
        if pd.isna(value):
            return blank
    except Exception:
        pass
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return blank

def render_dark_table(df, key=""):
    """Render a compact dark table for dashboard status views."""
    if df is None or df.empty:
        st.info("Không có dữ liệu.")
        return

    def cell_html(val):
        text = html.escape("" if val is None or pd.isna(val) else str(val))
        raw = str(val).strip() if val is not None and not pd.isna(val) else ""
        raw_upper = raw.upper()
        if raw_upper in {"YES", "✅", "TRUE"}:
            return '<span style="color:#22c55e;font-weight:700;">YES</span>'
        if raw_upper in {"NO", "❌", "FALSE"}:
            return '<span style="color:#f43f5e;font-weight:700;">NO</span>'
        if raw_upper in {"MUA", "BULLISH", "TANG", "TĂNG", "TĂNG MẠNH", "TANG MANH"}:
            return f'<span style="color:#22c55e;font-weight:700;">{text}</span>'
        if raw_upper in {"BÁN", "BAN", "BEARISH", "GIẢM", "GIAM", "GIẢM MẠNH", "GIAM MANH"}:
            return f'<span style="color:#f43f5e;font-weight:700;">{text}</span>'
        if raw_upper in {"GIỮ", "GIU"}:
            return f'<span style="color:#f59e0b;font-weight:700;">{text}</span>'
        return text

    header_html = "".join(
        f'<th style="padding:8px 12px;background:#1e293b;color:#94a3b8;'
        f'font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;'
        f'border-bottom:1px solid #334155;white-space:nowrap;">{html.escape(str(col))}</th>'
        for col in df.columns
    )
    row_html = []
    for _, row in df.iterrows():
        cells = "".join(
            f'<td style="padding:8px 12px;border-bottom:1px solid #1e293b;'
            f'color:#f8fafc;white-space:nowrap;">{cell_html(val)}</td>'
            for val in row.tolist()
        )
        row_html.append(f"<tr>{cells}</tr>")

    st.markdown(
        f"""
        <div class="two-stage-table" id="{key}">
          <div style="overflow-x:auto;border:1px solid #1e293b;border-radius:8px;background:#0f172a;">
            <table style="width:100%;border-collapse:collapse;background:#0f172a;">
              <thead><tr>{header_html}</tr></thead>
              <tbody>{''.join(row_html)}</tbody>
            </table>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_two_stage_buy_actions(stage2_results):
    """Render manual buy buttons under the two-stage table."""
    if not stage2_results:
        return

    st.caption("Bấm Manual BUY bên dưới để mua nhanh theo từng mã.")
    for idx, row in enumerate(stage2_results):
        ticker = str(row.get("ticker", "-")).upper()
        llm = row.get("llm") or {}
        ensemble = row.get("ensemble") or {}
        tradeable = bool(row.get("tradeable"))
        row_cols = st.columns([1.0, 1.0, 1.3, 1.2])
        row_cols[0].markdown(
            f"<div style='padding:0.45rem 0.55rem;background:#0f172a;color:#f8fafc;border:1px solid #1e293b;border-radius:6px;'>"
            f"<b>{html.escape(ticker)}</b></div>",
            unsafe_allow_html=True,
        )
        row_cols[1].markdown(
            f"<div style='padding:0.45rem 0.55rem;background:#0f172a;color:#cbd5e1;border:1px solid #1e293b;border-radius:6px;'>"
            f"{html.escape(str(ensemble.get('signal', 'N/A')))} | {html.escape(str(llm.get('action', 'skip')))}"
            f"</div>",
            unsafe_allow_html=True,
        )
        row_cols[2].markdown(
            f"<div style='padding:0.45rem 0.55rem;background:#0f172a;color:{'#22c55e' if tradeable else '#f59e0b'};"
            f"border:1px solid #1e293b;border-radius:6px;text-align:center;'>"
            f"{'Eligible for session horizon' if tradeable else 'Not eligible'}"
            f"</div>",
            unsafe_allow_html=True,
        )
        if row_cols[3].button("Manual BUY", key=f"two_stage_manual_buy_{ticker}_{idx}", width='stretch'):
            try:
                from auto_trader import buy_position

                ok, msg = buy_position(ticker, reason="Manual BUY từ Two-Stage Analysis")
                if ok:
                    st.success(msg)
                else:
                    st.warning(msg)
            except Exception as exc:
                st.error(f"Không thể mua {ticker}: {exc}")


def render_debate_result(ticker_result):
    """Hiển thị Bull vs Bear debate result nếu analysis đã có debate."""
    if not isinstance(ticker_result, dict):
        return

    llm = ticker_result.get("llm") or {}
    bull_case = ticker_result.get("bull_case") or {}
    bear_case = ticker_result.get("bear_case") or {}
    if not ticker_result.get("debate") or (not llm and not bull_case and not bear_case):
        return

    bull_summary = bull_case.get("summary") or llm.get("bull_summary")
    bear_summary = bear_case.get("summary") or llm.get("bear_summary")
    if not bull_summary and not bear_summary:
        return

    st.markdown("**Bull vs Bear Debate**")
    col_bull, col_bear = st.columns(2)

    with col_bull:
        st.markdown(
            f'<div style="border-left:3px solid #22C55E;padding:0.6rem 0.75rem;background:#0F172A;">'
            f'<div style="color:#22C55E;font-size:0.75rem;font-weight:700;text-transform:uppercase;">Bull case</div>'
            f'<div style="color:#F8FAFC;font-size:0.85rem;line-height:1.5;">{bull_summary or "N/A"}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_bear:
        st.markdown(
            f'<div style="border-left:3px solid #F43F5E;padding:0.6rem 0.75rem;background:#0F172A;">'
            f'<div style="color:#F43F5E;font-size:0.75rem;font-weight:700;text-transform:uppercase;">Bear case</div>'
            f'<div style="color:#F8FAFC;font-size:0.85rem;line-height:1.5;">{bear_summary or "N/A"}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    action = str(llm.get("action", "GIỮ"))
    agreed_with = str(llm.get("agreed_with", "neither"))
    rr = llm.get("risk_reward")
    action_color = {"MUA": "#22C55E", "BÁN": "#F43F5E", "GIỮ": "#F59E0B"}.get(action, "#94A3B8")
    rr_text = f" | R/R {float(rr):.1f}x" if isinstance(rr, (int, float)) else ""
    st.markdown(
        f'<div style="margin-top:0.5rem;padding:0.55rem 0.75rem;background:#1E293B;border-radius:6px;">'
        f'<span style="color:#94A3B8;font-size:0.75rem;">PM verdict: </span>'
        f'<span style="color:{action_color};font-weight:700;">{action}</span>'
        f'<span style="color:#94A3B8;font-size:0.75rem;"> | đồng ý: {agreed_with}{rr_text}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

def parse_confidence(result):
    patterns = [
        r"tin cậy.*?(\d{1,3})\s*%",
        r"confidence.*?(\d{1,3})\s*%",
        r"độ tự tin.*?(\d{1,3})\s*%",
        r"do tu tin.*?(\d{1,3})\s*%",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(result or ""), re.IGNORECASE)
        if match:
            return max(0, min(100, int(match.group(1))))
    return None

def parse_ai_recommendation(result):
    text = str(result or "")
    patterns = [
        r"(?:Khuyến nghị|Khuyen nghi|Recommendation|Recommend).*?(MUA|BÁN|BAN|GIỮ|GIU)",
        r"\*\*(?:Khuyến nghị|Khuyen nghi)\*\*.*?(MUA|BÁN|BAN|GIỮ|GIU)",
        r"\b(MUA|BÁN|BAN|GIỮ|GIU)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return verdict_label(normalize_prediction(match.group(1)))
    return "N/A"

def parse_ai_number(result, labels):
    text = str(result or "")
    for label in labels:
        match = re.search(label + r".*?(\d[\d,\.]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).replace(",", "")
    return "0"

def compute_signal_scores(close, latest_row):
    sma20 = safe_sma(close, 20)
    sma50 = safe_sma(close, 50)
    ema12 = close.ewm(span=12).mean().iloc[-1]
    ema26 = close.ewm(span=26).mean().iloc[-1]
    macd = ema12 - ema26
    rsi_delta = close.diff()
    gain = rsi_delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-rsi_delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50
    if pd.isna(rsi):
        rsi = 50
    if pd.isna(macd):
        macd = 0
    if sma20 is None or sma50 is None:
        trend_score = 50
    else:
        trend_score = 80 if latest_row["close"] > sma20 > sma50 else 25 if latest_row["close"] < sma20 < sma50 else 50
    momentum_score = 75 if macd > 0 and 45 <= rsi <= 70 else 35 if macd < 0 or rsi > 78 or rsi < 30 else 55
    risk_score = 30 if rsi > 78 else 70 if 40 <= rsi <= 65 else 50
    return {
        "trend_score": int(trend_score),
        "momentum_score": int(momentum_score),
        "risk_score": int(risk_score),
        "rsi": float(rsi),
        "macd": float(macd),
        "sma20": float(sma20) if sma20 is not None else None,
        "sma50": float(sma50) if sma50 is not None else None,
    }

def load_lstm_validation(symbol):
    path = os.path.join(MODELS_DIR, f"{str(symbol).upper()}_validation.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}

def compute_rsi_normalized(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return ((100 - (100 / (1 + rs))).fillna(50)) / 100

def build_lstm_direction_features(df):
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
    df["rsi"] = compute_rsi_normalized(df["close"], 14)
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_position"] = ((df["close"] - (bb_mid - 2 * bb_std)) / (4 * bb_std)).clip(0, 1)
    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd_norm"] = (ema12 - ema26) / df["close"]
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr_norm"] = tr.rolling(14).mean() / df["close"]
    feature_cols = [
        "return_1d", "return_3d", "return_5d",
        "high_low_range", "gap_open", "upper_shadow", "lower_shadow",
        "sma_ratio", "volume_ratio", "rsi", "bb_position", "macd_norm", "atr_norm",
    ]
    df = df.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    return df, feature_cols

def build_indicator_snapshot(df, lstm_direction=0, lstm_reliable=False):
    close = df["close"].astype(float)
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    sma20 = safe_sma(close, 20)
    sma50 = safe_sma(close, 50)
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9).mean().iloc[-1]
    rsi_delta = close.diff()
    gain = rsi_delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-rsi_delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi_value = 100 - (100 / (1 + gain / loss)) if loss and not pd.isna(loss) else 50
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid.iloc[-1] + 2 * bb_std.iloc[-1]
    bb_lower = bb_mid.iloc[-1] - 2 * bb_std.iloc[-1]
    volume_ma20 = df["volume"].astype(float).rolling(20).mean().iloc[-1]
    volume_ratio = float(latest["volume"]) / volume_ma20 if volume_ma20 and not pd.isna(volume_ma20) else 1.0
    price_change = ((float(latest["close"]) - float(prev["close"])) / float(prev["close"])) * 100 if float(prev["close"]) else 0
    return {
        "close": float(latest["close"]),
        "price_change": float(price_change),
        "volume": float(latest["volume"]),
        "volume_ratio": float(volume_ratio),
        "rsi": float(rsi_value) if not pd.isna(rsi_value) else 50.0,
        "macd": float(macd_line.iloc[-1]) if not pd.isna(macd_line.iloc[-1]) else 0.0,
        "macd_signal": float(macd_signal) if not pd.isna(macd_signal) else 0.0,
        "sma20": float(sma20) if sma20 is not None else None,
        "sma50": float(sma50) if sma50 is not None else None,
        "bb_upper": float(bb_upper) if not pd.isna(bb_upper) else float(latest["close"]),
        "bb_lower": float(bb_lower) if not pd.isna(bb_lower) else float(latest["close"]),
        "lstm_reliable": bool(lstm_reliable),
        "lstm_direction": int(lstm_direction or 0),
    }

_CONFLUENCE_WEIGHTS = {
    "rsi": 0.15,
    "macd": 0.20,
    "sma_cross": 0.25,
    "volume": 0.20,
    "bollinger": 0.10,
    "lstm": 0.10,
}

def calculate_confluence(ticker_data):
    """Weighted confluence — mỗi signal có trọng số khác nhau."""
    signals = {}
    weighted_score = 0.0

    rsi = float(ticker_data.get("rsi", 50))
    signals["rsi"] = 1 if rsi < 35 else -1 if rsi > 65 else 0
    weighted_score += signals["rsi"] * _CONFLUENCE_WEIGHTS["rsi"]

    macd_bull = float(ticker_data.get("macd", 0)) > float(ticker_data.get("macd_signal", 0))
    signals["macd"] = 1 if macd_bull else -1
    weighted_score += signals["macd"] * _CONFLUENCE_WEIGHTS["macd"]

    sma20 = ticker_data.get("sma20")
    sma50 = ticker_data.get("sma50")
    if sma20 is None or sma50 is None:
        signals["sma_cross"] = 0
    else:
        sma_bull = float(sma20) > float(sma50)
        signals["sma_cross"] = 1 if sma_bull else -1
        weighted_score += signals["sma_cross"] * _CONFLUENCE_WEIGHTS["sma_cross"]

    vol_ratio = float(ticker_data.get("volume_ratio", 1.0))
    price_up = float(ticker_data.get("price_change", 0)) > 0
    if vol_ratio > 1.2 and price_up:
        signals["volume"] = 1
    elif vol_ratio > 1.2 and not price_up:
        signals["volume"] = -1
    else:
        signals["volume"] = 0
    weighted_score += signals["volume"] * _CONFLUENCE_WEIGHTS["volume"]

    close_value = float(ticker_data.get("close", 0))
    bb_lower = float(ticker_data.get("bb_lower", close_value))
    bb_upper = float(ticker_data.get("bb_upper", close_value))
    bb_width = bb_upper - bb_lower
    bb_pos = (close_value - bb_lower) / bb_width if bb_width else 0.5
    signals["bollinger"] = 1 if bb_pos < 0.2 else -1 if bb_pos > 0.8 else 0
    weighted_score += signals["bollinger"] * _CONFLUENCE_WEIGHTS["bollinger"]

    if ticker_data.get("lstm_reliable") and ticker_data.get("lstm_direction"):
        signals["lstm"] = int(ticker_data["lstm_direction"])
        weighted_score += signals["lstm"] * _CONFLUENCE_WEIGHTS["lstm"]

    if ticker_data.get("sentiment_signal"):
        signals["sentiment"] = int(ticker_data["sentiment_signal"])
        weighted_score += signals["sentiment"] * 0.05

    confluence_pct = int((weighted_score + 1) / 2 * 100)
    confluence_pct = max(0, min(100, confluence_pct))

    bullish_count = sum(1 for v in signals.values() if v == 1)
    bearish_count = sum(1 for v in signals.values() if v == -1)
    neutral_count = sum(1 for v in signals.values() if v == 0)
    return {
        "confluence_score": confluence_pct,
        "net_direction": "bullish" if weighted_score > 0.1 else "bearish" if weighted_score < -0.1 else "neutral",
        "weighted_score": round(weighted_score, 3),
        "signal_breakdown": {
            "bullish": bullish_count,
            "bearish": bearish_count,
            "neutral": neutral_count,
        },
        "signals": signals,
    }

def calculate_atr_from_df(df, period=14):
    if df is None or len(df) < period + 1:
        return None
    high = df["high"].astype(float) if "high" in df else df["close"].astype(float)
    low = df["low"].astype(float) if "low" in df else df["close"].astype(float)
    close = df["close"].astype(float)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not pd.isna(atr) else None

def latest_predictions_by_symbol():
    latest = {}
    history = sorted(load_history(), key=lambda h: parse_history_date(h.get("date")) or datetime.min)
    for item in history:
        sym = str(item.get("symbol", "")).upper()
        if sym:
            latest[sym] = item
    return latest

def symbol_accuracy_map():
    stats = {}
    for item in load_history():
        if item.get("correct") is None:
            continue
        sym = str(item.get("symbol", "")).upper()
        if not sym:
            continue
        bucket = stats.setdefault(sym, {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += 1 if item.get("correct") else 0
    for sym, bucket in stats.items():
        bucket["accuracy"] = bucket["correct"] / bucket["total"] * 100 if bucket["total"] else None
    return stats

def action_accuracy_map():
    stats = {}
    for item in load_history():
        if item.get("correct") is None:
            continue
        action = normalize_prediction(item.get("prediction"))
        bucket = stats.setdefault(action, {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += 1 if item.get("correct") else 0
    for action, bucket in stats.items():
        bucket["accuracy"] = bucket["correct"] / bucket["total"] * 100 if bucket["total"] else None
    return stats

def build_symbol_learning_context(symbol):
    symbol = str(symbol or "").upper()
    history = [h for h in load_history() if str(h.get("symbol", "")).upper() == symbol and h.get("correct") is not None]
    if not history:
        return ""
    recent = history[-8:]
    lines = []
    wrong_reasons = []
    for h in recent:
        status = "DUNG" if h.get("correct") else "SAI"
        lines.append(f"- {h.get('date')} | {h.get('prediction')} | gia vao {h.get('price_at_prediction')} | ket qua {status}")
        if h.get("correct") is False:
            wrong_reasons.append(normalize_prediction(h.get("prediction")))
    note = ""
    if wrong_reasons:
        note = f"\nCan canh giac: {symbol} tung sai voi khuyen nghi {', '.join(wrong_reasons[-4:])}."
    return "\nLICH SU RIENG CUA MA NAY:\n" + "\n".join(lines) + note + "\n"

def save_prediction(symbol, price, prediction, target, stoploss, timeframe, confidence=None, signal_scores=None, ai_result=None, **extra):
    history = load_history()
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = {
        "date": now_text,
        "symbol": symbol,
        "ticker": symbol,
        "price_at_prediction": price,
        "prediction": prediction,
        "action": prediction,
        "target": target,
        "stoploss": stoploss,
        "timeframe": timeframe,
        "confidence": confidence,
        "source": "rule_based",
        "outcome": None,
        "signal_scores": signal_scores or {},
        "ai_result": ai_result,
        "actual_price": None,
        "correct": None
    }
    row.update(extra)
    row["ticker"] = row.get("ticker") or row.get("symbol")
    row["action"] = row.get("action") or row.get("prediction")
    row_source = str(row.get("source") or "rule_based")
    today_key = now_text[:10]
    replaced = False
    for idx, existing in enumerate(history):
        existing_ticker = str(existing.get("ticker") or existing.get("symbol") or "").upper()
        existing_date = str(existing.get("date") or "")[:10]
        existing_source = str(existing.get("source") or "rule_based")
        if existing_ticker == str(symbol).upper() and existing_date == today_key and existing_source == row_source:
            history[idx] = row
            replaced = True
            break
    if not replaced:
        history.append(row)
    save_history(history)

def target_stoploss_alerts(symbols, max_items=12):
    latest_records = latest_predictions_by_symbol()
    alerts = []
    for sym in symbols:
        record = latest_records.get(str(sym).upper())
        if not record:
            continue
        try:
            current = latest_close_price(sym)
            if current is None:
                continue
            target = safe_float(record.get("target"))
            stoploss = safe_float(record.get("stoploss"))
            pred = normalize_prediction(record.get("prediction"))
            if target > 0 and pred == "MUA" and current >= target:
                alerts.append({"Mã": sym, "Loại": "Chạm target", "Giá": current, "Mốc": target})
            elif stoploss > 0 and pred == "MUA" and current <= stoploss:
                alerts.append({"Mã": sym, "Loại": "Chạm stoploss", "Giá": current, "Mốc": stoploss})
            elif stoploss > 0 and pred == "BAN" and current >= stoploss:
                alerts.append({"Mã": sym, "Loại": "Bán sai hướng", "Giá": current, "Mốc": stoploss})
        except Exception:
            continue
    return alerts[:max_items]

@st.cache_data(ttl=300, show_spinner=False)
def build_position_snapshot(symbol, buy_price=0, stop_loss=0, take_profit=0):
    symbol = str(symbol).upper().strip()
    if not symbol:
        return None

    try:
        from data_fetcher import get_stock_data_cached

        df = get_stock_data_cached(symbol, years=0.5)
    except Exception:
        df = None

    if df is None or len(df) < 20:
        return None

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    latest = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else latest
    change_pct = ((latest - prev) / prev) * 100 if prev else 0.0

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi_series = 100 - 100 / (1 + gain / (loss + 1e-9))
    rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()
    macd_val = float(macd.iloc[-1])
    macd_sig_val = float(macd_signal.iloc[-1])

    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    vol_ma20 = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
    vol_ratio = float(volume.iloc[-1] / vol_ma20) if vol_ma20 else None

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    atr_pct = (atr / latest * 100) if latest > 0 else 0.0

    buy_price = safe_float(buy_price, 0)
    stop_loss = safe_float(stop_loss, 0) or (buy_price * 0.95 if buy_price > 0 else 0)
    take_profit = safe_float(take_profit, 0) or (buy_price * 1.08 if buy_price > 0 else 0)
    pnl_pct = ((latest - buy_price) / buy_price) * 100 if buy_price > 0 else None

    technical_score = 0.0
    notes = []
    if sma50 and latest > sma20 > sma50:
        technical_score += 2.5
        notes.append("Giá > SMA20 > SMA50")
    elif latest > sma20:
        technical_score += 1.0
        notes.append("Giá trên SMA20")
    else:
        technical_score -= 1.5
        notes.append("Giá dưới SMA20")

    if macd_val > macd_sig_val:
        technical_score += 1.5
        notes.append("MACD bullish")
    else:
        technical_score -= 1.0
        notes.append("MACD yếu")

    if 45 <= rsi <= 65:
        technical_score += 1.0
        notes.append("RSI trung tính tích cực")
    elif rsi < 35:
        technical_score += 0.4
        notes.append("RSI thấp, có thể hồi")
    elif rsi > 75:
        technical_score -= 1.0
        notes.append("RSI quá nóng")

    if vol_ratio is not None and vol_ratio > 1.2:
        technical_score += 0.5
        notes.append("Volume cao hơn TB20")

    if len(close) >= 6 and close.iloc[-1] > close.iloc[-6]:
        technical_score += 0.5
        notes.append("Xu hướng 5 phiên vẫn tốt")
    elif len(close) >= 6 and close.iloc[-1] < close.iloc[-6]:
        technical_score -= 0.5
        notes.append("5 phiên gần nhất yếu")

    if buy_price > 0:
        if latest >= buy_price * 1.03:
            technical_score += 0.4
        elif latest <= buy_price * 0.97:
            technical_score -= 0.4

    if technical_score >= 4.0:
        forecast = "Tăng"
    elif technical_score >= 2.0:
        forecast = "Tăng nhẹ"
    elif technical_score <= -2.0:
        forecast = "Giảm"
    elif technical_score <= -0.5:
        forecast = "Yếu / Sideway"
    else:
        forecast = "Sideway"

    forecast_conf = int(max(35, min(90, 50 + abs(technical_score) * 10)))

    latest_record = latest_predictions_by_symbol().get(symbol, {})
    ai_verdict = verdict_label(normalize_prediction(latest_record.get("prediction")))
    ai_confidence = latest_record.get("confidence")
    ai_target = safe_float(latest_record.get("target"), 0)
    ai_stoploss = safe_float(latest_record.get("stoploss"), 0)

    # Auto-suggest levels if the user did not set them explicitly.
    # ATR provides the base risk box; trend strength nudges the take-profit distance.
    trend_bias = 1.0
    if technical_score >= 4.0:
        trend_bias = 1.35
    elif technical_score >= 2.0:
        trend_bias = 1.15
    elif technical_score <= -1.0:
        trend_bias = 0.85

    suggested_stop_loss = stop_loss
    if buy_price > 0:
        atr_stop = max(0.0, buy_price - (1.0 if technical_score >= 0 else 1.2) * atr)
        pct_stop = buy_price * (0.95 if technical_score >= 0 else 0.93)
        suggested_stop_loss = round(max(0.0, min(pct_stop, atr_stop)), 2)

    suggested_take_profit = take_profit
    if buy_price > 0:
        atr_target = buy_price + (2.0 * trend_bias) * atr
        pct_target = buy_price * (1.08 if technical_score < 2.0 else 1.12 if technical_score < 4.0 else 1.16)
        suggested_take_profit = round(max(pct_target, atr_target), 2)

    if not suggested_stop_loss and latest > 0:
        suggested_stop_loss = round(max(0.0, latest - 1.1 * atr), 2)
    if not suggested_take_profit and latest > 0:
        suggested_take_profit = round(latest + 2.2 * atr, 2)

    upside_to_take_profit = ((suggested_take_profit - latest) / latest) * 100 if suggested_take_profit > 0 and latest > 0 else None
    downside_to_stop = ((latest - suggested_stop_loss) / latest) * 100 if suggested_stop_loss > 0 and latest > 0 else None

    effective_stop_loss = stop_loss if stop_loss > 0 else suggested_stop_loss
    effective_take_profit = take_profit if take_profit > 0 else suggested_take_profit

    if effective_stop_loss > 0 and latest <= effective_stop_loss:
        sell_window = "Bán ngay"
    elif effective_take_profit > 0 and latest >= effective_take_profit * 0.995:
        sell_window = "0-1 phiên"
    elif upside_to_take_profit is not None and upside_to_take_profit <= 3:
        sell_window = "0-2 phiên"
    elif upside_to_take_profit is not None and upside_to_take_profit <= 6:
        sell_window = "2-5 phiên"
    elif forecast in {"Tăng", "Tăng nhẹ"}:
        sell_window = "1-2 tuần"
    else:
        sell_window = "Chưa rõ, chờ xác nhận"

    if effective_stop_loss > 0 and latest <= effective_stop_loss:
        alert = "Thoat ngay - thủng stop"
        alert_level = "critical"
    elif effective_stop_loss > 0 and latest <= effective_stop_loss * 1.03:
        alert = "Sắp lỗ"
        alert_level = "warning"
    elif effective_take_profit > 0 and latest >= effective_take_profit:
        alert = "Chốt lời ngay"
        alert_level = "success"
    elif effective_take_profit > 0 and latest >= effective_take_profit * 0.97:
        alert = "Sắp chốt lời"
        alert_level = "success"
    else:
        alert = "Bình thường"
        alert_level = "info"

    return {
        "symbol": symbol,
        "current_price": latest,
        "prev_close": prev,
        "change_pct": change_pct,
        "buy_price": buy_price,
        "pnl_pct": pnl_pct,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "suggested_stop_loss": suggested_stop_loss,
        "suggested_take_profit": suggested_take_profit,
        "upside_to_take_profit": upside_to_take_profit,
        "downside_to_stop": downside_to_stop,
        "sell_window": sell_window,
        "alert": alert,
        "alert_level": alert_level,
        "forecast": forecast,
        "forecast_conf": forecast_conf,
        "technical_score": round(technical_score, 2),
        "notes": notes,
        "rsi": rsi,
        "macd": macd_val,
        "macd_signal": macd_sig_val,
        "sma20": sma20,
        "sma50": sma50,
        "vol_ratio": vol_ratio,
        "atr": atr,
        "ai_verdict": ai_verdict,
        "ai_confidence": ai_confidence,
        "ai_target": ai_target,
        "ai_stoploss": ai_stoploss,
    }


def render_position_tracker_page():
    st.markdown(
        '<div class="section-title" style="margin-bottom:0.4rem;">Theo dõi mã đã mua</div>',
        unsafe_allow_html=True,
    )
    st.caption("Nhập mã bạn đã mua trên TCBS, đặt vùng cắt lỗ/chốt lời, và xem dự đoán ngắn hạn ngay trên dashboard.")

    rows = load_tracked_positions()
    top_cols = st.columns([1.1, 1.1, 1.1, 1.7])
    with top_cols[0]:
        st.metric("Mã đang theo dõi", len(rows))
    with top_cols[1]:
        st.caption("Số mã sắp lỗ sẽ tính sau khi tải dữ liệu thị trường.")
    with top_cols[2]:
        st.caption("Số mã sắp chốt lời sẽ tính sau khi tải dữ liệu thị trường.")
    with top_cols[3]:
        st.caption("Nếu chưa có dữ liệu lưu, trang sẽ hiển thị trống. Bạn có thể thêm mã thủ công bên dưới.")

    c1, c2 = st.columns([1.15, 0.85])
    with c1:
        with st.expander("Thêm / cập nhật mã", expanded=not rows):
            with st.form("position_add_form", clear_on_submit=True):
                form_cols = st.columns([1.2, 0.8, 1.0, 1.0])
                with form_cols[0]:
                    symbol = st.text_input("Mã", placeholder="STB")
                with form_cols[1]:
                    qty = st.number_input("SL", min_value=0, step=100, value=0)
                with form_cols[2]:
                    buy_price = st.number_input("Giá mua", min_value=0.0, step=0.1, value=0.0, format="%.2f")
                with form_cols[3]:
                    note = st.text_input("Ghi chú", placeholder="Mua từ TCBS / lướt sóng / trung hạn")
                submitted = st.form_submit_button("Lưu mã")
            if submitted:
                symbol = str(symbol).strip().upper()
                if not symbol:
                    st.error("Cần nhập mã cổ phiếu.")
                else:
                    updated_rows = [row for row in rows if str(row.get("symbol", "")).upper() != symbol]
                    updated_rows.append(
                        {
                            "symbol": symbol,
                            "qty": int(qty),
                            "buy_price": float(buy_price),
                            "stop_loss": 0.0,
                            "take_profit": 0.0,
                            "note": note,
                            "source": "manual",
                        }
                    )
                    save_tracked_positions(updated_rows)
                    st.success(f"Đã lưu {symbol}.")
                    st.rerun()

        if st.button("Nạp danh mục giấy hiện tại", width='stretch'):
            imported = load_tracked_positions()
            try:
                from auto_trader import _safe_read_portfolio

                portfolio = _safe_read_portfolio()
                positions = portfolio.get("positions", {}) or {}
                imported = []
                for symbol, pos in positions.items():
                    symbol = str(symbol).upper()
                    buy_price = safe_float(pos.get("avg_price") or pos.get("buy_price"), 0)
                    imported.append(
                        {
                            "symbol": symbol,
                            "qty": int(safe_float(pos.get("qty"), 0) or 0),
                            "buy_price": buy_price,
                            "stop_loss": safe_float(pos.get("stop_loss"), 0) or (buy_price * 0.95 if buy_price else 0),
                            "take_profit": safe_float(pos.get("take_profit"), 0) or (buy_price * 1.08 if buy_price else 0),
                            "note": "imported from paper portfolio",
                            "source": "portfolio",
                        }
                    )
                save_tracked_positions(imported)
                st.success("Đã nạp danh mục giấy vào trang theo dõi.")
                st.rerun()
            except Exception as exc:
                st.error(f"Không nạp được danh mục: {exc}")

        if st.button("Điền đề xuất cho mã thiếu SL/TP", width='stretch'):
            filled_rows = []
            for row in rows:
                symbol = str(row.get("symbol", "")).upper().strip()
                if not symbol:
                    continue
                current_row = dict(row)
                snap = build_position_snapshot(
                    symbol,
                    buy_price=current_row.get("buy_price", 0),
                    stop_loss=current_row.get("stop_loss", 0),
                    take_profit=current_row.get("take_profit", 0),
                )
                if snap:
                    current_row["stop_loss"] = float(snap.get("suggested_stop_loss") or current_row.get("stop_loss", 0) or 0)
                    current_row["take_profit"] = float(snap.get("suggested_take_profit") or current_row.get("take_profit", 0) or 0)
                filled_rows.append(current_row)
            save_tracked_positions(filled_rows)
            st.success("Đã tự động điền đề xuất cho toàn bộ mã.")
            st.rerun()

    with c2:
        if rows:
            editable = pd.DataFrame(rows)
            editable = editable[["symbol", "qty", "buy_price", "note", "source"]]
            edited = st.data_editor(
                editable,
                key="tracked_positions_editor",
                num_rows="dynamic",
                width='stretch',
                hide_index=True,
                column_config={
                    "symbol": st.column_config.TextColumn("Mã", help="Ví dụ: STB"),
                    "qty": st.column_config.NumberColumn("SL", min_value=0, step=100),
                    "buy_price": st.column_config.NumberColumn("Giá mua", min_value=0.0, format="%.2f"),
                    "note": st.column_config.TextColumn("Ghi chú"),
                    "source": st.column_config.TextColumn("Nguồn", disabled=True),
                },
            )
            if st.button("Lưu thay đổi", width='stretch'):
                cleaned_edited = []
                for item in edited.to_dict("records"):
                    cleaned_edited.append(
                        {
                            "symbol": item.get("symbol"),
                            "qty": item.get("qty", 0),
                            "buy_price": item.get("buy_price", 0),
                            "stop_loss": 0.0,
                            "take_profit": 0.0,
                            "note": item.get("note", ""),
                            "source": item.get("source", "manual"),
                        }
                    )
                save_tracked_positions(cleaned_edited)
                st.success("Đã lưu thay đổi.")
                st.rerun()

            delete_symbols = st.multiselect(
                "Chọn mã để xóa",
                options=[row["symbol"] for row in rows],
                key="tracked_positions_delete_symbols",
            )
            if st.button("Xóa mã đã chọn", width='stretch', type="secondary", disabled=not delete_symbols):
                remaining = [row for row in edited.to_dict("records") if str(row.get("symbol", "")).upper() not in set(delete_symbols)]
                save_tracked_positions(remaining)
                st.success(f"Đã xóa {len(delete_symbols)} mã.")
                st.rerun()
        else:
            st.info("Chưa có mã nào trong danh sách theo dõi.")

    if not rows:
        return

    snapshots = []
    for row in rows:
        try:
            snap = build_position_snapshot(
                row.get("symbol"),
                buy_price=row.get("buy_price", 0),
                stop_loss=row.get("stop_loss", 0),
                take_profit=row.get("take_profit", 0),
            )
            if snap:
                snap["qty"] = int(safe_float(row.get("qty"), 0) or 0)
                snap["note"] = row.get("note", "")
                snap["source"] = row.get("source", "manual")
                snapshots.append(snap)
        except Exception:
            continue

    if not snapshots:
        st.warning("Chưa lấy được dữ liệu thị trường cho các mã đã lưu.")
        return

    snap_df = pd.DataFrame(snapshots)
    snap_df["pnl_pct"] = snap_df["pnl_pct"].fillna(0)
    snap_df["alert_rank"] = snap_df["alert_level"].map({"critical": 0, "warning": 1, "success": 2, "info": 3}).fillna(3)
    snap_df = snap_df.sort_values(["alert_rank", "pnl_pct"], ascending=[True, True]).reset_index(drop=True)

    cnt_cols = st.columns(2)
    with cnt_cols[0]:
        st.metric("Sắp lỗ", int((snap_df["alert_level"] == "warning").sum() + (snap_df["alert_level"] == "critical").sum()))
    with cnt_cols[1]:
        st.metric("Sắp chốt lời", int((snap_df["alert_level"] == "success").sum()))

    st.markdown('<div class="section-title" style="margin:1rem 0 0.5rem;">Danh mục hiện tại</div>', unsafe_allow_html=True)
    summary_rows = []
    for _, row in snap_df.iterrows():
        current = float(row["current_price"])
        buy_price = float(row["buy_price"]) if row["buy_price"] else None
        summary_rows.append(
            {
                "Mã": row["symbol"],
                "SL": int(row["qty"]),
                "Giá mua": fmt_optional_number(buy_price, 2),
                "Giá hiện tại": f"{current:,.2f}",
                "P/L %": f"{row['pnl_pct']:+.2f}%" if row["pnl_pct"] is not None else "-",
                "Cắt lỗ": fmt_optional_number(row["suggested_stop_loss"] or row["stop_loss"], 2),
                "Chốt lời": fmt_optional_number(row["suggested_take_profit"] or row["take_profit"], 2),
                "Cảnh báo": row["alert"],
                "Dự đoán": f"{row['forecast']} ({row['forecast_conf']}%)",
                "Bán khi": row["sell_window"],
            }
        )
    render_dark_table(pd.DataFrame(summary_rows), key="position-tracker-summary")

    st.markdown('<div class="section-title" style="margin:1rem 0 0.5rem;">Chi tiết từng mã</div>', unsafe_allow_html=True)
    selected = st.selectbox("Chọn mã để xem chart và dự đoán", [row["symbol"] for row in snapshots], key="tracked_symbol_select")
    st.session_state["selected_symbol"] = selected
    st.session_state.setdefault("selected_interval", "1M")
    render_live_chart()

    for row in snapshots:
        alert_color = {
            "critical": "#f43f5e",
            "warning": "#f59e0b",
            "success": "#22c55e",
            "info": "#94a3b8",
        }.get(row["alert_level"], "#94a3b8")
        ai_conf_text = f" ({row['ai_confidence']:.0f}%)" if row["ai_confidence"] is not None else ""
        with st.expander(f"{row['symbol']} - {row['alert']} - {row['forecast']}", expanded=False):
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("Giá hiện tại", f"{row['current_price']:,.2f}", f"{row['change_pct']:+.2f}%")
            with c2:
                st.metric("P/L", f"{row['pnl_pct']:+.2f}%" if row["pnl_pct"] is not None else "-")
            with c3:
                st.metric("Cắt lỗ", fmt_optional_number(row["suggested_stop_loss"] or row["stop_loss"], 2))
            with c4:
                st.metric("Chốt lời", fmt_optional_number(row["suggested_take_profit"] or row["take_profit"], 2))
            with c5:
                st.metric("Dự đoán", f"{row['forecast']} ({row['forecast_conf']}%)")

            st.markdown(
                f'<div style="padding:0.6rem 0.75rem;border:1px solid {alert_color}33;border-radius:8px;'
                f'background:rgba(15,23,42,0.9);color:#e2e8f0;margin-bottom:0.5rem;">'
                f'<b>Cảnh báo:</b> <span style="color:{alert_color};font-weight:800;">{row["alert"]}</span> | '
                f'<b>AI:</b> {row["ai_verdict"]}{ai_conf_text} | '
                f'<b>Score:</b> {row["technical_score"]:+.2f}'
                f'</div>',
                unsafe_allow_html=True,
            )
            if row["notes"]:
                st.caption("Tín hiệu: " + " · ".join(row["notes"]))
            if row.get("note"):
                st.caption(f"Ghi chú: {row['note']}")
            if st.button(f"Xóa {row['symbol']}", key=f"delete_tracked_{row['symbol']}", width='stretch'):
                remaining = [item for item in rows if str(item.get("symbol", "")).upper() != row["symbol"]]
                save_tracked_positions(remaining)
                st.success(f"Đã xóa {row['symbol']}.")
                st.rerun()

            chart_cols = st.columns([1.2, 0.8])
            with chart_cols[0]:
                try:
                    from data_fetcher import get_stock_data_cached

                    df_chart = get_stock_data_cached(row["symbol"], years=0.25)
                    if df_chart is not None and len(df_chart) >= 5:
                        df_chart = df_chart.copy()
                        df_chart["time"] = pd.to_datetime(df_chart["time"])
                        df_chart = df_chart.sort_values("time")
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=df_chart["time"],
                            y=df_chart["close"],
                            mode="lines",
                            name="Close",
                            line=dict(color="#38bdf8", width=2),
                        ))
                        if row["buy_price"] > 0:
                            fig.add_hline(y=row["buy_price"], line_color="#f59e0b", line_dash="dot", annotation_text="Giá mua")
                        if row["stop_loss"] > 0:
                            fig.add_hline(y=row["stop_loss"], line_color="#f43f5e", line_dash="dash", annotation_text="Stop")
                        if row["take_profit"] > 0:
                            fig.add_hline(y=row["take_profit"], line_color="#22c55e", line_dash="dash", annotation_text="Target")
                        fig.update_layout(
                            template="plotly_dark",
                            height=300,
                            margin=dict(l=0, r=0, t=20, b=0),
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            showlegend=False,
                        )
                        st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
                    else:
                        st.info("Chưa đủ dữ liệu biểu đồ.")
                except Exception as exc:
                    st.caption(f"Không vẽ được chart: {exc}")
            with chart_cols[1]:
                st.markdown(
                    f"""
                    <div class="metric-box">
                        <div class="label">Tóm tắt</div>
                        <div class="value" style="font-size:1.15rem;color:{alert_color};">{row['alert']}</div>
                        <div class="delta" style="color:#cbd5e1;">
                            Xu hướng: {row['forecast']} ({row['forecast_conf']}%)<br>
                            RSI: {row['rsi']:.1f} | MACD: {row['macd']:.2f}<br>
                            SMA20: {row['sma20']:.2f} | SMA50: {fmt_optional_number(row['sma50'], 2)}<br>
                            Volume ratio: {fmt_optional_number(row['vol_ratio'], 2)}x<br>
                            Bán dự kiến: {row['sell_window']}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

def today_opportunities(symbols, max_items=5):
    latest_records = latest_predictions_by_symbol()
    accuracy_by_symbol = symbol_accuracy_map()
    rows = []
    for sym in symbols:
        sym = str(sym).upper()
        record = latest_records.get(sym)
        if not record:
            continue
        pred = normalize_prediction(record.get("prediction"))
        confidence = record.get("confidence")
        scores = record.get("signal_scores") or {}
        entry = safe_float(record.get("price_at_prediction"))
        target = safe_float(record.get("target"))
        stoploss = safe_float(record.get("stoploss"))
        upside = ((target - entry) / entry) * 100 if target > 0 and entry > 0 else None
        risk = ((entry - stoploss) / entry) * 100 if stoploss > 0 and entry > 0 else None
        acc = accuracy_by_symbol.get(sym, {}).get("accuracy")
        score = 0
        score += {"MUA": 35, "GIU": 8, "BAN": -20}.get(pred, 0)
        score += ((confidence or 50) - 50) * 0.45
        score += (scores.get("trend_score", 50) - 50) * 0.20
        score += (scores.get("momentum_score", 50) - 50) * 0.18
        if upside is not None:
            score += min(16, max(-10, upside))
        if risk is not None and risk < 3:
            score -= 8
        if acc is not None:
            score += (acc - 50) * 0.12
        rows.append({
            "Mã": sym,
            "AI": verdict_label(pred),
            "Conf": confidence,
            "Upside": upside,
            "Risk": risk,
            "Acc": acc,
            "Điểm": score,
            "Ngày": record.get("date", "")
        })
    return sorted(rows, key=lambda x: x["Điểm"], reverse=True)[:max_items]

def sma_trend_label(close, sma20, sma50):
    if pd.isna(sma20) or pd.isna(sma50):
        return "Sideway"
    if close > sma20 > sma50:
        return "Tăng"
    if close < sma20 < sma50:
        return "Giảm"
    return "Sideway"

def fetch_watchlist_comparison(watchlist_symbols, start, end):
    verdicts = latest_ai_verdicts()
    latest_records = latest_predictions_by_symbol()
    accuracy_by_symbol = symbol_accuracy_map()
    rows = []
    series = {}
    for sym in watchlist_symbols:
        try:
            quote = get_vnstock_client(sym, source='VCI')
            df_cmp = quote.history(start=start, end=end, interval='1D')
            if df_cmp is None or len(df_cmp) < 2:
                continue
            df_cmp = df_cmp.copy()
            df_cmp["time"] = pd.to_datetime(df_cmp["time"])
            df_cmp = df_cmp.sort_values("time").reset_index(drop=True)
            close_cmp = df_cmp["close"].astype(float)
            first_close = close_cmp.iloc[0]
            if first_close and first_close > 0:
                series[sym] = pd.DataFrame({
                    "time": df_cmp["time"],
                    "normalized": (close_cmp / first_close - 1) * 100
                })

            latest_cmp = df_cmp.iloc[-1]
            prev_cmp = df_cmp.iloc[-2]
            change_today = ((latest_cmp["close"] - prev_cmp["close"]) / prev_cmp["close"]) * 100 if prev_cmp["close"] else 0
            sma20_cmp = safe_sma(close_cmp, 20)
            sma50_cmp = safe_sma(close_cmp, 50)
            rsi_delta_cmp = close_cmp.diff()
            gain_cmp = rsi_delta_cmp.clip(lower=0).rolling(14).mean().iloc[-1]
            loss_cmp = (-rsi_delta_cmp.clip(upper=0)).rolling(14).mean().iloc[-1]
            if pd.isna(gain_cmp) or pd.isna(loss_cmp) or loss_cmp == 0:
                rsi_cmp = 50
            else:
                rsi_cmp = 100 - (100 / (1 + gain_cmp / loss_cmp))
            ema12_cmp = close_cmp.ewm(span=12).mean().iloc[-1]
            ema26_cmp = close_cmp.ewm(span=26).mean().iloc[-1]
            macd_cmp = ema12_cmp - ema26_cmp
            if pd.isna(macd_cmp):
                macd_cmp = 0
            trend_cmp = sma_trend_label(latest_cmp["close"], sma20_cmp, sma50_cmp)
            verdict_cmp = verdicts.get(sym.upper(), "N/A")
            latest_record = latest_records.get(sym.upper(), {})
            confidence = latest_record.get("confidence")
            target = safe_float(latest_record.get("target"))
            stoploss = safe_float(latest_record.get("stoploss"))
            current_price = float(latest_cmp["close"])
            upside = ((target - current_price) / current_price) * 100 if target > 0 and current_price > 0 else None
            downside = ((current_price - stoploss) / current_price) * 100 if stoploss > 0 and current_price > 0 else None
            sym_acc = accuracy_by_symbol.get(sym.upper(), {})
            accuracy = sym_acc.get("accuracy")

            score = 0
            score += float(change_today) * 2
            score += 12 if trend_cmp == "Tăng" else -12 if trend_cmp == "Giảm" else 0
            score += 8 if macd_cmp > 0 else -8
            score += 8 if 45 <= rsi_cmp <= 65 else -6 if rsi_cmp > 75 or rsi_cmp < 30 else 0
            score += {"MUA": 15, "GIU": 2, "BAN": -15}.get(verdict_cmp, 0)
            score += ((confidence or 50) - 50) * 0.25
            if upside is not None:
                score += min(12, max(-8, upside * 0.7))
            if downside is not None and downside < 3:
                score -= 7
            if accuracy is not None:
                score += (accuracy - 50) * 0.18

            rows.append({
                "Mã": sym,
                "Giá": current_price,
                "% hôm nay": float(change_today),
                "RSI": float(rsi_cmp),
                "MACD": float(macd_cmp),
                "SMA trend": trend_cmp,
                "AI verdict": verdict_label(verdict_cmp),
                "Confidence": confidence,
                "Upside %": upside,
                "Risk %": downside,
                "Accuracy %": accuracy,
                "Điểm tiềm năng": float(score)
            })
        except Exception:
            continue

    comparison = pd.DataFrame(rows)
    if not comparison.empty:
        comparison = comparison.sort_values("Điểm tiềm năng", ascending=False).reset_index(drop=True)
    return comparison, series

def lstm_predict(symbol):
    try:
        import joblib
        from tensorflow.keras.models import load_model

        symbol = str(symbol).upper()
        model_path = os.path.join(MODELS_DIR, f"{symbol}_direction_model.h5")
        scaler_path = os.path.join(MODELS_DIR, f"{symbol}_direction_scaler.pkl")
        validation = load_lstm_validation(symbol)
        if validation and not validation.get("is_reliable", False):
            return {
                "reliable": False,
                "direction": None,
                "probability": None,
                "message": f"Model chưa đạt ngưỡng tin cậy (acc={validation.get('directional_accuracy', 0):.1%})",
            }
        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            return {
                "reliable": False,
                "direction": None,
                "probability": None,
                "message": "Chưa có direction model/scaler",
            }

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        stock = get_vnstock_client(symbol, source="VCI")
        df_r = stock.history(start=start, end=end, interval="1D")
        if df_r is None or len(df_r) == 0:
            raise ValueError("Không có dữ liệu LSTM")
        df_r["time"] = pd.to_datetime(df_r["time"])
        df_r = df_r.sort_values("time").reset_index(drop=True)
        df_features, feature_cols = build_lstm_direction_features(df_r)

        payload = joblib.load(scaler_path)
        scaler = payload["scaler"]
        feature_cols = payload.get("feature_cols", feature_cols)
        seq_len = int(payload.get("sequence_len", 20))
        if len(df_features) < seq_len:
            return {
                "reliable": False,
                "direction": None,
                "probability": None,
                "message": "Không đủ dữ liệu để predict direction",
            }

        X = scaler.transform(df_features[feature_cols].tail(seq_len))
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
    except Exception as exc:
        return {
            "reliable": False,
            "direction": None,
            "probability": None,
            "message": f"LSTM lỗi: {exc}",
        }

def ensemble_direction_predict(symbol, current_data=None):
    symbol = str(symbol).upper()
    ensemble_predict = get_ensemble_predictor()
    if ensemble_predict is None:
        return lstm_predict(symbol)

    try:
        df_e = current_data
        if df_e is None:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
            stock = get_vnstock_client(symbol, source="VCI")
            df_e = stock.history(start=start, end=end, interval="1D")
            if df_e is None or len(df_e) == 0:
                raise ValueError("Không có dữ liệu ensemble")
            df_e["time"] = pd.to_datetime(df_e["time"])
            df_e = df_e.sort_values("time").reset_index(drop=True)

        result = ensemble_predict(symbol, df_e)
        if result.get("reliable"):
            result["model_type"] = "ensemble"
            return result

        fallback = lstm_predict(symbol)
        fallback.setdefault("message", result.get("message", "Ensemble chưa có model"))
        fallback["model_type"] = "lstm"
        return fallback
    except Exception as exc:
        fallback = lstm_predict(symbol)
        if fallback.get("reliable"):
            fallback["model_type"] = "lstm"
            fallback["message"] = f"Ensemble lỗi, dùng LSTM: {exc}"
            return fallback
        return {
            "reliable": False,
            "direction": None,
            "probability": None,
            "message": f"Ensemble lỗi: {exc}",
            "model_type": "ensemble",
        }

def render_ensemble_votes(lstm_result):
    if not lstm_result or not lstm_result.get("reliable"):
        return
    votes = {k: v for k, v in (lstm_result.get("votes") or {}).items() if isinstance(v, int)}
    if not votes:
        return

    cols = st.columns(min(len(votes) + 1, 5))
    if lstm_result.get("direction") == 0 or not lstm_result.get("high_confidence", False):
        cols[0].metric(
            "Ensemble",
            "Không rõ xu hướng",
            f"prob {float(lstm_result.get('probability') or 0.5):.1%}",
        )
    else:
        direction_emoji = "📈" if lstm_result.get("direction") == 1 else "📉"
        cols[0].metric(
            "Ensemble",
            f"{direction_emoji} {str(lstm_result.get('signal', '')).upper()}",
            f"prob {float(lstm_result.get('probability') or 0.5):.1%}",
        )
    for idx, (model_name, vote) in enumerate(votes.items(), 1):
        cols[idx % len(cols)].metric(model_name.upper(), "📈" if vote == 1 else "📉")
    if lstm_result.get("consensus") and lstm_result.get("high_confidence", False):
        st.success(f"{lstm_result.get('total_models', len(votes))}/{lstm_result.get('total_models', len(votes))} models đồng thuận - signal mạnh.")
    elif not lstm_result.get("high_confidence", False):
        st.caption("Ensemble: Không rõ xu hướng (confidence thấp)")

def portfolio_exposure_pct(symbol):
    try:
        portfolio_summary = get_portfolio_summary_cached()
        equity = float(portfolio_summary.get("equity", 0) or 0)
        pos = portfolio_summary.get("positions", {}).get(str(symbol).upper())
        if not pos or not equity:
            return 0.0
        price = paper_trader.current_price(str(symbol).upper())
        if price is None:
            return 0.0
        return float(pos.get("qty", 0)) * float(price) / float(equity) * 100
    except Exception:
        return 0.0

def ollama_technical_agent(symbol, indicators, reflection_context=""):
    prompt = f"""Bạn là Technical Analyst cho cổ phiếu VN.
Chỉ trả JSON thuần, không markdown, không giải thích ngoài JSON.
Nếu không đủ dữ liệu, strength < 40.

{reflection_context}

Input:
symbol={symbol}
rsi={indicators.get('rsi'):.1f}
macd={indicators.get('macd'):.4f}
macd_signal={indicators.get('macd_signal'):.4f}
sma20={fmt_optional_number(indicators.get('sma20'), 2)}
sma50={fmt_optional_number(indicators.get('sma50'), 2)}
bollinger_upper={indicators.get('bb_upper'):.2f}
bollinger_lower={indicators.get('bb_lower'):.2f}
close={indicators.get('close'):.2f}
volume_ratio={indicators.get('volume_ratio'):.2f}
trend_score={indicators.get('trend_score', 50)}
confluence={indicators.get('confluence_score', 0)}

Output schema:
{{"signal":"bullish|bearish|neutral","strength":0,"reasons":["..."]}}"""
    default = {"signal": "neutral", "strength": 30, "reasons": ["Không đọc được phản hồi technical agent"]}
    data = call_llm_json(
        prompt,
        system="Bạn là technical analyst. Chỉ trả về JSON thuần.",
        max_tokens=500,
    ) or default
    data["signal"] = str(data.get("signal", "neutral")).lower()
    data["strength"] = max(0, min(100, int(safe_float(data.get("strength"), 30))))
    data["reasons"] = data.get("reasons") if isinstance(data.get("reasons"), list) else [str(data.get("reasons", ""))]
    return data

def ollama_sentiment_agent(symbol, news_items, reflection_context=""):
    news_lines = "\n".join([f"- {item.get('title', '')}" for item in (news_items or [])[:8]]) or "- Không có tin tức 3 ngày gần nhất"
    prompt = f"""Bạn là News/Sentiment Analyst cho cổ phiếu VN.
Chỉ trả JSON thuần, không markdown, không text thừa.
Nếu không đủ dữ liệu, score gần 0 và confidence thấp.

{reflection_context}

symbol={symbol}
Tin tức 3 ngày gần nhất:
{news_lines}

Output schema:
{{"sentiment":"positive|negative|neutral","score":0.0,"key_event":"..."}}"""
    default = {"sentiment": "neutral", "score": 0.0, "key_event": "Không có dữ liệu sentiment đáng tin"}
    data = call_llm_json(
        prompt,
        system="Bạn là sentiment analyst. Chỉ trả về JSON thuần.",
        max_tokens=300,
    ) or default
    data["sentiment"] = str(data.get("sentiment", "neutral")).lower()
    data["score"] = max(-1.0, min(1.0, safe_float(data.get("score"), 0.0)))
    data["key_event"] = str(data.get("key_event", ""))[:500]
    return data

def ollama_risk_agent(symbol, technical, sentiment, lstm_result, exposure_pct, market_regime, reflection_context=""):
    lstm_direction_value = (lstm_result or {}).get("direction")
    lstm_direction = "none" if not lstm_direction_value else "up" if lstm_direction_value > 0 else "down"
    lstm_probability = (lstm_result or {}).get("probability")
    prompt = f"""Bạn là Portfolio Risk Manager cho paper trading cổ phiếu VN.
Chỉ trả JSON thuần, không markdown, không text thừa.
Nếu không đủ dữ liệu, confidence < 40 và action HOLD.

{reflection_context}

Input:
symbol={symbol}
technical_signal={technical.get('signal')}
technical_strength={technical.get('strength')}
sentiment={sentiment.get('sentiment')}
sentiment_score={sentiment.get('score')}
lstm_direction={lstm_direction}
lstm_probability={lstm_probability}
lstm_reliable={bool((lstm_result or {}).get('reliable'))}
current_portfolio_exposure_pct={round(exposure_pct, 2)}
market_regime={market_regime.get('regime', 'UNKNOWN')}
market_regime_note={market_regime.get('recommendation', '')}

Output schema:
{{"action":"BUY|SELL|HOLD","confidence":0,"position_size_pct":0,"stop_loss_atr_mult":2.0,"hold_days":5,"reasoning":"..."}}"""
    default = {
        "action": "HOLD",
        "confidence": 30,
        "position_size_pct": 0,
        "stop_loss_atr_mult": 2.0,
        "hold_days": 5,
        "reasoning": "Không đọc được phản hồi risk agent",
    }
    data = call_llm_json(
        prompt,
        system="Bạn là risk manager. Chỉ trả về JSON thuần.",
        max_tokens=500,
    ) or default
    action = str(data.get("action", "HOLD")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"
    data["action"] = action
    data["confidence"] = max(0, min(100, int(safe_float(data.get("confidence"), 30))))
    data["position_size_pct"] = max(0, min(20, safe_float(data.get("position_size_pct"), 0)))
    data["stop_loss_atr_mult"] = max(1.5, min(3.0, safe_float(data.get("stop_loss_atr_mult"), 2.0)))
    data["hold_days"] = max(3, min(15, int(safe_float(data.get("hold_days"), 5))))
    data["reasoning"] = str(data.get("reasoning", ""))[:700]
    return data

def normalize_agent_action(value, fallback_direction=None):
    text = str(value or "").upper()
    if "MUA" in text or text == "BUY":
        return "MUA"
    if "BÁN" in text or "BAN" in text or text == "SELL":
        return "BÁN"
    if fallback_direction == 1:
        return "MUA"
    if fallback_direction == -1:
        return "BÁN"
    return "GIỮ"

def get_analysis_for_ticker(ticker, confluence_score, current_price, atr):
    """
    Prefer higher-quality two-stage and learning-engine predictions over the
    local confluence fallback used by the dashboard batch analyzer.
    """
    ticker = str(ticker or "").upper()
    current_price = safe_float(current_price, 0.0)
    atr = safe_float(atr, 0.0)

    def build_trade_levels(action, target_pct=0, stoploss_pct=0):
        target_pct = safe_float(target_pct, 0.0)
        stoploss_pct = safe_float(stoploss_pct, 0.0)
        if action == "MUA" and current_price > 0:
            target = round(current_price * (1 + target_pct / 100), 1) if target_pct else round(current_price + 2.0 * atr, 1)
            stoploss = round(current_price * (1 - stoploss_pct / 100), 1) if stoploss_pct else round(current_price - atr, 1)
            return target, stoploss
        if action == "BÁN" and current_price > 0:
            target = round(current_price * (1 - target_pct / 100), 1) if target_pct else round(current_price - 2.0 * atr, 1)
            stoploss = round(current_price * (1 + stoploss_pct / 100), 1) if stoploss_pct else round(current_price + atr, 1)
            return target, stoploss
        return 0, 0

    try:
        results_path = os.path.join(os.path.dirname(__file__), "analysis_results.json")
        if os.path.exists(results_path):
            with open(results_path, encoding="utf-8") as f:
                ar = json.load(f)
            if ar.get("date") == date.today().isoformat():
                for item in ar.get("stage2_results", []):
                    if str(item.get("ticker", "")).upper() != ticker:
                        continue
                    llm = item.get("llm") or {}
                    ensemble = item.get("ensemble") or {}
                    direction = int(safe_float(ensemble.get("direction"), 0))
                    action = normalize_agent_action(llm.get("action"), direction)
                    confidence = safe_float(llm.get("confidence"), safe_float(ensemble.get("confidence"), 50))
                    target, stoploss = build_trade_levels(action, llm.get("target_pct", 0), llm.get("stoploss_pct", 0))
                    return {
                        "action": action,
                        "confidence": confidence,
                        "target": target,
                        "stoploss": stoploss,
                        "reason": llm.get("reason") or f"Two-stage ensemble signal={ensemble.get('signal', 'N/A')}",
                        "source": "two_stage",
                        "ensemble_signal": ensemble.get("signal", "N/A"),
                        "stage1_score": item.get("score", 0),
                        "confluence": confluence_score,
                    }
    except Exception:
        pass

    try:
        pred_log_path = os.path.join(os.path.dirname(__file__), "prediction_log.json")
        if os.path.exists(pred_log_path):
            with open(pred_log_path, encoding="utf-8") as f:
                logs = json.load(f)
            today = date.today().isoformat()
            for pred in logs.values():
                if str(pred.get("ticker", "")).upper() != ticker or pred.get("date") != today:
                    continue
                direction = int(safe_float(pred.get("predicted_direction"), 0))
                action = normalize_agent_action(None, direction)
                target, stoploss = build_trade_levels(action)
                return {
                    "action": action,
                    "confidence": safe_float(pred.get("confidence"), 50),
                    "target": target,
                    "stoploss": stoploss,
                    "reason": f"Learning engine direction={direction}",
                    "source": "learning_engine",
                    "ensemble_signal": "tăng" if direction == 1 else "giảm" if direction == -1 else "neutral",
                    "confluence": confluence_score,
                }
    except Exception:
        pass

    if confluence_score >= 60:
        action = "MUA"
        confidence = min(90, safe_float(confluence_score, 60))
        target, stoploss = build_trade_levels(action)
        reason = f"Confluence {confluence_score}% đủ ngưỡng"
    elif confluence_score >= 40:
        action = "GIỮ"
        confidence = 40
        target = 0
        stoploss = 0
        reason = f"Confluence {confluence_score}% - tín hiệu chưa đủ mạnh"
    else:
        action = "GIỮ"
        confidence = 25
        target = 0
        stoploss = 0
        reason = f"Confluence chỉ {confluence_score}% - không đủ điều kiện"

    return {
        "action": action,
        "confidence": confidence,
        "target": target,
        "stoploss": stoploss,
        "reason": reason,
        "source": "rule_based",
        "ensemble_signal": "N/A",
        "confluence": confluence_score,
    }

def analyze_one(symbol):
    try:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        stock = get_vnstock_client(symbol, source='VCI')
        df_a = stock.history(start=start, end=end, interval='1D')
        if df_a is None or len(df_a) < 2:
            return f"Lỗi: không đủ dữ liệu giá cho {symbol}"
        df_a["time"] = pd.to_datetime(df_a["time"])
        df_a = df_a.sort_values("time").reset_index(drop=True)
        latest_a = df_a.iloc[-1]
        prev_a = df_a.iloc[-2]
        change_pct_a = ((latest_a["close"] - prev_a["close"]) / prev_a["close"]) * 100
        close_a = df_a["close"]
        sma20_a = safe_sma(close_a, 20)
        sma50_a = safe_sma(close_a, 50)
        signal_scores = compute_signal_scores(close_a, latest_a)
        ev_badge_text, _ = get_ev_badge(symbol)
        news_a = get_news(symbol)
        try:
            from data_fetcher import get_news_sentiment_score

            news_list = [item.get("title", "") for item in (news_a or [])[:5]]
            sentiment_score = get_news_sentiment_score(symbol, news_list)
        except Exception:
            sentiment_score = 0.0
        sentiment_signal = 1 if sentiment_score > 0.3 else (-1 if sentiment_score < -0.3 else 0)
        lstm_result = ensemble_direction_predict(symbol, df_a)
        lstm_result["sentiment_score"] = sentiment_score
        lstm_reliable = bool(lstm_result.get("reliable") and lstm_result.get("high_confidence"))
        indicators = build_indicator_snapshot(
            df_a,
            lstm_direction=lstm_result.get("direction") if lstm_reliable else 0,
            lstm_reliable=lstm_reliable,
        )
        indicators.update(signal_scores)
        indicators["sentiment_signal"] = sentiment_signal
        indicators["sentiment_score"] = sentiment_score
        confluence = calculate_confluence(indicators)
        indicators["confluence_score"] = confluence["confluence_score"]
        indicators["net_direction"] = confluence["net_direction"]
        atr = calculate_atr_from_df(df_a) or float(latest_a["close"]) * 0.03
        reflection_context = REFLECTION.build_reflection_context(symbol)
        market_regime = get_market_regime_cached()

        analysis = get_analysis_for_ticker(symbol, confluence["confluence_score"], float(latest_a["close"]), atr)
        analysis_source = analysis.get("source", "rule_based")

        if analysis_source in {"two_stage", "learning_engine"}:
            technical = {
                "signal": confluence["net_direction"],
                "strength": confluence["confluence_score"],
                "reasons": [f"Dùng tín hiệu {analysis_source} thay cho fallback confluence"],
            }
            sentiment = {
                "sentiment": "positive" if sentiment_score > 0.3 else "negative" if sentiment_score < -0.3 else "neutral",
                "score": sentiment_score,
                "key_event": "Không gọi sentiment agent đầy đủ vì đã có tín hiệu two-stage/learning engine",
            }
            risk = {
                "action": {"MUA": "BUY", "BÁN": "SELL", "GIỮ": "HOLD"}.get(analysis.get("action"), "HOLD"),
                "confidence": analysis.get("confidence", 50),
                "position_size_pct": 0 if analysis.get("action") == "GIỮ" else 5,
                "stop_loss_atr_mult": 2.0,
                "hold_days": 3,
                "reasoning": analysis.get("reason", ""),
            }
            ollama_called = False
        elif confluence["confluence_score"] >= 60:
            technical = ollama_technical_agent(symbol, indicators, reflection_context)
            sentiment = ollama_sentiment_agent(symbol, news_a, reflection_context)
            if abs(safe_float(sentiment.get("score"), 0.0)) < abs(sentiment_score):
                sentiment["score"] = sentiment_score
            risk = ollama_risk_agent(
                symbol,
                technical,
                sentiment,
                lstm_result,
                portfolio_exposure_pct(symbol),
                market_regime,
                reflection_context,
            )
            ollama_called = True
        else:
            technical = {
                "signal": confluence["net_direction"],
                "strength": confluence["confluence_score"],
                "reasons": [analysis.get("reason", "Confluence chưa đủ ngưỡng")],
            }
            sentiment = {
                "sentiment": "positive" if sentiment_score > 0.3 else "negative" if sentiment_score < -0.3 else "neutral",
                "score": sentiment_score,
                "key_event": "LLM sentiment nhanh từ tin tức; không gọi sentiment agent đầy đủ do confluence thấp",
            }
            risk = {
                "action": "HOLD",
                "confidence": analysis.get("confidence", 25),
                "position_size_pct": 0,
                "stop_loss_atr_mult": 2.0,
                "hold_days": 3,
                "reasoning": analysis.get("reason", "Tín hiệu kỹ thuật chưa đủ đồng thuận."),
            }
            ollama_called = False

        action_map = {"BUY": "MUA", "SELL": "BÁN", "HOLD": "GIỮ"}
        prediction = action_map.get(risk.get("action", "HOLD"), "GIỮ")
        confidence = int(risk.get("confidence", 35))
        if analysis_source in {"two_stage", "learning_engine", "rule_based"}:
            target = analysis.get("target", 0)
            stoploss = analysis.get("stoploss", 0)
        else:
            target = 0
            stoploss = 0
        target_pct = 0.04 + (confidence / 100 * 0.08)
        if not target and prediction == "MUA":
            target = round(float(latest_a["close"]) * (1 + target_pct), 2)
            stoploss = round(float(latest_a["close"]) - atr * float(risk.get("stop_loss_atr_mult", 2.0)), 2)
        elif not target and prediction == "BÁN":
            target = round(float(latest_a["close"]) * (1 - target_pct), 2)
            stoploss = round(float(latest_a["close"]) + atr * float(risk.get("stop_loss_atr_mult", 2.0)), 2)
        elif prediction == "GIỮ":
            target = 0
            stoploss = 0
        timeframe = f"{int(risk.get('hold_days', 3))} phiên"
        model_label = "Ensemble" if lstm_result.get("model_type") == "ensemble" else "LSTM"
        vote_items = [
            f"{name.upper()}={'UP' if vote == 1 else 'DOWN'}"
            for name, vote in (lstm_result.get("votes") or {}).items()
            if isinstance(vote, int)
        ]
        vote_line = f"Votes: {', '.join(vote_items)}" if vote_items else "Votes: chưa có ensemble model"
        lstm_line = (
            f"{model_label} Direction: {'TĂNG' if lstm_result.get('direction') == 1 else 'GIẢM'} "
            f"(prob={lstm_result.get('probability'):.1%}, confidence={lstm_result.get('confidence')}%)"
            if lstm_reliable
            else f"{model_label} Direction: không dùng vào confluence - "
            f"{'confidence thấp' if lstm_result.get('reliable') else lstm_result.get('message', 'model chưa reliable')}"
        )
        result_lines = [
            f"Khuyến nghị: {prediction}",
            f"Mục tiêu chốt lời: {target}",
            f"Cắt lỗ tại: {stoploss}",
            f"Thời gian nắm giữ: {timeframe}",
            f"Độ tin cậy: {confidence}%",
            f"Backtest EV: {ev_badge_text}",
            f"Confluence: {confluence['confluence_score']}% ({confluence['net_direction']})",
            f"Technical: {technical.get('signal')} {technical.get('strength')}/100",
            f"Sentiment: {sentiment.get('sentiment')} ({sentiment.get('score')}) - {sentiment.get('key_event')}",
            f"News sentiment score: {sentiment_score:+.2f}",
            f"Market regime: {market_regime.get('regime')} - {market_regime.get('recommendation')}",
            f"Source: {analysis_source}",
            lstm_line,
            vote_line,
            f"Lý do: {risk.get('reasoning')}",
        ]
        result = "\n".join(result_lines)
        save_prediction(
            symbol, float(latest_a['close']),
            prediction,
            target,
            stoploss,
            timeframe,
            confidence=confidence,
            signal_scores=signal_scores,
            ai_result=result,
            confluence=confluence,
            agent_outputs={
                "technical": technical,
                "sentiment": sentiment,
                "risk": risk,
                "ollama_called": ollama_called,
                "sentiment_score": sentiment_score,
            },
            source=analysis_source,
            action=prediction,
            reason=analysis.get("reason", risk.get("reasoning", "")),
            ensemble_signal=analysis.get("ensemble_signal", "N/A"),
            stage1_score=analysis.get("stage1_score"),
            market_regime=market_regime,
            lstm_reliable=lstm_reliable,
            lstm_direction=lstm_result.get("direction") if lstm_reliable else 0,
            lstm_probability=lstm_result.get("probability"),
            lstm_signal=lstm_result.get("signal"),
            atr=atr,
        )
        return result
    except Exception as e:
        return f"Lỗi: {e}"

def load_auto_analysis_state():
    if os.path.exists(AUTO_ANALYSIS_STATE_FILE):
        with open(AUTO_ANALYSIS_STATE_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_auto_analysis_state(state):
    with open(AUTO_ANALYSIS_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def should_run_auto_analysis():
    now = datetime.now()
    scheduled_at = now.replace(
        hour=AUTO_ANALYSIS_HOUR,
        minute=AUTO_ANALYSIS_MINUTE,
        second=0,
        microsecond=0
    )
    if now < scheduled_at:
        return False
    today_key = now.strftime("%Y-%m-%d")
    state = load_auto_analysis_state()
    today_state = state.get(today_key, {})
    return today_state.get("status") != "completed"

def run_auto_analysis_if_due(symbols, ollama_ok, list_name="watchlist"):
    # On the Streamlit Cloud viewer the GitHub Actions scheduler owns analysis —
    # never run it on page load (it would re-fire on every reload and burn LLM).
    try:
        import cloud_bootstrap

        if cloud_bootstrap.is_cloud_viewer():
            return
    except Exception:
        pass
    if not should_run_auto_analysis():
        return
    now = datetime.now()
    today_key = now.strftime("%Y-%m-%d")
    state = load_auto_analysis_state()

    if not ollama_ok:
        set_system_status("waiting_for_ollama", "Đang chờ LLM Router", "Chưa thể phân tích bù watchlist hôm nay")
        state[today_key] = {
            "status": "waiting_for_ollama",
            "last_checked": now.strftime("%Y-%m-%d %H:%M")
        }
        save_auto_analysis_state(state)
        st.warning("AI tự động đang chờ LLM Router khả dụng để phân tích bù hôm nay.")
        return

    state[today_key] = {
        "status": "running",
        "started_at": now.strftime("%Y-%m-%d %H:%M"),
        "symbols": symbols,
        "list_name": list_name
    }
    save_auto_analysis_state(state)

    with st.spinner(f"AI tự động đang phân tích {len(symbols)} mã trong {list_name} cho ngày {today_key}..."):
        progress = st.progress(0)
        for i, sym in enumerate(symbols):
            set_system_status(
                "auto_analysis",
                f"AI đang phân tích {sym}",
                f"Mã {i + 1}/{len(symbols)} trong {list_name}"
            )
            st.write(f"Tự động phân tích {sym}...")
            analyze_one(sym)
            progress.progress((i + 1) / len(symbols))

    state = load_auto_analysis_state()
    state[today_key] = {
        "status": "completed",
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "symbols": symbols,
        "list_name": list_name
    }
    save_auto_analysis_state(state)
    st.success(f"AI tự động đã phân tích xong {list_name} hôm nay.")

    set_system_status("idle", "AI tự động đã phân tích xong", f"Hoàn tất {len(symbols)} mã trong {list_name}")

def live_refresh_interval(seconds, enabled):
    return f"{int(seconds)}s" if enabled else None

def fetch_symbol_history(symbol, start_date, end_date):
    try:
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        years = max(0.1, (end_dt - start_dt).days / 365)
        df_live = get_stock_data_st(symbol, years=years)
        if df_live is None or len(df_live) == 0:
            return pd.DataFrame()
        df_live = df_live.copy()
        df_live["time"] = pd.to_datetime(df_live["time"])
        df_live = df_live[(df_live["time"] >= start_dt) & (df_live["time"] <= end_dt)]
        return df_live.sort_values("time")
    except BaseException as exc:
        set_system_status("rate_limited", "VNStock tạm thời bị giới hạn", str(exc)[:250])
        return pd.DataFrame()

def render_live_header_metrics():
    symbol = st.session_state.get("selected_symbol", "VNM")
    interval = st.session_state.get("selected_interval", "1M")
    days = {"1D": 1, "1W": 7, "1M": 30, "3M": 90}.get(interval, 30)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    _hdr_src = source_manager.get_indicator()
    st.markdown(
        f'<div class="app-header">'
        f'<div>'
        f'<div class="app-kicker">Bảng điều khiển giao dịch</div>'
        f'<div class="app-title">{symbol} Market Overview</div>'
        f'<div class="app-subtitle">VN Market · {datetime.now().strftime("%d/%m/%Y")}</div>'
        f'</div>'
        f'<div style="display:flex;gap:0.45rem;align-items:center;flex-wrap:wrap;justify-content:flex-end;">'
        f'<span class="header-pill">Khung: {interval}</span>'
        f'<span class="header-pill">Cập nhật: {datetime.now().strftime("%H:%M:%S")}</span>'
        f'<span class="header-pill" style="color:{_hdr_src[2]};border-color:{_hdr_src[2]}33;">● {_hdr_src[0]}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True
    )
    try:
        df_live = fetch_symbol_history(symbol, start_date, end_date)
    except BaseException as exc:
        set_system_status("rate_limited", "Header tạm dừng do VNStock", str(exc)[:250])
        st.warning("VNStock đang rate limit. Header tạm dừng, chờ một lát rồi refresh lại.")
        return
    if df_live is None or df_live.empty:
        st.warning("VNStock đang rate limit hoặc chưa trả dữ liệu. Chờ một lát rồi refresh lại.")
        return
    latest = df_live.iloc[-1]
    prev = df_live.iloc[-2] if len(df_live) > 1 else latest
    change_pct = ((latest["close"] - prev["close"]) / prev["close"]) * 100
    avg_volume_20 = df_live["volume"].rolling(20).mean().iloc[-1] if len(df_live) >= 20 else None
    vol_ratio = latest["volume"] / avg_volume_20 if avg_volume_20 else None
    try:
        vni_price, vni_chg, _ = get_vnindex()
    except BaseException:
        vni_price, vni_chg = None, None
    market_regime = get_market_regime_cached()
    latest_record = latest_predictions_by_symbol().get(symbol.upper(), {})
    conf = latest_record.get("confidence")
    conf_text = f"{conf:.0f}%" if conf is not None else "-"
    verdict = verdict_label(normalize_prediction(latest_record.get("prediction")))
    # Fix #3 – low confidence (<40%) signals are unreliable; mute them visually
    _conf_is_low = conf is not None and conf < 40
    action_color = "#22c55e" if "MUA" in verdict.upper() else "#f43f5e" if "BÁN" in verdict.upper() else "#f59e0b"
    if _conf_is_low:
        action_color = "#64748b"  # muted – signal not trustworthy
    price_color = "#22c55e" if change_pct >= 0 else "#f43f5e"
    vol_color = "#22c55e" if (vol_ratio or 0) > 1.2 else "#f59e0b" if (vol_ratio or 0) > 0.8 else "#94a3b8"
    regime_color_map = {
        "BULL_TREND": "#22c55e",
        "BEAR_TREND": "#f43f5e",
        "HIGH_VOL_RANGING": "#f59e0b",
        "LOW_VOL_RANGING": "#94a3b8",
        "UNKNOWN": "#94a3b8",
    }
    regime_value = market_regime.get("regime", "UNKNOWN")
    regime_color = regime_color_map.get(regime_value, "#94a3b8")

    col_p1, col_p2, col_p3, col_p4 = st.columns([2.2, 1.2, 1.2, 1.6])
    with col_p1:
        st.markdown(
            f'<div class="metric-box">'
            f'<div class="label">Giá hiện tại</div>'
            f'<div class="value">{latest["close"]:,.1f}</div>'
            f'<div class="delta" style="color:{price_color};font-weight:800;">'
            f'{"▲" if change_pct >= 0 else "▼"} {change_pct:+.2f}%</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with col_p2:
        if vol_ratio:
            st.markdown(
                f'<div class="metric-box">'
                f'<div class="label">Volume</div>'
                f'<div class="value" style="font-size:1.18rem;color:{vol_color};">{latest["volume"]/1e6:,.1f}M</div>'
                f'<div class="delta" style="color:#94a3b8;">{vol_ratio:.1f}x TB20</div>'
                f'</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '<div class="metric-box"><div class="label">Volume</div><div class="value">N/A</div><div class="delta">Chưa đủ dữ liệu</div></div>',
                unsafe_allow_html=True
            )
    with col_p3:
        st.markdown(
            f'<div class="metric-box">'
            f'<div class="label">Khung</div>'
            f'<div class="value" style="font-size:1.18rem;">{interval}</div>'
            f'<div class="delta" style="color:#94a3b8;">Cập nhật realtime</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with col_p4:
        _low_warn = ' <span style="font-size:0.68rem;background:rgba(244,63,94,0.12);color:#f87171;border-radius:4px;padding:0.1rem 0.35rem;vertical-align:middle;">⚠ thấp</span>' if _conf_is_low else ""
        _box_border = "border-color:rgba(244,63,94,0.22);" if _conf_is_low else ""
        st.markdown(
            f'<div class="metric-box" style="{_box_border}">'
            f'<div class="label">AI verdict</div>'
            f'<div class="value" style="font-size:1.2rem;color:{action_color};">{verdict}{_low_warn}</div>'
            f'<div class="delta" style="color:#94a3b8;">Tin cậy {conf_text}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    sec1, sec2, sec3, sec4 = st.columns(4)
    with sec1:
        st.markdown(
            f'<div class="metric-box"><div class="label">Cao nhất</div>'
            f'<div class="value">{latest["high"]:,.1f}</div>'
            f'<div class="delta" style="color:#94a3b8;">Trong phiên</div></div>',
            unsafe_allow_html=True
        )
    with sec2:
        st.markdown(
            f'<div class="metric-box"><div class="label">Thấp nhất</div>'
            f'<div class="value">{latest["low"]:,.1f}</div>'
            f'<div class="delta" style="color:#94a3b8;">Trong phiên</div></div>',
            unsafe_allow_html=True
        )
    with sec3:
        if vni_price is not None:
            st.markdown(
                f'<div class="metric-box"><div class="label">VN-Index</div>'
                f'<div class="value">{vni_price:,.1f}</div>'
                f'<div class="delta" style="color:{("#22c55e" if vni_chg >= 0 else "#f43f5e")};">'
                f'{"▲" if vni_chg >= 0 else "▼"} {vni_chg:+.2f}%</div></div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '<div class="metric-box"><div class="label">VN-Index</div><div class="value">N/A</div><div class="delta">Không lấy được dữ liệu</div></div>',
                unsafe_allow_html=True
            )
    with sec4:
        # Fix #4 – prevent long regime names (BEAR_TREND) from wrapping mid-word
        _regime_short = regime_value.replace("_TREND", "").replace("_RANGING", " RNG")
        _regime_icon_map = {"BULL": "🟢", "BEAR": "🔴", "HIGH": "🟠", "LOW": "🟡"}
        _regime_icon = next((v for k, v in _regime_icon_map.items() if k in regime_value), "")
        st.markdown(
            f'<div class="metric-box"><div class="label">Regime</div>'
            f'<div class="value" style="font-size:1.08rem;color:{regime_color};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
            f'{_regime_icon} {_regime_short}</div>'
            f'<div class="delta" style="color:#94a3b8;font-size:0.7rem;line-height:1.3;">{market_regime.get("recommendation","")}</div></div>',
            unsafe_allow_html=True
        )
def render_live_chart():
    symbol = st.session_state.get("selected_symbol", "VNM")
    interval = st.session_state.get("selected_interval", "1M")
    days = {"1D": 1, "1W": 7, "1M": 30, "3M": 90}.get(interval, 30)
    end_date = datetime.now().strftime("%Y-%m-%d")
    visible_start = datetime.now() - timedelta(days=days)
    indicator_start = datetime.now() - timedelta(days=max(days, 90))
    start_date = indicator_start.strftime("%Y-%m-%d")
    try:
        df_live = fetch_symbol_history(symbol, start_date, end_date)
    except BaseException as exc:
        set_system_status("rate_limited", "Chart tạm dừng do VNStock", str(exc)[:250])
        st.warning("VNStock đang rate limit. Biểu đồ tạm dừng, chờ một lát rồi refresh lại.")
        return
    if df_live is None or df_live.empty:
        st.warning("VNStock chưa trả dữ liệu biểu đồ. Chờ một lát rồi refresh lại.")
        return
    close = df_live["close"]
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    histogram = macd - signal
    avg_volume_20 = df_live["volume"].rolling(20).mean().iloc[-1] if len(df_live) >= 20 else None
    vol_ratio = float(df_live["volume"].iloc[-1] / avg_volume_20) if avg_volume_20 else None

    latest_record = latest_predictions_by_symbol().get(symbol.upper(), {})
    target_price = safe_float(latest_record.get("target"))
    stoploss_price = safe_float(latest_record.get("stoploss"))
    entry_price = safe_float(latest_record.get("price_at_prediction"))

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df_live["time"], open=df_live["open"], high=df_live["high"], low=df_live["low"], close=df_live["close"],
        increasing_line_color="#22c55e", decreasing_line_color="#f43f5e",
        name="Giá", line=dict(width=1)
    ))
    fig.add_trace(go.Scatter(x=df_live["time"], y=sma20, name="SMA20", line=dict(color="#38bdf8", width=1.5)))
    fig.add_trace(go.Scatter(x=df_live["time"], y=sma50, name="SMA50", line=dict(color="#f59e0b", width=1.5)))
    fig.add_trace(go.Scatter(x=df_live["time"], y=bb_upper, name="BB Trên", line=dict(color="#94a3b8", width=1, dash="dash")))
    fig.add_trace(go.Scatter(x=df_live["time"], y=bb_lower, name="BB Dưới", line=dict(color="#94a3b8", width=1, dash="dash"), fill="tonexty", fillcolor="rgba(148,163,184,0.06)"))
    if target_price > 0:
        fig.add_hline(y=target_price, line_dash="dash", line_color="#22c55e", annotation_text=f"Target {target_price:,.1f}", annotation_position="right")
    if stoploss_price > 0:
        fig.add_hline(y=stoploss_price, line_dash="dash", line_color="#f43f5e", annotation_text=f"Stop {stoploss_price:,.1f}", annotation_position="right")
    if entry_price > 0:
        fig.add_hline(y=entry_price, line_dash="dot", line_color="#f59e0b", annotation_text=f"Entry {entry_price:,.1f}", annotation_position="right")
    fig.update_layout(title=f"<b>{symbol}</b> | {interval}", template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=40, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#a8b2d1"), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(gridcolor="#1e2140", showgrid=True)
    fig.update_yaxes(gridcolor="#1e2140", showgrid=True)
    fig.update_xaxes(range=[visible_start, datetime.now()])
    st.plotly_chart(fig, width='stretch')

    if len(df_live) >= 20:
        fig_vol = go.Figure()
        vol_colors = ["#22c55e" if c >= o else "#f43f5e" for c, o in zip(df_live["close"], df_live["open"])]
        fig_vol.add_trace(go.Bar(x=df_live["time"], y=df_live["volume"], marker_color=vol_colors, name="Volume", opacity=0.85))
        fig_vol.add_trace(go.Scatter(x=df_live["time"], y=df_live["volume"].rolling(20).mean(), name="Vol MA20", line=dict(color="#38bdf8", width=1.2)))
        fig_vol.update_layout(title="<b>Volume</b>", template="plotly_dark", height=160, margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#a8b2d1"), hovermode="x unified", showlegend=False)
        fig_vol.update_xaxes(gridcolor="#1e2140", showgrid=True)
        fig_vol.update_yaxes(gridcolor="#1e2140", showgrid=True)
        fig_vol.update_xaxes(range=[visible_start, datetime.now()])
        st.plotly_chart(fig_vol, width='stretch')

    fig_macd = go.Figure()
    colors = ["#22c55e" if v >= 0 else "#f43f5e" for v in histogram]
    fig_macd.add_trace(go.Bar(x=df_live["time"], y=histogram, name="Histogram", marker_color=colors, opacity=0.8))
    fig_macd.add_trace(go.Scatter(x=df_live["time"], y=macd, name="MACD", line=dict(color="#38bdf8", width=1.5)))
    fig_macd.add_trace(go.Scatter(x=df_live["time"], y=signal, name="Signal", line=dict(color="#f59e0b", width=1.5)))
    fig_macd.update_layout(title="<b>MACD</b>", template="plotly_dark", height=250, margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#a8b2d1"), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig_macd.update_xaxes(gridcolor="#1e2140", showgrid=True)
    fig_macd.update_yaxes(gridcolor="#1e2140", showgrid=True)
    fig_macd.update_xaxes(range=[visible_start, datetime.now()])
    st.plotly_chart(fig_macd, width='stretch')

    sma20_last = safe_sma(close, 20)
    sma50_last = safe_sma(close, 50)
    if sma20_last is None or sma50_last is None:
        trend_text = "Chưa đủ data"
        trend_cls = "warn"
    else:
        trend_text = "Tăng" if sma20_last >= sma50_last else "Giảm"
        trend_cls = "up" if trend_text == "Tăng" else "down"
    momentum_val = 0 if pd.isna(histogram.iloc[-1]) else float(histogram.iloc[-1])
    momentum_cls = "up" if momentum_val >= 0 else "down"
    vol_ratio_text = f"{vol_ratio:.1f}x avg" if vol_ratio else "Chưa đủ dữ liệu"
    vol_cls = "up" if (vol_ratio or 0) > 1.2 else "warn"
    st.markdown(
        f'<div class="signal-summary">'
        f'<div class="signal-chip"><div class="k">Trend</div><div class="v {trend_cls}">{trend_text}</div></div>'
        f'<div class="signal-chip"><div class="k">Momentum</div><div class="v {momentum_cls}">MACD {momentum_val:+.2f}</div></div>'
        f'<div class="signal-chip"><div class="k">Volume</div><div class="v {vol_cls}">{vol_ratio_text}</div></div>'
        f'</div>',
        unsafe_allow_html=True
    )
    with st.expander("Giải thích chỉ báo", expanded=False):
        st.markdown(
            "- Nến xanh: đóng cửa cao hơn mở cửa.\n"
            "- SMA20 / SMA50: xu hướng ngắn và trung hạn.\n"
            "- Bollinger Bands: biên dao động; chạm dải trên thường nóng hơn, dải dưới thường yếu hơn.\n"
            "- MACD / Signal: MACD cắt lên Signal thường cải thiện động lượng."
        )

@st.fragment(run_every=live_refresh_interval(refresh_sec, background_refresh))
def render_live_news():
    symbol = st.session_state.get("selected_symbol", "VNM")
    st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Tin tức mới nhất</div>', unsafe_allow_html=True)
    news_list = get_news(symbol)
    if news_list:
        relevant = [a for a in news_list if symbol.lower() in a["title"].lower()]
        other = [a for a in news_list if a not in relevant]
        if relevant:
            st.markdown(f'<div style="color:#f0b90b;font-weight:600;margin-bottom:0.5rem;">🎯 Tin liên quan {symbol}</div>', unsafe_allow_html=True)
            for article in relevant[:5]:
                st.markdown(f'<div class="card card-glow"><div style="display:flex;justify-content:space-between;align-items:start;"><div style="font-weight:600;color:#e2e8f0;font-size:0.9rem;">{article["title"]}</div></div><div style="color:#c8d2e0;font-size:0.75rem;margin:0.3rem 0;">📰 {article.get("source","")} | 🕐 {article["published"]}</div><div style="color:#e2e8f0;font-size:0.85rem;">{article["summary"]}</div><a href="{article["link"]}" target="_blank" style="color:#667eea;font-size:0.8rem;text-decoration:none;">🔗 Đọc thêm →</a></div>', unsafe_allow_html=True)
        if other:
            st.markdown(f'<div style="color:#e2e8f0;font-weight:600;margin:0.8rem 0 0.5rem 0;">Tin thị trường chung</div>', unsafe_allow_html=True)
            for article in other[:5]:
                st.markdown(f'<div class="card"><div style="font-weight:600;color:#e2e8f0;font-size:0.9rem;">{article["title"]}</div><div style="color:#c8d2e0;font-size:0.75rem;margin:0.3rem 0;">📰 {article.get("source","")} | 🕐 {article["published"]}</div><div style="color:#e2e8f0;font-size:0.85rem;">{article["summary"]}</div><a href="{article["link"]}" target="_blank" style="color:#667eea;font-size:0.8rem;text-decoration:none;">🔗 Đọc thêm →</a></div>', unsafe_allow_html=True)
    else:
        st.warning("Không lấy được tin tức.")

@st.fragment
def render_history_section():
    st.markdown('<div style="display:flex;align-items:center;gap:1rem;margin-bottom:0.5rem;"><span class="section-title">Lịch sử dự đoán & Độ chính xác</span></div>', unsafe_allow_html=True)
    view = st.radio("Hiển thị", ["AI Signals", "Tất cả"], horizontal=True, key="history_view_mode")

    def history_source(record):
        return str(record.get("source") or "rule_based")

    def history_action(record):
        action = record.get("action")
        if action:
            return normalize_prediction(action)
        return normalize_prediction(record.get("prediction"))

    def is_ai_signal(record):
        source = history_source(record)
        action = history_action(record)
        return source in {"two_stage", "learning_engine"} or action in {"MUA", "BAN"}

    def ai_signal_accuracy_rows(records):
        rows = []
        for item in records:
            if item.get("correct") is None:
                continue
            if item.get("source") == "rule_based":
                continue
            if not is_ai_signal(item):
                continue
            dt = parse_history_date(item.get("evaluated_at")) or parse_history_date(item.get("date"))
            if dt:
                rows.append({"date": dt.date(), "correct": bool(item.get("correct"))})
        return pd.DataFrame(rows)

    history_all = load_history()
    history_display = [h for h in history_all if is_ai_signal(h)] if view == "AI Signals" else history_all
    history_display_sorted = sorted(
        history_display,
        key=lambda h: parse_history_date(h.get("date")) or datetime.min,
        reverse=True
    )
    ai_history = [h for h in history_all if is_ai_signal(h)]
    ai_resolved = [h for h in ai_history if h.get("correct") is not None]
    ai_counts = {"MUA": 0, "BAN": 0, "GIU": 0}
    for item in ai_history:
        ai_counts[history_action(item)] = ai_counts.get(history_action(item), 0) + 1
    ai_accuracy_df = ai_signal_accuracy_rows(history_all)
    ai_accuracy = None
    if not ai_accuracy_df.empty:
        ai_accuracy = float(ai_accuracy_df["correct"].mean() * 100)

    summary_cols = st.columns(4)
    with summary_cols[0]:
        st.markdown(
            f'<div class="metric-box"><div class="label">Tổng AI signals</div>'
            f'<div class="value">{len(ai_history)}</div>'
            f'<div class="delta" style="color:#8892b0;">MUA: {ai_counts.get("MUA", 0)} | BÁN: {ai_counts.get("BAN", 0)} | GIỮ: {ai_counts.get("GIU", 0)}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with summary_cols[1]:
        acc_color = "#00d4aa" if (ai_accuracy or 0) >= 50 else "#ff6b6b"
        acc_text = f"{ai_accuracy:.1f}%" if ai_accuracy is not None else "N/A"
        resolved_text = f"{len(ai_resolved)} resolved"
        st.markdown(
            f'<div class="metric-box"><div class="label">AI Accuracy</div>'
            f'<div class="value" style="color:{acc_color};">{acc_text}</div>'
            f'<div class="delta" style="color:#8892b0;">{resolved_text}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with summary_cols[2]:
        st.markdown(
            f'<div class="metric-box"><div class="label">Đang hiển thị</div>'
            f'<div class="value">{len(history_display_sorted)}</div>'
            f'<div class="delta" style="color:#8892b0;">{"AI Signals" if view == "AI Signals" else "Tất cả"}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with summary_cols[3]:
        st.markdown(
            f'<div class="metric-box"><div class="label">Resolved AI</div>'
            f'<div class="value">{len(ai_resolved)}</div>'
            f'<div class="delta" style="color:#8892b0;">Chỉ source ≠ rule_based</div>'
            f'</div>',
            unsafe_allow_html=True
        )

    col_h1, col_h2 = st.columns([1, 3])
    with col_h1:
        if st.button("🔄 Cập nhật kết quả", width='stretch'):
            updated = update_results(force=True)
            if updated:
                st.success(f"Đã cập nhật {updated} dự đoán")
            else:
                st.info("Không có dự đoán cần cập nhật")
        if ai_accuracy is not None and len(ai_resolved) > 0:
            acc_color = "#00d4aa" if ai_accuracy >= 50 else "#ff6b6b"
            st.markdown(
                f'<div class="metric-box" style="margin-top:0.5rem;">'
                f'<div class="label">Độ chính xác</div>'
                f'<div class="value" style="color:{acc_color};">{ai_accuracy:.1f}%</div>'
                f'<div class="delta" style="color:#8892b0;">{sum(1 for h in ai_resolved if h.get("correct"))}/{len(ai_resolved)} đúng</div>'
                f'</div>',
                unsafe_allow_html=True
            )
        else:
            st.info("Chưa có dữ liệu.")
        df_acc_time = ai_accuracy_df.copy()
        if not df_acc_time.empty:
            daily = df_acc_time.groupby("date").agg(total=("correct", "count"), correct=("correct", "sum")).reset_index()
            daily["accuracy"] = daily["correct"] / daily["total"] * 100
            daily["rolling_accuracy"] = daily["correct"].cumsum() / daily["total"].cumsum() * 100
            fig_acc = go.Figure()
            fig_acc.add_trace(go.Scatter(
                x=daily["date"],
                y=daily["rolling_accuracy"],
                mode="lines+markers",
                name="Rolling accuracy",
                line=dict(color="#38bdf8", width=3),
                marker=dict(size=7)
            ))
            fig_acc.add_trace(go.Bar(
                x=daily["date"],
                y=daily["accuracy"],
                name="Daily accuracy",
                marker_color="rgba(34,197,94,0.35)"
            ))
            fig_acc.update_layout(
                height=260,
                margin=dict(l=10, r=10, t=20, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#a8b2d1"),
                yaxis=dict(title="%", range=[0, 100], gridcolor="#1e2140"),
                xaxis=dict(title="", gridcolor="#1e2140"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
            )
            st.plotly_chart(fig_acc, width='stretch')
    with col_h2:
        if history_display_sorted:
            page_size = st.selectbox(
                "So dong moi trang",
                [10, 20, 50, 100],
                index=1,
                key="history_page_size"
            )
            if st.session_state.get("history_page_size_prev") != page_size:
                st.session_state["history_page"] = 1
                st.session_state["history_page_size_prev"] = page_size

            total_rows = len(history_display_sorted)
            total_pages = max(1, (total_rows + page_size - 1) // page_size)
            current_page = int(st.session_state.get("history_page", 1))
            current_page = max(1, min(current_page, total_pages))
            st.session_state["history_page"] = current_page

            nav_first, nav_prev, nav_info, nav_next, nav_last = st.columns([1, 1, 2, 1, 1])
            with nav_first:
                if st.button("Dau", width='stretch', disabled=current_page <= 1):
                    st.session_state["history_page"] = 1
                    st.rerun(scope="fragment")
            with nav_prev:
                if st.button("Truoc", width='stretch', disabled=current_page <= 1):
                    st.session_state["history_page"] = current_page - 1
                    st.rerun(scope="fragment")
            with nav_info:
                st.markdown(
                    f'<div style="text-align:center;color:#c8d2e0;font-weight:700;padding-top:0.45rem;">'
                    f'Trang {current_page}/{total_pages}</div>',
                    unsafe_allow_html=True
                )
            with nav_next:
                if st.button("Sau", width='stretch', disabled=current_page >= total_pages):
                    st.session_state["history_page"] = current_page + 1
                    st.rerun(scope="fragment")
            with nav_last:
                if st.button("Cuoi", width='stretch', disabled=current_page >= total_pages):
                    st.session_state["history_page"] = total_pages
                    st.rerun(scope="fragment")

            current_page = int(st.session_state.get("history_page", 1))
            start_idx = (current_page - 1) * page_size
            end_idx = min(start_idx + page_size, total_rows)
            page_records = history_display_sorted[start_idx:end_idx]
            st.caption(f"Hien thi {start_idx + 1}-{end_idx} / {total_rows} du doan")

            df_hist = pd.DataFrame(page_records)
            df_hist = df_hist.reindex(columns=["date","symbol","price_at_prediction","prediction","confidence","target","stoploss","source","outcome","actual_price","correct"])
            df_hist.columns = ["Ngày","Mã","Giá vào","Dự đoán","Tin cậy","Target","Stoploss","Source","Outcome","Giá thực","Kết luận"]
            def source_badge(value):
                return {
                    "two_stage": "🔵 AI",
                    "learning_engine": "🟢 Ensemble",
                    "ensemble": "🟢 Ensemble",
                    "rule_based": "Rule",
                }.get(str(value or "rule_based"), str(value or "Rule"))
            def outcome_badge(row):
                outcome = row.get("Outcome")
                if outcome == "correct":
                    return "✅"
                if outcome == "incorrect":
                    return "❌"
                if row.get("Kết luận") is True:
                    return "✅"
                if row.get("Kết luận") is False:
                    return "❌"
                return ""
            def conclusion_row(row):
                val = row["Kết luận"]
                if val is None and normalize_prediction(row.get("Dự đoán")) == "N/A" and pd.notna(row.get("Giá thực")):
                    return '<span style="color:#94a3b8;font-weight:700;">Không chấm</span>'
                if val is True:
                    return '<span style="color:#00d4aa;font-weight:700;">✅ ĐÚNG</span>'
                elif val is False:
                    return '<span style="color:#ff6b6b;font-weight:700;">❌ SAI</span>'
                else:
                    return '<span style="color:#f0b90b;font-weight:600;">⏳ Đang chờ</span>'
            df_hist["Kết luận"] = df_hist.apply(conclusion_row, axis=1)
            df_hist["Source"] = df_hist["Source"].apply(source_badge)
            df_hist["Outcome"] = df_hist.apply(outcome_badge, axis=1)
            def pred_label(val):
                v = str(val).upper()
                if "MUA" in v:
                    return f'<span style="color:#00d4aa;font-weight:700;">▲ {val}</span>'
                elif "BÁN" in v:
                    return f'<span style="color:#ff6b6b;font-weight:700;">▼ {val}</span>'
                else:
                    return f'<span style="color:#f0b90b;font-weight:600;">▬ {val}</span>'
            df_hist["Dự đoán"] = df_hist["Dự đoán"].apply(pred_label)
            df_hist["Tin cậy"] = df_hist["Tin cậy"].apply(lambda v: "" if pd.isna(v) else f"{float(v):.0f}%")
            df_hist["Target"] = df_hist["Target"].apply(lambda v: "" if pd.isna(v) or safe_float(v, 0) == 0 else f"{safe_float(v, 0):,.1f}")
            df_hist["Stoploss"] = df_hist["Stoploss"].apply(lambda v: "" if pd.isna(v) or safe_float(v, 0) == 0 else f"{safe_float(v, 0):,.1f}")
            st.write(df_hist.to_html(escape=False, index=False), unsafe_allow_html=True)
            st.markdown(
                '<style>table { width: 100%; border-collapse: collapse; font-size: 0.85rem; } '
                'th { background: #1a1d2e; color: #c8d2e0; padding: 0.6rem 0.5rem; text-align: left; border-bottom: 1px solid #2a2d4a; } '
                'td { padding: 0.5rem; border-bottom: 1px solid #1e2140; color: #e2e8f0; } '
                'tr:hover td { background: rgba(255,255,255,0.04); }</style>',
                unsafe_allow_html=True
            )
            st.markdown(
                """
                <div class="info-box" style="margin-top:0.85rem;">
                  <div class="section-title" style="margin-bottom:0.55rem;">Cách đọc bảng lịch sử</div>
                  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0.7rem;">
                    <div><b>Ngày</b><br><span style="color:#cbd5e1;">Thời điểm AI tạo dự đoán.</span></div>
                    <div><b>Mã</b><br><span style="color:#cbd5e1;">Mã cổ phiếu được phân tích.</span></div>
                    <div><b>Giá vào</b><br><span style="color:#cbd5e1;">Giá cổ phiếu tại lúc AI đưa ra dự đoán.</span></div>
                    <div><b>Dự đoán</b><br><span style="color:#cbd5e1;">Khuyến nghị của AI: MUA, BÁN, GIỮ hoặc N/A.</span></div>
                    <div><b>Target</b><br><span style="color:#cbd5e1;">Mục tiêu chốt lời AI đề xuất. Số 0 nghĩa là AI không đưa target rõ ràng.</span></div>
                    <div><b>Stoploss</b><br><span style="color:#cbd5e1;">Mức cắt lỗ AI đề xuất. Dùng để giới hạn rủi ro nếu giá đi ngược dự đoán.</span></div>
                    <div><b>Source</b><br><span style="color:#cbd5e1;">Nguồn tín hiệu: two-stage, ensemble hoặc rule-based.</span></div>
                    <div><b>Outcome</b><br><span style="color:#cbd5e1;">Kết quả sync từ learning engine khi dự đoán đã resolve.</span></div>
                    <div><b>Giá thực</b><br><span style="color:#cbd5e1;">Giá thực tế mới nhất khi hệ thống chấm lại dự đoán.</span></div>
                    <div><b>Kết luận</b><br><span style="color:#cbd5e1;">ĐÚNG/SAI/Đang chờ. Đang chờ nghĩa là chưa đủ dữ liệu hoặc chưa đến lúc chấm.</span></div>
                  </div>
                  <div style="color:#92a1b6;font-size:0.82rem;line-height:1.55;margin-top:0.75rem;">
                    Quy tắc chấm hiện tại: MUA đúng nếu giá thực cao hơn giá vào; BÁN đúng nếu giá thực thấp hơn giá vào;
                    GIỮ đúng nếu giá biến động trong khoảng ±3% so với giá vào.
                  </div>
                </div>
                """,
                unsafe_allow_html=True
            )
        else:
            st.info("Chưa có lịch sử.")

def render_auto_trader_section():
    st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Paper Auto Trader</div>', unsafe_allow_html=True)
    st.caption("Giao dịch giấy, không đặt lệnh thật. Vốn mặc định 100M VND.")

    default_symbols = normalize_symbol_list(st.session_state.get("training_watchlist", load_training_watchlist()))
    if not default_symbols:
        default_symbols = paper_trader.DEFAULT_SYMBOLS

    cfg1, cfg2, cfg3 = st.columns([3, 1, 1])
    with cfg1:
        symbol_text = st.text_area(
            "Danh sách mã auto trader",
            value=", ".join(default_symbols),
            height=90,
            key="paper_trader_symbols",
        )
    with cfg2:
        use_ollama_vote = st.checkbox("LLM vote", value=True, key="paper_trader_ollama")
        st.caption("LLM phân tích qua Groq / Gemini / Ollama")
    with cfg3:
        if st.button("Reset paper account", width='stretch'):
            paper_trader.reset_state()
            st.success("Đã reset tài khoản giấy về 100M VND.")

    trader_symbols = paper_trader.normalize_symbols(symbol_text) or default_symbols
    trader_tabs = st.tabs(["AI fund", "Signal scanner", "Portfolio & PnL", "Trade history"])
    with trader_tabs[0]:
        paper_trader.render_ai_fund(trader_symbols)
    with trader_tabs[1]:
        paper_trader.render_scanner(trader_symbols, use_ollama_vote, False)
    with trader_tabs[2]:
        paper_trader.render_portfolio()
    with trader_tabs[3]:
        paper_trader.render_history()

    analysis_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis_results.json")
    if os.path.exists(analysis_path):
        st.divider()
        st.subheader("Two-Stage Analysis")
        try:
            with open(analysis_path, "r", encoding="utf-8") as f:
                analysis = json.load(f)
            if analysis.get("method") == "two_stage":
                stage2 = analysis.get("stage2_results") or []
                tradeable = analysis.get("tradeable") or []
                horizon_sessions, horizon_label = _analysis_horizon_label(analysis)
                st.caption(
                    f"Stage 2 candidates: {len(stage2)} | Eligible for {horizon_label}: {len(tradeable)}"
                )
                if tradeable:
                    st.success(f"Eligible for {horizon_label}: {[row.get('ticker') for row in tradeable]}")
                else:
                    st.info(f"Không có mã đủ điều kiện cho horizon {horizon_label}")

                if stage2:
                    rows = []
                    for row in stage2:
                        llm = row.get("llm") or {}
                        ensemble = row.get("ensemble") or {}
                        rows.append(
                            {
                                "Ticker": row.get("ticker"),
                                "S1 Score": row.get("score"),
                                "Final": row.get("final_score"),
                                "RSI": row.get("rsi"),
                                "Ensemble": ensemble.get("signal", "N/A"),
                                "LLM": llm.get("action", "skip"),
                                "Confidence": llm.get("confidence", "-"),
                                "Debate": "Yes" if row.get("debate") else "No",
                                "Eligible 5-6 sessions": "Yes" if row.get("tradeable") else "No",
                            }
                        )
                    render_dark_table(pd.DataFrame(rows), key="two-stage")
                    render_two_stage_buy_actions(stage2)
        except Exception as exc:
            st.caption(f"Không đọc được analysis_results.json: {exc}")

def get_lstm_manager_watchlist():
    raw = []
    if os.path.exists(TRAINING_WATCHLIST_FILE):
        try:
            with open(TRAINING_WATCHLIST_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = []
    if isinstance(raw, dict):
        raw = list(raw.keys())
    if not raw:
        raw = st.session_state.get("training_watchlist", load_training_watchlist())
    return normalize_symbol_list(raw)

def get_all_lstm_status():
    rows = []
    for ticker in get_lstm_manager_watchlist():
        val_path = os.path.join(MODELS_DIR, f"{ticker}_validation.json")
        ensemble_val_path = os.path.join(MODELS_DIR, f"{ticker}_ensemble_validation.json")
        model_path = os.path.join(MODELS_DIR, f"{ticker}_direction_model.h5")
        xgb_path = os.path.join(MODELS_DIR, f"{ticker}_xgb.pkl")
        lgbm_path = os.path.join(MODELS_DIR, f"{ticker}_lgbm.pkl")
        rf_path = os.path.join(MODELS_DIR, f"{ticker}_rf.pkl")
        row = {
            "Ticker": ticker,
            "Accuracy": "",
            "Ensemble": "",
            "AUC": "",
            "Reliable": "",
            "LSTM": "✅" if os.path.exists(model_path) else "",
            "XGB": "✅" if os.path.exists(xgb_path) else "",
            "LGBM": "✅" if os.path.exists(lgbm_path) else "",
            "RF": "✅" if os.path.exists(rf_path) else "",
            "Ngày": "",
        }
        if os.path.exists(ensemble_val_path):
            try:
                with open(ensemble_val_path, "r", encoding="utf-8") as f:
                    val_e = json.load(f)
                row["Ensemble"] = f"{float(val_e.get('ensemble_accuracy', 0)):.1%}"
            except (json.JSONDecodeError, OSError, ValueError, TypeError):
                row["Ensemble"] = "ERR"
        if os.path.exists(val_path):
            try:
                with open(val_path, "r", encoding="utf-8") as f:
                    val = json.load(f)
                row.update({
                    "Accuracy": f"{float(val.get('directional_accuracy', 0)):.1%}",
                    "AUC": f"{float(val.get('auc', 0)):.3f}",
                    "Reliable": "✅" if val.get("is_reliable") else "❌",
                    "Ngày": str(val.get("last_validated", ""))[-5:],
                })
            except (json.JSONDecodeError, OSError, ValueError, TypeError):
                row["Reliable"] = "ERR"
        rows.append(row)
    return rows

def append_lstm_log(message):
    logs = st.session_state.setdefault("lstm_manager_logs", [])
    logs.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {message}")
    st.session_state["lstm_manager_logs"] = logs[-200:]

def render_lstm_manager_section():
    st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">LSTM Manager</div>', unsafe_allow_html=True)
    st.warning("Train có thể mất 5-15 phút. Đừng đóng tab.")

    manager_watchlist = get_lstm_manager_watchlist()
    if not manager_watchlist:
        manager_watchlist = ["VCB"]

    col1, col2 = st.columns([2, 3])
    with col1:
        selected_ticker = st.selectbox(
            "Chọn mã cổ phiếu",
            options=manager_watchlist,
            key="lstm_ticker_select",
        )
        custom_ticker = st.text_input(
            "Hoặc nhập mã khác",
            placeholder="VD: HPG",
            key="lstm_custom_ticker",
        ).upper().strip()
        ticker_to_use = custom_ticker if custom_ticker else selected_ticker
        st.caption(f"Mã đang chọn: **{ticker_to_use}**")

    with col2:
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("🔍 Validate (Walk-Forward)", width='stretch', key="lstm_btn_validate"):
                with st.spinner(f"Đang validate {ticker_to_use}... (3-10 phút)"):
                    try:
                        from train_lstm import walk_forward_validate
                        result = walk_forward_validate(ticker_to_use, n_splits=5)
                        append_lstm_log(
                            f"{ticker_to_use} validate OK: acc={result['directional_accuracy']:.1%}, "
                            f"auc={result.get('auc', 0):.3f}, reliable={result['is_reliable']}"
                        )
                        st.success(
                            f"Validate xong. Accuracy: {result['directional_accuracy']:.1%}, "
                            f"AUC: {result.get('auc', 0):.3f}, Reliable: {result['is_reliable']}"
                        )
                        st.json(result)
                    except Exception as e:
                        append_lstm_log(f"{ticker_to_use} validate lỗi: {str(e)[:120]}")
                        st.error(f"Lỗi: {str(e)}")

        with btn_col2:
            if st.button("🚀 Train Model", width='stretch', key="lstm_btn_train"):
                with st.spinner(f"Đang train {ticker_to_use}... (2-5 phút)"):
                    try:
                        from train_lstm import train_direction_model
                        result = train_direction_model(ticker_to_use)
                        append_lstm_log(f"{ticker_to_use} train OK: model saved")
                        st.success(f"Train xong. Model saved: lstm_models/{ticker_to_use}_direction_model.h5")
                        st.json(result)
                    except Exception as e:
                        append_lstm_log(f"{ticker_to_use} train lỗi: {str(e)[:120]}")
                        st.error(f"Lỗi: {str(e)}")

        if st.button("Train XGB + LGBM + RF (nhanh)", width='stretch', key="btn_train_ensemble"):
            train_ensemble_all = get_ensemble_trainer()
            if train_ensemble_all is None:
                st.error("Chưa import được train_ensemble. Kiểm tra xgboost/lightgbm.")
            else:
                with st.spinner(f"Đang train ensemble {ticker_to_use}... (~45s)"):
                    try:
                        elapsed = train_ensemble_all(ticker_to_use)
                        append_lstm_log(f"{ticker_to_use} ensemble train OK: {elapsed:.1f}s")
                        st.success(f"Train xong! {elapsed:.1f}s - 3 models saved")
                    except Exception as e:
                        append_lstm_log(f"{ticker_to_use} ensemble train lỗi: {str(e)[:120]}")
                        st.error(f"Lỗi: {str(e)}")

    st.divider()

    with st.expander("📦 Train tất cả watchlist", expanded=False):
        delay_sec = st.number_input(
            "Delay giữa các mã (giây) - tránh rate limit vnstock",
            min_value=10,
            max_value=300,
            value=60,
            step=10,
            key="lstm_batch_delay",
        )
        batch_mode = st.radio(
            "Chế độ",
            ["Validate + Train", "Chỉ Validate", "Chỉ Train"],
            horizontal=True,
            key="lstm_batch_mode",
        )
        batch_symbols_text = st.text_area(
            "Danh sách mã batch",
            value=", ".join(manager_watchlist),
            height=80,
            key="lstm_batch_symbols",
        )
        batch_symbols = normalize_symbol_list(re.split(r"[\s,;]+", batch_symbols_text.strip()))

        if st.button("Bắt đầu train tất cả", type="primary", key="lstm_btn_batch_train"):
            from train_lstm import train_direction_model, walk_forward_validate

            progress = st.progress(0)
            status_text = st.empty()
            log_container = st.empty()
            logs = []
            total = len(batch_symbols)
            if total == 0:
                st.warning("Danh sách batch đang trống.")
            for i, ticker in enumerate(batch_symbols):
                status_text.text(f"Đang xử lý {ticker} ({i + 1}/{total})...")
                try:
                    if batch_mode in ["Validate + Train", "Chỉ Validate"]:
                        val = walk_forward_validate(ticker, n_splits=5)
                        line = f"✅ {ticker} validate: acc={val['directional_accuracy']:.1%}, reliable={val['is_reliable']}"
                        logs.append(line)
                        append_lstm_log(line)

                    if batch_mode in ["Validate + Train", "Chỉ Train"]:
                        if batch_mode == "Validate + Train":
                            time.sleep(int(delay_sec))
                        train_direction_model(ticker)
                        line = f"✅ {ticker} train: model saved"
                        logs.append(line)
                        append_lstm_log(line)
                except Exception as e:
                    line = f"❌ {ticker} lỗi: {str(e)[:100]}"
                    logs.append(line)
                    append_lstm_log(line)

                log_container.text_area("Log", value="\n".join(logs[-20:]), height=220, key=f"lstm_batch_log_{i}")
                progress.progress((i + 1) / total if total else 1)
                if i < total - 1:
                    time.sleep(int(delay_sec))
            if total:
                status_text.text("Hoàn thành.")
                st.balloons()

    st.divider()

    st.subheader("Trạng thái models")
    if st.button("🔄 Refresh", key="lstm_btn_refresh_status"):
        st.rerun()

    status_rows = get_all_lstm_status()
    if status_rows:
        df_status = pd.DataFrame(status_rows)
        render_dark_table(df_status, key="lstm-status")
        reliable_count = sum(1 for row in status_rows if row["Reliable"] == "✅")
        lstm_count = sum(1 for row in status_rows if row["LSTM"] == "✅")
        xgb_count = sum(1 for row in status_rows if row["XGB"] == "✅")
        lgbm_count = sum(1 for row in status_rows if row["LGBM"] == "✅")
        rf_count = sum(1 for row in status_rows if row["RF"] == "✅")
        st.caption(
            f"Tổng: {len(status_rows)} mã | Reliable: {reliable_count} | "
            f"LSTM: {lstm_count} | XGB: {xgb_count} | LGBM: {lgbm_count} | RF: {rf_count}"
        )
    else:
        st.info("Chưa có model nào. Bấm Train để bắt đầu.")

    st.subheader("Log output")
    logs = st.session_state.get("lstm_manager_logs", [])
    st.text_area("Log output", value="\n".join(logs[-60:]), height=220, key="lstm_log_output", label_visibility="collapsed")


def render_backtest_section():
    st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Backtesting Engine</div>', unsafe_allow_html=True)
    st.caption("Replay strategy trên dữ liệu lịch sử. Chưa bao gồm phí giao dịch TCBS khoảng 0.15%/chiều.")

    bt_watchlist = normalize_symbol_list(st.session_state.get("training_watchlist", load_training_watchlist()))
    if not bt_watchlist:
        bt_watchlist = normalize_symbol_list(st.session_state.get("watchlist", load_watchlist()))
    if not bt_watchlist:
        bt_watchlist = ["VCB"]

    col1, col2 = st.columns(2)
    with col1:
        bt_ticker = st.selectbox("Mã cổ phiếu", bt_watchlist, key="bt_ticker")
        bt_years = st.slider("Số năm backtest", 1, 4, 2, key="bt_years")
    with col2:
        bt_stop = st.slider("Stop loss (ATR)", 1.0, 3.0, 1.0, 0.5, key="bt_stop")
        bt_target = st.slider("Target (ATR)", 2.0, 5.0, 2.0, 0.5, key="bt_target")

    use_ens = st.toggle("Dùng Ensemble Filter", value=True, key="bt_ensemble")
    use_pro = st.toggle("Dùng backtesting.py", value=True, key="bt_use_pro")

    if st.button("Chạy Backtest", type="primary", key="btn_backtest"):
        with st.spinner(f"Đang backtest {bt_ticker}..."):
            try:
                if use_pro:
                    from backtester_pro import run_backtest_pro

                    result, stats, bt = run_backtest_pro(
                        bt_ticker,
                        years=bt_years,
                        atr_stop=bt_stop,
                        atr_target=bt_target,
                    )
                    st.session_state["last_backtest_result"] = {
                        "engine": "pro",
                        "result": result,
                        "stats": dict(stats),
                    }
                else:
                    from backtester import run_backtest

                    result = run_backtest(
                        bt_ticker,
                        years=bt_years,
                        use_ensemble=use_ens,
                        atr_stop=bt_stop,
                        atr_target=bt_target,
                    )
                    st.session_state["last_backtest_result"] = {
                        "engine": "legacy",
                        "result": result,
                    }
            except Exception as exc:
                st.error(f"Lỗi backtest: {exc}")

    payload = st.session_state.get("last_backtest_result")
    if payload:
        engine = payload.get("engine", "legacy") if isinstance(payload, dict) else "legacy"
        result = payload.get("result", payload) if isinstance(payload, dict) else payload

        if engine == "pro":
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Return", f"{result.get('return_pct', 0):+.2f}%", delta=f"vs B&H {result.get('buy_hold_pct', 0):+.2f}%")
            col_b.metric("Sharpe", f"{result.get('sharpe', 0):.3f}")
            col_c.metric("Profit Factor", f"{result.get('profit_factor', 0):.2f}")

            col_d, col_e, col_f = st.columns(3)
            col_d.metric("Win Rate", f"{result.get('win_rate', 0):.1%}")
            col_e.metric("Max Drawdown", f"{result.get('max_drawdown_pct', 0):.1f}%")
            col_f.metric("Expectancy", f"{result.get('expectancy_pct', 0):+.2f}%/trade")

            kelly = float(result.get("kelly", 0) or 0)
            if kelly > 0.25:
                st.success(f"Kelly Criterion: {kelly:.2f} - position size cap around {kelly * 100:.0f}%")
            elif kelly > 0:
                st.warning(f"Kelly Criterion: {kelly:.2f} - edge is small")
            else:
                st.error(f"Kelly Criterion: {kelly:.2f} - no edge")

            chart_path = os.path.join("backtest_results", f"{bt_ticker}_pro_chart.html")
            if os.path.exists(chart_path):
                st.info(f"Chart saved: {chart_path}")

            with st.expander("JSON kết quả"):
                st.json(result)
        else:
            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Không có Ensemble")
                m = result.get("without_ensemble", {})
                st.metric("Win Rate", f"{m.get('win_rate', 0):.1%}")
                st.metric("EV/Trade", f"{m.get('ev_per_trade_pct', 0):+.2f}%")
                st.metric("Max Drawdown", f"{m.get('max_drawdown_pct', 0):.1f}%")
                st.metric("Total Return", f"{m.get('total_return_pct', 0):+.1f}%")
                st.metric("Sharpe", f"{m.get('sharpe_ratio', 0):.2f}")
                st.metric("Trades", m.get("total_trades", 0))

            with col_b:
                st.subheader("Có Ensemble Filter")
                m = result.get("with_ensemble", {})
                st.metric("Win Rate", f"{m.get('win_rate', 0):.1%}")
                st.metric("EV/Trade", f"{m.get('ev_per_trade_pct', 0):+.2f}%")
                st.metric("Max Drawdown", f"{m.get('max_drawdown_pct', 0):.1f}%")
                st.metric("Total Return", f"{m.get('total_return_pct', 0):+.1f}%")
                st.metric("Sharpe", f"{m.get('sharpe_ratio', 0):.2f}")
                st.metric("Trades", m.get("total_trades", 0))

            imp = result.get("ensemble_improvement", {})
            ev_change = imp.get("ev_change", 0)
            if ev_change > 0:
                st.success(f"Ensemble cải thiện EV {ev_change:+.3f}%/trade, lọc bỏ {imp.get('trades_filtered_out')} lệnh.")
            else:
                st.warning(f"Ensemble giảm EV {ev_change:.3f}%/trade. Cân nhắc tắt filter cho mã này.")

            monthly = result.get("with_ensemble", {}).get("monthly_returns", {})
            if monthly:
                monthly_df = pd.DataFrame(list(monthly.items()), columns=["Month", "Return%"])
                st.bar_chart(monthly_df.set_index("Month"))

            with st.expander("JSON kết quả"):
                st.json(result)

    if use_pro and st.button("Tối ưu tham số tự động", key="btn_optimize"):
        with st.spinner(f"Đang optimize {bt_ticker}..."):
            try:
                from backtester_pro import optimize_strategy

                best = optimize_strategy(bt_ticker, years=bt_years)
                st.session_state["last_optimize_result"] = best
                st.success(
                    f"Best: stop={best['atr_stop']} target={best['atr_target']} "
                    f"confluence={best['confluence_min']} Expectancy {best['expectancy']}%"
                )
            except Exception as exc:
                st.error(f"Lỗi optimize: {exc}")

    st.divider()
    with st.expander("Backtest toàn bộ watchlist"):
        max_symbols = st.slider("Số mã tối đa", 3, min(20, max(3, len(bt_watchlist))), min(10, len(bt_watchlist)), key="bt_portfolio_limit")
        if st.button("Chạy Portfolio Backtest", key="btn_portfolio_bt"):
            try:
                if use_pro:
                    from backtester_pro import run_portfolio_backtest_pro
                else:
                    from backtester import run_portfolio_backtest

                with st.spinner("Đang backtest watchlist..."):
                    if use_pro:
                        results = run_portfolio_backtest_pro(bt_watchlist[:max_symbols], years=2, optimize=False)
                    else:
                        results = run_portfolio_backtest(
                            bt_watchlist[:max_symbols],
                            years=2,
                            atr_stop=bt_stop,
                            atr_target=bt_target,
                        )
                st.session_state["last_portfolio_backtest"] = results
            except Exception as exc:
                st.error(f"Lỗi portfolio backtest: {exc}")

        portfolio_results = st.session_state.get("last_portfolio_backtest")
        if portfolio_results:
            rows = []
            for ticker, item in portfolio_results.items():
                if use_pro:
                    rows.append(
                        {
                            "Ticker": ticker,
                            "Win Rate": f"{item.get('win_rate', 0):.1%}",
                            "EV/Trade": f"{item.get('expectancy_pct', 0):+.2f}%",
                            "Max DD": f"{item.get('max_drawdown_pct', 0):.1f}%",
                            "Return": f"{item.get('return_pct', 0):+.1f}%",
                            "Sharpe": f"{item.get('sharpe', 0):.2f}",
                            "Trades": item.get("trades", 0),
                        }
                    )
                else:
                    m = item.get("with_ensemble", {})
                    rows.append(
                        {
                            "Ticker": ticker,
                            "Win Rate": f"{m.get('win_rate', 0):.1%}",
                            "EV/Trade": f"{m.get('ev_per_trade_pct', 0):+.2f}%",
                            "Max DD": f"{m.get('max_drawdown_pct', 0):.1f}%",
                            "Return": f"{m.get('total_return_pct', 0):+.1f}%",
                            "Sharpe": f"{m.get('sharpe_ratio', 0):.2f}",
                            "Trades": m.get("total_trades", 0),
                        }
                    )
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)


def render_live_dashboard():
    # ===================== BULK OPERATIONS =====================
    auto_update_results_after_15h()
    run_auto_analysis_if_due(training_watchlist, ollama_ok, "training watchlist")
    render_market_regime_card()
    render_confluence_card(st.session_state.get("selected_symbol", "VNM"))

    if st.session_state.get("retry_training_failed"):
        st.session_state["retry_training_failed"] = False
        state = load_training_ai_state()
        retry_symbols = normalize_symbol_list((state.get("failed") or {}).keys())
        if not retry_symbols:
            st.info("Khong co ma loi can chay lai.")
        else:
            state["status"] = "running"
            state["stop_requested"] = False
            state["message"] = "Retry failed symbols"
            save_training_ai_state(state)
            set_system_status("manual_analysis", "Training AI chay lai ma loi", f"Tong cong {len(retry_symbols)} ma")
            with st.spinner(f"Training AI dang chay lai {len(retry_symbols)} ma loi..."):
                progress = st.progress(0)
                for i, sym in enumerate(retry_symbols):
                    set_system_status("manual_analysis", f"Training AI retry {sym}", f"Ma {i + 1}/{len(retry_symbols)}")
                    st.write(f"Chay lai {sym}...")
                    result = analyze_one(sym)
                    state = load_training_ai_state()
                    failed = state.get("failed") or {}
                    if str(result).startswith("Lỗi:"):
                        failed[sym] = result
                    else:
                        failed.pop(sym, None)
                    state["failed"] = failed
                    save_training_ai_state(state)
                    progress.progress((i + 1) / len(retry_symbols))
                state = load_training_ai_state()
                state["status"] = "completed" if not state.get("failed") else "paused"
                state["message"] = "Retry completed"
                save_training_ai_state(state)
                st.success("Da chay lai xong cac ma loi.")

    if st.session_state.get("bulk_training_analyze") or st.session_state.get("force_bulk_training_analyze"):
        force_rerun_today = bool(st.session_state.get("force_bulk_training_analyze"))
        st.session_state["bulk_training_analyze"] = False
        st.session_state["force_bulk_training_analyze"] = False
        training_symbols = normalize_symbol_list(st.session_state.get("training_watchlist", load_training_watchlist()))
        auto_completed_today, auto_today_state = today_auto_completed_for_symbols(training_symbols)
        if auto_completed_today and not force_rerun_today:
            completed_at = auto_today_state.get("completed_at", "-")
            set_system_status("idle", "Training AI da co ket qua hom nay", f"Da phan tich {len(training_symbols)} ma luc {completed_at}")
            st.info(f"Hom nay da phan tich xong {len(training_symbols)} ma luc {completed_at}. Khong chay lai de tranh trung lap.")
            return
        state = load_training_ai_state()
        if state.get("symbols") != training_symbols or state.get("status") == "completed":
            state = default_training_ai_state()
            state["symbols"] = training_symbols
            state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state["status"] = "running"
        state["stop_requested"] = False
        state["completed"] = [sym for sym in state.get("completed", []) if sym in training_symbols]
        state["current_index"] = len(state["completed"])
        save_training_ai_state(state)

        start_index = len(state["completed"])
        total_symbols = len(training_symbols)
        if total_symbols == 0:
            state["status"] = "completed"
            state["message"] = "No symbols"
            save_training_ai_state(state)
            st.warning("Training watchlist dang trong.")
            st.stop()
        set_system_status("manual_analysis", "Training AI dang chay", f"Tiep tuc tu ma {start_index + 1}/{total_symbols}")
        with st.spinner(f"Training AI dang phan tich {total_symbols - start_index}/{total_symbols} ma..."):
            progress = st.progress(start_index / total_symbols if total_symbols else 0)
            for i, sym in enumerate(training_symbols[start_index:], start=start_index):
                state = load_training_ai_state()
                if state.get("stop_requested"):
                    state["status"] = "paused"
                    state["message"] = f"Paused before {sym}"
                    save_training_ai_state(state)
                    set_system_status("idle", "Training AI da tam dung", f"Da xong {len(state.get('completed', []))}/{total_symbols} ma")
                    st.warning("Training AI da tam dung. Bam Start / Resume de chay tiep.")
                    break

                set_system_status("manual_analysis", f"Training AI dang phan tich {sym}", f"Ma {i + 1}/{total_symbols}")
                st.write(f"Training AI dang phan tich {sym}...")
                result = analyze_one(sym)

                state = load_training_ai_state()
                completed = normalize_symbol_list(state.get("completed", []))
                if sym not in completed:
                    completed.append(sym)
                failed = state.get("failed") or {}
                if str(result).startswith("Lỗi:"):
                    failed[sym] = result
                else:
                    failed.pop(sym, None)
                state["symbols"] = training_symbols
                state["completed"] = completed
                state["failed"] = failed
                state["current_index"] = i + 1
                state["status"] = "running"
                save_training_ai_state(state)
                progress.progress((i + 1) / total_symbols if total_symbols else 1)

                state = load_training_ai_state()
                if state.get("stop_requested"):
                    state["status"] = "paused"
                    state["message"] = f"Paused after {sym}"
                    save_training_ai_state(state)
                    set_system_status("idle", "Training AI da tam dung", f"Da xong {len(completed)}/{total_symbols} ma")
                    st.warning("Training AI da tam dung. Bam Start / Resume de chay tiep.")
                    break
            else:
                state = load_training_ai_state()
                failed = state.get("failed") or {}
                state["status"] = "completed" if not failed else "paused"
                state["stop_requested"] = False
                state["current_index"] = total_symbols
                state["message"] = "Completed" if not failed else f"Completed with {len(failed)} failed"
                save_training_ai_state(state)
                set_system_status("idle", "Training AI da phan tich xong", f"Hoan tat {total_symbols} ma, loi {len(failed)}")
                if failed:
                    st.warning(f"Training AI xong nhung con {len(failed)} ma loi. Bam Chay lai ma loi de retry.")
                else:
                    st.success("Training AI da phan tich xong training watchlist!")

    if False and st.session_state.get("bulk_training_analyze"):
        st.session_state["bulk_training_analyze"] = False
        training_symbols = st.session_state.get("training_watchlist", load_training_watchlist())
        set_system_status("manual_analysis", "Đang phân tích training watchlist", f"Tổng cộng {len(training_symbols)} mã")
        with st.spinner(f"Đang phân tích {len(training_symbols)} mã training..."):
            progress = st.progress(0)
            for i, sym in enumerate(training_symbols):
                set_system_status("manual_analysis", f"Training AI đang phân tích {sym}", f"Mã {i + 1}/{len(training_symbols)}")
                st.write(f"Đang phân tích training {sym}...")
                analyze_one(sym)
                progress.progress((i + 1) / len(training_symbols))
            set_system_status("idle", "Đã phân tích training watchlist xong", f"Hoàn tất {len(training_symbols)} mã")
            st.success("Đã phân tích xong training watchlist!")

    # ===================== LIVE HEADER + METRICS =====================
    render_world_market_strip()
    render_live_header_metrics()

    # ===================== DATA FOR STATIC PANELS =====================
    try:
        df = fetch_symbol_history(symbol, start_date, end_date)
        if df is None or df.empty:
            st.warning("VNStock đang rate limit hoặc chưa trả dữ liệu cho mã hiện tại. Chờ một lát rồi refresh lại.")
            return
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        change = latest["close"] - prev["close"]
        change_pct = (change / prev["close"]) * 100
        vni_price, vni_chg, _ = get_vnindex()

        st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

        # ===== MAIN PAGES =====
        page_options = [
            "Tổng quan",
            "Theo dõi mã mua",
            "Biểu đồ",
            "So sánh",
            "AI Phân tích",
            "Chuyên gia TC",
            "Auto Trader",
            "Backtest",
            "Lịch sử",
            "Tin tức",
            "LSTM",
        ]
        active_page = st.pills(
            "Trang",
            page_options,
            default=page_options[0],
            selection_mode="single",
            key="main_page_pills_v5",
            label_visibility="collapsed"
        ) or page_options[0]
        st.markdown(
            """
            <style>
            div[data-testid="stPills"] {
                margin: 0.25rem 0 0.9rem;
            }
            /* Fix #2 – keep all 9 tabs on one scrollable row, no wrapping */
            div[data-testid="stButtonGroup"] > div {
                flex-wrap: nowrap !important;
                overflow-x: auto !important;
                overflow-y: hidden !important;
                scrollbar-width: thin;
                scrollbar-color: rgba(148,163,184,0.25) transparent;
                padding-bottom: 2px;
            }
            div[data-testid="stButtonGroup"] > div::-webkit-scrollbar { height: 3px; }
            div[data-testid="stButtonGroup"] > div::-webkit-scrollbar-thumb { background: rgba(148,163,184,0.28); border-radius: 3px; }
            </style>
            """,
            unsafe_allow_html=True
        )
        if active_page == "Tổng quan":
            # Financials + quick overview
            st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Tai chinh co ban</div>', unsafe_allow_html=True)
            render_world_market_section()
            fin = get_financials(symbol)
            if fin:
                st.markdown(f'<div style="color:#8892b0;font-size:0.8rem;margin-bottom:0.5rem;">Năm {fin.get("year","")}</div>', unsafe_allow_html=True)
                fc1, fc2, fc3, fc4 = st.columns(4)
                if fin.get("revenue"):
                    with fc1:
                        st.markdown(f'<div class="metric-box"><div class="label">Doanh thu thuần</div><div class="value">{fin["revenue"]/1e9:,.0f} tỷ</div></div>', unsafe_allow_html=True)
                if fin.get("gross_profit"):
                    with fc2:
                        st.markdown(f'<div class="metric-box"><div class="label">Lợi nhuận gộp</div><div class="value">{fin["gross_profit"]/1e9:,.0f} tỷ</div></div>', unsafe_allow_html=True)
                if fin.get("profit"):
                    with fc3:
                        st.markdown(f'<div class="metric-box"><div class="label">Lợi nhuận ròng</div><div class="value">{fin["profit"]/1e9:,.0f} tỷ</div></div>', unsafe_allow_html=True)
                if fin.get("eps"):
                    with fc4:
                        st.markdown(f'<div class="metric-box"><div class="label">EPS</div><div class="value">{fin["eps"]:,.0f} VNĐ</div></div>', unsafe_allow_html=True)
            else:
                st.info("Không lấy được dữ liệu tài chính.")

            st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)

            # Quick stats
            st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Chi bao ky thuat</div>', unsafe_allow_html=True)
            close = df["close"]
            sma20_val = safe_sma(close, 20)
            sma50_val = safe_sma(close, 50)
            rsi_delta = close.diff()
            gain = rsi_delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss = (-rsi_delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50
            ema12_val = close.ewm(span=12).mean().iloc[-1]
            ema26_val = close.ewm(span=26).mean().iloc[-1]
            macd_val = ema12_val - ema26_val
            bb_mid_val = close.rolling(20).mean().iloc[-1]
            bb_std_val = close.rolling(20).std().iloc[-1]

            tc1, tc2, tc3, tc4, tc5 = st.columns(5)
            with tc1:
                st.markdown(f'<div class="metric-box"><div class="label">SMA20</div><div class="value">{fmt_optional_number(sma20_val, 1)}</div></div>', unsafe_allow_html=True)
            with tc2:
                st.markdown(f'<div class="metric-box"><div class="label">SMA50</div><div class="value">{fmt_optional_number(sma50_val, 1)}</div></div>', unsafe_allow_html=True)
            with tc3:
                rsi_color = "metric-up" if rsi > 60 else "metric-down" if rsi < 40 else "metric-neutral"
                st.markdown(f'<div class="metric-box"><div class="label">RSI (14)</div><div class="value {rsi_color}">{rsi:.1f}</div></div>', unsafe_allow_html=True)
            with tc4:
                macd_color = "metric-up" if macd_val > 0 else "metric-down"
                st.markdown(f'<div class="metric-box"><div class="label">MACD</div><div class="value {macd_color}">{macd_val:.2f}</div></div>', unsafe_allow_html=True)
            with tc5:
                avg_volume_20 = df["volume"].rolling(20).mean().iloc[-1]
                vol_ratio = latest["volume"] / avg_volume_20 if avg_volume_20 > 0 else 1
                st.markdown(f'<div class="metric-box"><div class="label">KL / TB20</div><div class="value">{vol_ratio:.2f}x</div></div>', unsafe_allow_html=True)

            st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)
            st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Cơ hội hôm nay</div>', unsafe_allow_html=True)
            opportunities = today_opportunities(training_watchlist)
            if opportunities:
                opp_cols = st.columns(min(5, len(opportunities)))
                for idx, item in enumerate(opportunities):
                    with opp_cols[idx]:
                        conf = "-" if item["Conf"] is None else f'{item["Conf"]:.0f}%'
                        upside_text = "-" if item["Upside"] is None else f'{item["Upside"]:+.1f}%'
                        st.markdown(
                            f'<div class="metric-box">'
                            f'<div class="label">{item["AI"]} | Conf {conf}</div>'
                            f'<div class="value">{item["Mã"]}</div>'
                            f'<div class="delta metric-up">Upside {upside_text} | {item["Điểm"]:+.1f} điểm</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
            else:
                st.info("Chưa có đủ lịch sử Training AI để xếp hạng cơ hội.")

            alerts = target_stoploss_alerts(training_watchlist, max_items=6)
            if alerts:
                st.markdown('<div class="section-title" style="margin:0.9rem 0 0.5rem;">Cảnh báo target / stoploss</div>', unsafe_allow_html=True)
                st.dataframe(pd.DataFrame(alerts), width='stretch', hide_index=True)

        elif active_page == "Theo dõi mã mua":
            render_position_tracker_page()

        elif active_page == "Biểu đồ":
            render_live_chart()

        elif active_page == "So sánh":
            st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">So sánh 50 mã training</div>', unsafe_allow_html=True)
            compare_watchlist = st.session_state.get("training_watchlist", training_watchlist)
            compare_watchlist = [str(s).upper() for s in compare_watchlist if str(s).strip()]
            compare_start = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
            compare_end = datetime.now().strftime("%Y-%m-%d")

            if not compare_watchlist:
                st.info("Training watchlist đang trống.")
            else:
                with st.spinner("Đang tải dữ liệu so sánh từ VCI..."):
                    df_compare, normalized_series = fetch_watchlist_comparison(compare_watchlist, compare_start, compare_end)

                if df_compare.empty:
                    st.warning("Không lấy được dữ liệu so sánh cho training watchlist hiện tại.")
                else:
                    f1, f2, f3, f4 = st.columns(4)
                    with f1:
                        action_filter = st.multiselect("AI verdict", ["MUA", "GIỮ", "BÁN", "N/A"], default=["MUA", "GIỮ", "BÁN", "N/A"])
                    with f2:
                        min_conf = st.slider("Confidence tối thiểu", 0, 100, 0, 5)
                    with f3:
                        rsi_max = st.slider("RSI tối đa", 30, 100, 100, 1)
                    with f4:
                        only_upside = st.checkbox("Có upside dương", value=False)

                    filtered_compare = df_compare.copy()
                    filtered_compare = filtered_compare[filtered_compare["AI verdict"].isin(action_filter)]
                    filtered_compare = filtered_compare[filtered_compare["RSI"] <= rsi_max]
                    if min_conf > 0:
                        filtered_compare = filtered_compare[(filtered_compare["Confidence"].fillna(0) >= min_conf)]
                    if only_upside:
                        filtered_compare = filtered_compare[(filtered_compare["Upside %"].fillna(-999) > 0)]

                    if filtered_compare.empty:
                        st.info("Không có mã nào khớp bộ lọc. Đang hiển thị lại toàn bộ 50 mã.")
                        filtered_compare = df_compare.copy()
                    df_compare = filtered_compare.sort_values("Điểm tiềm năng", ascending=False).reset_index(drop=True)
                    best_symbol = df_compare.iloc[0]["Mã"]
                    worst_symbol = df_compare.iloc[-1]["Mã"]

                    fig_compare = go.Figure()
                    palette = ["#38bdf8", "#22c55e", "#f59e0b", "#e879f9", "#fb7185", "#a3e635", "#60a5fa", "#f97316"]
                    for idx, (sym, df_norm) in enumerate(normalized_series.items()):
                        width = 3 if sym in [best_symbol, worst_symbol] else 1.7
                        color = "#22c55e" if sym == best_symbol else "#f43f5e" if sym == worst_symbol else palette[idx % len(palette)]
                        fig_compare.add_trace(go.Scatter(
                            x=df_norm["time"],
                            y=df_norm["normalized"],
                            mode="lines",
                            name=sym,
                            line=dict(color=color, width=width)
                        ))
                    fig_compare.add_hline(y=0, line_dash="dash", line_color="rgba(148,163,184,0.45)")
                    fig_compare.update_layout(
                        title="<b>Overlay hiệu suất</b> | Normalize % so với ngày đầu tiên",
                        template="plotly_dark",
                        height=460,
                        margin=dict(l=0, r=0, t=45, b=0),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#a8b2d1"),
                        hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        yaxis=dict(title="%")
                    )
                    fig_compare.update_xaxes(gridcolor="#1e2140", showgrid=True)
                    fig_compare.update_yaxes(gridcolor="#1e2140", showgrid=True, zeroline=False)
                    st.plotly_chart(fig_compare, width='stretch')

                    rank_cols = st.columns(3)
                    with rank_cols[0]:
                        st.markdown(
                            f'<div class="metric-box" style="border-color:rgba(34,197,94,0.55);">'
                            f'<div class="label">Tốt nhất</div><div class="value" style="color:#22c55e;">{best_symbol}</div>'
                            f'<div class="delta metric-up">{df_compare.iloc[0]["Điểm tiềm năng"]:+.1f} điểm</div></div>',
                            unsafe_allow_html=True
                        )
                    with rank_cols[1]:
                        st.markdown(
                            f'<div class="metric-box"><div class="label">Số mã so sánh</div>'
                            f'<div class="value">{len(df_compare)}</div><div class="delta" style="color:#8892b0;">VCI daily</div></div>',
                            unsafe_allow_html=True
                        )
                    with rank_cols[2]:
                        st.markdown(
                            f'<div class="metric-box" style="border-color:rgba(244,63,94,0.55);">'
                            f'<div class="label">Yếu nhất</div><div class="value" style="color:#f43f5e;">{worst_symbol}</div>'
                            f'<div class="delta metric-down">{df_compare.iloc[-1]["Điểm tiềm năng"]:+.1f} điểm</div></div>',
                            unsafe_allow_html=True
                        )

                    st.markdown('<div class="divider-custom"></div>', unsafe_allow_html=True)
                    st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">Bảng so sánh & ranking</div>', unsafe_allow_html=True)

                    display_compare = df_compare.copy()
                    display_compare.insert(0, "Rank", range(1, len(display_compare) + 1))
                    rank_rows = []
                    for row in display_compare.to_dict("records"):
                        is_best = row["Mã"] == best_symbol
                        is_worst = row["Mã"] == worst_symbol
                        row_cls = "rank-best" if is_best else "rank-worst" if is_worst else "rank-normal"
                        verdict_cls = "verdict-buy" if row["AI verdict"] == "MUA" else "verdict-sell" if row["AI verdict"] == "BÁN" else "verdict-hold"
                        score_cls = "score-up" if row["Điểm tiềm năng"] > 0 else "score-down" if row["Điểm tiềm năng"] < 0 else "score-flat"
                        day_cls = "score-up" if row["% hôm nay"] > 0 else "score-down" if row["% hôm nay"] < 0 else "score-flat"
                        macd_cls = "score-up" if row["MACD"] > 0 else "score-down" if row["MACD"] < 0 else "score-flat"
                        conf_text = "" if pd.isna(row["Confidence"]) else f'{row["Confidence"]:.0f}%'
                        upside_text = "" if pd.isna(row["Upside %"]) else f'{row["Upside %"]:+.1f}%'
                        risk_text = "" if pd.isna(row["Risk %"]) else f'{row["Risk %"]:.1f}%'
                        acc_text = "" if pd.isna(row["Accuracy %"]) else f'{row["Accuracy %"]:.1f}%'
                        rank_rows.append(
                            f'<tr class="{row_cls}">'
                            f'<td>{row["Rank"]}</td>'
                            f'<td class="symbol-cell">{row["Mã"]}</td>'
                            f'<td>{row["Giá"]:,.1f}</td>'
                            f'<td class="{day_cls}">{row["% hôm nay"]:+.2f}%</td>'
                            f'<td>{row["RSI"]:.1f}</td>'
                            f'<td class="{macd_cls}">{row["MACD"]:+.2f}</td>'
                            f'<td>{row["SMA trend"]}</td>'
                            f'<td class="{verdict_cls}">{row["AI verdict"]}</td>'
                            f'<td>{conf_text}</td>'
                            f'<td>{upside_text}</td>'
                            f'<td>{risk_text}</td>'
                            f'<td>{acc_text}</td>'
                            f'<td class="{score_cls}">{row["Điểm tiềm năng"]:+.1f}</td>'
                            f'</tr>'
                        )
                    st.markdown(
                        '<div class="rank-table-wrap"><table class="rank-table">'
                        '<thead><tr><th>Rank</th><th>Mã</th><th>Giá</th><th>% hôm nay</th><th>RSI</th><th>MACD</th><th>SMA trend</th><th>AI verdict</th><th>Conf</th><th>Upside</th><th>Risk</th><th>Acc</th><th>Điểm</th></tr></thead>'
                        f'<tbody>{"".join(rank_rows)}</tbody></table></div>',
                        unsafe_allow_html=True
                    )

        elif active_page == "AI Phân tích":
            st.markdown('<div class="section-title" style="margin-bottom:0.5rem;">AI Phan tich toan dien</div>', unsafe_allow_html=True)
            badge_text, badge_type = get_ev_badge(symbol)
            if badge_type == "success":
                st.success(badge_text)
            elif badge_type == "warning":
                st.warning(badge_text)
            elif badge_type == "error":
                st.error(badge_text)
            else:
                st.info(badge_text)

            selected_symbol_local = st.session_state.get("selected_symbol", symbol)
            tradeable_today, tradeable_date = get_today_tradeable_symbols()
            ai_options = normalize_symbol_list((watchlist or []) + tradeable_today)
            if not ai_options:
                ai_options = [symbol]

            ai_ticker_default = next(
                (
                    candidate
                    for candidate in (
                        selected_symbol_local,
                        tradeable_today[0] if tradeable_today else None,
                        watchlist[0] if watchlist else None,
                        symbol,
                    )
                    if candidate and candidate in ai_options
                ),
                ai_options[0],
            )
            col_sym, col_btn = st.columns([3, 1])
            with col_sym:
                ai_ticker = st.selectbox(
                    "Chọn mã phân tích",
                    options=ai_options,
                    index=ai_options.index(ai_ticker_default) if ai_ticker_default in ai_options else 0,
                    key="ai_analysis_ticker",
                )
                if tradeable_today:
                    st.caption(
                        "Eligible 5-6 sessions: "
                        + ", ".join(tradeable_today)
                        + (f" | ngày {tradeable_date}" if tradeable_date else "")
                    )
                else:
                    st.caption("Chưa có danh sách eligible 5-6 sessions hôm nay, dùng watchlist hiện tại.")
            with col_btn:
                st.markdown("<br>", unsafe_allow_html=True)
                run_analysis = st.button("Phân tích ngay", type="primary", width='stretch', key="btn_ai_analyze")

            results_path = BASE_DIR / "analysis_results.json"
            ai_result_row = None
            analysis = {}
            if results_path.exists():
                try:
                    with open(results_path, encoding="utf-8") as f:
                        analysis = json.load(f) or {}
                except Exception as exc:
                    st.caption(f"Không đọc được analysis_results.json: {exc}")
                    analysis = {}

            if analysis:
                today_key = date.today().isoformat()
                analysis_date = str(analysis.get("date") or analysis.get("today") or "")
                horizon_sessions, horizon_label = _analysis_horizon_label(analysis)
                if not analysis_date or analysis_date == today_key:
                    for item in analysis.get("stage2_results", []) or []:
                        if str(item.get("ticker", "")).upper() == str(ai_ticker).upper():
                            ai_result_row = item
                            break
                    if ai_result_row is None:
                        tradeable = analysis.get("tradeable") or analysis.get("tradeable_tickers") or []
                        st.info(f"Chưa có phân tích cho {ai_ticker} hôm nay. Bấm Phân tích ngay.")
                        if tradeable:
                            st.caption(f"Eligible {horizon_label}: {', '.join([str(x) for x in tradeable])}")
                else:
                    st.info("Analysis hôm nay chưa chạy. Bấm Phân tích ngay hoặc chờ scheduler 8:30.")

            if ai_result_row:
                st.divider()
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.metric("Stage 2 Score", f"{safe_float(ai_result_row.get('final_score'), 0):.1f}/10")
                with col_b:
                    ensemble = ai_result_row.get("ensemble") or {}
                    ens_signal = ensemble.get("signal", "N/A")
                    st.metric("Ensemble", str(ens_signal))
                with col_c:
                    llm = ai_result_row.get("llm") or {}
                    action = llm.get("action", "skip")
                    conf = llm.get("confidence", 0)
                    delta_text = f"Confidence {conf}%" if str(action).lower() != "skip" else None
                    st.metric("LLM Verdict", str(action), delta=delta_text)

                signals = ai_result_row.get("signals", {}) or {}
                if signals:
                    sig_rows = pd.DataFrame(
                        [{"Signal": k, "Value": v} for k, v in signals.items()]
                    )
                    render_dark_table(sig_rows, key="ai-signals")

                llm = ai_result_row.get("llm") or {}
                reason = llm.get("reason") or llm.get("reasoning")
                if reason:
                    st.info(f"Phản hồi: {reason}")

                tradeable_flag = ai_result_row.get("tradeable")
                if tradeable_flag:
                    st.success(f"{ai_ticker} đủ điều kiện trade theo horizon {horizon_label}")
                else:
                    st.warning(f"{ai_ticker} chưa đủ điều kiện trade theo horizon {horizon_label}")

                render_debate_result(ai_result_row)
            else:
                st.caption("Chưa có kết quả lưu cho mã này trong file analysis_results.json.")

            if run_analysis:
                with st.spinner(f"Đang phân tích {ai_ticker}..."):
                    try:
                        from auto_trader import stage1_quick_scan, stage2_deep_analysis

                        stage1 = stage1_quick_scan([ai_ticker], top_n=1)
                        if stage1:
                            stage2_deep_analysis(stage1, use_llm=True, use_ensemble=True)
                            st.success("Phân tích xong!")
                            st.rerun()
                        else:
                            st.warning("Stage 1 không trả về tín hiệu cho mã này.")
                    except Exception:
                        try:
                            result = analyze_one(ai_ticker)
                            if str(result).startswith("Lỗi:"):
                                st.error(result)
                            else:
                                st.success("Phân tích xong!")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Lỗi: {e}")

            if st.session_state.get("ai_result"):
                st.markdown(
                    f'<div class="info-box" style="margin-top:0.9rem;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">'
                    f'<span style="color:#f8fafc;font-weight:700;">Kết quả LLM gần nhất</span>'
                    f'<span style="color:#cbd5e1;font-size:0.75rem;">{st.session_state.get("ai_time","")}</span>'
                    f'</div>'
                    f'<div style="color:#cbd5e1;line-height:1.6;">{st.session_state["ai_result"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

        elif active_page == "Auto Trader":
            render_auto_trader_section()

        elif active_page == "Backtest":
            render_backtest_section()

        elif active_page == "Lịch sử":
            render_history_section()

        elif active_page == "Tin tức":
            render_live_news()

        elif active_page == "LSTM":
            render_lstm_manager_section()

        elif active_page == "Chuyên gia TC":
            try:
                import financial_advisor
                financial_advisor.render_financial_advisor_section()
            except Exception as _fa_err:
                st.error(f"Không tải được Chuyên gia Tài chính: {_fa_err}")

    except Exception as e:
        st.error(f"Lỗi lấy dữ liệu: {e}")
        df = None

if __name__ == "__main__":
    render_live_dashboard()
