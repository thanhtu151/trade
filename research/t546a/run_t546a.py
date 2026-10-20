"""T547 — Win-rate v2 Phần A: meta-labeling LightGBM trên sự kiện S1 breakout10 / S2 momentum5p (PIT).
Theo research/PROTOCOL_v2.md mục 2 + đăng ký T547 trong trial_log.md. CHỈ dữ liệu <= 2023-12-31.
  .venv/bin/python t546a/run_t546a.py dry   # chạy thử: S1, cửa sổ 1, không tính chỉ số OOS
  .venv/bin/python t546a/run_t546a.py run   # chạy đủ A1-A5 đúng 1 lần (khóa t546a/runs.json trước khi tính)
  .venv/bin/python t546a/run_t546a.py score # chỉ chấm lại từ file đã lưu (không train/chạy lại)"""
import json
import os
import sys
import time
RES = "/Users/drone/Downloads/tradeclone/research/"
sys.path.insert(0, RES)
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from engine.data import Panel, universe_all, pit_universe, END
from engine.core import run, metrics, Strategy
from engine.candidates import Breakout, Momentum5, atr14, NON_TRADABLE
from engine.strategies import per_symbol, rsi
from engine.bench import protocol_config, causal_check, deflated_sharpe, gate_a, index_bench, evaluate, zgap
from engine.portfolio import cscv_pbo

OUT = RES + "t546a/"
FOREIGN = RES + "data/foreign_vnd/foreign_2018_2023.csv"
CFG = protocol_config(limits=True, max_pos=10, pos_pct=0.10, max_new=3, delist_haircut=0.30)
TRAIN_START = "2018-01-01"
WINDOWS = [("2021-01-01", "2021-12-31"), ("2022-01-01", "2022-12-31"), ("2023-01-01", "2023-12-31")]
OOS = ("2021-01-01", "2023-12-31")
OOS_PERIODS = [("A", "2021-01-01", "2022-06-30"), ("B", "2022-07-01", "2023-12-31"), ("FULL",) + OOS]
EMB, KFOLD, SEEDS, BOOT = 5, 5, 50, 10000
LGB = dict(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=50, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=546, verbose=-1, n_jobs=4)
BASE_FEATS = ["vn30_ma10m", "breadth50", "atr_pct", "log_tv20", "tv5_60", "c_ma20", "c_ma200", "rsi14", "ret1", "sig"]
FLOW_FEATS = ["fnet5", "fnet20"]
N_PREV = 103
# cấu hình: (tín hiệu, meta?, đặc trưng khối ngoại?)
CONFIGS = {"A1": ("S1", False, False), "A2": ("S2", False, False), "A3": ("S1", True, False), "A4": ("S2", True, False)}
SIG_PARAMS = {"S1": 4, "S2": 5}


def make_sig(name):
    return {"S1": lambda: Breakout(n=50, m_stop=2.0).with_pit(),
            "S2": lambda: Momentum5(lookback=60, m_stop=2.0).with_pit()}[name]


def load_foreign():
    f = pd.read_csv(FOREIGN, usecols=["code", "date", "netVal"], parse_dates=["date"])
    assert f.date.max() <= pd.Timestamp(END), "dữ liệu khối ngoại vượt holdout"
    return f.pivot_table(index="date", columns="code", values="netVal", aggfunc="last")


