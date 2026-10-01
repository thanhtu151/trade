"""T42 — kiểm tra HOLDOUT 2024-01-01 -> hết dữ liệu (snapshot 2026-09-29). CHƯA ĐƯỢC CHẠY. CHỜ SẾP DUYỆT.

Điều kiện chạy thật (cả hai bắt buộc):
  1) Sếp tạo file research/t42/HOLDOUT_APPROVED.txt (không rỗng, ghi tên + ngày duyệt).
  2) research/t42/holdout_runs.json chưa có ứng viên đó -> mỗi ứng viên chỉ chạy ĐÚNG MỘT LẦN.
     File khóa được ghi TRƯỚC khi nạp bất kỳ dữ liệu nào sau 2023.
Tham số chốt (T38 chọn trên nửa A 2018-20, T42 không chọn lại) — KHÔNG sửa sau khi xem kết quả:
  momentum5p: L=60, stop 2 ATR, bỏ 5 phiên, top 5, giữ trong top 10, cổng VN30>=MA10 tháng
  breakout10: N=50, stop/trail 2 ATR, cổng VN30>=MA10 tháng
  Cả hai: universe PIT top 50 thanh khoản, 10 vị thế x 10%, engine chuẩn + khóa trần/sàn, hủy niêm yết -30%.
Tiêu chí đạt (đăng ký trước trong research/trial_log.md, T42): đủ 6/6.

Lệnh:
  .venv/bin/python research/t42/holdout.py --dry     # kiểm code trên 2023 (dữ liệu <= END), không cần duyệt, không khóa
  .venv/bin/python research/t42/holdout.py           # THẬT — chỉ sau khi sếp duyệt
"""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import pandas as pd
from engine.data import Panel, universe_all, holdout_approved, END, HOLDOUT_APPROVAL
from engine.core import run, metrics, Config
from engine.candidates import Breakout, Momentum5
from engine.strategies import TrendETF, BuyHold
from engine.bench import random_baseline, zgap

OUT = "/Users/drone/Downloads/tradeclone/research/t42/"
LOCK = OUT + "holdout_runs.json"
HOLDOUT = ("2024-01-01", "2026-09-29", "2021-01-01")     # (bắt đầu, kết thúc, bắt đầu panel để làm nóng chỉ báo)
DRY = ("2023-01-01", END, "2020-01-01")
SEEDS = 50
CFG = Config(limits=True, max_pos=10, pos_pct=0.10, delist_haircut=0.30)
CANDS = {"momentum5p": lambda: Momentum5(lookback=60, m_stop=2.0).with_pit(),
         "breakout10": lambda: Breakout(n=50, m_stop=2.0).with_pit()}


def check_locked_params():
    ch = json.load(open("/Users/drone/Downloads/tradeclone/research/t38/chosen2.json"))
    assert ch["momentum10"]["params"] == dict(lookback=60, m_stop=2.0)
    assert ch["breakout10"]["params"] == dict(n=50, m_stop=2.0)


def bench(P, a, b):
    """Mốc cùng kỳ: VN-Index (giá), E1VFVN30 mua-giữ và ETF VN30>=MA10 (engine, có phí)."""
    c = P.close["VNINDEX"].loc[a:b].dropna()
    r = c.pct_change().dropna()
    vni = dict(cagr=(c.iloc[-1] / c.iloc[0]) ** (252 / len(r)) - 1, maxdd=(c / c.cummax() - 1).min())
    ecfg = Config(max_pos=1, pos_pct=1.0, max_new=1)
    etf = metrics(*run(P, TrendETF(calendar=P.idx), a, b, ecfg)[:2])
    bh = metrics(*run(P, BuyHold("E1VFVN30"), a, b, ecfg)[:2])
    return vni, etf, bh


