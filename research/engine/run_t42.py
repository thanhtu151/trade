"""T42: universe theo thời điểm (khử survivorship) + momentum 5 tham số. CHỈ dữ liệu <= 2023-12-31.
  .venv/bin/python research/engine/run_t42.py universe        # dựng + thống kê universe PIT
  .venv/bin/python research/engine/run_t42.py final [seeds]   # chạy mỗi cấu hình đăng ký trước đúng 1 lần
Đăng ký trước: research/trial_log.md mục T42. Tham số chốt từ T38 (research/t38/chosen2.json), không chọn lại."""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all, universe_backtest_config, pit_universe, listing, NON_STOCK
from engine.core import run, Config
from engine.candidates import Breakout, Momentum5
from engine.bench import evaluate, gate_a, deflated_sharpe, causal_check

OUT = "/Users/drone/Downloads/tradeclone/research/t42/"
T38 = "/Users/drone/Downloads/tradeclone/research/t38/"
START = "2016-01-01"
HAIRCUT = 0.30
N_TRIALS = 45 + 24 + 8          # T38 + T34/T37 + T42 (trial_log)
ETF_REF = dict(cagr=0.074, sharpe=0.56)
pd.set_option("display.width", 250)


def cfg(h=HAIRCUT):
    return Config(limits=True, max_pos=10, pos_pct=0.10, delist_haircut=h)


def make(fam):
    ch = json.load(open(T38 + "chosen2.json"))
    return {"momentum": lambda: Momentum5(**ch["momentum10"]["params"]).with_pit(),
            "breakout": lambda: Breakout(**ch["breakout10"]["params"]).with_pit()}[fam]


# khóa: (tên, họ, gồm DELISTED?, haircut, seeds?)
RUNS = [("momentum_pit", "momentum", True, HAIRCUT, True), ("breakout_pit", "breakout", True, HAIRCUT, True),
        ("momentum_pit_nodelist", "momentum", False, HAIRCUT, False), ("breakout_pit_nodelist", "breakout", False, HAIRCUT, False),
        ("momentum_pit_h0", "momentum", True, 0.0, False), ("breakout_pit_h0", "breakout", True, 0.0, False),
        ("momentum_pit_h100", "momentum", True, 1.0, False), ("breakout_pit_h100", "breakout", True, 1.0, False)]


def panel(include_delisted=True):
    return Panel(universe_all(include_delisted) + ["VN30"], start=START)


def universe(P):
    syms = [s for s in P.symbols if s not in NON_STOCK]
    U = pit_universe(P, syms).loc["2018-01-01":]
    ex = listing()
    old = set(universe_backtest_config())
    m = U.groupby(U.index.to_period("M")).first()
    rows = [dict(month=str(p), n=int(r.sum()), overlap_old50=len(set(r[r].index) & old),
                 delisted=" ".join(s for s in r[r].index if ex.get(s) == "DELISTED"),
                 symbols=" ".join(r[r].index)) for p, r in m.iterrows()]
    R = pd.DataFrame(rows)
    R.to_csv(OUT + "universe_pit.csv", index=False)
    ever = m.any()
    ever = list(ever[ever].index)
    has = P.close.notna().values
    lastk = len(P.idx) - 1 - np.argmax(has[::-1], axis=0)
    ended = [s for s, k in zip(P.symbols, lastk) if s not in NON_STOCK and k < len(P.idx) - 21 and P.idx[k] >= pd.Timestamp("2018-01-01")]
    print("tháng: %d | mã/tháng: %s | số mã khác nhau từng vào: %d | trùng TB với 50 mã cũ: %.1f/50" % (
        len(R), R.n.unique().tolist(), len(ever), R.overlap_old50.mean()))
    print("50 mã cũ có mặt trong universe PIT ít nhất 1 tháng: %d" % len(old & set(ever)))
    dl = [s for s in ever if ex.get(s) == "DELISTED"]
    print("mã DELISTED từng vào universe: %d -> %s" % (len(dl), dl))
    print("mã dừng GD trong 2018-2023 (>20 phiên trước cuối panel): %d, trong đó từng vào universe: %s" % (
        len(ended), [s for s in ended if s in ever]))
    print("số tháng mỗi mã DELISTED ở trong universe:", {s: int(m[s].sum()) for s in dl})
    print(R[["month", "overlap_old50", "delisted"]].iloc[::6].to_string())


