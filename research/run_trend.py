"""Pre-registered trend overlay on a VN30 ETF proxy (risk reduction, not alpha).

The VN30 index stands in for a VN30 ETF: the ETF itself only trades from 2016
and was too illiquid early on. Holding costs (ETF expense ratio) are deducted
while invested; every switch pays brokerage, exchange fee, 0.1% sell tax and
slippage through the engine. Cash earns 0% (sensitivity: 4%/year).

Trials (fixed before any result):
  trend_ma200d  hold while VN30 closes at/above its 200-session average (daily check)
  trend_ma10m   hold while the month-end close is at/above the 10 month-end average

Gate "C" (risk overlay), all against buy-and-hold of the same proxy:
  1. Sharpe >= buy-and-hold Sharpe
  2. max drawdown <= 60% of buy-and-hold max drawdown
  3. CAGR >= buy-and-hold CAGR - 3%/year
  4. <= 6 switches (entries + exits) per year on average
  5. criteria 1 and 2 also hold in each half of the development period

    python -m research.run_trend dev
    python -m research.run_trend holdout    # only if dev passes; one shot
"""

import argparse
import json
import math
from datetime import datetime

import numpy as np
import pandas as pd

from research import metrics
from research.data import latest_snapshot
from research.engine import Config, simulate
from research.run_momentum import HOLDOUT_LOCK, REPORT_DIR, _clean, load_panel, log_trial

SYMBOL = "VN30"
DEV = ("2013-01-01", "2023-12-31")
HOLDOUT = ("2024-01-01", None)
EXPENSE_RATIO = 0.0065
CASH_YIELD_SENSITIVITY = 0.04
PRIMARY = "trend_ma200d"
TRIALS = {
    "trend_ma200d": dict(rule="daily", window=200),
    "trend_ma10m": dict(rule="monthly", window=10),
}


class Trend:
    """Fully invested in `symbol` while its trend rule is on, otherwise cash."""

    def __init__(self, panel, symbol=SYMBOL, rule="daily", window=200):
        close = panel.frame("last_close")[symbol]
        if rule == "daily":
            on = close >= close.rolling(window, min_periods=window).mean()
            check = pd.Series(True, index=close.index)
        else:
            month_end = close.groupby([close.index.year, close.index.month]).transform(lambda s: s.index.max())
            is_month_end = close.index == month_end.values
            monthly = close[is_month_end]
            on_m = monthly >= monthly.rolling(window, min_periods=window).mean()
            on = on_m.astype(float).reindex(close.index).ffill().fillna(0.0).astype(bool)
            check = pd.Series(is_month_end, index=close.index)
        valid = close.rolling(window if rule == "daily" else 1).mean().notna()
        self.on = (on & valid).to_numpy()
        self.check = check.to_numpy()
        self.symbol = symbol

    def __call__(self, panel, i, holdings):
        if not self.check[i]:
            return None
        invested = bool(holdings.get(self.symbol))
        if self.on[i] and not invested:
            return {self.symbol: 0.99}
        if not self.on[i] and invested:
            return {}
        return None


class Hold:
    def __init__(self, symbol=SYMBOL):
        self.symbol = symbol

    def __call__(self, panel, i, holdings):
        return None if holdings.get(self.symbol) else {self.symbol: 0.99}


def run(panel, strategy, start, end, cash_yield=0.0):
    out = simulate(panel, strategy, Config(lot=1, stop_loss=None, max_adv_fraction=1.0), start=start, end=end)
    curve = out["curve"]
    invested = 1 - curve["cash"] / curve["equity"]
    daily = curve["return"] - invested.shift(1).fillna(0) * EXPENSE_RATIO / 252
    daily = daily + (1 - invested.shift(1).fillna(1)) * cash_yield / 252
    trades = out["trades"]
    years = len(curve) / 252
    return daily, (len(trades) / years if years else 0.0), trades