class Feats:
    """Đặc trưng tại đóng cửa t (chỉ dữ liệu <= t). prepare() trả bảng rộng (ngày x (đặc trưng, mã)) cho causal_check."""

    def __init__(self, sig, flow=None):
        self.sig, self.flow = sig, flow

    def prepare(self, panel):
        syms = [s for s in panel.symbols if s not in NON_TRADABLE]
        sub = object.__new__(type(panel))
        sub.__dict__.update(panel.__dict__)
        sub.symbols = syms
        L = 50 if self.sig == "S1" else 60

        def fn(d):
            c = d.close
            sig = c / c.shift(50) - 1 if self.sig == "S1" else c.shift(5) / c.shift(L) - 1
            return (atr14(d) / c, c / c.rolling(20).mean() - 1, c / c.rolling(200).mean() - 1, rsi(c, 14),
                    c / c.shift(1) - 1, sig, (c > c.rolling(50).mean()).astype(float).where(c.rolling(50).mean().notna()))
        atr_pct, c20, c200, r14, ret1, sig, above = per_symbol(sub, fn)
        C, V = panel.close[syms], panel.volume[syms]
        tv = (C * V).fillna(0)
        tv20, tv5, tv60 = tv.rolling(20).mean(), tv.rolling(5).mean(), tv.rolling(60).mean()
        U = pit_universe(panel, syms)
        ab = above.ffill().reindex(columns=syms)
        breadth = ((ab == 1) & U).sum(axis=1) / U.sum(axis=1).replace(0, np.nan)
        c = panel.close["VN30"].dropna()
        per = c.index.to_period("M")
        me = c.groupby(per).last()
        r = me / me.rolling(10).mean() - 1
        vn = pd.Series((per - 1).map(r), index=c.index).astype(float).reindex(panel.idx).ffill()
        F = dict(vn30_ma10m=pd.DataFrame(np.repeat(vn.values[:, None], len(syms), 1), index=panel.idx, columns=syms),
                 breadth50=pd.DataFrame(np.repeat(breadth.values[:, None], len(syms), 1), index=panel.idx, columns=syms),
                 atr_pct=atr_pct, log_tv20=np.log1p(tv20 * 1000), tv5_60=tv5 / tv60.replace(0, np.nan),
                 c_ma20=c20, c_ma200=c200, rsi14=r14, ret1=ret1, sig=sig)
        if self.flow is not None:
            nv = self.flow.reindex(index=panel.idx, columns=syms)          # dòng ngày không có trong lịch giá bị bỏ
            tvv = tv * 1000
            for w in (5, 20):
                F["fnet%d" % w] = nv.rolling(w, min_periods=w).sum() / tvv.rolling(w).sum().replace(0, np.nan)
        self.F, self.syms = F, syms
        return pd.concat({k: v.reindex(columns=syms) for k, v in F.items()}, axis=1)

    def matrix(self, ks, ss):
        col = {s: i for i, s in enumerate(self.syms)}
        j = np.array([col[s] for s in ss])
        names = BASE_FEATS + (FLOW_FEATS if self.flow is not None else [])
        return np.column_stack([self.F[n].reindex(columns=self.syms).values[ks, j] for n in names])


def raw_signals(st, P):
    """Mọi (k, mã) mà tín hiệu gốc muốn mua ở mở cửa k+1 (cổng VN30 bật, trong PIT, ATR>0, đúng thứ tự candidates)."""
    out = []
    for k in range(len(P.idx) - 1):
        for s, _ in st.candidates(k, P.idx[k], set()):
            out.append((k, s))
    return pd.DataFrame(out, columns=["k", "sym"])


