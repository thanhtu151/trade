"""T48 (ý #1): lõi ETF VN30 — MA10 tháng vs MA200 ngày, + breadth. CHỈ dữ liệu <= 2023-12-31.
  ../.venv/bin/python research/t47/run_t47.py
Đăng ký trước: research/trial_log.md mục T48."""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all, pit_universe
from engine.core import run, metrics, Strategy
from engine.strategies import BuyHold
from engine.bench import PERIODS, index_bench, deflated_sharpe, protocol_config
from engine.portfolio import cscv_pbo

OUT = "/Users/drone/Downloads/tradeclone/research/t47/"
T38 = "/Users/drone/Downloads/tradeclone/research/t38/"
N_TOTAL = 85 + 6
CFGS = {"E1_ma10m": ("m", None), "E2_ma200d": ("d", None),
        "E3_ma10m_b50": ("m", 0.5), "E4_ma10m_b60": ("m", 0.6),
        "E5_ma200d_b50": ("d", 0.5), "E6_ma200d_b60": ("d", 0.6)}
CRASH = {"2018": ("2018-01-01", "2018-12-31"), "3/2020": ("2020-01-15", "2020-04-30"),
         "2022": ("2022-01-01", "2022-12-31")}


def signals(P):
    """-> dict tên -> bool Series theo P.idx (want ở phiên t, biết sau đóng cửa t)."""
    idx = P.idx
    vn30 = P.close["VN30"].dropna().reindex(idx).ffill()
    ma200 = vn30.rolling(200, min_periods=200).mean()
    day_on = (vn30 > ma200) & ma200.notna()
    stocks = [s for s in P.symbols if s != "VN30"]
    U = pit_universe(P, stocks)
    C = P.close_ff[stocks]
    m = C.rolling(200, min_periods=200).mean()
    valid = m.notna() & U
    breadth = ((C > m) & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)
    nxt = pd.Series(idx[1:].append(pd.DatetimeIndex([pd.NaT])), index=idx)
    is_me = pd.Series([nxt[t] is pd.NaT or nxt[t].month != t.month for t in idx], index=idx)
    c_me = vn30[is_me]
    ma10 = c_me.rolling(10).mean()
    m_on_me = ((c_me >= ma10) & ma10.notna())
    m_on = m_on_me.reindex(idx).ffill().fillna(False).astype(bool)      # tín hiệu cuối tháng giữ cả tháng sau
    out = {}
    for k, (kind, thr) in CFGS.items():
        if kind == "m":
            on = m_on_me if thr is None else (m_on_me & (breadth[is_me] > thr))
            out[k] = on.reindex(idx).ffill().fillna(False).astype(bool)
        else:
            out[k] = day_on if thr is None else (day_on & (breadth > thr))
    return out, breadth


class WantETF(Strategy):
    def __init__(self, name, want, etf="E1VFVN30"):
        self.name, self.n_params, self.etf, self._w = name, 1, etf, want

    def prepare(self, panel):
        self.want = self._w.reindex(panel.idx).fillna(False).values
        return pd.DataFrame({self.etf: self.want.astype(float)}, index=panel.idx)

    def eligible(self, k, d):
        return [self.etf]

    def candidates(self, k, d, held):
        return [(self.etf, {})] if self.want[k] and self.etf not in held else []

    def weight(self, sym, meta):
        return 1.0

    def exit_signal(self, k, d, sym, pos):
        return "trend_off" if not self.want[k] else None


def dd_window(nav, a, b):
    x = nav.loc[a:b]
    return (x / x.cummax() - 1).min(), x.iloc[-1] / x.iloc[0] - 1