def stats(daily):
    s = metrics.summarize(daily)
    return {k: s[k] for k in ("start", "end", "cagr", "vol_annual", "sharpe_annual", "max_drawdown",
                              "sharpe_annual_p05", "psr_vs_0")}


def gate_c(res, bh, halves):
    checks = {
        "sharpe_ge_buy_hold": res["sharpe_annual"] >= bh["sharpe_annual"],
        "max_dd_le_60pct_of_buy_hold": abs(res["max_drawdown"]) <= 0.6 * abs(bh["max_drawdown"]),
        "cagr_ge_buy_hold_minus_3pct": res["cagr"] >= bh["cagr"] - 0.03,
        "switches_le_6_per_year": res["switches_per_year"] <= 6,
        "holds_in_both_halves": all(
            h["trend"]["sharpe_annual"] >= h["buy_hold"]["sharpe_annual"]
            and abs(h["trend"]["max_drawdown"]) <= 0.6 * abs(h["buy_hold"]["max_drawdown"])
            for h in halves),
    }
    return {"passed": all(checks.values()), "checks": checks}


def evaluate(panel, period_name):
    start, end = DEV if period_name == "dev" else HOLDOUT
    bh_daily, _, _ = run(panel, Hold(), start, end)
    bh = stats(bh_daily)
    report = {"experiment": "trend", "primary": PRIMARY, "period": period_name, "range": [start, end],
              "generated_at": datetime.now().isoformat(timespec="seconds"), "snapshot": latest_snapshot().name,
              "buy_hold": bh, "buy_hold_by_year": _by_year(bh_daily), "trials": {}}
    cut = bh_daily.index[len(bh_daily) // 2]
    for name, params in TRIALS.items():
        daily, switches, trades = run(panel, Trend(panel, **params), start, end)
        res = stats(daily)
        res["switches_per_year"] = switches
        res["time_invested"] = float(_invested_share(trades, daily.index))
        halves = [{"trend": stats(daily[daily.index < cut]), "buy_hold": stats(bh_daily[bh_daily.index < cut])},
                  {"trend": stats(daily[daily.index >= cut]), "buy_hold": stats(bh_daily[bh_daily.index >= cut])}]
        sens, _, _ = run(panel, Trend(panel, **params), start, end, cash_yield=CASH_YIELD_SENSITIVITY)
        report["trials"][name] = {
            "summary": res, "halves": halves, "by_year": _by_year(daily),
            "cash_yield_4pct": stats(sens), "gate_c": gate_c(res, bh, halves),
        }
        log_trial(period_name, name, params, (start, end), metrics.summarize(daily))
    return report


def _by_year(daily):
    return {str(k): float(v) for k, v in ((1 + daily).groupby(daily.index.year).prod() - 1).items()}


def _invested_share(trades, index):
    if trades.empty:
        return 0.0
    state = pd.Series(np.nan, index=index)
    for _, t in trades.iterrows():
        state.loc[t["date"]] = 1.0 if t["side"] == "BUY" else 0.0
    return state.ffill().fillna(0).mean()


def pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%".replace(".", ",")


def num(x):
    return "—" if x is None else f"{x:.2f}".replace(".", ",")


def render(report):
    names = {"trend_ma200d": "VN30 ≥ MA200 ngày", "trend_ma10m": "VN30 ≥ MA 10 tháng"}
    labels = {
        "sharpe_ge_buy_hold": "Sharpe ≥ mua & giữ",
        "max_dd_le_60pct_of_buy_hold": "Max DD ≤ 60% của mua & giữ",
        "cagr_ge_buy_hold_minus_3pct": "CAGR ≥ mua & giữ − 3%/năm",
        "switches_le_6_per_year": "≤ 6 lần vào/ra mỗi năm",
        "holds_in_both_halves": "Sharpe và DD vẫn đạt ở cả hai nửa giai đoạn",
    }
    start, end = report["range"]
    bh = report["buy_hold"]
    lines = [f"# Thí nghiệm 3 — Lọc xu hướng trên ETF VN30 ({'phát triển' if report['period'] == 'dev' else 'holdout'} "
             f"{start} → {end or 'nay'})", "",
             "Đại diện ETF = chỉ số VN30, trừ phí quản lý 0,65%/năm khi nắm giữ; mỗi lần vào/ra chịu phí môi giới, "
             "phí Sở, thuế 0,1% và trượt giá. Tiền mặt lãi 0% (độ nhạy: 4%/năm).", "",
             "| | CAGR | Sharpe | Max DD | Biến động | Lần vào/ra/năm | % thời gian nắm giữ | CAGR nếu tiền mặt 4% |",
             "|---|---|---|---|---|---|---|---|",
             f"| Mua & giữ ETF VN30 | {pct(bh['cagr'])} | {num(bh['sharpe_annual'])} | {pct(bh['max_drawdown'])} | "
             f"{pct(bh['vol_annual'])} | — | 100% | — |"]
    for key, t in report["trials"].items():
        s = t["summary"]
        lines.append(f"| {names.get(key, key)} | {pct(s['cagr'])} | {num(s['sharpe_annual'])} | {pct(s['max_drawdown'])} | "
                     f"{pct(s['vol_annual'])} | {s['switches_per_year']:.1f} | {s['time_invested'] * 100:.0f}% | "
                     f"{pct(t['cash_yield_4pct']['cagr'])} |")
    years = list(report["buy_hold_by_year"])
    lines += ["", "| Năm | " + " | ".join(years) + " |", "|---|" + "---|" * len(years),
              "| Mua & giữ | " + " | ".join(pct(report["buy_hold_by_year"][y]) for y in years) + " |"]
    for key, t in report["trials"].items():
        lines.append(f"| {names.get(key, key)} | " + " | ".join(pct(t["by_year"].get(y)) for y in years) + " |")
    for key, t in report["trials"].items():
        g = t["gate_c"]
        lines += ["", f"**{names.get(key, key)}{' (chính)' if key == report['primary'] else ''}: "
                      f"{'ĐẠT' if g['passed'] else 'KHÔNG ĐẠT'} cửa C**", ""]
        lines += [f"- {'✅' if ok else '❌'} {labels[k]}" for k, ok in g["checks"].items()]
        for n, h in enumerate(t["halves"], 1):
            lines.append(f"  - Nửa {n} ({h['trend']['start']} → {h['trend']['end']}): Sharpe {num(h['trend']['sharpe_annual'])} "
                         f"vs {num(h['buy_hold']['sharpe_annual'])}, Max DD {pct(h['trend']['max_drawdown'])} "
                         f"vs {pct(h['buy_hold']['max_drawdown'])}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("period", choices=("dev", "holdout"))
    args = parser.parse_args()
    locks = json.loads(HOLDOUT_LOCK.read_text()) if HOLDOUT_LOCK.exists() else {}
    if args.period == "holdout":
        if "trend" in locks:
            raise SystemExit(f"holdout already evaluated for trend: {locks['trend']}")
        dev = json.loads((REPORT_DIR / "trend_dev.json").read_text())
        if not dev["trials"][PRIMARY]["gate_c"]["passed"]:
            raise SystemExit("primary trial did not pass gate C on the development period; holdout stays unused")
    panel = load_panel()
    report = evaluate(panel, args.period)
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"trend_{args.period}.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=1))
    (REPORT_DIR / f"trend_{args.period}.md").write_text(render(_clean(report)), encoding="utf-8")
    if args.period == "holdout":
        locks["trend"] = {"evaluated_at": report["generated_at"], "snapshot": report["snapshot"], "forced": False}
        HOLDOUT_LOCK.write_text(json.dumps(locks, indent=1))
    print(REPORT_DIR / f"trend_{args.period}.md")


if __name__ == "__main__":
    main()
