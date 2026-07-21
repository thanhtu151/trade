"""Cloud bootstrap for the Streamlit dashboard.

On Streamlit Community Cloud the app is deployed from the `main` branch, but the
live runtime state (portfolio, trades, analysis, prediction history...) lives on
the separate `state` branch that the GitHub Actions scheduler writes to after
every task. This module:

  1. bridge_secrets() — copies Streamlit secrets into os.environ so llm_router
     (which reads os.getenv) picks up the LLM API keys.
  2. sync_state()     — downloads the latest state files from the `state` branch
     into the working dir so the dashboard's existing local-file reads work
     unchanged, without any other code change.

Both are NO-OPS locally: with no secrets.toml and no CLOUD_STATE_REPO secret,
nothing is fetched or overwritten, so the dev machine is untouched.

Note: the cloud dashboard is a live VIEWER. Writes made in the cloud UI (manual
trades, "run now" buttons) land on the ephemeral container and are NOT pushed
back to the `state` branch — only the scheduler persists state. Refresh picks up
the scheduler's latest state within the cache TTL below.
"""

import os
from pathlib import Path

import requests
import streamlit as st

BASE_DIR = Path(__file__).parent.resolve()

# Keep in sync with STATE_FILES in .github/workflows/scheduler.yml
STATE_FILES = [
    "paper_portfolio.json",
    "paper_trades.json",
    "scheduler_state.json",
    "analysis_results.json",
    "prediction_history.json",
    "prediction_log.json",
    "learning_memory.json",
    "debate_log.json",
    "portfolio_snapshots.json",
    "performance_report.json",
    "system_status.json",
    "tracked_positions.json",
    "advisor_audit_log.json",
    "intraday_alerts.json",
    "auto_analysis_state.json",
    "vn_live_data_cache.json",
    "llm_router_usage.json",
]


def _secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def bridge_secrets():
    """st.secrets -> os.environ (only fills keys not already set)."""
    try:
        for key, val in st.secrets.items():
            if isinstance(val, str) and not os.environ.get(key):
                os.environ[key] = val
    except Exception:
        # No secrets.toml locally -> nothing to bridge.
        pass


@st.cache_data(ttl=300, show_spinner=False)
def _download_state(repo, ref):
    """Fetch each state file from raw.githubusercontent. Cached ~5 min so the
    dashboard reflects the scheduler's latest run without hammering GitHub."""
    pulled = []
    for name in STATE_FILES:
        url = f"https://raw.githubusercontent.com/{repo}/{ref}/{name}"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200 and resp.content:
                (BASE_DIR / name).write_bytes(resp.content)
                pulled.append(name)
        except Exception:
            continue
    return pulled


def sync_state():
    """Pull the `state` branch into the working dir when running on cloud.

    Gated on the CLOUD_STATE_REPO secret (e.g. "thanhtu151/trade"); absent it is
    a no-op so local runs use their own files.
    """
    repo = _secret("CLOUD_STATE_REPO")
    if not repo:
        return []
    ref = _secret("CLOUD_STATE_REF", "state")
    return _download_state(repo, ref)
