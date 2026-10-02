"""T37: kiểm chứng engine. Chạy: .venv/bin/python research/engine/run_t37.py {t34|stage1|etf|all}"""
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import pandas as pd
from engine.data import Panel, universe_backtest_config
from engine.core import run, metrics, fmt, Config
from engine.strategies import Stage1, TrendETF, BuyHold
from engine.bench import PERIODS, evaluate, gate_a, index_bench

pd.set_option("display.width", 250)
COLS = ["cagr", "sharpe", "maxdd", "n", "win", "pf", "exp_pct", "exp_vnd", "hold", "open_pos",
        "rand_sharpe", "rand_sharpe_sd", "z_sharpe", "z_cagr", "VNINDEX_cagr", "E1VFVN30_cagr", "VNINDEX_maxdd"]


def show_eval(E, strat):
    print(E[[c for c in COLS if c in E]].round(4).to_string())
    for k, (v, ok) in gate_a(E, strat.n_params).items():
        print("  [%s] %s: %s" % ("ĐẠT" if ok else "KHÔNG", k, v))


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    t0 = time.time()
    if step in ("all", "t34", "stage1"):
        P = Panel(universe_backtest_config())
        print("universe %d mã; lịch %s -> %s" % (len(P.symbols), P.idx[0].date(), P.idx[-1].date()))
    if step in ("all", "t34"):
        print("\n## T37-1 đối chiếu T34: stop_mode=close, slip=0 (phải ~ T34 N=1)")
        for nm, a, b in PERIODS:
            nav, tr, _ = run(P, Stage1(), a, b, Config(slip=0.0, stop_mode="close"))
            print(nm, fmt(metrics(nav, tr)))
    if step in ("all", "stage1"):
        print("\n## T37-2 Stage1 hiện tại, engine chuẩn (intraday stop/target, slip 0.1%%), %d seed ngẫu nhiên" % seeds)
        s = Stage1()
        E, T = evaluate(P, s, Config(), seeds=seeds)
        show_eval(E, s)
        print("  theo lý do thoát FULL:", T["FULL"].groupby("why").net.agg(["count", "mean"]).round(0).to_dict())
    if step in ("all", "etf"):
        Q = Panel(["E1VFVN30", "VN30"])
        cfg = Config(max_pos=1, pos_pct=1.0, max_new=1)
        cfg_core = Config(max_pos=1, pos_pct=1.0, max_new=1, fee=0.0018, slip=0.0015)  # hằng số etf_core.py
        for label, c in (("engine chuẩn (phí 0.15%, slip 0.1%)", cfg), ("hằng số etf_core (phí 0.18%, slip 0.15%)", cfg_core)):
            print("\n## T37-3 tham chiếu: VN30 >= MA10 tháng -> giữ E1VFVN30; %s" % label)
            for nm, a, b in PERIODS:
                m1 = metrics(*run(Q, TrendETF(calendar=Q.idx), a, b, c)[:2])
                m2 = metrics(*run(Q, BuyHold("E1VFVN30"), a, b, c)[:2])
                print(nm, "trend:", fmt(m1))
                print(nm, "buyhold ETF:", fmt({k: m2[k] for k in ("cagr", "sharpe", "maxdd", "n")}))
        for nm, a, b in PERIODS:
            print("  chỉ số thô", nm, {k: fmt(v) for k, v in index_bench(a, b).items()})
    print("\n(%.0fs)" % (time.time() - t0))
