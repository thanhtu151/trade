"""T330: thuật toán tiến hoá (GA) chọn mã vs hệ thống hiện tại (Stage1 + VN30>MA10 tháng).
CHỈ dữ liệu <= 2023-12-31. Đăng ký trước: research/trial_log.md mục T330.
  ../.venv/bin/python research/t330/run_t330.py speed   # đo tốc độ 1 lần chạy
  ../.venv/bin/python research/t330/run_t330.py run     # GA walk-forward 10 cửa sổ + baseline + báo cáo số
"""
import json
import os
import sys
import time
from multiprocessing import get_context
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel
from engine.core import run, metrics, Strategy, RandomEntry
from engine.strategies import rsi, Stage1, TrendETF, BuyHold, per_symbol
from engine.bench import protocol_config, causal_check, deflated_sharpe

OUT = "/Users/drone/Downloads/tradeclone/research/t330/"
RES = "/Users/drone/Downloads/tradeclone/research/"
UNIVERSE = ("VCB BID CTG TCB MBB ACB VPB STB HDB VIB VND HCM VCI SHS VIC VHM VRE NVL KDH DXG FRT VNM "
            "GAS POW PVD PVS DPM DCM GMD VSC PC1").split()     # paper_ai_fund_config.json last_universe
EXTRA = ["VN30", "E1VFVN30"]
CFG = dict(fee=0.0015, tax=0.001, slip=0.00075, settle=3, max_pos=5, pos_pct=0.20, max_new=3,
           limits=True, delist_haircut=0.30)
N_TOTAL = 99 + 2
POP, GENS, TOUR, ELITE, PX, PM, MIN_TR = 50, 30, 3, 2, 0.8, 0.15, 30

# không gian gen: tên -> các giá trị
GENES = dict(
    use_mom=[0, 1], mom_len=[5, 10, 20, 60, 120], mom_thr=[-0.10, -0.05, 0.0, 0.02, 0.05, 0.10, 0.20],
    use_ma=[0, 1], ma_len=[10, 20, 50, 100, 200],
    use_rsi=[0, 1], rsi_len=[7, 14, 21], rsi_lo=[0, 20, 30, 40, 50], rsi_hi=[50, 60, 70, 80, 100],
    use_vol=[0, 1], vol_thr=[0.8, 1.0, 1.2, 1.5, 2.0, 3.0],
    min_score=[1, 2, 3, 4],
    stop=[0.03, 0.05, 0.07, 0.10, 0.15, None], tp=[0.05, 0.10, 0.15, 0.20, 0.30, None],
    max_hold=[5, 10, 15, 20, 40, 60], vn30=[0, 1])
NAMES = list(GENES)


def windows():
    """10 cửa sổ test 6 tháng 2019H1..2023H2, train = 3 năm liền trước."""
    out = []
    for y in range(2019, 2024):
        for h in (1, 2):
            ts = pd.Timestamp(y, 1 if h == 1 else 7, 1)
            te = pd.Timestamp(y, 6, 30) if h == 1 else pd.Timestamp(y, 12, 31)
            out.append(("%dH%d" % (y, h), str((ts - pd.DateOffset(years=3)).date()),
                        str((ts - pd.Timedelta(days=1)).date()), str(ts.date()), str(te.date())))
    return out


def cfg():
    return protocol_config(**CFG)


def build_panel():
    return Panel(UNIVERSE + EXTRA, start="2015-01-01")


def indicators(P):
    """Chỉ báo nhân quả cho mọi biến thể gen, tính trên phiên có giao dịch của từng mã."""
    stocks = [s for s in P.symbols if s in UNIVERSE]

    def fn(d):
        c, v = d.close, d.volume
        out = [c.rolling(200).mean().notna().astype(float)]
        out += [c / c.shift(L) - 1 for L in GENES["mom_len"]]
        out += [(c > c.rolling(L).mean()).astype(float) for L in GENES["ma_len"]]
        out += [rsi(c, n) for n in GENES["rsi_len"]]
        out += [v / (v.rolling(20).mean() + 1e-9)]
        return out
    sub = object.__new__(Panel)
    sub.symbols, sub.idx = stocks, P.idx
    for f in ("open", "high", "low", "close", "volume"):
        setattr(sub, f, getattr(P, f)[stocks])
    res = per_symbol(sub, fn)
    cols = list(P.symbols)
    res = [r.reindex(columns=cols).values for r in res]
    i = 0
    ind = dict(valid=np.nan_to_num(res[i]) > 0)
    i += 1
    for L in GENES["mom_len"]:
        ind["mom%d" % L] = res[i]; i += 1
    for L in GENES["ma_len"]:
        ind["ma%d" % L] = res[i]; i += 1
    for n in GENES["rsi_len"]:
        ind["rsi%d" % n] = res[i]; i += 1
    ind["vr"] = res[i]
    vn = TrendETF(etf="E1VFVN30", signal="VN30", months=10)
    vn.prepare(P)
    ind["vn30_on"] = vn.want
    return ind


