"""T34: walk-forward out-of-sample cho luật Stage1 + thoát ATR đang chạy paper.

Tái tạo phần có thể tái tạo của auto_trader.stage1_quick_scan (origin/main auto_trader.py:230-383)
+ cổng score>=3 (auto_trader.py:530-534) + kế hoạch ATR (stop 1xATR, target 2xATR, tối đa 15 phiên,
backtest_config.json optimal_config) + tối đa 5 vị thế, 20%/vị thế, tối đa 3 lệnh mua mới/ngày.
KHÔNG tái tạo được: tin tức (news_score=0), trọng số learning_engine (=1), ensemble, LLM/debate.

Quy ước không look-ahead:
- tín hiệu tính trên nến đóng cửa ngày t, mua ở giá mở cửa t+1;
- T+2: quyết định bán sớm nhất ở đóng cửa ngày (mua)+2, bán ở giá mở cửa phiên kế tiếp;
- phí: 0.15% mỗi chiều + 0.1% thuế bán; lô 100 cp.
- dữ liệu cắt ở 2023-12-31 TRƯỚC khi tính gì (holdout 2024-2026 không dùng).
Hạn chế: universe = 50 mã trong backtest_config.json hiện tại (survivorship bias, thiên lạc quan).
"""
import json
import sys
import numpy as np
import pandas as pd

D = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/prices/"
CFG = json.load(open("/Users/drone/Downloads/tradeclone/backtest_config.json"))
POS_EV = CFG["positive_ev_tickers"]
UNIVERSE = POS_EV + CFG["negative_ev_tickers"]
END = "2023-12-31"
FEE, TAX = 0.0015, 0.001


def load(s):
    d = pd.read_parquet(D + s + ".parquet")
    d["time"] = pd.to_datetime(d["time"]).dt.normalize()
    d = d.drop_duplicates("time").set_index("time").sort_index()
    return d.loc[:END].astype(float)


