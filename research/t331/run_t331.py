"""T331: giữ chân chọn mã Stage1 hay dồn về lõi ETF E1. Dùng lại khung T330 (không chạy lại GA).
CHỈ dữ liệu <= 2023-12-31. Đăng ký trước: research/trial_log.md mục T331.
  ../.venv/bin/python t331/run_t331.py
"""
import json
import os
import sys
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research/t330")
import numpy as np
import pandas as pd
from engine.core import run, metrics
from engine.strategies import TrendETF
from engine.bench import deflated_sharpe
from run_t330 import windows, cfg, build_panel, Stage1Prod

OUT = "/Users/drone/Downloads/tradeclone/research/t331/"
OPTS = dict(a=0.50, b=0.25, c=0.0)          # tỷ trọng chân Stage1; phần còn lại là lõi ETF
N_TOTAL = 101 + 2
B, BLOCK, SEED = 10000, 20, 331


def m_of(nav, tr):
    m = metrics(nav, tr)
    m["ret"] = nav.iloc[-1] / nav.iloc[0] - 1
    return m


def sharpe(r):
    return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0


def stats(r):
    nav = np.cumprod(1 + r)
    return sharpe(r), nav[-1] ** (252 / len(r)) - 1, (nav / np.maximum.accumulate(nav) - 1).min()


def block_idx(n, rng):
    st = rng.integers(0, n, size=(n + BLOCK - 1) // BLOCK)
    return ((st[:, None] + np.arange(BLOCK)[None, :]) % n).ravel()[:n]


def main():
    lock = OUT + "runs.json"
    if os.path.exists(lock):
        raise SystemExit("runs.json đã khóa — T331 chỉ chạy 1 lần")
    W = windows()
    json.dump(dict(task="T331", date="2026-10-08", windows=W, opts=OPTS, n_total=N_TOTAL,
                   bootstrap=dict(B=B, block=BLOCK, seed=SEED)), open(lock, "w"), indent=1)
    P = build_panel()
    C = cfg()
    rows, rets, trs, s1 = [], {k: [] for k in OPTS}, [], []
    for nm, a, b, ta, tb in W:
        assert tb <= "2023-12-31"
        ne, te, _ = run(P, TrendETF(etf="E1VFVN30", signal="VN30", months=10), ta, tb, C)
        ns, ts, _ = run(P, Stage1Prod(), ta, tb, C)
        ne, ns = ne / ne.iloc[0], ns / ns.iloc[0]
        assert ne.index.equals(ns.index)
        trs.append(ts.assign(window=nm))
        s1.append(ns.pct_change().dropna())
        row = dict(window=nm, n_stage1=len(ts), n_etf=len(te))
        for k, w in OPTS.items():
            nav = (1 - w) * ne + w * ns                   # chia lại vốn đầu cửa sổ, 2 chân chạy độc lập
            r = nav.pct_change().dropna()
            rets[k].append(r)
            m = m_of(nav, ts if w else ts.iloc[:0])
            row.update({k + "_sharpe": m["sharpe"], k + "_maxdd": m["maxdd"], k + "_ret": m["ret"]})
        row["stage1_ret"], row["etf_ret"] = ns.iloc[-1] - 1, ne.iloc[-1] - 1
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(OUT + "windows.csv", index=False)
    T = pd.concat(trs)
    T.to_csv(OUT + "trades_stage1.csv", index=False)
    R = {k: pd.concat(v) for k, v in rets.items()}
    n_etf = int(df.n_etf.sum())
    summ = {}
    for k, w in OPTS.items():
        r = R[k]
        nav = (1 + r).cumprod()
        s = dict(cagr=nav.iloc[-1] ** (252 / len(r)) - 1, sharpe=sharpe(r), maxdd=(nav / nav.cummax() - 1).min(),
                 total_ret=nav.iloc[-1] - 1, n_trades=int(len(T) * (w > 0)) + n_etf,
                 n_stage1=int(len(T) * (w > 0)), n_etf=n_etf)
        for h, (lo, hi) in dict(A=("2019", "2020"), B=("2021", "2023")).items():
            rh = r.loc[lo:hi]
            nh = (1 + rh).cumprod()
            s["sharpe_" + h], s["cagr_" + h] = sharpe(rh), nh.iloc[-1] ** (252 / len(rh)) - 1
            s["maxdd_" + h] = (nh / nh.cummax() - 1).min()
        s["win_vs_c"] = int((df[k + "_sharpe"] > df["c_sharpe"]).sum())
        s["tie_vs_c"] = int(np.isclose(df[k + "_sharpe"], df["c_sharpe"]).sum())
        summ[k] = s
    # block bootstrap vòng trên lợi nhuận ngày nối, cặp (k − c) dùng cùng chỉ số
    X = np.column_stack([R[k].values for k in OPTS])
    rng = np.random.default_rng(SEED)
    boot = np.empty((B, len(OPTS), 3))
    for i in range(B):
        xi = X[block_idx(len(X), rng)]
        boot[i] = [stats(xi[:, j]) for j in range(len(OPTS))]
    ic = list(OPTS).index("c")
    obs = np.array([stats(X[:, j]) for j in range(len(OPTS))])
    bs = {}
    for j, k in enumerate(OPTS):
        if k == "c":
            continue
        d, d0 = boot[:, j] - boot[:, ic], obs[j] - obs[ic]
        bs[k + "-c"] = {nm: dict(obs=float(d0[q]), lo=float(np.percentile(d[:, q], 2.5)),
                                 hi=float(np.percentile(d[:, q], 97.5)), p_le0=float(np.mean(d[:, q] <= 0)))
                        for q, nm in enumerate(("sharpe", "cagr", "maxdd"))}
    # bootstrap theo lệnh: kỳ vọng/lệnh sau phí của chân Stage1
    x = T.net_ret.values
    em = np.array([rng.choice(x, len(x)).mean() for _ in range(B)])
    bs["stage1_exp"] = dict(obs=float(x.mean()), lo=float(np.percentile(em, 2.5)), hi=float(np.percentile(em, 97.5)),
                            p_le0=float(np.mean(em <= 0)), n=int(len(x)), median=float(np.median(x)),
                            win=float((x > 0).mean()))
    # DSR với N=103 (phương sai Sharpe ước lượng từ 3 phương án + chân Stage1 đứng riêng)
    srs = [summ[k]["sharpe"] for k in OPTS] + [sharpe(pd.concat(s1))]
    dsr = {k: float(deflated_sharpe((1 + R[k]).cumprod(), N_TOTAL, srs)[0]) for k in OPTS}
    # quy tắc quyết định đăng ký trước
    ok = {k: bs[k + "-c"]["sharpe"]["p_le0"] < 0.05 and bs[k + "-c"]["maxdd"]["obs"] >= -0.05 for k in ("a", "b")}
    choice = "a" if ok["a"] else ("b" if ok["b"] else "c")
    res = dict(summary=summ, bootstrap=bs, dsr=dsr, rule_ok=ok, choice=choice)
    json.dump(res, open(OUT + "summary.json", "w"), indent=1, default=float)
    pd.set_option("display.width", 200)
    print(df.round(3).to_string(index=False))
    print(pd.DataFrame(summ).T.round(4).to_string())
    print(json.dumps(bs, indent=1, default=float))
    print("DSR", dsr, "rule_ok", ok, "CHỌN", choice)


if __name__ == "__main__":
    main()
