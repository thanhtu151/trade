"""T38: chọn tham số trên nửa A, chốt, rồi chạy B đúng một lần cho mỗi ứng viên.
  .venv/bin/python research/engine/run_t38.py grid            # chỉ nửa A 2018-2020, ghi research/t38/chosen.json
  .venv/bin/python research/engine/run_t38.py final [seeds]   # A/B/FULL + baseline + gate_a + DSR (B chỉ 1 lần)
Luật chọn (chốt trước khi chạy): Sharpe nửa A cao nhất trong các cấu hình có >= 100 lệnh ở nửa A."""
import itertools
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import pandas as pd
from engine.data import Panel, universe_backtest_config
from engine.core import run, metrics, Config
from engine.candidates import Breakout, Momentum, Reversion
from engine.bench import evaluate, gate_a, deflated_sharpe, causal_check

OUT = "/Users/drone/Downloads/tradeclone/research/t38/"
CFG = Config(limits=True)
A = ("2018-01-01", "2020-12-31")
MIN_N_A = 100
GRIDS = {
    "breakout": (Breakout, [dict(n=n, m_stop=m) for n, m in itertools.product((20, 50, 100), (2.0, 3.0, 4.0))]),
    "momentum": (Momentum, [dict(lookback=L, m_stop=m) for L, m in itertools.product((60, 120, 250), (2.0, 3.0, 4.0))]),
    "reversion": (Reversion, [dict(x=x, m_stop=m) for x, m in itertools.product((5, 10, 20), (2.0, 3.0, 4.0))]),
}
CFG10 = Config(limits=True, max_pos=10, pos_pct=0.10)
ROUNDS = {"chosen.json": ({k: GRIDS[k] for k in GRIDS}, CFG),
          "chosen2.json": ({k + "10": GRIDS[k] for k in ("breakout", "momentum")}, CFG10)}
ETF_REF = dict(cagr=0.074, sharpe=0.56)   # T37 N=93
pd.set_option("display.width", 250)


def panel():
    return Panel(universe_backtest_config() + ["VN30"])


def grid(P, fname):
    grids, cfg = ROUNDS[fname]
    chosen, rows = {}, []
    for fam, (cls, grid_) in grids.items():
        for p in grid_:
            nav, tr, ex = run(P, cls(**p), *A, cfg)
            m = metrics(nav, tr)
            rows.append(dict(family=fam, params=json.dumps(p), **{k: m[k] for k in ("cagr", "sharpe", "maxdd", "n", "pf", "exp_pct", "hold")},
                             blocked_buy=ex["blocked_buy"], blocked_sell=ex["blocked_sell"]))
        R = pd.DataFrame([r for r in rows if r["family"] == fam])
        ok = R[R.n >= MIN_N_A]
        best = ok.loc[ok.sharpe.idxmax()] if len(ok) else None
        chosen[fam] = dict(params=json.loads(best.params), sharpe_A=best.sharpe, n_A=int(best.n),
                           grid_sharpes_A=R.sharpe.tolist()) if best is not None else None
    R = pd.DataFrame(rows)
    print(R.round(3).to_string())
    os.makedirs(OUT, exist_ok=True)
    R.to_csv(OUT + fname.replace("chosen", "grid_A").replace(".json", ".csv"), index=False)
    if os.path.exists(OUT + fname):
        sys.exit(fname + " đã tồn tại — không ghi đè lựa chọn đã chốt")
    json.dump(chosen, open(OUT + fname, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: (v and v["params"]) for k, v in chosen.items()}))


def final(P, seeds):
    chosen, cfgs, fams = {}, {}, {}
    for fname, (grids, cfg) in ROUNDS.items():
        for k, v in json.load(open(OUT + fname)).items():
            chosen[k], cfgs[k], fams[k] = v, cfg, grids[k][0]
    done_f = OUT + "b_runs.json"
    done = json.load(open(done_f)) if os.path.exists(done_f) else {}
    n_trials_t38 = sum(len(g) for grids, _ in ROUNDS.values() for _, g in grids.values())
    all_sr = [s for v in chosen.values() if v for s in v["grid_sharpes_A"]]
    for fam, v in chosen.items():
        if v is None:
            print("\n##", fam, ": không cấu hình nào đủ", MIN_N_A, "lệnh ở A -> loại")
            continue
        if fam in done:
            print("\n##", fam, ": ĐÃ chạy B ngày", done[fam], "-> không chạy lại")
            continue
        cls, cfg = fams[fam], cfgs[fam]
        s = cls(**v["params"])
        bad = causal_check(lambda: cls(**v["params"]), P, list(P.idx[P.idx >= "2018-01-01"][::180]))
        done[fam] = time.strftime("%Y-%m-%d %H:%M")
        json.dump(done, open(done_f, "w"), indent=1)          # khoá trước khi chạy B
        E, T = evaluate(P, s, cfg, seeds=seeds)
        nav = run(P, s, "2018-01-01", "2023-12-31", cfg)[0]
        dsr = {n: deflated_sharpe(nav, n, all_sr) for n in (n_trials_t38, n_trials_t38 + 24)}  # + 24 lần thử chiến lược trước đó (T34/T37, không tính seed ngẫu nhiên)
        g = gate_a(E, s.n_params)
        print("\n## %s — %s (trường phái %s, %d vị thế), causal vi phạm: %d ngày" % (fam, s.name, cls.school, cfg.max_pos, len(bad)))
        cols = ["cagr", "sharpe", "maxdd", "n", "win", "pf", "exp_pct", "exp_vnd", "hold", "open_pos", "blocked_buy",
                "blocked_sell", "rand_sharpe", "rand_sharpe_sd", "z_sharpe", "VNINDEX_cagr", "E1VFVN30_cagr", "VNINDEX_maxdd"]
        print(E[cols].round(4).to_string())
        for k, (val, ok) in g.items():
            print("  [%s] %s: %s" % ("ĐẠT" if ok else "KHÔNG", k, val))
        F = E.loc["FULL"]
        print("  gate_a: %d/8 | vs mốc ETF: CAGR %s, Sharpe %s" % (
            sum(ok for _, ok in g.values()), F.cagr > ETF_REF["cagr"], F.sharpe > ETF_REF["sharpe"]))
        for n, (p, sr0) in dsr.items():
            print("  DSR (N=%d thử, SR0 năm hoá %.2f): %.3f" % (n, sr0, p))
        print("  thoát FULL:", T["FULL"].groupby("why").net.agg(["count", "mean"]).round(0).to_dict())
        E.to_csv(OUT + "eval_%s.csv" % fam)


if __name__ == "__main__":
    t0 = time.time()
    P = panel()
    print("universe %d mã + VN30; %s -> %s" % (len(P.symbols) - 1, P.idx[0].date(), P.idx[-1].date()))
    if sys.argv[1] == "grid":
        grid(P, "chosen.json")
    elif sys.argv[1] == "grid2":
        grid(P, "chosen2.json")
    elif sys.argv[1] == "final":
        final(P, int(sys.argv[2]) if len(sys.argv) > 2 else 50)
    print("(%.0fs)" % (time.time() - t0))