class GAStrat(Strategy):
    """Cá thể GA: điểm = số điều kiện bật thỏa; mua khi điểm >= min_score (+ bộ lọc VN30 nếu bật)."""

    def __init__(self, g, ind=None):
        self.g, self.ind = dict(g), ind
        self.name, self.n_params = "ga", 1 + sum(1 for k in ("use_mom", "use_ma", "use_rsi", "use_vol")
                                                if g[k]) * 2 + 4

    def n_used(self):
        return sum(self.g[k] for k in ("use_mom", "use_ma", "use_rsi", "use_vol"))

    def prepare(self, panel):
        ind = self.ind if self.ind is not None else indicators(panel)
        g = self.g
        valid = ind["valid"].copy()
        mom = ind["mom%d" % g["mom_len"]]
        score = np.zeros(valid.shape)
        with np.errstate(invalid="ignore"):
            if g["use_mom"]:
                score += np.nan_to_num(mom) > g["mom_thr"]
            if g["use_ma"]:
                score += np.nan_to_num(ind["ma%d" % g["ma_len"]]) > 0
            if g["use_rsi"]:
                lo, hi = sorted((g["rsi_lo"], g["rsi_hi"]))
                r = ind["rsi%d" % g["rsi_len"]]
                score += (r >= lo) & (r <= hi)
            if g["use_vol"]:
                score += np.nan_to_num(ind["vr"]) >= g["vol_thr"]
        valid &= ~np.isnan(mom)
        need = min(g["min_score"], self.n_used())
        self.ok = valid & (score >= need) if need > 0 else np.zeros_like(valid)
        self.valid, self.score, self.prio = valid, score, np.nan_to_num(mom, nan=-9)
        self.on = ind["vn30_on"] if g["vn30"] else np.ones(len(valid), bool)
        self.cols = list(panel.symbols)
        sig = np.where(valid, score * self.ok, np.nan)
        return pd.DataFrame(sig, index=panel.idx, columns=self.cols)

    def eligible(self, k, d):
        return [self.cols[j] for j in np.nonzero(self.valid[k])[0]]

    def meta(self, k, s):
        return {}

    def candidates(self, k, d, held):
        if not self.on[k]:
            return []
        js = np.nonzero(self.ok[k])[0]
        if not len(js):
            return []
        js = js[np.lexsort((-self.prio[k, js], -self.score[k, js]))]
        return [(self.cols[j], {}) for j in js if self.cols[j] not in held]

    def levels(self, sym, fill_px, meta):
        st, tp = self.g["stop"], self.g["tp"]
        return (fill_px * (1 - st) if st else None), (fill_px * (1 + tp) if tp else None)

    def exit_signal(self, k, d, sym, pos):
        return "timeout" if k - pos["k"] >= self.g["max_hold"] else None


class Stage1Prod(Stage1):
    """Phần tái lập được của hệ thống hiện tại: Stage1 score>=3 + VN30>=MA10 tháng,
    stop max(px-1ATR, px*0.95), target min(px+2ATR, px*1.15), giữ tối đa 15 phiên."""

    def __init__(self):
        super().__init__(thr=3.0, m_stop=1.0, m_tgt=2.0, max_hold=15)
        self.name = "stage1_prod+vn30"

    def prepare(self, panel):
        S = super().prepare(panel)
        drop = [c for c in S.columns if c not in UNIVERSE]
        self.S[drop] = np.nan
        vn = TrendETF(etf="E1VFVN30", signal="VN30", months=10)
        vn.prepare(panel)
        self.on = vn.want
        return self.S

    def candidates(self, k, d, held):
        return super().candidates(k, d, held) if self.on[k] else []

    def levels(self, sym, fill_px, meta):
        a = meta["atr"]
        return max(fill_px - self.m_stop * a, fill_px * 0.95), min(fill_px + self.m_tgt * a, fill_px * 1.15)


