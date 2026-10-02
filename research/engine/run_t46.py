"""T46: ghép 2 túi (ETF lõi + xu hướng), vol-scaling, lọc trần + PBO/CSCV. CHỈ dữ liệu <= 2023-12-31.
  .venv/bin/python research/engine/run_t46.py smoke          # kiểm cơ chế trên 2017-H1 (ngoài A/B/FULL), không in chỉ số, không khóa
  .venv/bin/python research/engine/run_t46.py final [seeds]  # chạy mỗi cấu hình đăng ký trước đúng 1 lần
Đăng ký trước: research/trial_log.md mục T46."""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all
from engine.core import run, metrics, Config, RandomEntry
from engine.candidates import Breakout, Momentum5, BreakoutNoLimitUp
from engine.strategies import TrendETF
from engine.bench import PERIODS, index_bench, gate_a, deflated_sharpe, causal_check, zgap
from engine.portfolio import combine, vol_overlay, cscv_pbo

OUT = "/Users/drone/Downloads/tradeclone/research/t46/"
T42 = "/Users/drone/Downloads/tradeclone/research/t42/"
T38 = "/Users/drone/Downloads/tradeclone/research/t38/"
N_TRIALS = 77 + 8
ETF_REF = dict(cagr=0.074, sharpe=0.56, maxdd=-0.23)
VS = dict(target=0.15, lookback=60)
CH = json.load(open(T38 + "chosen2.json"))
SLEEVES = {"breakout": lambda: Breakout(**CH["breakout10"]["params"]).with_pit(),
           "momentum": lambda: Momentum5(**CH["momentum10"]["params"]).with_pit(),
           "nolu": lambda: BreakoutNoLimitUp(jump=0.065, **CH["breakout10"]["params"]).with_pit()}
NP = {"breakout": 4, "momentum": 5}
# cấu hình T46: tên -> (túi xu hướng, w ETF hoặc None, vol-scaling?, n_params)
CONFIGS = {"C1_etf70_breakout30": ("breakout", 0.7, False, 5), "C2_etf50_breakout50": ("breakout", 0.5, False, 5),
           "C3_etf70_momentum30": ("momentum", 0.7, False, 6), "C4_etf50_momentum50": ("momentum", 0.5, False, 6),
           "V1_breakout_vs": ("breakout", None, True, 6), "V2_momentum_vs": ("momentum", None, True, 7),
           "V3_bestcombo_vs": None, "L1_breakout_nolimitup": ("nolu", None, False, 5)}
REF = {"R_breakout10": ("breakout", None, False, 4), "R_momentum5p": ("momentum", None, False, 5),
       "R_etf": (None, 1.0, False, 1)}
cfg_trend = Config(limits=True, max_pos=10, pos_pct=0.10, delist_haircut=0.30)
cfg_etf = Config(max_pos=1, pos_pct=1.0, max_new=1)


def components(periods, seeds, save):
    P = Panel(universe_all(True) + ["VN30"], start="2016-01-01")
    Q = Panel(["E1VFVN30", "VN30"])
    comp = {}
    for nm, a, b in periods:
        nav, tr, _ = run(Q, TrendETF(calendar=Q.idx), a, b, cfg_etf)
        comp[("etf", nm)] = (pd.DataFrame({"strat": nav}), tr)
    for sl, make in SLEEVES.items():
        for nm, a, b in periods:
            s = make()
            nav, tr, _ = run(P, s, a, b, cfg_trend)
            navs = {"strat": nav}
            for i in range(seeds):
                navs["r%d" % i] = run(P, RandomEntry(s, i), a, b, cfg_trend, prepared=True)[0]
            comp[(sl, nm)] = (pd.DataFrame(navs), tr)
            assert comp[(sl, nm)][0].index.equals(comp[("etf", nm)][0].index)
    if save:
        for (sl, nm), (N, tr) in comp.items():
            N.to_csv(OUT + "nav_%s_%s.csv" % (sl, nm))
            tr.to_csv(OUT + "trades_%s_%s.csv" % (sl, nm), index=False)
    return P, comp


def load_components(periods):
    comp = {}
    for sl in ["etf"] + list(SLEEVES):
        for nm, _, _ in periods:
            N = pd.read_csv(OUT + "nav_%s_%s.csv" % (sl, nm), index_col=0, parse_dates=True)
            tr = pd.read_csv(OUT + "trades_%s_%s.csv" % (sl, nm), parse_dates=["sig_buy", "buy", "sig_sell", "sell"])
            comp[(sl, nm)] = (N, tr)
    return comp


