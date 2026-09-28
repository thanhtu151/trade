"""Render research/reports/momentum_*.json into a Vietnamese markdown report.

    python -m research.report
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports"

GATE_LABELS = {
    "sharpe_ge_0.8": "Sharpe năm ≥ 0,8",
    "sharpe_beats_vn30": "Sharpe cao hơn VN30",
    "dsr_ge_0.95": "Deflated Sharpe ≥ 0,95",
    "pbo_lt_0.2": "PBO < 0,2",
    "excess_tstat_vs_vn30_ge_2": "t-stat lợi nhuận vượt VN30 ≥ 2",
    "max_drawdown_le_30pct": "Max drawdown ≤ 30%",
    "costs_le_30pct_of_gross": "Chi phí ≤ 30% lợi nhuận gộp",
    "bootstrap_sharpe_p05_gt_0": "Phân vị 5% Sharpe (bootstrap) > 0",
    "positive_without_best_year": "Vẫn lãi khi bỏ năm tốt nhất",
}
NAMES = {
    "mom_6_1": "Momentum 6-1 (chính)",
    "mom_12_1": "Momentum 12-1",
    "mom_6_1_regime0": "6-1 + lọc MA200 (0%)",
    "mom_6_1_regime50": "6-1 + lọc MA200 (50%)",
}


def pct(x, digits=1):
    return "—" if x is None else f"{x * 100:+.{digits}f}%".replace(".", ",")


def num(x, digits=2):
    return "—" if x is None else f"{x:.{digits}f}".replace(".", ",")


def section(report):
    period = report["period"]
    start, end = report["range"]
    lines = [f"## Giai đoạn {'phát triển' if period == 'dev' else 'holdout'} ({start} → {end or 'nay'})", ""]
    lines += ["| Biến thể | CAGR | Sharpe năm | Max DD | DSR | PSR>0 | Vượt VN30/năm | t-stat | Chi phí/gộp | Vòng quay/năm | Lệnh |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, res in report["trials"].items():
        s, v = res["summary"], res["vs_vn30"]
        lines.append(
            f"| {NAMES.get(key, key)} | {pct(s['cagr'])} | {num(s['sharpe_annual'])} | {pct(s['max_drawdown'])} | "
            f"{num(s['dsr'])} | {num(s['psr_vs_0'])} | {pct(v.get('excess_annual'))} | {num(v.get('excess_tstat'))} | "
            f"{pct(res['cost_share_of_gross'], 0) if res['cost_share_of_gross'] is not None else '—'} | "
            f"{num(res['turnover_per_year'], 1)}× | {res['trades']} |")
    for name, b in report["benchmarks"].items():
        lines.append(f"| {name} (chỉ số, không phí) | {pct(b['cagr'])} | {num(b['sharpe_annual'])} | "
                     f"{pct(b['max_drawdown'])} | — | — | — | — | — | — | — |")
    lines += ["", f"- PBO (4 biến thể, CSCV 8 khối): **{num(report['pbo'])}**"]
    low = report.get("primary_low_cost")
    if low:
        lines.append(f"- Độ nhạy chi phí (6-1, broker 0 phí, trượt giá 0,1%): CAGR {pct(low['cagr'])}, "
                     f"Sharpe {num(low['sharpe_annual'])}")
    pl = report["placebo"]
    if pl["runs"]:
        lines.append(f"- Placebo chọn mã ngẫu nhiên ({pl['runs']} lần): Sharpe trung vị {num(pl['sharpe_p50'])}, "
                     f"phân vị 95% {num(pl['sharpe_p95'])}; chiến lược chính đứng ở phân vị "
                     f"**{num(pl['primary_percentile'] * 100, 0)}%**")
    primary = report["trials"]["mom_6_1"]
    years = primary["by_year"]
    lines += ["", "Lợi nhuận theo năm (Momentum 6-1, sau phí):", "",
              "| " + " | ".join(years) + " |", "|" + "---|" * len(years),
              "| " + " | ".join(pct(v) for v in years.values()) + " |", ""]
    gate = report["gate1"]
    lines += [f"**Cửa 1 (backtest → paper) cho biến thể chính: {'ĐẠT' if gate['passed'] else 'KHÔNG ĐẠT'}**", ""]
    lines += [f"- {'✅' if ok else '❌'} {GATE_LABELS.get(k, k)}" for k, ok in gate["checks"].items()]
    return "\n".join(lines)


def render():
    parts = ["# Kết quả thí nghiệm momentum trên HOSE/HNX", ""]
    for period in ("dev", "holdout"):
        path = REPORT_DIR / f"momentum_{period}.json"
        if path.exists():
            report = json.loads(path.read_text(encoding="utf-8"))
            if period == "dev":
                parts += [f"Snapshot dữ liệu: `{report['snapshot']}` · {report['universe_symbols_in_panel']} mã "
                          f"trong panel · tạo lúc {report['generated_at']}", ""]
            parts += [section(report), ""]
    out = REPORT_DIR / "momentum.md"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(render())