def m_of(nav, tr):
    m = metrics(nav, tr)
    m["ret"] = nav.iloc[-1] / nav.iloc[0] - 1
    return m


# ---------------------------------------------------------------- GA (mỗi cửa sổ 1 tiến trình)
_P = _IND = None


def _init():
    global _P, _IND
    _P = build_panel()
    _IND = indicators(_P)


def rand_genome(rng):
    return tuple(int(rng.integers(len(GENES[n]))) for n in NAMES)


def decode(gi):
    return {n: GENES[n][i] for n, i in zip(NAMES, gi)}


def fitness(gi, a, b, cache):
    if gi in cache:
        return cache[gi]
    st = GAStrat(decode(gi), _IND)
    if st.n_used() == 0:
        cache[gi] = (-5.0, 0.0, 0.0, 0)
        return cache[gi]
    nav, tr, _ = run(_P, st, a, b, cfg())
    m = m_of(nav, tr)
    f = m["sharpe"] + 0.5 * m["maxdd"] if m["n"] >= MIN_TR else -5.0
    cache[gi] = (f, m["sharpe"], m["maxdd"], m["n"])
    return cache[gi]


def evolve(w):
    nm, a, b, ta, tb = w
    wi = [x[0] for x in windows()].index(nm)
    rng = np.random.default_rng(wi)
    cache, hist = {}, []
    pop = [rand_genome(rng) for _ in range(POP)]
    t0 = time.time()
    for gen in range(GENS):
        fit = [fitness(g, a, b, cache)[0] for g in pop]
        order = np.argsort(fit)[::-1]
        hist.append(dict(gen=gen, best=float(fit[order[0]]), median=float(np.median(fit))))
        if gen == GENS - 1:
            break
        new = [pop[i] for i in order[:ELITE]]

        def pick():
            c = rng.choice(POP, TOUR, replace=False)
            return pop[max(c, key=lambda i: fit[i])]
        while len(new) < POP:
            p1, p2 = pick(), pick()
            child = [x if rng.random() < 0.5 else y for x, y in zip(p1, p2)] if rng.random() < PX else list(p1)
            for j, n in enumerate(NAMES):
                if rng.random() < PM:
                    child[j] = int(rng.integers(len(GENES[n])))
            new.append(tuple(child))
        pop = new
    best = pop[int(np.argmax(fit))]
    # 50 gen ngẫu nhiên (không tiến hoá) để so OOS
    rgen = np.random.default_rng(1000 + wi)
    rand_g = [rand_genome(rgen) for _ in range(50)]
    return dict(window=nm, best=best, train=cache[best], hist=hist, n_evals=len(cache),
                rand_genomes=rand_g, secs=time.time() - t0)


def oos_eval(P, IND, w, res):
    nm, a, b, ta, tb = w
    C = cfg()
    out = dict(window=nm, train=(a, b), test=(ta, tb), genome=decode(res["best"]),
               train_fit=res["train"][0], train_sharpe=res["train"][1], train_maxdd=res["train"][2],
               train_n=res["train"][3], n_evals=res["n_evals"])
    st = GAStrat(decode(res["best"]), IND)
    nav, tr, ex = run(P, st, ta, tb, C)
    out["ga"] = m_of(nav, tr)
    out["ga_blocked"] = ex
    navs = dict(ga=nav)
    trs = dict(ga=tr)
    rs = [m_of(*run(P, RandomEntry(st, s), ta, tb, C, prepared=True)[:2])["sharpe"] for s in range(50)]
    out["rand_entry_sharpe_mean"], out["rand_entry_sharpe_sd"] = float(np.mean(rs)), float(np.std(rs, ddof=1))
    out["z_rand_entry"] = (out["ga"]["sharpe"] - np.mean(rs)) / np.std(rs, ddof=1) if np.std(rs) > 0 else np.nan
    rg = []
    for gi in res["rand_genomes"]:
        s2 = GAStrat(decode(gi), IND)
        rg.append(m_of(*run(P, s2, ta, tb, C)[:2])["sharpe"] if s2.n_used() else 0.0)
    out["rand_genome_sharpe_median"] = float(np.median(rg))
    out["ga_pct_vs_rand_genome"] = float(np.mean(np.array(rg) < out["ga"]["sharpe"]))
    for k, s in (("stage1", Stage1Prod()), ("e1_bh", BuyHold("E1VFVN30")),
                 ("etf_core", TrendETF(etf="E1VFVN30", signal="VN30", months=10))):
        n2, t2, _ = run(P, s, ta, tb, C)
        out[k], navs[k], trs[k] = m_of(n2, t2), n2, t2
    c = P.close["VN30"].loc[ta:tb].dropna()
    navs["vn30_raw"] = c
    out["vn30_raw"] = m_of(c, pd.DataFrame())
    return out, navs, trs


