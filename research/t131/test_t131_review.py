"""T131: independent review probes for fix/corporate-action-stop-guard (4622127).

Run from the temp worktree so `scheduler` is the reviewed commit:
  cd research/t131/wt && ../../../.venv/bin/python -m pytest -q --tb=short -s -p no:cacheprovider ../test_t131_review.py
Everything is written to tmp_path; nothing here touches the real ledger.
"""
import json
import os
import sys
from pathlib import Path

WT = Path(__file__).resolve().parent / "wt"
sys.path[:0] = [str(WT), str(WT / "tests")]
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.pop("DISCORD_WEBHOOK_URL", None)

import pandas as pd  # noqa: E402

from test_ci_intraday_stop import STOP, _bars, _env, _sells, _state  # noqa: E402

PRICES = Path(__file__).resolve().parents[1] / "data" / "2026-09-29" / "prices"


def _real(ticker):
    df = pd.read_parquet(PRICES / f"{ticker}.parquet")
    return df.assign(time=pd.to_datetime(df["time"]))


def test_real_snapshot_pvd_vpb_detected_tcb_vhm_not():
    import scheduler

    out = {}
    for ticker, pos in {
        "PVD": {"avg_price": 33.2, "entry_date": "2026-07-10 10:44:00"},
        "VPB": {"avg_price": 28.0, "entry_date": "2026-09-21 14:32:00"},
        "VPB_mark": {"avg_price": 28.0, "entry_date": "2026-09-21 14:32:00",
                     "current_price": 28.0, "price_mark_date": "2026-09-23"},
        "TCB": {"avg_price": 32.0, "entry_date": "2026-07-15 09:24:00"},
    }.items():
        out[ticker] = scheduler._history_rebase_factor(pos, _real(ticker.split("_")[0]))
    print("\nREAL", {k: (round(v, 4) if v else v) for k, v in out.items()})
    assert out["PVD"] and out["VPB"] and out["VPB_mark"] and out["TCB"] is None


def test_real_gap_and_limit_down_are_not_flagged_on_whole_universe_sample():
    """Mark at day D's close, then replay every later day: unchanged history must never flag."""
    import scheduler

    flagged = checked = worst = 0
    for ticker in ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "STB", "HDB", "VIB", "SSI", "VND", "HCM", "VCI",
                   "HPG", "VIC", "VHM", "FPT", "MWG", "VNM", "PVD"]:
        df = _real(ticker).tail(260).reset_index(drop=True)
        ret = df["close"].pct_change()
        worst = min(worst, float(ret.min()))
        for i in range(len(df) - 1):
            pos = {"avg_price": float(df["open"].iloc[i]), "entry_date": str(df["time"].iloc[i].date()),
                   "current_price": float(df["close"].iloc[i]), "price_mark_date": str(df["time"].iloc[i].date())}
            checked += 1
            flagged += bool(scheduler._history_rebase_factor(pos, df.iloc[: i + 2]))
    print(f"\nFALSE-POS checked={checked} flagged={flagged} worst_1d_return={worst:.4f}")
    assert flagged == 0


def test_real_stop_still_sells_when_bars_carry_low_high(monkeypatch, tmp_path):
    """PR #26 path: a genuine gap-down through the stop, with full OHLC bars, must still sell."""
    import data_fetcher

    scheduler = _env(monkeypatch, tmp_path)  # avg 19,800 / stop 19,500 / entry 2026-09-15
    monkeypatch.setattr(scheduler, "INTRADAY_ALERTS_FILE", str(tmp_path / "intraday_alerts.json"))
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: _bars([
        ("2026-09-15", 19_700.0, 19_900.0, 19_650.0, 19_850.0),
        ("2026-09-21", 19_800.0, 19_900.0, 19_700.0, 19_750.0),
        ("2026-09-22", 18_400.0, 18_450.0, 18_370.0, 18_370.0)]))  # limit-down gap, -7%
    import auto_trader
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 19_750.0, "day_low": 18_370.0, "day_high": 18_450.0})
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    assert "PVD" not in portfolio["positions"] and trades[-1]["reason"] == "stop_loss_intraday"


def _rebased(monkeypatch, tmp_path):
    import data_fetcher

    scheduler = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(scheduler, "INTRADAY_ALERTS_FILE", str(tmp_path / "intraday_alerts.json"))
    monkeypatch.setattr(data_fetcher, "get_stock_data_cached", lambda *_a, **_k: _bars([
        ("2026-09-15", 11_900.0, 12_000.0, 11_700.0, 11_880.0),
        ("2026-09-22", 11_900.0, 11_950.0, 11_800.0, 11_900.0)]))
    import auto_trader
    monkeypatch.setattr(auto_trader, "market_reference", lambda *_a, **_k: {
        "prev_close": 11_950.0, "day_low": 11_800.0, "day_high": 11_950.0})
    return scheduler


def test_probe_discord_failure_is_never_retried(monkeypatch, tmp_path):
    import notify

    calls = []
    monkeypatch.setattr(notify, "send_once", lambda *a, **k: calls.append(a[0]) or False)  # webhook down
    scheduler = _rebased(monkeypatch, tmp_path)
    for _ in range(3):
        scheduler.task_intraday_monitor()
    print(f"\nDISCORD attempts over 3 runs with delivery failing: {len(calls)}")
    assert len(calls) == 1  # documents the gap: one best-effort attempt, then silence


def test_probe_flag_survives_manual_adjustment_of_avg_only(monkeypatch, tmp_path):
    """Operator rescales qty/avg/stop as the alert asks; does the stop come back?"""
    scheduler = _rebased(monkeypatch, tmp_path)
    scheduler.task_intraday_monitor()
    path = tmp_path / "paper_portfolio.json"
    portfolio = json.loads(path.read_text())
    pos = portfolio["positions"]["PVD"]
    pos.update(qty=1650, avg_price=12_000.0, stop_loss=11_950.0)  # today trades 11,900 <= stop
    path.write_text(json.dumps(portfolio))
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    still = portfolio["positions"].get("PVD", {})
    print(f"\nADJUSTED sold={bool(_sells(trades))} flag_still_present={'corporate_action_suspected' in still}")
    assert _sells(trades), "adjusted position must be stoppable again"


def test_probe_marked_position_needs_mark_adjusted_too(monkeypatch, tmp_path):
    """Same, but the position already carries current_price/price_mark_date (the normal case after deploy)."""
    scheduler = _rebased(monkeypatch, tmp_path)
    path = tmp_path / "paper_portfolio.json"
    portfolio = json.loads(path.read_text())
    portfolio["positions"]["PVD"].update(current_price=19_750.0, price_mark_date="2026-09-15")
    path.write_text(json.dumps(portfolio))
    scheduler.task_intraday_monitor()
    portfolio = json.loads(path.read_text())
    portfolio["positions"]["PVD"].update(qty=1650, avg_price=12_000.0, stop_loss=11_950.0)
    path.write_text(json.dumps(portfolio))
    scheduler.task_intraday_monitor()
    portfolio, trades = _state(tmp_path)
    print(f"\nMARKED+ADJUSTED sold={bool(_sells(trades))} (False = still suspended after the documented adjustment)")
