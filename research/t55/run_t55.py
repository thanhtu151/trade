"""T55 — HOLDOUT MỘT LẦN cho E1, E6 (lõi ETF, khóa từ T48). Đăng ký trước: research/trial_log.md mục T55.
  .venv/bin/python research/t55/run_t55.py
Tái dùng nguyên signals()/WantETF của t47/run_t47.py, không đổi tham số."""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research/t47")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all, holdout_approved, HOLDOUT_APPROVAL
from engine.core import run, metrics
from engine.strategies import BuyHold
from engine.bench import protocol_config
from run_t47 import signals, WantETF

OUT = "/Users/drone/Downloads/tradeclone/research/t55/"
LOCK = OUT + "holdout_runs.json"
A, B = "2024-01-01", "2026-09-29"
KEYS = {"E1": "E1_ma10m", "E6": "E6_ma200d_b60"}
IS_SHARPE = {"E1": 0.57, "E6": 0.81}


def dd_win(x, a, b):
    x = x.loc[a:b]
    return (x / x.cummax() - 1).min(), x.iloc[-1] / x.iloc[0] - 1


def episodes(c, thr=0.10):
    """Đợt giảm liên tiếp của chuỗi giá c: từ đỉnh chạy đến đáy, tách khi phục hồi về đỉnh cũ. Giữ đợt >= thr."""
    peak_d, peak, out = c.index[0], c.iloc[0], []
    trough_d, trough = peak_d, peak
    for d, v in c.items():
        if v >= peak:
            if trough / peak - 1 <= -thr:
                out.append((peak_d, trough_d, peak, trough))
            peak_d, peak, trough_d, trough = d, v, d, v
        elif v < trough:
            trough_d, trough = d, v
    if trough / peak - 1 <= -thr:
        out.append((peak_d, trough_d, peak, trough))
    return out