def final(seeds):
    lock_f = OUT + "runs.json"
    done = json.load(open(lock_f)) if os.path.exists(lock_f) else {}
    ch = json.load(open(T38 + "chosen2.json"))
    all_sr = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(T38 + f)).values() if v
              for s in v["grid_sharpes_A"]]
    panels, summary = {}, {}
    for key, fam, incl, h, with_seeds in RUNS:
        if incl not in panels:
            panels[incl] = panel(incl)
        P = panels[incl]
        s = make(fam)()
        c = cfg(h)
        if key in done:
            if not (with_seeds and os.path.exists(OUT + "eval_%s.csv" % key)):
                print("\n##", key, ": ĐÃ chạy", done[key], "-> không chạy lại")
                continue
            # đã chạy + đã lưu eval (lần chạy trước dừng vì lỗi in ấn): chỉ chấm lại từ kết quả đã lưu
            E = pd.read_csv(OUT + "eval_%s.csv" % key, index_col=0)
            print("\n## %s — %s | ĐÃ chạy %s: chấm gate/DSR từ eval đã lưu" % (key, s.name, done[key]))
        else:
            done[key] = time.strftime("%Y-%m-%d %H:%M")
            json.dump(done, open(lock_f, "w"), indent=1)                 # khoá trước khi chạy
            E, T = evaluate(P, s, c, seeds=seeds if with_seeds else 0)
            E.to_csv(OUT + "eval_%s.csv" % key)
            T["FULL"].to_csv(OUT + "trades_%s.csv" % key, index=False)
            why = T["FULL"].groupby("why").net.agg(["count", "mean"]).round(0)
            print("\n## %s — %s | %d mã trong panel | haircut %.0f%%" % (key, s.name, len(P.symbols) - 1, h * 100))
            cols = ["cagr", "sharpe", "maxdd", "n", "win", "pf", "exp_pct", "exp_vnd", "hold", "open_pos", "blocked_buy", "blocked_sell"]
            if with_seeds:
                cols += ["rand_sharpe", "rand_sharpe_sd", "z_sharpe"]
            print(E[cols + ["VNINDEX_cagr", "E1VFVN30_cagr", "VNINDEX_maxdd"]].round(4).to_string())
            print("  thoát FULL:", why.to_dict())
        F = E.loc["FULL"]
        if not with_seeds:
            continue
        bad = causal_check(make(fam), P, list(P.idx[P.idx >= "2018-01-01"][::120]))
        nav = run(P, s, "2018-01-01", "2023-12-31", c)[0]
        dsr = deflated_sharpe(nav, N_TRIALS, all_sr)
        g = gate_a(E, s.n_params)
        for k, (val, ok) in g.items():
            print("  [%s] %s: %s" % ("ĐẠT" if ok else "KHÔNG", k, val))
        print("  gate_a: %d/8 | vs mốc ETF: CAGR %s, Sharpe %s | causal vi phạm: %d ngày" % (
            sum(ok for _, ok in g.values()), F.cagr > ETF_REF["cagr"], F.sharpe > ETF_REF["sharpe"], len(bad)))
        print("  DSR (N=%d, SR0 năm hoá %.2f): %.3f" % (N_TRIALS, dsr[1], dsr[0]))
    # momentum10 universe cũ (T38, N=142) chấm lại với n_params=5 — không chạy lại, chỉ đọc kết quả đã có
    E = pd.read_csv(T38 + "eval_momentum10.csv", index_col=0)
    g = gate_a(E, 5)
    print("\n## T38 momentum10 (universe 50 mã cũ) chấm lại với 5 tham số: %d/8 — %s" % (
        sum(ok for _, ok in g.values()), {k: v for k, (v, ok) in g.items() if not ok}))


if __name__ == "__main__":
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    if sys.argv[1] == "universe":
        P = panel()
        print("panel %d mã + VN30; %s -> %s" % (len(P.symbols) - 1, P.idx[0].date(), P.idx[-1].date()))
        universe(P)
    elif sys.argv[1] == "final":
        final(int(sys.argv[2]) if len(sys.argv) > 2 else 50)
    print("(%.0fs)" % (time.time() - t0))