class Sim:
    """Mô phỏng ĐỘC LẬP 1 lệnh cho mỗi sự kiện, đúng luật engine.core.run (protocol_config, limits, haircut 30%)."""

    def __init__(self, P, st, cfg=CFG):
        self.P, self.st, self.cfg = P, st, cfg
        self.col = {s: i for i, s in enumerate(P.symbols)}
        self.O, self.H, self.L, self.C = (getattr(P, f).values for f in ("open", "high", "low", "close_ff"))
        ref = P.close_ff.shift(1).values
        lim = P.limit.reindex(P.symbols).fillna(0.07).values
        self.CEIL, self.FLOOR = ref * (1 + lim - cfg.lim_tol), ref * (1 - lim + cfg.lim_tol)
        has = P.close.notna().values
        n = len(P.idx)
        self.lastk = np.where(has.any(0), n - 1 - np.argmax(has[::-1], axis=0), -1)
        self.dead = (self.lastk >= 0) & (self.lastk < n - 1 - cfg.delist_gap)

    def one(self, k0, s):
        c, P, st = self.cfg, self.P, self.st
        j, n = self.col[s], len(P.idx)
        kb = k0 + 1
        o = self.O[kb, j]
        if np.isnan(o):
            return dict(status="nofill")
        if o >= self.CEIL[kb, j]:
            return dict(status="ceiling")
        px = o * (1 + c.slip)
        atr = st.A.iat[k0, st.col[s]]
        stop = px - st.m_stop * atr
        cost = px * (1 + c.fee)
        pos = dict(k=kb, meta=dict(atr=atr), stop=stop)
        sell_c = 1 - c.fee - c.tax
        pend = None

        def done(k, sx, why, open_end=False):
            return dict(status="ok", k_exit=k, ret=sx * sell_c / cost - 1, why=why, open_end=open_end)
        for k in range(kb, n):
            if k > kb and pend is not None:
                o = self.O[k, j]
                if not np.isnan(o) and not self.H[k, j] <= self.FLOOR[k, j]:
                    return done(k, o * (1 - c.slip), pend)
            if pend is None and k - kb >= c.settle:
                o, l = self.O[k, j], self.L[k, j]
                if not (np.isnan(o) or np.isnan(l)):
                    if self.H[k, j] <= self.FLOOR[k, j]:
                        if l <= stop:
                            pend = "stop_locked"
                    elif o <= stop:
                        return done(k, o * (1 - c.slip), "stop_gap")
                    elif l <= stop:
                        return done(k, stop * (1 - c.slip), "stop")
            if self.dead[j] and k >= self.lastk[j]:
                return done(k, self.C[k, j] * (1 - c.delist_haircut), "delist")
            if k == n - 1:
                return done(k, self.C[k, j] * (1 - c.slip), "open_end", True)
            if pend is None and k + 1 - kb >= c.settle:
                why = st.exit_signal(k, P.idx[k], s, pos)
                if why:
                    pend = why
        raise RuntimeError("không tới được")


def build_events(P, sig):
    st = make_sig(sig)()
    st.prepare(P)
    raw = raw_signals(st, P)
    sim = Sim(P, st)
    busy, rows = {}, []
    for k, s in raw.itertuples(index=False):
        if k < busy.get(s, -1):
            continue                                   # sự kiện trước của mã chưa đóng -> không đếm trùng
        r = sim.one(k, s)
        r.update(k=k, sym=s, t=P.idx[k])
        if r["status"] == "ok":
            busy[s] = r["k_exit"]
            r["t_exit"] = P.idx[r["k_exit"]]
        rows.append(r)
    ev = pd.DataFrame(rows)
    return st, raw, ev


