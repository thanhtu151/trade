import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_legacy_optimizer_runs_each_atr_pair_once_without_ensemble(monkeypatch, tmp_path):
    import backtester
    import backtester_pro

    calls = []

    def fake_run(ticker, years=2, use_ensemble=True, atr_stop=1.5, atr_target=3.0):
        calls.append((atr_stop, atr_target, use_ensemble, years))
        return {"without_ensemble": {"ev_per_trade_pct": atr_target - atr_stop, "total_trades": 60},
                "with_ensemble": {"ev_per_trade_pct": 99.0, "total_trades": 60}}

    monkeypatch.setattr(backtester_pro, "BACKTESTING_AVAILABLE", False)
    monkeypatch.setattr(backtester_pro, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(backtester, "run_backtest", fake_run)
    best = backtester_pro.optimize_strategy("VCB", years=6)
    pairs = [(s, t) for s, t, _, _ in calls]
    assert len(pairs) == len(set(pairs)) == 15
    assert all(not ensemble and years == 6 for _, _, ensemble, years in calls)
    assert best["expectancy"] == 2.2  # ranked on the unfiltered (leak-free) metrics


def test_legacy_fallback_backtest_uses_unfiltered_metrics(monkeypatch):
    import backtester
    import backtester_pro

    seen = {}

    def fake_run(ticker, years=2, use_ensemble=True, atr_stop=1.5, atr_target=3.0):
        seen["use_ensemble"] = use_ensemble
        return {"without_ensemble": {"total_trades": 70, "ev_per_trade_pct": 0.3, "profit_factor": 1.1},
                "with_ensemble": {"total_trades": 9, "ev_per_trade_pct": 5.0}}

    monkeypatch.setattr(backtester, "run_backtest", fake_run)
    result, _, _ = backtester_pro._legacy_fallback_backtest("VCB", 6, 1.0, 2.0)
    assert seen["use_ensemble"] is False and result["trades"] == 70 and result["expectancy_pct"] == 0.3


def test_scheduler_rebacktest_uses_six_years():
    import scheduler

    assert scheduler.REBACKTEST_YEARS == 6
