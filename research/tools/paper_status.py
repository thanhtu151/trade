"""Tóm tắt sổ paper (<=15 dòng). Chỉ đọc: `git show <ref>:<file>`, không checkout.
Dùng: python3 research/tools/paper_status.py [--ref state]
Logic phiên/đối soát lấy từ research/t7/audit.py."""
import argparse, json, subprocess, sys
from datetime import datetime

REPO = "/Users/drone/Downloads/tradeclone"
SESSIONS = [(540, 690), (780, 885)]  # 9:00-11:30, 13:00-14:45 (như audit.py)


def show(ref, f):
    return json.loads(subprocess.check_output(
        ["git", "show", f"{ref}:{f}"], cwd=REPO, stderr=subprocess.PIPE))


def out_of_session(t):
    try:
        d = datetime.strptime(t["time"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    m = d.hour * 60 + d.minute
    return d.weekday() >= 5 or not any(a <= m <= b for a, b in SESSIONS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="state", help="nhánh state; tự thử origin/state nếu không có")
    a = ap.parse_args()
    ref = a.ref
    try:
        P = show(ref, "paper_portfolio.json")
    except subprocess.CalledProcessError:
        if ref == "origin/state":
            sys.exit("Không đọc được paper_portfolio.json từ " + ref)
        ref = "origin/state"
        P = show(ref, "paper_portfolio.json")
    T = show(ref, "paper_trades.json")
    trades = [t for t in T if "side" in t]
    ep = P.get("ledger_epoch")
    cur = [t for t in trades if t.get("epoch_id") == ep]
    pos = P.get("positions", {})
    cash, init = P["cash"], P.get("initial_cash", 0)
    mv = sum(p.get("market_value", 0) for p in pos.values())
    unreal = sum(p.get("unrealized_pnl", 0) for p in pos.values())
    real = sum(t["pnl"] for t in cur if t["side"] == "SELL" and t.get("pnl") is not None)
    flow = sum(-t["value"] if t["side"] == "BUY" else t["value"] for t in cur)
    warns = []
    if abs(init + flow - cash) > 1:
        warns.append(f"cash lệch sổ: {init + flow:,.0f} vs {cash:,.0f}")
    bad = [t for t in cur if t.get("price", 1e9) < 1000]
    if bad:
        warns.append(f"{len(bad)} lệnh giá<1000")
    if any(t["side"] == "SELL" and t.get("pnl") is None for t in cur):
        warns.append("có SELL pnl=None")
    n_out = sum(out_of_session(t) for t in cur)
    if n_out:
        warns.append(f"{n_out} lệnh ngoài phiên")
    for s, p in pos.items():
        ps = p.get("plan", {}).get("stop_loss")
        if ps is not None and p.get("stop_loss") != ps:
            warns.append(f"{s} stop position {p.get('stop_loss')} != plan {ps}")
        if p.get("qty", 0) != int(p.get("qty", 0)):
            warns.append(f"{s} qty lẻ {p['qty']}")
    nav = cash + mv
    print(f"Sổ paper [{ref}] epoch {ep}: {len(trades)} lệnh ({len(cur)} epoch này), {len(T)-len(trades)} RESET")
    print(f"Tiền mặt {cash:,.0f} | vị thế {mv:,.0f} | NAV {nav:,.0f} | vốn đầu {init:,.0f}")
    pct = (nav - init) / init * 100 if init else 0
    print(f"P&L tổng {nav-init:,.0f} ({pct:+.2f}%) | đã chốt {real:,.0f} | chưa chốt {unreal:,.0f} (chưa trừ phí)")
    print(f"Vị thế ({len(pos)}):")
    for s, p in list(pos.items())[:5]:
        print(f"  {s} qty {p.get('qty')} vốn {p.get('avg_price', 0):,.0f} giá {p.get('current_price', 0):,.0f} "
              f"{p.get('pnl_pct')}% stop {p.get('stop_loss') or 0:,.0f}")
    print("Lệnh gần nhất:")
    for t in trades[-3:]:
        print(f"  {t['time']} {t['side']} {t['symbol']} {t['qty']}@{t['price']:,.0f} pnl={t.get('pnl')} {str(t.get('reason'))[:24]}")
    print("Cảnh báo: " + ("; ".join(warns[:4]) if warns else "không"))


if __name__ == "__main__":
    main()