def rsi(close, n):
    delta = close.diff()
    g = delta.clip(lower=0).rolling(n).mean()
    l = (-delta.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def stage1_score(d):
    """Điểm Stage1 như auto_trader.py:299-343 (news=0)."""
    c, h, l, v = d.close, d.high, d.low, d.volume
    r = rsi(c, 14)
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    sig = macd.ewm(span=9).mean()
    s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
    vr = v / (v.rolling(20).mean() + 1e-9)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    bb_mid, bb_std = c.rolling(20).mean(), c.rolling(20).std()
    bb_pos = (c - (bb_mid - 2 * bb_std)) / (4 * bb_std + 1e-9)
    mom5 = c / c.shift(5) - 1
    # weekly trend như data_fetcher.get_weekly_trend:635-679
    ws = (c.rolling(25).mean() > c.rolling(50).mean()).astype(int) + (rsi(c, 21) > 50).astype(int) \
        + (c / c.shift(20) - 1 > 0.02).astype(int)
    wt = np.where(ws >= 2, 1, np.where(ws <= 0, -1, 0))
    sc = pd.Series(0.0, index=c.index)
    sc += np.where(r < 35, 1.5, np.where(r < 45, 0.5, 0))
    sc += (macd > sig) * 1.0
    sc += (s20 > s50) * 1.0
    sc += np.where((vr > 1.5) & (c > c.shift()), 1.5, np.where(vr > 1.2, 0.5, 0))
    sc += (bb_pos < 0.2) * 1.0
    sc += (mom5 > 0.01) * 0.5
    sc += np.where(wt == -1, -1.5, np.where(wt == 1, 0.5, 0))
    sc = sc.clip(lower=0)
    sc[s50.isna() | atr.isna()] = np.nan
    return sc, atr


def build(universe):
    idx = load("VNINDEX").index
    O, C, S, A = {}, {}, {}, {}
    for s in universe:
        try:
            d = load(s)
        except Exception:
            continue
        sc, atr = stage1_score(d)
        d = d.reindex(idx)
        O[s], C[s] = d.open, d.close.ffill()
        S[s], A[s] = sc.reindex(idx), atr.reindex(idx)
    return idx, pd.DataFrame(O), pd.DataFrame(C), pd.DataFrame(S), pd.DataFrame(A)


def run(data, start, end, thr=3.0, m_stop=1.0, m_tgt=2.0, max_hold=15, max_pos=5, pos_pct=0.20,
        max_new=3, rng=None):
    idx, O, C, S, A = data
    days = idx[(idx >= start) & (idx <= end)]
    cash, pos, nav, trades = 1e8, {}, [], []
    pend_buy, pend_sell = [], []
    for k, d in enumerate(days):
        # 1) khớp lệnh ở giá mở cửa hôm nay (quyết định từ hôm qua)
        for s in pend_sell:
            p = pos.pop(s)
            px = O.at[d, s]
            if np.isnan(px):
                px = C.at[d, s]
            val = p["sh"] * px * 1000
            cash += val * (1 - FEE - TAX)
            trades.append(dict(sym=s, buy=p["date"], sell=d, ret=px / p["px"] - 1,
                               net=val * (1 - FEE - TAX) - p["cost"], why=p.get("why")))
        pend_sell = []
        for s, tgt_val, atr in pend_buy:
            px = O.at[d, s]
            if np.isnan(px) or len(pos) >= max_pos:
                continue
            sh = int(min(tgt_val, cash / (1 + FEE)) / (px * 1000) / 100) * 100
            if sh <= 0:
                continue
            cost = sh * px * 1000 * (1 + FEE)
            cash -= cost
            pos[s] = dict(sh=sh, px=px, cost=cost, date=d, k=k,
                          stop=px - m_stop * atr, tgt=px + m_tgt * atr)
        pend_buy = []
        # 2) NAV đóng cửa
        mv = sum(p["sh"] * C.at[d, s] * 1000 for s, p in pos.items())
        navd = cash + mv
        nav.append((d, navd))
        if k == len(days) - 1:
            break
        # 3) quyết định bán (T+2: ít nhất 2 phiên sau ngày mua)
        for s, p in pos.items():
            if k - p["k"] < 2:
                continue
            c = C.at[d, s]
            why = "stop" if c <= p["stop"] else "target" if c >= p["tgt"] else \
                "timeout" if k - p["k"] >= max_hold else None
            if why:
                p["why"] = why
                pend_sell.append(s)
        # 4) quyết định mua
        slots = max_pos - (len(pos) - len(pend_sell))
        if slots <= 0:
            continue
        row = S.loc[d].dropna()
        row = row[[s for s in row.index if s not in pos]]
        if rng is not None:  # baseline: vào lệnh ngẫu nhiên, cùng số lượng ứng viên như luật thật
            n = int((row >= thr).sum())
            cand = list(rng.choice(row.index, size=min(n, len(row)), replace=False)) if n else []
        else:
            cand = list(row[row >= thr].sort_values(ascending=False).index)
        for s in cand[:min(slots, max_new)]:
            if not np.isnan(A.at[d, s]):
                pend_buy.append((s, pos_pct * navd, A.at[d, s]))
    return pd.Series(dict(nav)), pd.DataFrame(trades)


def metrics(nav, tr):
    r = nav.pct_change().dropna()
    yrs = len(r) / 252
    out = dict(cagr=(nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1,
               sharpe=r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0,
               maxdd=(nav / nav.cummax() - 1).min(), n=len(tr))
    if len(tr):
        w, l = tr[tr.net > 0].net, tr[tr.net <= 0].net
        out.update(win=len(w) / len(tr), pf=w.sum() / -l.sum() if l.sum() < 0 else np.inf,
                   exp_pct=tr.ret.mean() - 2 * FEE - TAX, exp_vnd=tr.net.mean())
    return out


def bench(start, end):
    res = {}
    for s in ["VNINDEX", "VN30", "E1VFVN30"]:
        c = load(s).close.loc[start:end]
        r = c.pct_change().dropna()
        res[s] = dict(cagr=(c.iloc[-1] / c.iloc[0]) ** (252 / len(r)) - 1,
                      sharpe=r.mean() / r.std() * np.sqrt(252), maxdd=(c / c.cummax() - 1).min())
    return res


def fmt(m):
    return " ".join("%s=%s" % (k, ("%.3f" % v) if isinstance(v, float) else v) for k, v in m.items())


PERIODS = [("A 2018-2020", "2018-01-01", "2020-12-31"), ("B 2021-2023", "2021-01-01", "2023-12-31"),
           ("FULL 2018-2023", "2018-01-01", "2023-12-31")]

if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    full = build(UNIVERSE)
    print("universe loaded:", full[2].shape[1], "mã; lịch", full[0][0].date(), "->", full[0][-1].date())
    if step in ("all", "prod"):
        print("\n## T34-1 luật hiện tại (thr3, stop1ATR, tgt2ATR, hold15), universe 50 mã")
        for nm, a, b in PERIODS:
            nav, tr = run(full, a, b)
            print(nm, fmt(metrics(nav, tr)))
            if nm.startswith("FULL"):
                print("  theo lý do thoát:", tr.groupby("why").net.agg(["count", "mean"]).round(0).to_dict())
        for nm, a, b in PERIODS:
            print("  bench", nm, {k: fmt(v) for k, v in bench(a, b).items()})
    if step in ("all", "rand"):
        print("\n## T34-2 baseline vào lệnh ngẫu nhiên, cùng luật thoát (20 seed)")
        for nm, a, b in PERIODS:
            ms = [metrics(*run(full, a, b, rng=np.random.default_rng(i))) for i in range(20)]
            print(nm, fmt({k: float(np.mean([m[k] for m in ms])) for k in ("cagr", "sharpe", "maxdd", "exp_pct")}))
    if step in ("all", "wf"):
        print("\n## T34-3 walk-forward: tối ưu trên A, kiểm trên B (18 tổ hợp)")
        grid = [(t, s, g) for t in (3.0, 4.0) for s in (1.0, 2.0, 3.0) for g in (2.0, 3.0, 4.0)]
        res = []
        for t, s, g in grid:
            mA = metrics(*run(full, "2018-01-01", "2020-12-31", thr=t, m_stop=s, m_tgt=g))
            mB = metrics(*run(full, "2021-01-01", "2023-12-31", thr=t, m_stop=s, m_tgt=g))
            res.append((t, s, g, mA["sharpe"], mA["cagr"], mB["sharpe"], mB["cagr"], mB["maxdd"], mB["n"]))
        R = pd.DataFrame(res, columns=["thr", "stop", "tgt", "shA", "cagrA", "shB", "cagrB", "ddB", "nB"])
        print(R.round(3).to_string())
        best = R.loc[R.shA.idxmax()]
        print("chọn theo A:", best[["thr", "stop", "tgt"]].to_dict(), "-> B sharpe %.3f cagr %.3f" % (best.shB, best.cagrB))
        print("tương quan xếp hạng Sharpe A vs B: %.2f" % R.shA.rank().corr(R.shB.rank()))
    if step in ("all", "posev"):
        print("\n## T34-4 chỉ universe positive_ev_tickers (danh sách chọn 2026-06 => thông tin tương lai)")
        pe = build(POS_EV)
        for nm, a, b in PERIODS:
            print(nm, fmt(metrics(*run(pe, a, b))))