def stitch(navs_list, key):
    r = pd.concat([n[key].pct_change().dropna() for n in navs_list])
    return (1 + r).cumprod()


def main(mode):
    W = windows()
    if mode == "speed":
        _init()
        g = dict(use_mom=1, mom_len=20, mom_thr=0.02, use_ma=1, ma_len=50, use_rsi=0, rsi_len=14, rsi_lo=30,
                 rsi_hi=70, use_vol=1, vol_thr=1.2, min_score=2, stop=0.07, tp=0.15, max_hold=20, vn30=1)
        t0 = time.time()
        nav, tr, ex = run(_P, GAStrat(g, _IND), W[0][1], W[0][2], cfg())
        print("1 lần chạy train 3 năm: %.2fs" % (time.time() - t0), m_of(nav, tr), ex)
        t0 = time.time()
        bad = causal_check(lambda: GAStrat(g), _P, pd.to_datetime(["2019-03-29", "2020-06-30", "2021-11-30",
                                                                    "2023-06-30"]))
        print("causal_check vi phạm:", bad, "%.1fs" % (time.time() - t0))
        return
    lock = OUT + "runs.json"
    if mode == "summary":       # GA đã chạy 1 lần (evolution.json); chỉ chấm lại OOS các vô địch đã đóng băng
        t0 = time.time()
        evo = [dict(e, best=tuple(e["best"]), rand_genomes=[tuple(x) for x in e["rand_genomes"]],
                    train=tuple(e["train"])) for e in json.load(open(OUT + "evolution.json"))]
        return report(W, evo, t0)
    if os.path.exists(lock):
        raise SystemExit("runs.json đã khóa — T330 chỉ chạy 1 lần")
    json.dump(dict(task="T330", date="2026-10-08", windows=W, genes={k: [str(x) for x in v] for k, v in GENES.items()},
                   ga=dict(pop=POP, gens=GENS, tour=TOUR, elite=ELITE, px=PX, pm=PM, min_trades=MIN_TR,
                           fitness="sharpe+0.5*maxdd"), cfg=CFG, universe=UNIVERSE, n_total=N_TOTAL),
              open(lock, "w"), indent=1)
    t0 = time.time()
    with get_context("spawn").Pool(10, initializer=_init) as pool:
        evo = pool.map(evolve, W)
    print("GA xong %.0fs" % (time.time() - t0), flush=True)
    json.dump([dict(e, best=list(e["best"]), rand_genomes=[list(x) for x in e["rand_genomes"]]) for e in evo],
              open(OUT + "evolution.json", "w"), indent=1, default=float)
    report(W, evo, t0)