def main():
    if not holdout_approved():
        sys.exit("CHƯA DUYỆT: thiếu %s" % HOLDOUT_APPROVAL)
    if os.path.exists(LOCK):
        sys.exit("T55 holdout đã chạy: %s -> không chạy lại" % open(LOCK).read())
    json.dump({k: time.strftime("%Y-%m-%d %H:%M") for k in KEYS}, open(LOCK, "w"), indent=1)   # khóa TRƯỚC khi nạp dữ liệu > 2023
    P = Panel(universe_all(True) + ["VN30"], end=B, start="2016-01-01", allow_holdout=True)
    Q = Panel(["E1VFVN30", "VN30"], end=B, allow_holdout=True)
    V = Panel(["VNINDEX"], end=B, allow_holdout=True).close["VNINDEX"].dropna()
    print("panel %d mã, %s -> %s" % (len(P.symbols), P.idx[0].date(), P.idx[-1].date()))
    sig, breadth = signals(P)
    cfg = protocol_config(max_pos=1, pos_pct=1.0, max_new=1)
    navs, res = {}, {}
    for nm, k in KEYS.items():
        nav, tr, ex = run(Q, WantETF(k, sig[k]), A, B, cfg)
        m = metrics(nav, tr)
        on = sig[k].loc[A:B]
        m["days_on"], m["months_on"] = int(on.sum()), on.sum() / 21
        m["open_at_end"] = ex["open_positions"]
        navs[nm], res[nm] = nav, m
        tr.to_csv(OUT + "trades_%s.csv" % nm, index=False)
    bh_nav, bh_tr, _ = run(Q, BuyHold(), A, B, cfg)
    bh = metrics(bh_nav, bh_tr)
    vi = V.loc[A:B]
    r = vi.pct_change().dropna()
    vni = dict(cagr=(vi.iloc[-1] / vi.iloc[0]) ** (252 / len(r)) - 1, sharpe=r.mean() / r.std() * np.sqrt(252),
               maxdd=(vi / vi.cummax() - 1).min())
    print("\nHOLDOUT %s -> %s (%d phiên)" % (A, B, len(vi)))
    f = "%-10s CAGR %6.1f%% | Sharpe %5.2f | MaxDD %6.1f%%"
    for nm, m in res.items():
        print(f % (nm, m["cagr"] * 100, m["sharpe"], m["maxdd"] * 100),
              "| %d lệnh | %d phiên bật = %.1f tháng | mở cuối kỳ %d | Sharpe OOS/IS %.2f (IS %.2f) -> %s" % (
                  m["n"], m["days_on"], m["months_on"], m["open_at_end"], m["sharpe"] / IS_SHARPE[nm], IS_SHARPE[nm],
                  "XÁC NHẬN" if m["sharpe"] >= 0.5 * IS_SHARPE[nm] else "KHÔNG XÁC NHẬN"))
    print(f % ("E1VFVN30 BH", bh["cagr"] * 100, bh["sharpe"], bh["maxdd"] * 100))
    print(f % ("VN-Index", vni["cagr"] * 100, vni["sharpe"], vni["maxdd"] * 100))
    # đợt giảm VNI >= 10%
    eps = episodes(vi, 0.10)
    print("\nĐợt giảm VN-Index >= 10%% (đỉnh -> đáy): DD/lợi suất trong đợt")
    rows = []
    for pd_, td, pk, tr_ in eps:
        row = dict(dot="%s->%s" % (pd_.date(), td.date()), vni_peak=round(pk), vni_trough=round(tr_), VNI=(tr_ / pk - 1) * 100)
        for nm, n in list(navs.items()) + [("BH", bh_nav)]:
            row[nm] = dd_win(n, pd_, td)[1] * 100        # thay đổi NAV đỉnh->đáy VNI
        rows.append(row)
    pd.set_option("display.width", 250)
    if rows:
        print(pd.DataFrame(rows).round(1).to_string(index=False))
    # cú 9/3/2026
    w = ("2026-03-02", "2026-03-31")
    print("\nCửa sổ %s..%s: DD (đỉnh-đáy trong cửa sổ) | lợi suất" % w)
    for nm, x in [("VNI", vi)] + list(navs.items()) + [("BH", bh_nav)]:
        d, rt = dd_win(x, *w)
        print("  %-4s DD %6.1f%% | ret %6.1f%%" % (nm, d * 100, rt * 100))
    print("  VNI 5-13/3/2026:", ", ".join("%s %.0f" % (d.strftime("%d/%m"), v) for d, v in vi.loc["2026-03-05":"2026-03-13"].items()))
    # đợt VNI 1920 -> 1686
    seg = vi[vi.between(1600, 2100)]
    pk = vi[(vi - 1920).abs() < 15]
    print("\nNgày VNI gần 1920:", [d.strftime("%Y-%m-%d") for d in pk.index][:12])
    tr_d = vi[(vi - 1686).abs() < 12]
    print("Ngày VNI gần 1686:", [d.strftime("%Y-%m-%d") for d in tr_d.index][:12])
    # tín hiệu hiện tại
    last = P.idx[-1]
    print("\nPhiên cuối dữ liệu:", last.date())
    for nm, k in KEYS.items():
        print("  %s tín hiệu tại %s: %s" % (nm, last.date(), "NẮM GIỮ" if sig[k].iloc[-1] else "TIỀN MẶT"))
    vn30 = P.close["VN30"].dropna()
    print("  VN30 %.1f | MA200 %.1f | breadth %.1f%%" % (vn30.iloc[-1], vn30.rolling(200).mean().iloc[-1], breadth.iloc[-1] * 100))
    print("  tháng gần nhất kết thúc trước %s; E1 dùng tín hiệu cuối tháng trước." % last.date())
    json.dump(dict(run_at=time.strftime("%Y-%m-%d %H:%M"), period=[A, B], results={k: {a: float(b) for a, b in v.items()} for k, v in res.items()},
                   bh={a: float(b) for a, b in bh.items()}, vni=vni), open(OUT + "holdout_result.json", "w"), indent=1, default=float)
    navs_df = pd.DataFrame(dict(navs, BH=bh_nav, VNI=vi))
    navs_df.to_csv(OUT + "navs.csv")
    sig_df = pd.DataFrame({nm: sig[k].loc["2023-10-01":B] for nm, k in KEYS.items()})
    sig_df["breadth"] = breadth.loc["2023-10-01":B]
    sig_df.to_csv(OUT + "signals.csv")


if __name__ == "__main__":
    main()