def build(spec, comp, nm):
    """-> (NAV cấu hình, bảng lệnh đã nhân tỷ trọng, danh sách NAV ngẫu nhiên)."""
    sl, w, vs, _ = spec
    E, etr = comp[("etf", nm)]
    if sl is None:                                         # ETF riêng
        return E["strat"], (etr.assign(wt=1.0) if len(etr) else etr), []
    N, tr = comp[(sl, nm)]

    def make(col):
        nav = N[col] if w is None else combine(E["strat"], N[col], w)[0]
        return vol_overlay(nav, **VS) if vs else (nav, pd.Series(1.0, index=nav.index))
    nav, e = make("strat")
    rand = [make(c)[0] for c in N.columns if c != "strat"]
    parts = [(tr, 1.0 if w is None else 1 - w)] + ([(etr, w)] if w is not None else [])
    parts = [t.assign(wt=f * e.reindex(t.buy).values) for t, f in parts if len(t)]
    return nav, (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()), rand


def trade_stats(tr):
    if not len(tr):
        return dict(n=0, win=np.nan, pf=np.nan, exp_pct=np.nan, exp_vnd=np.nan)
    net = tr.net * tr.wt
    w, l = net[net > 0], net[net <= 0]
    return dict(n=len(tr), win=len(w) / len(tr), pf=w.sum() / -l.sum() if l.sum() < 0 else np.inf,
                exp_pct=tr.net_ret.mean(), exp_vnd=net.mean())


def score(spec, comp, periods, bench):
    rows, navs = [], {}
    for nm, a, b in periods:
        nav, tr, rand = build(spec, comp, nm)
        m = {k: v for k, v in metrics(nav, pd.DataFrame()).items() if k in ("cagr", "sharpe", "maxdd")}
        m.update(trade_stats(tr), period=nm)
        if rand:
            rs = pd.Series([metrics(r, pd.DataFrame())["sharpe"] for r in rand])
            m.update(rand_sharpe=rs.mean(), rand_sharpe_sd=rs.std(ddof=1), z_sharpe=zgap(m["sharpe"], rs))
        for s in ("VNINDEX", "E1VFVN30"):
            for x in ("cagr", "sharpe", "maxdd"):
                m["%s_%s" % (s, x)] = bench[nm][s][x]
        rows.append(m)
        navs[nm] = nav
    return pd.DataFrame(rows).set_index("period"), navs


