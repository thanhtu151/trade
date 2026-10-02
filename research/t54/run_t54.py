"""T54: ý #5 positive-MAX / tiếp diễn trần (M1 giữ 1 tháng, M2 giữ 2 tháng), CHỈ dữ liệu <= 2023-12-31.
Ý #4 (sự kiện index/ETF): không chạy — thiếu dữ liệu thành phần rổ PIT (xem trial_log.md mục T54).
  ../.venv/bin/python t54/run_t54.py prep   # dựng tín hiệu + kiểm nhân quả + độ phủ, KHÔNG chạy engine
  ../.venv/bin/python t54/run_t54.py run    # chạy 2 cấu hình đã khóa trong t54/runs.json (1 lần)
Đăng ký trước: research/trial_log.md mục T54."""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all, pit_universe, NON_STOCK
from engine.core import run, Strategy
from engine.bench import evaluate, gate_a, deflated_sharpe, protocol_config, index_bench
from engine.portfolio import cscv_pbo

RES = "/Users/drone/Downloads/tradeclone/research/"
T38, T47, T51, OUT = RES + "t38/", RES + "t47/", RES + "t51/", RES + "t54/"
N_TOTAL = 97 + 2
THR = 0.065
N_PARAMS = 3
CFGS = {"M1_h1": dict(top=10, hold=1), "M2_h2": dict(top=5, hold=2)}
WF = [("2018-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
      ("2018-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
      ("2018-01-01", "2022-12-31", "2023-01-01", "2023-12-31")]
FLOOR = 0.93


def month_ends(idx):
    nxt = pd.Series(idx[1:].month.tolist() + [-1], index=idx)
    return idx[(nxt.values != idx.month) & (idx >= "2018-01-01")]


def signals(P, rb_days):
    """ranked[k] = [(mã, MAX)...] MAX giảm dần, elig[k] = universe có giao dịch tại k. Chỉ dùng dữ liệu <= k."""
    stocks = [s for s in P.symbols if s not in NON_STOCK]
    U = pit_universe(P, stocks)
    C, Cf = P.close[stocks], P.close_ff[stocks]
    r = C / Cf.shift(1) - 1
    mx = r.groupby(P.idx.to_period("M")).max()
    ranked, elig, cov = {}, {}, []
    for d in rb_days:
        if d not in P.idx:
            continue
        k = P.idx.get_loc(d)
        ok = U.iloc[k].values & C.iloc[k].notna().values
        names = np.array(stocks)[ok]
        m = mx.loc[d.to_period("M")][names]
        q = m[m >= THR]
        ranked[k] = [(s, float(q[s])) for s in sorted(q.index, key=lambda s: (-q[s], s))]
        elig[k] = list(names)
        cov.append(dict(date=d, universe=int(ok.sum()), qualifying=len(q)))
    return ranked, elig, pd.DataFrame(cov)


class MaxStrat(Strategy):
    def __init__(self, name, ranked, elig, top, hold):
        self.name, self.n_params = name, N_PARAMS
        self.elig, self.hold = elig, hold
        ks = sorted(ranked)
        self.rbpos = {k: i for i, k in enumerate(ks)}
        self.buy = {k: [s for s, _ in ranked[k][:top]] for k in ks}
        self.keep = {}
        for i, k in enumerate(ks):
            keep = set(self.buy[k])
            for j in range(1, hold):
                if i - j >= 0:
                    keep |= set(self.buy[ks[i - j]])
            self.keep[k] = keep

    def prepare(self, panel):
        return None

    def eligible(self, k, d):
        return self.elig.get(k, [])

    def meta(self, k, s):
        return {}

    def candidates(self, k, d, held):
        return [(s, {}) for s in self.buy.get(k, []) if s not in held]

    def exit_signal(self, k, d, sym, pos):
        if k not in self.keep or sym in self.keep[k]:
            return None
        age = self.rbpos[k] - self.rbpos.get(pos["sig_k"], -10 ** 6)
        return "rebal" if age >= self.hold else None


def sharpe_of(r):
    return float(r.mean() / r.std() * np.sqrt(252)) if len(r) > 2 and r.std() > 0 else 0.0


def causal(P, rb_days, ranked):
    bad = 0
    for t in rb_days:
        k = P.idx.get_loc(t)
        rt, _, _ = signals(P.truncate(t), [t])
        bad += rt.get(k, []) != ranked.get(k, [])
    return bad, len(rb_days)


def stress(tr, n):
    s = tr.proceeds * FLOOR ** n / tr.cost - 1
    return dict(worst=float(s.min()), mean=float(s.mean()), median=float(s.median()))


def main(mode):
    t0 = time.time()
    P = Panel(universe_all(True) + ["VN30"], start="2016-01-01")
    rb = month_ends(P.idx)
    ranked, elig, cov = signals(P, rb)
    info = dict(n_stocks=len([s for s in P.symbols if s not in NON_STOCK]), rebalance_days=len(cov),
                universe_mean=float(cov.universe.mean()), qualifying_mean=float(cov.qualifying.mean()),
                qualifying_min=int(cov.qualifying.min()), months_lt10=int((cov.qualifying < 10).sum()),
                months_lt5=int((cov.qualifying < 5).sum()))
    bad, tot = causal(P, rb, ranked)
    info["causal_violations"], info["causal_checks"] = int(bad), int(tot)
    print(json.dumps(info, indent=1), flush=True)
    json.dump(info, open(OUT + "coverage_causal.json", "w"), indent=1)
    cov.to_csv(OUT + "coverage_rebalance.csv", index=False)
    if mode != "run":
        print("(prep %.0fs)" % (time.time() - t0))
        return
    if os.path.exists(OUT + "summary.csv"):
        print("T54 đã chạy -> không chạy lại")
        return
    cfg = protocol_config(max_pos=10, pos_pct=0.10, max_new=10, limits=True, delist_haircut=0.30)
    e1 = pd.read_csv(T47 + "summary.csv").set_index("config").loc["E1_ma10m"]
    etf = dict(cagr=float(e1.cagr_FULL), sharpe=float(e1.sharpe_FULL), maxdd=float(e1.maxdd_FULL))
    mk = lambda k: MaxStrat(k, ranked, elig, **CFGS[k])
    Es, navs, trs, extras = {}, {}, {}, {}
    for k in CFGS:
        Es[k], _ = evaluate(P, mk(k), cfg, seeds=50)
        navs[k], trs[k], extras[k] = run(P, mk(k), "2018-01-01", "2023-12-31", cfg)
        print(k, "xong (%.0fs)" % (time.time() - t0), flush=True)
    old = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(T38 + f)).values() if v
           for s in v["grid_sharpes_A"]]
    t47s = pd.read_csv(T47 + "summary.csv").sharpe_FULL.dropna().tolist()[:6]
    t51s = pd.read_csv(T51 + "summary.csv").sharpe_FULL.dropna().tolist()[:6]
    new_sr = [float(Es[k].loc["FULL"].sharpe) for k in CFGS]
    var_old, var_all = np.var(old, ddof=1), np.var(old + t47s + t51s + new_sr, ddof=1)
    sr_list = old if var_old >= var_all else old + t47s + t51s + new_sr
    ret = pd.DataFrame({k: navs[k].pct_change() for k in CFGS}).iloc[1:]
    ret.to_csv(OUT + "returns_full.csv")
    e1r = pd.read_csv(T47 + "returns_full.csv", index_col=0, parse_dates=True)["E1_ma10m"]
    names = list(CFGS)
    pb = cscv_pbo(ret[names].assign(E1_ref=e1r.reindex(ret.index)).fillna(0.0), 16)
    pbo = dict(pbo=pb["pbo"], n_splits=pb["n_splits"], is_sr=float(pb["is_best_is_sr"].mean()),
               oos_sr=float(pb["is_best_oos_sr"].mean()), p_oos_loss=pb["p_oos_loss"])
    pb2 = cscv_pbo(ret[names].fillna(0.0), 16)
    pbo["pbo_2cfg_only"] = pb2["pbo"]
    oos, is_sh, picks = [], [], []
    for a, b, c, d in WF:
        tr_sh = {k: sharpe_of(navs[k].loc[a:b].pct_change().dropna()) for k in names}
        best = max(tr_sh, key=tr_sh.get)
        picks.append(best)
        is_sh.append(tr_sh[best])
        oos.append(navs[best].loc[c:d].pct_change().dropna())
    o = pd.concat(oos)
    wf = dict(picks=picks, is_sharpe_each=is_sh, is_sharpe_mean=float(np.mean(is_sh)), oos_sharpe=sharpe_of(o),
              oos_ok=bool(np.mean(is_sh) > 0 and sharpe_of(o) >= 0.5 * np.mean(is_sh)),
              oos_cagr=float((1 + o).prod() ** (252 / len(o)) - 1),
              oos_by_year={str(pd.Timestamp(c).year): sharpe_of(x) for (_, _, c, _), x in zip(WF, oos)},
              etf_oos_sharpe=sharpe_of(e1r.loc["2021-01-01":"2023-12-31"]))
    vn30 = index_bench("2018-01-01", "2023-12-31", ("VN30",))["VN30"]
    rows, gates = [], {}
    for k in names:
        dsr, sr0 = deflated_sharpe(navs[k], N_TOTAL, sr_list)
        g = gate_a(Es[k], N_PARAMS, etf_core=etf, dsr=dsr, pbo=pbo["pbo"])
        gates[k] = {n: dict(value=str(v[0]), ok=bool(v[1])) for n, v in g.items()}
        row = dict(config=k, dsr=dsr, sr0=sr0, gate_pass=sum(v[1] for v in g.values()), gate_total=len(g))
        for nm in ("A", "B", "FULL"):
            for x in ("cagr", "sharpe", "maxdd", "n"):
                row["%s_%s" % (x, nm)] = float(Es[k].loc[nm][x])
        F_, tr = Es[k].loc["FULL"], trs[k]
        s2, s3 = stress(tr, 2), stress(tr, 3)
        row.update(pf=float(F_.pf), exp_mean=float(tr.net_ret.mean()), exp_median=float(tr.net_ret.median()),
                   win=float(F_.win), hold=float(F_.hold), z_sharpe=float(F_.z_sharpe), rand_sharpe=float(F_.rand_sharpe),
                   VNINDEX_cagr=float(F_.VNINDEX_cagr), VNINDEX_sharpe=float(F_.VNINDEX_sharpe), VNINDEX_maxdd=float(F_.VNINDEX_maxdd),
                   VN30_cagr=vn30["cagr"], VN30_sharpe=vn30["sharpe"], VN30_maxdd=vn30["maxdd"],
                   E1VFVN30_cagr=float(F_.E1VFVN30_cagr), E1VFVN30_maxdd=float(F_.E1VFVN30_maxdd),
                   blocked_buy=int(F_.blocked_buy), blocked_sell=int(F_.blocked_sell), open_pos=int(F_.open_pos),
                   worst_trade=float(tr.net_ret.min()), n_delist=int((tr.why == "delist").sum()),
                   stress2_worst=s2["worst"], stress2_mean=s2["mean"], stress2_median=s2["median"],
                   stress3_worst=s3["worst"], stress3_mean=s3["mean"], stress3_median=s3["median"],
                   stress3_nav_per_pos=0.10 * s3["worst"])
        rows.append(row)
    Sm = pd.DataFrame(rows).set_index("config")
    Sm.to_csv(OUT + "summary.csv")
    json.dump(pbo, open(OUT + "pbo.json", "w"), indent=1)
    json.dump(wf, open(OUT + "walk_forward.json", "w"), indent=1)
    json.dump(gates, open(OUT + "gate_a.json", "w"), indent=1, ensure_ascii=False)
    yr = pd.DataFrame({k: trs[k].groupby(trs[k].buy.dt.year).size() for k in names}).fillna(0).astype(int)
    yr.to_csv(OUT + "entries_per_year.csv")
    for k in names:
        trs[k].to_csv(OUT + "trades_%s.csv" % k, index=False)
    pd.set_option("display.width", 250, "display.max_columns", 80)
    print(Sm.T.to_string())
    print(yr.to_string())
    print(json.dumps(pbo)); print(json.dumps(wf))
    print(json.dumps(gates, ensure_ascii=False, indent=0))
    print("var_old %.4f var_all %.4f n_old %d | E1 %s | (%.0fs)" % (var_old, var_all, len(old), etf, time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "prep")