def main():
    if os.path.exists(OUT + "summary.csv"):
        print("T47/T48 đã chạy -> không chạy lại")
        return
    json.dump({k: time.strftime("%Y-%m-%d %H:%M") for k in CFGS}, open(OUT + "runs.json", "w"), indent=1)  # khóa trước
    P = Panel(universe_all(True) + ["VN30"], start="2016-01-01")
    Q = Panel(["E1VFVN30", "VN30"])
    sig, breadth = signals(P)
    # kiểm nhân quả: cắt dữ liệu tại t, tín hiệu ngày t phải bằng bản đầy đủ
    ix = P.idx
    me = [t for i, t in enumerate(ix[:-1]) if t >= pd.Timestamp("2018-01-01") and ix[i + 1].month != t.month]
    dates = me[::6]      # cắt tại phiên cuối tháng thật (tín hiệu tháng chỉ xác định ở đó)
    bad = 0
    for t in dates:
        s2, _ = signals(P.truncate(t))
        bad += sum(bool(s2[k].loc[t]) != bool(sig[k].loc[t]) for k in CFGS)
    print("causal: %d/%d vi phạm" % (bad, len(dates) * len(CFGS)))
    cfg = protocol_config(max_pos=1, pos_pct=1.0, max_new=1)
    navs, trs = {}, {}
    for nm, a, b in PERIODS:
        for k in CFGS:
            nav, tr, _ = run(Q, WantETF(k, sig[k]), a, b, cfg)
            navs[(k, nm)], trs[(k, nm)] = nav, tr
        navs[("BH", nm)] = run(Q, BuyHold(), a, b, cfg)[0]
    bench = {nm: index_bench(a, b) for nm, a, b in PERIODS}
    all_old = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(T38 + f)).values() if v
               for s in v["grid_sharpes_A"]]
    rows = []
    new_sr = [metrics(navs[(k, "FULL")], trs[(k, "FULL")])["sharpe"] for k in CFGS]
    var_old, var_all = np.var(all_old, ddof=1), np.var(all_old + new_sr, ddof=1)
    sr_list = all_old if var_old >= var_all else all_old + new_sr
    for k in list(CFGS) + ["BH"]:
        row = dict(config=k)
        for nm, _, _ in PERIODS:
            m = metrics(navs[(k, nm)], trs.get((k, nm), pd.DataFrame()))
            for x in ("cagr", "sharpe", "maxdd"):
                row["%s_%s" % (x, nm)] = m[x]
        tr = trs.get((k, "FULL"))
        if tr is not None:
            row["n_trades"] = len(tr)
            row["entries_per_yr"] = len(tr) / 6
            row["dsr"], row["sr0"] = deflated_sharpe(navs[(k, "FULL")], N_TOTAL, sr_list)
            row["exp_pct"] = tr.net_ret.mean() if len(tr) else np.nan
            row["invested_pct"] = float(sig[k].loc["2018-01-01":"2023-12-31"].mean())
        rows.append(row)
    S = pd.DataFrame(rows).set_index("config")
    S.to_csv(OUT + "summary.csv")
    # vào/ra mỗi năm
    yr = pd.DataFrame({k: trs[(k, "FULL")].groupby(trs[(k, "FULL")].buy.dt.year).size() for k in CFGS}).fillna(0).astype(int)
    yr.to_csv(OUT + "entries_per_year.csv")
    # crash
    cr = []
    for w, (a, b) in CRASH.items():
        r = dict(crash=w)
        for k in list(CFGS) + ["BH"]:
            r[k] = dd_window(navs[(k, "FULL")], a, b)[0]
        r["VN30_index"] = dd_window(P.close["VN30"].dropna().loc["2017-06-01":"2023-12-31"], a, b)[0]
        cr.append(r)
    C = pd.DataFrame(cr).set_index("crash")
    C.to_csv(OUT + "crash_dd.csv")
    ret = pd.DataFrame({k: navs[(k, "FULL")].pct_change() for k in CFGS}).iloc[1:]
    ret.to_csv(OUT + "returns_full.csv")
    pbo_main = cscv_pbo(ret, 16)
    pbo_ref = cscv_pbo(ret.assign(BH=navs[("BH", "FULL")].pct_change().iloc[1:]), 16)
    pbo = {"6cfg": dict(pbo=pbo_main["pbo"], n_splits=pbo_main["n_splits"],
                        is_sr=float(pbo_main["is_best_is_sr"].mean()), oos_sr=float(pbo_main["is_best_oos_sr"].mean())),
           "6cfg_plus_BH": dict(pbo=pbo_ref["pbo"], n_splits=pbo_ref["n_splits"])}
    json.dump(pbo, open(OUT + "pbo.json", "w"), indent=1)
    pd.set_option("display.width", 250)
    print(S.round(3).to_string())
    print("\nvào ETF theo năm:\n", yr.to_string())
    print("\nDD crash:\n", (C * 100).round(1).to_string())
    print("\nPBO:", json.dumps(pbo), "| VN-Index FULL:", {k: round(v, 3) for k, v in bench["FULL"]["VNINDEX"].items()},
          "| var_old %.4f var_all %.4f" % (var_old, var_all))


if __name__ == "__main__":
    t0 = time.time()
    main()
    print("(%.0fs)" % (time.time() - t0))