def final(seeds, smoke=False):
    periods = [("SMOKE", "2017-01-02", "2017-06-30")] if smoke else PERIODS
    lock_f = OUT + "runs.json"
    if smoke:
        P, comp = components(periods, seeds, save=False)
        for k, (N, tr) in comp.items():
            assert np.isfinite(N.values).all() and N.shape[1] == (1 if k[0] == "etf" else 1 + seeds), k
            print("smoke", k, "nav", N.shape, "lệnh", len(tr))
        bench = {nm: index_bench(a, b) for nm, a, b in periods}
        for key, spec in list(CONFIGS.items()) + list(REF.items()):
            spec = spec or ("breakout", 0.5, True, 7)
            E, navs = score(spec, comp, periods, bench)
            assert np.isfinite(navs["SMOKE"].values).all() and E.shape[0] == 1
            print("smoke", key, "ok: cột", len(E.columns))
        print("SMOKE xong — không in chỉ số, không ghi khóa")
        return
    if os.path.exists(OUT + "summary.csv"):
        print("T46 đã chạy (summary.csv tồn tại) -> không chạy lại")
        return
    if os.path.exists(lock_f):
        print("khóa có sẵn nhưng chưa có summary -> chấm lại từ thành phần đã lưu (không chạy lại engine)")
        comp = load_components(periods)
        P = None
    else:
        json.dump({k: time.strftime("%Y-%m-%d %H:%M") for k in CONFIGS}, open(lock_f, "w"), indent=1)  # khóa trước
        P, comp = components(periods, seeds, save=True)
    bench = {nm: index_bench(a, b) for nm, a, b in periods}
    all_sr = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(T38 + f)).values() if v
              for s in v["grid_sharpes_A"]]
    res, fullnav, gates = {}, {}, {}

    def do(key, spec):
        E, navs = score(spec, comp, periods, bench)
        E.to_csv(OUT + "E_%s.csv" % key)
        g = gate_a(E, spec[3])
        dsr = deflated_sharpe(navs["FULL"], N_TRIALS, all_sr)
        res[key], fullnav[key], gates[key] = (E, dsr, spec), navs["FULL"], g
    for key in REF:
        do(key, REF[key])
    for key, spec in CONFIGS.items():
        if spec is None:                                      # V3: ghép tốt nhất C1-C4 theo luật đăng ký trước
            cs = [k for k in CONFIGS if k.startswith("C")]
            best = max(cs, key=lambda k: (sum(ok for _, ok in gates[k].values()), res[k][0].loc["FULL", "sharpe"]))
            sl, w, _, npar = CONFIGS[best]
            spec = (sl, w, True, npar + 2)
            print("V3 chọn:", best)
        do(key, spec)
    # bảng tổng hợp
    rows = []
    for key, (E, dsr, spec) in res.items():
        F, g = E.loc["FULL"], gates[key]
        rows.append(dict(config=key, sleeve=spec[0], w_etf=spec[1], vs=spec[2], n_params=spec[3],
                         cagr=F.cagr, sharpe=F.sharpe, maxdd=F.maxdd, n=F.n, win=F.win, pf=F.pf,
                         exp_pct=F.exp_pct, cagr_A=E.loc["A", "cagr"], cagr_B=E.loc["B", "cagr"],
                         sharpe_A=E.loc["A", "sharpe"], sharpe_B=E.loc["B", "sharpe"],
                         z=F.get("z_sharpe", np.nan), rand_sharpe=F.get("rand_sharpe", np.nan),
                         gate=sum(ok for _, ok in g.values()), gate_fail="; ".join(k for k, (v, ok) in g.items() if not ok),
                         dsr=dsr[0], sr0=dsr[1], beat_etf=bool(F.cagr > ETF_REF["cagr"] and F.sharpe > ETF_REF["sharpe"])))
    S = pd.DataFrame(rows).set_index("config")
    R = pd.DataFrame({k: v.pct_change() for k, v in fullnav.items()}).iloc[1:]
    R.to_csv(OUT + "returns_full.csv")
    main = cscv_pbo(R[list(CONFIGS)], 16)
    aux = cscv_pbo(R, 16)
    pbo = {lbl: dict(pbo=r["pbo"], n_splits=r["n_splits"], n_configs=n, median_logit=float(np.median(r["logits"])),
                     is_best_oos_sr_mean=float(r["is_best_oos_sr"].mean()), p_oos_loss=r["p_oos_loss"],
                     is_best_is_sr_mean=float(r["is_best_is_sr"].mean()))
           for lbl, r, n in (("T46_8", main, 8), ("T46_8_plus_3ref", aux, 11))}
    json.dump(pbo, open(OUT + "pbo.json", "w"), indent=1)
    S.to_csv(OUT + "summary.csv")
    # kiểm tái tạo thành phần với T42
    print("\n## Kiểm tái tạo (FULL):")
    for key, f in (("R_breakout10", "eval_breakout_pit.csv"), ("R_momentum5p", "eval_momentum_pit.csv")):
        old = pd.read_csv(T42 + f, index_col=0).loc["FULL"]
        new = res[key][0].loc["FULL"]
        print("  %s: CAGR %.4f vs T42 %.4f | Sharpe %.4f vs %.4f | lệnh %d vs %d | rand %.3f vs %.3f" % (
            key, new.cagr, old.cagr, new.sharpe, old.sharpe, new.n, old.n, new.rand_sharpe, old.rand_sharpe))
    print("  R_etf: CAGR %.4f Sharpe %.3f DD %.3f (T37 N=93: 7,4%%/0,56/-23%%)" % tuple(res["R_etf"][0].loc["FULL", ["cagr", "sharpe", "maxdd"]]))
    if P is not None:
        bad = causal_check(SLEEVES["nolu"], P, list(P.idx[P.idx >= "2018-01-01"][::120]))
        print("  causal L1: %d ngày vi phạm" % len(bad))
    pd.set_option("display.width", 250)
    print("\n## Tổng hợp FULL 2018-2023")
    print(S[["cagr", "sharpe", "maxdd", "n", "win", "pf", "exp_pct", "cagr_A", "cagr_B", "sharpe_A", "sharpe_B",
             "z", "rand_sharpe", "gate", "dsr"]].round(3).to_string())
    for k in S.index:
        print("  %s trượt: %s" % (k, S.loc[k, "gate_fail"]))
    print("\n## PBO:", json.dumps(pbo, indent=1))


def diag_nolu():
    """Chẩn đoán (không phải lần thử): lệnh breakout10 PIT của T42 (N=294) tách theo lợi suất phiên tín hiệu >= 6,5%."""
    tr = pd.read_csv(T42 + "trades_breakout_pit.csv", parse_dates=["sig_buy"])
    P = Panel(sorted(tr.sym.unique()), start="2016-01-01")
    ret = []
    for s, d in zip(tr.sym, tr.sig_buy):
        c = P.close[s].dropna()
        k = c.index.get_loc(d)
        ret.append(c.iloc[k] / c.iloc[k - 1] - 1)
    tr["jump"] = np.array(ret) >= 0.065
    tr["half"] = np.where(tr.sig_buy < "2021-01-01", "A", "B")
    g = tr.groupby(["half", "jump"]).agg(n=("net_ret", "size"), exp_pct=("net_ret", "mean"),
                                         win=("net", lambda x: (x > 0).mean()), net_sum=("net", "sum"))
    g.to_csv(OUT + "diag_nolu.csv")
    print("\n## Chẩn đoán lọc trần trên lệnh T42 breakout10 PIT (jump = lợi suất phiên tín hiệu >= 6,5%)")
    print(g.round(4).to_string())


if __name__ == "__main__":
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    if sys.argv[1] == "smoke":
        final(2, smoke=True)
    elif sys.argv[1] == "final":
        final(int(sys.argv[2]) if len(sys.argv) > 2 else 50)
    elif sys.argv[1] == "diag":
        diag_nolu()
    print("(%.0fs)" % (time.time() - t0))
