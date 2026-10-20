"""T192 phần B: sự kiện Stage1 lịch sử 2018–2023 (không holdout), chỉ MUA, giữ 3 phiên.
Vào mở cửa t+1 (mở cửa >= trần thì bỏ), ra đóng cửa t+3 (T+2). Đúng = close[t+3] > open[t+1].
Lãi sau phí = close[t+3]/open[t+1] - 1 - 0,5%. Mọi đặc trưng chỉ dùng dữ liệu <= t."""
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.data import Panel, universe_all, pit_universe            # noqa: E402
from engine.strategies import stage1_score, per_symbol                # noqa: E402
from engine.candidates import NON_TRADABLE                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.005
WINDOWS = [("2018-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
           ("2018-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
           ("2018-01-01", "2022-12-31", "2023-01-01", "2023-12-31")]
MIN_N_TRAIN, TARGET = 100, 0.80
GRID = dict(thr=(3, 4, 5), mkt=("none", "vn30_ma10m", "vni_ma50"), liq=("top50", "top20"))


def wilson(w, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = w / n
    c = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    d = 1 + z * z / n
    return ((p + z * z / (2 * n) - c) / d, (p + z * z / (2 * n) + c) / d)


def build():
    P = Panel(universe_all(True) + ["VN30", "VNINDEX"], start="2016-01-01")
    syms = [s for s in P.symbols if s not in NON_TRADABLE + ("VNINDEX",)]
    S, _ = per_symbol(P, stage1_score)
    S = S[syms]
    U = pit_universe(P, syms)
    tv = (P.close[syms] * P.volume[syms]).fillna(0).rolling(60, min_periods=60).mean()
    top20 = tv.where(U).rank(axis=1, ascending=False, method="first") <= 20
    # VN30 >= MA10 tháng tại phiên cuối tháng trước (áp cho cả tháng sau, như etf_core)
    c30 = P.close["VN30"].dropna()
    per = c30.index.to_period("M")
    me = c30.groupby(per).last()
    on = (me >= me.rolling(10).mean()).where(me.rolling(10).mean().notna())
    vn30_on = pd.Series(on.reindex(P.idx.to_period("M") - 1).values, index=P.idx).fillna(False).astype(bool)
    vni = P.close["VNINDEX"]
    vni_on = (vni > vni.rolling(50).mean()).fillna(False)
    O, C = P.open[syms], P.close[syms]
    prev_c = P.close_ff[syms].shift(1)
    lim = P.limit.reindex(syms).fillna(0.07)
    o1 = O.shift(-1)                                     # mở cửa t+1 (giá khớp, không dùng làm đặc trưng)
    ceil1 = P.close_ff[syms] * (1 + lim)                # trần phiên t+1 theo đóng cửa t (sai số làm tròn: 0,5%)
    c3 = C.shift(-3)                                     # đóng cửa t+3
    rows = []
    for s in syms:
        m = U[s] & S[s].notna() & (S[s] >= 3) & o1[s].notna() & c3[s].notna()
        idx = m[m].index
        for t in idx:
            if o1.at[t, s] >= ceil1.at[t, s] * 0.995:
                continue                                 # mở cửa trần → không mua được
            r = c3.at[t, s] / o1.at[t, s] - 1
            rows.append((t, s, S.at[t, s], bool(top20.at[t, s]), bool(vn30_on[t]), bool(vni_on[t]), r))
    ev = pd.DataFrame(rows, columns=["date", "sym", "score", "top20", "vn30_on", "vni_on", "ret"])
    ev = ev[(ev.date >= "2018-01-01") & (ev.date <= "2023-12-31")]
    ev["hit"] = ev.ret > 0
    ev["net"] = ev.ret - FEE
    # tỉ lệ nền: mọi mã-phiên trong top-50 (không cần tín hiệu), cùng cách vào/ra
    ok = U & o1.notna() & c3.notna() & (o1 < ceil1 * 0.995)
    base = (c3 / o1 - 1).where(ok).loc["2018-01-01":"2023-12-31"]
    return ev, base, prev_c


def sel(ev, f):
    m = ev.score >= f["thr"]
    if f["mkt"] == "vn30_ma10m":
        m &= ev.vn30_on
    elif f["mkt"] == "vni_ma50":
        m &= ev.vni_on
    if f["liq"] == "top20":
        m &= ev.top20
    return ev[m]


def st(x, months=None):
    n, w = len(x), int(x.hit.sum())
    lo, hi = wilson(w, n)
    d = dict(n=n, prec=w / n if n else np.nan, lo=lo, hi=hi, exp_net=x.net.mean() if n else np.nan,
             med_net=x.net.median() if n else np.nan, win_net=(x.net > 0).mean() if n else np.nan,
             days=x.date.nunique())
    if months:
        d["per_month"] = n / months
    return d


def fmt(d):
    return "n=%d (phiên %d) prec %.1f%% [Wilson %.1f–%.1f] exp sau phí %+.2f%% (trung vị %+.2f%%, thắng sau phí %.1f%%)" % (
        d["n"], d["days"], 100 * d["prec"], 100 * d["lo"], 100 * d["hi"], 100 * d["exp_net"], 100 * d["med_net"], 100 * d["win_net"])


def main():
    ev, base, _ = build()
    ev.to_csv(os.path.join(HERE, "events_b.csv"), index=False)
    filters = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    out = ["# T192 phần B — Stage1 lịch sử 2018–2023, giữ 3 phiên, chỉ MUA", ""]
    bv = base.stack()
    out.append("Tỉ lệ nền (mọi mã-phiên top-50, vào mở t+1 ra đóng t+3): n=%d, tăng %.1f%%, exp sau phí %+.2f%%" % (
        len(bv), 100 * (bv > 0).mean(), 100 * (bv.mean() - FEE)))
    out.append("Sự kiện score≥3: %d" % len(ev))
    out.append("")
    out.append("## Lưới 18 bộ lọc — FULL 2018–2023 (chỉ mô tả)")
    out.append("| bộ lọc | FULL | /tháng |")
    out.append("|---|---|---|")
    full = {}
    for f in filters:
        d = st(sel(ev, f), 72)
        full[str(f)] = d
        out.append("| %s | %s | %.1f |" % (f, fmt(d), d["per_month"]))
    out.append("")
    out.append("## Walk-forward (chọn trên train, đóng băng, kiểm năm sau)")
    oos = []
    wf = []
    for a, b, c, e in WINDOWS:
        tr = ev[(ev.date >= a) & (ev.date <= b)]
        te = ev[(ev.date >= c) & (ev.date <= e)]
        cand = [(f, st(sel(tr, f))) for f in filters]
        cand = [x for x in cand if x[1]["n"] >= MIN_N_TRAIN]
        ok = [x for x in cand if x[1]["prec"] >= TARGET and x[1]["exp_net"] > 0]
        tag = "đạt trên train" if ok else "KHÔNG bộ lọc nào đạt ≥80% & exp>0 trên train → mô tả bộ lọc Wilson lo cao nhất"
        f, s = max(ok or cand, key=lambda x: x[1]["lo"])
        x = sel(te, f)
        t = st(x, 12)
        if ok:
            oos.append(x)
        wf.append(dict(train=[a, b], test=[c, e], chosen=f, qualified=bool(ok), train_stats=s, test_stats=t))
        out.append("- Train %s→%s, test %s: %s; chọn %s" % (a[:4], b[:4], c[:4], tag, f))
        out.append("  - train: %s" % fmt(s))
        out.append("  - test : %s; %.1f tín hiệu/tháng%s" % (fmt(t), t["per_month"], " ⚠ n<30" if t["n"] < 30 else ""))
    if oos:
        g = st(pd.concat(oos))
        out.append("OOS gộp (chỉ cửa sổ có bộ lọc đạt trên train): %s" % fmt(g))
    else:
        out.append("OOS gộp: không có cửa sổ nào chọn được bộ lọc đạt trên train → KHÔNG ĐẠT")
    best = max(full.items(), key=lambda kv: kv[1]["prec"])
    out.append("")
    out.append("Precision FULL cao nhất trong lưới (hậu nghiệm, chỉ mô tả): %s → %s" % (best[0], fmt(best[1])))
    json.dump(dict(walk_forward=wf, full=full), open(os.path.join(HERE, "part_b.json"), "w"), default=str, indent=1)
    txt = "\n".join(out)
    open(os.path.join(HERE, "part_b.md"), "w").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