def report(W, evo, t0):
    P = build_panel()
    IND = indicators(P)
    # chống look-ahead cho các vô địch
    dates = pd.to_datetime(["2019-03-29", "2020-06-30", "2021-11-30", "2023-06-30"])
    causal = {e["window"]: [str(x) for x in causal_check(lambda g=decode(e["best"]): GAStrat(g), P, dates)]
              for e in evo}
    causal["stage1_prod"] = [str(x) for x in causal_check(Stage1Prod, P, dates)]
    json.dump(causal, open(OUT + "causal.json", "w"), indent=1)
    rows, navs_all, trs_all = [], [], []
    for w, e in zip(W, evo):
        o, nv, tr = oos_eval(P, IND, w, e)
        rows.append(o); navs_all.append(nv); trs_all.append(tr)
        print(w[0], "GA S=%.2f DD=%.1f%% | stage1 S=%.2f DD=%.1f%%" % (
            o["ga"]["sharpe"], 100 * o["ga"]["maxdd"], o["stage1"]["sharpe"], 100 * o["stage1"]["maxdd"]), flush=True)
    json.dump(rows, open(OUT + "oos_windows.json", "w"), indent=1, default=lambda x: None if x is None else float(x))
    pd.concat([t["ga"].assign(window=r["window"]) for t, r in zip(trs_all, rows) if len(t["ga"])]).to_csv(
        OUT + "trades_ga.csv", index=False)
    # bảng cửa sổ
    tab = []
    for o in rows:
        d = dict(window=o["window"])
        for k in ("ga", "stage1", "e1_bh", "etf_core", "vn30_raw"):
            d[k + "_sharpe"], d[k + "_maxdd"], d[k + "_ret"] = o[k]["sharpe"], o[k]["maxdd"], o[k]["ret"]
            d[k + "_n"] = o[k]["n"]
        d["ga_exp"] = o["ga"]["exp_pct"]
        d["stage1_exp"] = o["stage1"]["exp_pct"]
        d["train_sharpe"], d["train_maxdd"], d["train_n"] = o["train_sharpe"], o["train_maxdd"], o["train_n"]
        d["z_rand_entry"], d["rand_genome_med"], d["ga_pct_rand_genome"] = (
            o["z_rand_entry"], o["rand_genome_sharpe_median"], o["ga_pct_vs_rand_genome"])
        d["win_sharpe"] = o["ga"]["sharpe"] > o["stage1"]["sharpe"]
        d["win_dd"] = o["ga"]["maxdd"] > o["stage1"]["maxdd"]
        d["win_both"] = d["win_sharpe"] and d["win_dd"]
        d["win_both_vs_e1"] = o["ga"]["sharpe"] > o["e1_bh"]["sharpe"] and o["ga"]["maxdd"] > o["e1_bh"]["maxdd"]
        tab.append(d)
    T = pd.DataFrame(tab).set_index("window")
    T.to_csv(OUT + "oos_windows.csv")
    # đường OOS nối 2019-2023 và 2 nửa
    summ = {}
    for k in ("ga", "stage1", "e1_bh", "etf_core", "vn30_raw"):
        nav = stitch(navs_all, k)
        for nm, a, b in (("A_2019_20", "2019-01-01", "2020-12-31"), ("B_2021_23", "2021-01-01", "2023-12-31"),
                         ("OOS_2019_23", "2019-01-01", "2023-12-31")):
            n = nav.loc[a:b]
            n = n / n.iloc[0]
            parts = [t[k] for t, r in zip(trs_all, rows) if r["test"][0] >= a and r["test"][1] <= b
                     and k in t and len(t[k])]
            trs = pd.concat(parts) if parts else pd.DataFrame()
            m = m_of(n, trs)
            summ["%s|%s" % (k, nm)] = {x: (None if (isinstance(v, float) and np.isnan(v)) else float(v))
                                       for x, v in m.items()}
        if k == "ga":
            ga_nav = nav
        if k == "stage1":
            s1_nav = nav
    # DSR (lạc quan: N = 101)
    old = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(RES + "t38/" + f)).values() if v
           for s in v["grid_sharpes_A"]]
    prev = sum((pd.read_csv(RES + t + "/summary.csv").sharpe_FULL.dropna().tolist()[:6]
                for t in ("t47", "t51", "t54")), [])
    new = [summ["ga|OOS_2019_23"]["sharpe"], summ["stage1|OOS_2019_23"]["sharpe"]]
    allsr = old + prev + new
    sr_list = old if np.var(old, ddof=1) >= np.var(allsr, ddof=1) else allsr
    dsr = {k: deflated_sharpe(n, N_TOTAL, sr_list) for k, n in (("ga", ga_nav), ("stage1", s1_nav))}
    summ["dsr"] = {k: dict(dsr=float(v[0]), sr0=float(v[1])) for k, v in dsr.items()}
    summ["windows_win_both"] = int(T.win_both.sum())
    summ["windows_win_sharpe"] = int(T.win_sharpe.sum())
    summ["windows_win_dd"] = int(T.win_dd.sum())
    summ["windows_win_both_vs_e1"] = int(T.win_both_vs_e1.sum())
    summ["train_test_gap_mean"] = float((T.train_sharpe - T.ga_sharpe).mean())
    summ["n_evals_total"] = int(sum(e["n_evals"] for e in evo))
    json.dump(summ, open(OUT + "summary.json", "w"), indent=1)
    pd.set_option("display.width", 250)
    print(T[["train_sharpe", "ga_sharpe", "ga_maxdd", "ga_n", "stage1_sharpe", "stage1_maxdd", "stage1_n",
             "e1_bh_sharpe", "e1_bh_maxdd", "z_rand_entry", "ga_pct_rand_genome", "win_both"]].round(2))
    print(json.dumps(summ, indent=1, default=str)[:3000])
    print("tổng %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "speed")