def criteria(m, z, vni, etf, bh):
    return {
        "CAGR > 0": ("%.1f%%" % (m["cagr"] * 100), m["cagr"] > 0),
        "Sharpe >= 0,5": (round(m["sharpe"], 2), m["sharpe"] >= 0.5),
        "CAGR > E1VFVN30 mua-giữ và > ETF VN30>=MA10": ("%.1f%% vs %.1f%% / %.1f%%" % (m["cagr"] * 100, bh["cagr"] * 100, etf["cagr"] * 100),
                                                      m["cagr"] > bh["cagr"] and m["cagr"] > etf["cagr"]),
        "MaxDD không tệ hơn VN-Index": ("%.1f%% vs %.1f%%" % (m["maxdd"] * 100, vni["maxdd"] * 100), m["maxdd"] >= vni["maxdd"]),
        "PF >= 1,2": (round(m.get("pf", float("nan")), 2), m.get("pf", 0) >= 1.2),
        "Sharpe hơn ngẫu nhiên >= 1 SD": (round(z, 2), z >= 1),
    }


def main(dry):
    check_locked_params()
    a, b, p0 = DRY if dry else HOLDOUT
    if not dry:
        if not holdout_approved():
            sys.exit("CHƯA DUYỆT: thiếu %s -> không chạy holdout" % HOLDOUT_APPROVAL)
        done = json.load(open(LOCK)) if os.path.exists(LOCK) else {}
        todo = [k for k in CANDS if k not in done]
        if not todo:
            sys.exit("Holdout đã chạy cho mọi ứng viên: %s -> không chạy lại" % done)
        for k in todo:
            done[k] = time.strftime("%Y-%m-%d %H:%M")
        json.dump(done, open(LOCK, "w"), indent=1)       # khóa TRƯỚC khi nạp dữ liệu sau 2023
    else:
        todo = list(CANDS)
    P = Panel(universe_all() + ["VN30", "VNINDEX", "E1VFVN30"], end=b, start=p0, allow_holdout=not dry)
    print("%s | panel %d mã, %s -> %s | kiểm %s -> %s" % ("DRY-RUN (không phải holdout)" if dry else "HOLDOUT",
          len(P.symbols), P.idx[0].date(), P.idx[-1].date(), a, b))
    vni, etf, bh = bench(P, a, b)
    print("mốc: VN-Index CAGR %.1f%% DD %.1f%% | E1VFVN30 mua-giữ %.1f%% Sharpe %.2f | ETF VN30>=MA10 %.1f%% Sharpe %.2f" % (
        vni["cagr"] * 100, vni["maxdd"] * 100, bh["cagr"] * 100, bh["sharpe"], etf["cagr"] * 100, etf["sharpe"]))
    res = {}
    for k in todo:
        s = CANDS[k]()
        nav, tr, ex = run(P, s, a, b, CFG)
        m = metrics(nav, tr)
        R = random_baseline(P, s, a, b, CFG, SEEDS)
        z = zgap(m["sharpe"], R.sharpe)
        g = criteria(m, z, vni, etf, bh)
        n_ok = sum(ok for _, ok in g.values())
        print("\n## %s — %s" % (k, s.name))
        print("  CAGR %.1f%% | Sharpe %.2f | MaxDD %.1f%% | %d lệnh | PF %.2f | exp %.2f%%/lệnh | mở cuối kỳ %d | ngẫu nhiên Sharpe %.2f (SD %.2f)" % (
            m["cagr"] * 100, m["sharpe"], m["maxdd"] * 100, m["n"], m.get("pf", float("nan")), m.get("exp_pct", float("nan")) * 100,
            ex["open_positions"], R.sharpe.mean(), R.sharpe.std(ddof=1)))
        for c, (v, ok) in g.items():
            print("  [%s] %s: %s" % ("ĐẠT" if ok else "KHÔNG", c, v))
        print("  => %d/6 — %s" % (n_ok, "QUA HOLDOUT" if n_ok == 6 else "KHÔNG QUA HOLDOUT"))
        res[k] = dict(metrics={x: float(v) for x, v in m.items()}, z=float(z), passed=n_ok, of=6)
    if not dry:
        json.dump(dict(run_at=time.strftime("%Y-%m-%d %H:%M"), period=[a, b], results=res,
                       bench=dict(vni=vni, etf=etf, bh=bh)), open(OUT + "holdout_result.json", "w"), indent=1, default=float)
        print("\nGhi research/t42/holdout_result.json. Nhớ ghi trial_log.")


if __name__ == "__main__":
    main("--dry" in sys.argv)