def purged_oof(k, ke, X, y):
    """OOF purged K-fold, 5 nhóm liên tiếp theo ngày; bỏ train chồng [min t, max t_exit] của nhóm + embargo 5 phiên."""
    n = len(k)
    g0 = np.minimum(np.arange(n) * KFOLD // n, KFOLD - 1)
    first = pd.Series(g0).groupby(k).transform("first").values        # cùng ngày -> cùng nhóm
    p = np.full(n, np.nan)
    for g in range(KFOLD):
        te = first == g
        if not te.any():
            continue
        a, b, e = k[te].min(), k[te].max(), ke[te].max()
        tr = ~te & ~((k <= e) & (ke >= a)) & ~((k > b) & (k <= b + EMB))
        m = lgb.LGBMClassifier(**LGB).fit(X[tr], y[tr])
        p[te] = m.predict_proba(X[te])[:, 1]
    return p


def walk_forward(P, ev, raw, fe, windows):
    """Trả (dự báo cho sự kiện test, dự báo cho tín hiệu thô test, thông tin từng cửa sổ)."""
    ok = ev[ev.status == "ok"].sort_values(["k", "sym"]).reset_index(drop=True)
    X = fe.matrix(ok.k.values, ok.sym.values)
    y = (ok.ret.values > 0).astype(int)
    Xr = fe.matrix(raw.k.values, raw.sym.values)
    ev_p, raw_p, info = [], [], []
    k0 = P.idx.searchsorted(pd.Timestamp(TRAIN_START))
    for a, b in windows:
        ka, kz = P.idx.searchsorted(pd.Timestamp(a)), P.idx.searchsorted(pd.Timestamp(b), side="right") - 1
        tr = ((ok.k >= k0) & (ok.k < ka - EMB) & (ok.k_exit < ka)).values
        oof = purged_oof(ok.k.values[tr], ok.k_exit.values[tr], X[tr], y[tr])
        tau = float(np.median(oof))
        m = lgb.LGBMClassifier(**LGB).fit(X[tr], y[tr])
        te = ((ok.k >= ka) & (ok.k <= kz)).values
        pe = ok[te].assign(p=m.predict_proba(X[te])[:, 1], tau=tau, window=a[:4])
        rt = ((raw.k >= ka) & (raw.k <= kz)).values
        pr = raw[rt].assign(p=m.predict_proba(Xr[rt])[:, 1], tau=tau, window=a[:4])
        info.append(dict(window=a[:4], n_train=int(tr.sum()), train_pos=float(y[tr].mean()),
                         train_nan_rate=float(np.isnan(X[tr]).mean()), oof_auc=float(roc_auc_score(y[tr], oof)),
                         tau=tau, n_test=int(te.sum()), n_keep=int((pe.p >= tau).sum()), n_raw_test=int(rt.sum()),
                         n_raw_keep=int((pr.p >= tau).sum())))
        ev_p.append(pe)
        raw_p.append(pr)
    return pd.concat(ev_p), pd.concat(raw_p), info


class Filtered(Strategy):
    """Tín hiệu gốc, chỉ nhận ứng viên (k, mã) có p >= τ của cửa sổ (mô hình đóng băng trước cửa sổ)."""

    def __init__(self, base, keep, n_params, name):
        self.base, self.keep, self.n_params, self.name = base, keep, n_params, name

    def prepare(self, panel):
        return self.base.prepare(panel)

    def eligible(self, k, d):
        return self.base.eligible(k, d)

    def meta(self, k, s):
        return self.base.meta(k, s)

    def candidates(self, k, d, held):
        return [(s, m) for s, m in self.base.candidates(k, d, held) if (k, s) in self.keep]

    def levels(self, sym, fill_px, meta):
        return self.base.levels(sym, fill_px, meta)

    def exit_signal(self, k, d, sym, pos):
        return self.base.exit_signal(k, d, sym, pos)


def wilson(x, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = x / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def check_sim(P, st, ev, a="2018-01-01", b="2020-12-31"):
    """Đối chiếu bộ mô phỏng sự kiện với engine.core.run (danh mục) trên giai đoạn train 2018-2020."""
    _, tr, _ = run(P, st, a, b, CFG)
    tr = tr.assign(k=P.idx.get_indexer(tr.sig_buy))
    m = tr.merge(ev[ev.status == "ok"], on=["k", "sym"], how="inner", suffixes=("_pf", "_ev"))
    m = m[m.why_pf != "open_end"]
    same_exit = (P.idx.get_indexer(m.sell) == m.k_exit).mean()
    dret = (m.net_ret - m.ret).abs()
    return dict(pf_trades=len(tr), matched=len(m), same_exit=float(same_exit), max_abs_dret=float(dret.max()),
                n_dret_gt_1bp=int((dret > 1e-4).sum()))


def panel():
    return Panel(universe_all(True) + ["VN30"], start="2016-01-01")


def dry():
    t0 = time.time()
    P = panel()
    fe = Feats("S1")
    bad = causal_check(lambda: Feats("S1"), P, list(P.idx[P.idx >= "2018-01-01"][::150]))
    print("causal S1 vi phạm: %d / %d ngày (%.0fs)" % (len(bad), len(P.idx[P.idx >= "2018-01-01"][::150]), time.time() - t0), flush=True)
    st, raw, ev = build_events(P, "S1")
    print("S1: tín hiệu thô %d, sự kiện %d, trạng thái %s (%.0fs)" % (len(raw), len(ev), ev.status.value_counts().to_dict(), time.time() - t0), flush=True)
    print("đối chiếu engine 2018-2020:", check_sim(P, st, ev), flush=True)
    fe.prepare(P)
    _, _, info = walk_forward(P, ev, raw, fe, WINDOWS[:1])
    i = info[0]
    print("cửa sổ 1 (train 2018-20): n_train %d, tỉ lệ nhãn 1 %.3f, NaN đặc trưng %.4f, NaN nhãn %d, tau %.3f, OOF AUC %.3f" % (
        i["n_train"], i["train_pos"], i["train_nan_rate"], int(ev[ev.status == "ok"].ret.isna().sum()), i["tau"], i["oof_auc"]))
    print("test 2021 (chỉ đếm, không nhãn): sự kiện %d, giữ %d; tín hiệu thô %d, giữ %d" % (
        i["n_test"], i["n_keep"], i["n_raw_test"], i["n_raw_keep"]))
    # luật chọn tín hiệu cho A5 (đăng ký trước): nhiều sự kiện hơn trong 2018-09-01 -> 2020-12-31, CHỈ đếm
    cnt = {}
    for sig, e in (("S1", ev), ("S2", build_events(P, "S2")[2])):
        ok = e[e.status == "ok"]
        cnt[sig] = int(((ok.t >= "2018-09-01") & (ok.t <= "2020-12-31")).sum())
    print("đếm sự kiện 2018-09→2020 (chọn A5):", cnt, "->", max(cnt, key=cnt.get))
    json.dump(dict(causal_bad=len(bad), info=info, a5_count=cnt, a5_signal=max(cnt, key=cnt.get)),
              open(OUT + "dry.json", "w"), indent=1, default=str)
    print("(%.0fs)" % (time.time() - t0))


def fit_all():
    """Chạy đủ: sự kiện, nhãn, walk-forward A3/A4/A5 -> lưu file. Khóa runs.json TRƯỚC."""
    lock = OUT + "runs.json"
    a5sig = json.load(open(OUT + "dry.json"))["a5_signal"]
    cfgs = dict(CONFIGS, A5=(a5sig, True, True))
    done = json.load(open(lock)) if os.path.exists(lock) else {}
    if done:
        print("ĐÃ chạy:", done, "-> không chạy lại; dùng `score`")
        return
    ts = time.strftime("%Y-%m-%d %H:%M")
    json.dump({k: dict(time=ts, cfg=v) for k, v in cfgs.items()}, open(lock, "w"), indent=1)
    t0 = time.time()
    P = panel()
    flow = load_foreign()
    causal = {}
    for sig in ("S1", "S2"):
        dates = list(P.idx[P.idx >= "2018-01-01"][::150])
        causal[sig] = len(causal_check(lambda: Feats(sig), P, dates))
    causal["flow_" + a5sig] = len(causal_check(lambda: Feats(a5sig, flow), P, list(P.idx[P.idx >= "2018-09-01"][::150])))
    print("causal vi phạm:", causal, flush=True)
    if any(causal.values()):
        raise SystemExit("vi phạm nhân quả -> dừng")
    infos, simchk = {}, {}
    for sig in ("S1", "S2"):
        st, raw, ev = build_events(P, sig)
        simchk[sig] = check_sim(P, st, ev)
        ev.to_csv(OUT + "events_%s.csv" % sig, index=False)
        for name, (s2, meta, fl) in cfgs.items():
            if s2 != sig or not meta:
                continue
            fe = Feats(sig, flow if fl else None)
            fe.prepare(P)
            pe, pr, info = walk_forward(P, ev, raw, fe, WINDOWS)
            pe.to_csv(OUT + "pred_events_%s.csv" % name, index=False)
            pr.to_csv(OUT + "pred_raw_%s.csv" % name, index=False)
            infos[name] = info
            print(name, "xong (%.0fs)" % (time.time() - t0), info, flush=True)
    json.dump(dict(causal=causal, sim_check=simchk, windows=infos, a5_signal=a5sig),
              open(OUT + "fit.json", "w"), indent=1, default=str)


def score():
    t0 = time.time()
    rng = np.random.default_rng(546)
    fit = json.load(open(OUT + "fit.json"))
    a5sig = fit["a5_signal"]
    cfgs = dict(CONFIGS, A5=(a5sig, True, True))
    P = panel()
    ev = {s: pd.read_csv(OUT + "events_%s.csv" % s, parse_dates=["t", "t_exit"]) for s in ("S1", "S2")}
    rows, navs, Es = {}, {}, {}
    e1r = pd.read_csv(RES + "t47/returns_full.csv", index_col=0, parse_dates=True)["E1_ma10m"].loc[OOS[0]:OOS[1]]
    e1nav = (1 + e1r).cumprod()
    e1 = dict(cagr=float(e1nav.iloc[-1] ** (252 / len(e1r)) - 1), sharpe=float(e1r.mean() / e1r.std() * np.sqrt(252)),
              maxdd=float((e1nav / e1nav.cummax() - 1).min()))
    for name, (sig, meta, fl) in cfgs.items():
        e = ev[sig]
        base = e[(e.status == "ok") & (e.t >= OOS[0]) & (e.t <= OOS[1])].copy()
        base["window"] = base.t.dt.year.astype(str)
        if meta:
            pe = pd.read_csv(OUT + "pred_events_%s.csv" % name, dtype={"window": str})
            pe["keep"] = pe.p >= pe.tau
            kept = pe[pe.keep]
            pr = pd.read_csv(OUT + "pred_raw_%s.csv" % name)
            keepset = set(map(tuple, pr.loc[pr.p >= pr.tau, ["k", "sym"]].values.tolist()))
            # 50 seed lọc ngẫu nhiên, cùng số giữ từng cửa sổ
            rp, rm = [], []
            for sd in range(SEEDS):
                r = np.random.default_rng(sd)
                pick = np.concatenate([r.choice(g.ret.values, int(g.keep.sum()), replace=False) for _, g in pe.groupby("window")])
                rp.append((pick > 0).mean())
                rm.append(pick.mean())
            rp, rm = pd.Series(rp), pd.Series(rm)
        else:
            kept, keepset = base, None
        x = kept.ret.values
        n, w = len(x), int((x > 0).sum())
        bm = np.array([rng.choice(x, n).mean() for _ in range(BOOT)])
        mo = pd.to_datetime(kept.t).dt.to_period("M").values
        grp = pd.DataFrame(dict(m=mo, r=x)).groupby("m").r.agg(["sum", "count"])
        bi = rng.integers(0, len(grp), (BOOT, len(grp)))
        bmb = grp["sum"].values[bi].sum(1) / grp["count"].values[bi].sum(1)
        row = dict(config=name, signal=sig, meta=meta, flow=fl, n_oos_base=len(base), n=n,
                   keep_rate=n / len(base), prec_base=float((base.ret > 0).mean()), prec=w / n,
                   prec_ci=wilson(w, n), prec_base_ci=wilson(int((base.ret > 0).sum()), len(base)),
                   exp_mean=float(x.mean()), exp_median=float(np.median(x)),
                   exp_ci=(float(np.percentile(bm, 2.5)), float(np.percentile(bm, 97.5))),
                   exp_ci_month=(float(np.percentile(bmb, 2.5)), float(np.percentile(bmb, 97.5))),
                   exp_base=float(base.ret.mean()), open_end=int((kept.open_end == True).sum()),
                   n_params=SIG_PARAMS[sig] + ((len(BASE_FEATS) + (len(FLOW_FEATS) if fl else 0) + 1) if meta else 0))
        by_w = {}
        for wn, g in base.groupby("window"):
            k_ = kept[kept.window.astype(str) == wn]
            by_w[wn] = dict(n_base=len(g), prec_base=float((g.ret > 0).mean()), n_keep=len(k_),
                            prec_keep=float((k_.ret > 0).mean()) if len(k_) else np.nan,
                            exp_keep=float(k_.ret.mean()) if len(k_) else np.nan,
                            auc=float(roc_auc_score((pe[pe.window == wn].ret > 0), pe[pe.window == wn].p)) if meta else np.nan)
        row["by_window"] = by_w
        if meta:
            row.update(rand_prec_mean=float(rp.mean()), rand_prec_sd=float(rp.std(ddof=1)), z_prec=float(zgap(row["prec"], rp)),
                       pct_prec=float(((rp < row["prec"]).sum() + 0.5 * (rp == row["prec"]).sum()) / SEEDS * 100),
                       rand_exp_mean=float(rm.mean()), z_exp=float(zgap(row["exp_mean"], rm)),
                       pct_exp=float(((rm < row["exp_mean"]).sum() + 0.5 * (rm == row["exp_mean"]).sum()) / SEEDS * 100))
        # danh mục OOS 2021-2023
        st = make_sig(sig)()
        strat = Filtered(st, keepset, row["n_params"], name) if meta else st
        E, T = evaluate(P, strat, CFG, seeds=SEEDS, periods=OOS_PERIODS)
        nav, tr, _ = run(P, strat, OOS[0], OOS[1], CFG)
        Es[name], navs[name] = E, nav
        T["FULL"].to_csv(OUT + "trades_pf_%s.csv" % name, index=False)
        F = E.loc["FULL"]
        row.update(pf=dict(cagr=F.cagr, sharpe=F.sharpe, maxdd=F.maxdd, n=int(F.n), win=F.win, pf=F.pf,
                           exp_pct=F.exp_pct, z_sharpe=F.z_sharpe, rand_sharpe=F.rand_sharpe,
                           A_cagr=E.loc["A"].cagr, B_cagr=E.loc["B"].cagr))
        rows[name] = row
        print(name, "chấm xong (%.0fs)" % (time.time() - t0), flush=True)
    # DSR, PBO, gate_a
    n_total = N_PREV + len(cfgs)
    old = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(RES + "t38/" + f)).values() if v
           for s in v["grid_sharpes_A"]]
    prev = sum((pd.read_csv(RES + t + "/summary.csv").sharpe_FULL.dropna().tolist()[:6] for t in ("t47", "t51", "t54")), [])
    new = [float(Es[k].loc["FULL"].sharpe) for k in cfgs]
    allsr = old + prev + new
    sr_list = old if np.var(old, ddof=1) >= np.var(allsr, ddof=1) else allsr
    R = pd.DataFrame({k: navs[k].pct_change() for k in cfgs}).iloc[1:]
    R["E1_ref"] = e1r.reindex(R.index)
    R.to_csv(OUT + "returns_oos.csv")
    pb = cscv_pbo(R.fillna(0.0), 16)
    bench = index_bench(OOS[0], OOS[1], ("VNINDEX", "VN30", "E1VFVN30"))
    for k in cfgs:
        dsr, sr0 = deflated_sharpe(navs[k], n_total, sr_list)
        g = gate_a(Es[k], rows[k]["n_params"], etf_core=e1, dsr=dsr, pbo=pb["pbo"])
        rows[k].update(dsr=float(dsr), sr0=float(sr0), gate=sum(v[1] for v in g.values()), gate_total=len(g),
                       gate_detail={n_: [str(v[0]), bool(v[1])] for n_, v in g.items()})
    # quy tắc quyết định (đăng ký trước)
    basemap = {"S1": "A1", "S2": "A2"}
    for k, (sig, meta, fl) in cfgs.items():
        r = rows[k]
        b = rows[basemap[sig]]
        if meta:
            nw = sum(v["prec_keep"] >= v["prec_base"] for v in r["by_window"].values())
            c = {"(1) >=200 sự kiện giữ": r["n"] >= 200, "(2) kỳ vọng/lệnh > 0": r["exp_mean"] > 0,
                 "(3) Δprecision >= 5đ% và z >= 2": (r["prec"] - b["prec"] >= 0.05) and r["z_prec"] >= 2,
                 "(4) precision lọc >= gốc ở >= 2/3 cửa sổ": nw >= 2}
            r["decision"] = {n_: bool(v) for n_, v in c.items()}
            r["windows_better"] = int(nw)
            r["pass_winrate"] = bool(all(c.values()))
            r["paper_eligible"] = bool(r["pass_winrate"] and r["gate"] == r["gate_total"])
        r["precision_reportable"] = bool(r["exp_mean"] > 0 and r["n"] >= 200)
    out = dict(n_total=n_total, sr_list_n=len(sr_list), pbo=pb["pbo"], pbo_splits=pb["n_splits"], e1_oos=e1,
               bench_oos=bench, configs=rows, fit=fit)
    json.dump(out, open(OUT + "summary.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print(json.dumps({k: {x: rows[k].get(x) for x in ("n", "prec_base", "prec", "exp_mean", "z_prec", "pct_prec", "dsr", "gate",
                                                       "pass_winrate", "precision_reportable")} for k in rows}, indent=1, default=str))
    print("PBO %.3f | E1 OOS %s | N %d | (%.0fs)" % (pb["pbo"], e1, n_total, time.time() - t0))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    {"dry": dry, "run": fit_all, "score": score}[sys.argv[1]]()
