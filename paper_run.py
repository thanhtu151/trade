"""Dry-run one paper session of the ETF core: E1 drives orders, E6 is monitor-only.

    python paper_run.py --date 2026-09-30 [--source data_fetcher|research] [--reset]

If `--date` has no bar in the data, the latest session <= date is used (and reported).
Writes JSONL under paper_logs/ (orders.jsonl, signals.jsonl) and keeps state in
paper_logs/paper_state.json. Never places real orders: PaperBroker only.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from broker.paper import PaperBroker
from runner import default_state, is_month_end, run_session
from signals import E1Ma10Month, E6Ma200Breadth

ETF = "E1VFVN30"
INITIAL_CASH = 100_000_000.0
RESEARCH_PRICES = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/prices/"   # thousand VND
EXCLUDE = {"VN30", "VNINDEX", "VN100", "HNX", "HNX30", "UPCOM", "E1VFVN30", "FUEVFVND"}
BASE = Path(__file__).resolve().parent


def _series(df, col="close", scale=1.0):
    d = df.copy()
    d["time"] = pd.to_datetime(d["time"]).dt.normalize()
    d = d.drop_duplicates("time", keep="last").set_index("time").sort_index()
    return d[col] * scale if col in d else d


def liquid_candidates(n=70):
    """Shortlist of the n most liquid symbols (60-session avg traded value) from the research
    snapshot. Only the symbol list comes from research; prices and the final top-50 ranking
    (done by E6) come from the live fetch."""
    if not os.path.isdir(RESEARCH_PRICES):        # e.g. GitHub Actions: use the committed snapshot list
        return json.loads((BASE / "paper_universe.json").read_text(encoding="utf-8"))["symbols"][:n]
    scored = {}
    for name in os.listdir(RESEARCH_PRICES):
        sym = name[:-len(".parquet")]
        if sym in EXCLUDE:
            continue
        df = pd.read_parquet(RESEARCH_PRICES + name).tail(60)
        if len(df) >= 60:
            scored[sym] = float((df["close"] * df["volume"]).mean())
    return sorted(scored, key=scored.get, reverse=True)[:n]


def load_data_fetcher(universe):
    from data_fetcher import get_stock_data_cached as get
    etf, vn30 = get(ETF, years=0.5), get("VN30", years=1.5)
    closes, values, failed = {}, {}, []
    for s in universe:
        try:
            df = get(s, years=1.5)
            closes[s] = _series(df)
            values[s] = closes[s] * _series(df, "volume")
        except Exception:
            failed.append(s)
    if failed:
        print(f"Cảnh báo: không lấy được giá {len(failed)} mã: {','.join(failed)}", file=sys.stderr)
    return _bars(etf), _series(vn30), pd.DataFrame(closes), pd.DataFrame(values)


def load_research(_universe=None):
    def read(sym):
        return pd.read_parquet(RESEARCH_PRICES + sym + ".parquet")
    etf = read(ETF)
    for c in ("open", "high", "low", "close"):
        etf[c] = etf[c] * 1000                     # research files are in thousand VND
    vn30 = _series(read("VN30"))
    closes, values = {}, {}
    for name in sorted(os.listdir(RESEARCH_PRICES)):
        sym = name[:-len(".parquet")]
        if sym in EXCLUDE:
            continue
        df = read(sym)
        c = _series(df, scale=1000)
        if c.index.max() < vn30.index.max() - pd.Timedelta(days=40) or len(c) < 200:
            continue
        closes[sym] = c.iloc[-400:]
        values[sym] = closes[sym] * _series(df, "volume").iloc[-400:]
    return _bars(etf), vn30, pd.DataFrame(closes), pd.DataFrame(values)


def _bars(df):
    d = df.copy()
    d["time"] = pd.to_datetime(d["time"]).dt.normalize()
    return d.drop_duplicates("time", keep="last").set_index("time").sort_index()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--source", choices=["data_fetcher", "research"], default="data_fetcher")
    ap.add_argument("--universe", default="top",
                    help="'top' = 70 most liquid candidates (E6 keeps top 50), or comma-separated symbols")
    ap.add_argument("--logs-dir", default=str(BASE / "paper_logs"))
    ap.add_argument("--reset", action="store_true", help="start from fresh state and empty logs")
    ap.add_argument("--require-exact", action="store_true",
                    help="if --date has no bar, print 'no session' and exit 0 instead of using an earlier session")
    ap.add_argument("--catch-up", action="store_true",
                    help="also run every missed session after the last date in --summary-csv, oldest first")
    ap.add_argument("--summary-csv", help="append one row per session date (skipped if the date is already there)")
    args = ap.parse_args(argv)

    want = date.fromisoformat(args.date)
    try:
        if args.source == "research":
            etf, vn30, closes, values = load_research()
        else:
            uni = liquid_candidates() if args.universe == "top" else [s for s in args.universe.split(",") if s]
            etf, vn30, closes, values = load_data_fetcher(uni)
    except Exception as exc:
        print(f"LỖI lấy giá ({args.source}): {exc!r}. Thử --source research.", file=sys.stderr)
        return 2

    sessions = [d for d in etf.index if d.date() <= want and d in vn30.index]
    if not sessions:
        print("LỖI: không có phiên nào <= ngày yêu cầu", file=sys.stderr)
        return 2
    latest = sessions[-1].date()
    exact = latest == want
    if args.require_exact and not exact:
        print(f"no session: {want} (phiên gần nhất có dữ liệu: {latest})")
        if not args.catch_up:
            return 0

    todo = [sessions[-1]] if exact or not args.require_exact else []
    last_done = _last_summary_day(Path(args.summary_csv)) if args.summary_csv else None
    if args.catch_up and last_done:
        todo = [t for t in sessions if t.date() > last_done]      # missed sessions, oldest first
        if len(todo) > 1:
            print(f"Chạy bù {len(todo) - 1} phiên bị lỡ sau {last_done}: {', '.join(str(t.date()) for t in todo[:-1])}")

    logs = Path(args.logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    orders_path, signals_path, state_path = logs / "orders.jsonl", logs / "signals.jsonl", logs / "paper_state.json"
    if args.reset:
        for p in (orders_path, signals_path, state_path):
            p.unlink(missing_ok=True)

    broker = PaperBroker(INITIAL_CASH, journal_path=orders_path)
    state = default_state()
    if state_path.exists():
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        broker.load_dict(saved["broker"])
        state = saved["runner"]

    for ts in todo:
        rc = _session(args, want, ts, etf, vn30, closes, values, broker, state, signals_path)
        if rc:
            return rc
        state_path.write_text(json.dumps({"broker": broker.to_dict(), "runner": state}, ensure_ascii=False,
                                         indent=1), encoding="utf-8")
    return 0


def _last_summary_day(path):
    if not path.exists():
        return None
    days = [l.split(",")[0] for l in path.read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    return date.fromisoformat(max(days)) if days else None


def _session(args, want, ts, etf, vn30, closes, values, broker, state, signals_path):
    day = ts.date()
    prev = etf.loc[etf.index < ts]
    if prev.empty:
        print("LỖI: thiếu phiên trước để lấy giá tham chiếu", file=sys.stderr)
        return 2
    bars = {"ref": float(prev["close"].iloc[-1]), "open": float(etf.loc[ts, "open"]),
            "close": float(etf.loc[ts, "close"])}
    from trading_calendar import is_trading_day
    month_end = is_month_end(day, is_trading_day)
    data = {"VN30": vn30, "stocks_close": closes, "stocks_value": values}
    out = run_session(day, ETF, bars, data, E1Ma10Month(), [E6Ma200Breadth()], broker, state,
                      month_end, signal_journal=signals_path)

    print(f"Phiên: {day} (yêu cầu {want}){' [cuối tháng]' if month_end else ''}; nguồn: {args.source}")
    if out.get("skipped"):
        print(f"Bỏ qua: {out['skipped']}")
    for name, t in out["signals"].items():
        tag = "theo dõi" if name.startswith("E6") else ("điều khiển lệnh" if month_end else "điều khiển lệnh, chỉ đổi ở cuối tháng")
        print(f"  {name}: {'ON' if t.weight else 'OFF'} ({t.reason}) {t.details} [{tag}]")
    for f in out["orders"]:
        print(f"  Lệnh: {f.side} {f.qty} {f.symbol} {f.status} @ {f.price:,.0f} phí {f.fee:,.0f} trượt {f.slippage_pct}% {f.reason}")
    if not out["orders"]:
        print("  Lệnh: không có")
    print(f"  Chờ khớp phiên sau: {out.get('pending')}")
    print(f"  NAV: {out['nav']:,.0f} VND (vốn đầu {INITIAL_CASH:,.0f})")
    if args.summary_csv and not out.get("skipped"):
        _summary_row(Path(args.summary_csv), day, out)
    return 0


def _summary_row(path, day, out):
    """One row per session date; a re-run of the same date never adds a second row."""
    header = "date,e1,e6,vn30,nav,orders"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if any(l.startswith(f"{day.isoformat()},") for l in lines):
        return
    sig = out["signals"]
    e1 = next((t for n, t in sig.items() if n.startswith("E1")), None)
    e6 = next((t for n, t in sig.items() if n.startswith("E6")), None)
    onoff = lambda t: "" if t is None else ("ON" if t.weight else "OFF")
    vn30 = (e1.details.get("close") if e1 else None) or (e6.details.get("vn30") if e6 else "")
    row = f"{day.isoformat()},{onoff(e1)},{onoff(e6)},{vn30},{out['nav']:.0f},{len(out['orders'])}"
    path.write_text("\n".join((lines or [header]) + [row]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
