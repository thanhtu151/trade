"""T51: ý #3 value+profitability (V1-V3) và ý #2 PEAD (P1-P3), CHỈ dữ liệu <= 2023-12-31.
  ../.venv/bin/python t51/run_t51.py prep   # dựng tín hiệu + kiểm nhân quả + độ phủ, KHÔNG chạy engine/chỉ số
  ../.venv/bin/python t51/run_t51.py run    # khóa runs.json rồi chạy 6 cấu hình (1 lần)
Đăng ký trước: research/trial_log.md mục T51."""
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all, pit_universe, NON_STOCK
from engine.core import run, metrics, Strategy
from engine.bench import PERIODS, evaluate, gate_a, deflated_sharpe, protocol_config
from engine.portfolio import cscv_pbo

RES = "/Users/drone/Downloads/tradeclone/research/"
FUND = RES + "data/2026-09-29/fundamentals/"
T38 = RES + "t38/"
T47 = RES + "t47/"
OUT = RES + "t51/"
N_TOTAL = 91 + 6
STALE = 200            # ngày kể từ cuối quý; quá hạn -> coi như thiếu
MAX_LAG = 100          # publicDate trễ hơn -> dòng bị coi là công bố lại/lỗi
TOP_N = 10
PCT = 0.8
MIN_KNOWN = 10         # số SUE đã biết tối thiểu để tính phân vị 80% tại ngày sự kiện
CFGS = {"V1_EP": ("value", "ep"), "V2_BP": ("value", "bp"), "V3_EPBP": ("value", "epbp"),
        "P1_h20": ("pead", 20), "P2_h40": ("pead", 40), "P3_h60": ("pead", 60)}
IDEAS = {"value": ["V1_EP", "V2_BP", "V3_EPBP"], "pead": ["P1_h20", "P2_h40", "P3_h60"]}
WF = [("2018-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
      ("2018-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
      ("2018-01-01", "2022-12-31", "2023-01-01", "2023-12-31")]


# ---------------------------------------------------------------- dữ liệu cơ bản
def _read(path, cols):
    """Đọc parquet; file thiếu/rỗng/không có đủ cột -> DataFrame rỗng (mã bị bỏ)."""
    try:
        return pd.read_parquet(path, columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


def load_fund(cal, symbols):
    """Bảng (mã, năm, quý): pe, pb, roe (ratio TTM) + eps isa23 (income) + ngày sẵn có A và ngày sử dụng."""
    rows = []
    for s in symbols:
        fi, fr = FUND + s + ".income.parquet", FUND + s + ".ratio.parquet"
        inc = _read(fi, ["yearReport", "lengthReport", "publicDate", "isa23"])
        rat = _read(fr, ["yearReport", "quarter", "ratioType", "pe", "pb", "roe"])
        rat = rat[rat.ratioType == "RATIO_TTM"].rename(columns={"quarter": "lengthReport"}).drop(columns="ratioType")
        if inc.empty and rat.empty:
            continue
        for d in (inc, rat):
            d["yearReport"], d["lengthReport"] = d.yearReport.astype(int), d.lengthReport.astype(int)
        m = inc.drop_duplicates(["yearReport", "lengthReport"], keep="last").merge(
            rat.drop_duplicates(["yearReport", "lengthReport"], keep="last"), on=["yearReport", "lengthReport"], how="outer")
        m["sym"] = s
        rows.append(m)
    F = pd.concat(rows, ignore_index=True)
    F = F[F.lengthReport.between(1, 4) & F.yearReport.between(2017, 2023)].copy()
    F = F.rename(columns={"yearReport": "year", "lengthReport": "q", "isa23": "eps"})
    F["qend"] = pd.to_datetime(dict(year=F.year, month=F.q * 3, day=1)) + pd.offsets.MonthEnd(0)
    F["pub"] = pd.to_datetime(F.publicDate, errors="coerce")
    F["lag"] = (F.pub - F.qend).dt.days
    F["no_pub"] = F.pub.isna()
    F["bad_lag"] = (~F.no_pub) & ((F.lag < 0) | (F.lag > MAX_LAG))
    floor = pd.to_timedelta(np.where(F.q == 4, 90, 45), unit="D")
    est = F.qend + floor
    good = ~(F.no_pub | F.bad_lag)
    F["A"] = est.where(~good, np.maximum(F.pub, est))
    pos = cal.searchsorted(F.A.values, side="right")            # phiên đầu tiên SAU ngày A
    F["usage"] = pd.Series(cal[np.minimum(pos, len(cal) - 1)], index=F.index).where(pos < len(cal))
    for c in ("pe", "pb", "roe", "eps"):
        F[c] = pd.to_numeric(F[c], errors="coerce")
    return F[F.usage.notna()].reset_index(drop=True)


def add_sue(F):
    """SUE = (EPS_q - EPS_{q-4}) / std(8 Δ gần nhất gồm cả quý hiện tại), cần đủ 8 Δ liên tiếp. Chỉ dùng dòng có trong F."""
    F = F.copy()
    F["sue"] = np.nan
    for s, g in F.groupby("sym"):
        qi = (g.year * 4 + g.q - 1).values
        e = pd.Series(g.eps.values, index=qi)
        e = e[~e.index.duplicated(keep="last")]
        full = e.reindex(range(int(qi.min()), int(qi.max()) + 1))
        d = full - full.shift(4)
        sd = d.rolling(8, min_periods=8).std(ddof=1)
        sue = (d / sd.where(sd > 0)).reindex(qi)
        F.loc[g.index, "sue"] = sue.values
    return F


def build_state(F, P):
    """Trạng thái hằng ngày (ngày x mã) từ dòng cơ bản có ngày sử dụng <= ngày cuối của P."""
    idx = P.idx
    n = len(idx)
    F = add_sue(F[F.usage <= idx[-1]])
    syms = sorted(F.sym.unique())
    S = {k: np.full((n, len(syms)), np.nan) for k in ("ep", "bp", "roe", "sue")}
    EV = np.zeros((n, len(syms)), bool)
    C = P.close_ff
    cvals = {s: C[s].values for s in syms if s in C.columns}
    for j, s in enumerate(syms):
        if s not in cvals:
            continue
        g = F[F.sym == s].sort_values("qend")
        up = idx.searchsorted(g.usage.values)
        key = np.full(n, -1)
        for r, p in enumerate(up):
            key[p] = max(key[p], r)
        latest = np.maximum.accumulate(key)
        has = latest >= 0
        li = np.where(has, latest, 0)
        qend = g.qend.values[li]
        age = (idx.values - qend).astype("timedelta64[D]").astype(int)
        ok = has & (age <= STALE)
        adjq = C[s].asof(pd.DatetimeIndex(g.qend)).values[li]
        px = cvals[s]
        for col, name in (("pe", "ep"), ("pb", "bp")):
            v = g[col].values[li]
            fac = np.where((px > 0) & (adjq > 0), adjq / np.where(px > 0, px, 1), np.nan)
            yld = np.where(v > 0, 1.0 / np.where(v > 0, v, 1), np.nan) * fac
            S[name][:, j] = np.where(ok, yld, np.nan)
        S["roe"][:, j] = np.where(ok, g.roe.values[li], np.nan)
        S["sue"][:, j] = np.where(ok, g.sue.values[li], np.nan)
        arrive = key == latest
        for r, p in enumerate(up):
            if key[p] == r and latest[p] == r and (p == 0 or latest[p - 1] != r):
                EV[p, j] = True
    st = {k: pd.DataFrame(v, index=idx, columns=syms) for k, v in S.items()}
    st["ev"] = pd.DataFrame(EV, index=idx, columns=syms)
    return st


def signals(P, F, rb_days):
    """-> value_targets[cfg][k] = [mã...] (ngày cân bằng), pead_events[k] = [(mã, sue)...] sắp xếp SUE giảm, elig, stats.
    Mọi giá trị tại ngày k chỉ dùng giá <= k và dòng cơ bản có ngày sử dụng <= k."""
    stocks = [s for s in P.symbols if s not in NON_STOCK]
    U = pit_universe(P, stocks)
    st = build_state(F, P)
    U = U.reindex(columns=st["ep"].columns, fill_value=False)
    idx = P.idx
    rb = np.where(idx.isin(rb_days))[0]
    tg = {c: {} for c in IDEAS["value"]}
    elig_v = {}
    cov = []
    for k in rb:
        u = U.iloc[k].values
        ep, bp, roe = (st[x].iloc[k].values for x in ("ep", "bp", "roe"))
        row = {}
        for c in IDEAS["value"]:
            ok = u & np.isfinite(roe) & (np.isfinite(ep) if c != "V2_BP" else True) & (np.isfinite(bp) if c != "V1_EP" else True)
            names = st["ep"].columns[ok]
            if len(names) == 0:
                tg[c][k] = []
                row[c] = 0
                continue
            r = lambda a: pd.Series(a[ok], index=names).rank(pct=True)
            val = r(ep) if c == "V1_EP" else r(bp) if c == "V2_BP" else (r(ep) + r(bp)) / 2
            score = 0.5 * val + 0.5 * r(roe)
            order = sorted(names, key=lambda s: (-score[s], s))
            tg[c][k] = order[:TOP_N]
            elig_v[(c, k)] = list(names)
            row[c] = len(names)
        cov.append(dict(date=idx[k], universe=int(u.sum()), **row))
    sue = st["sue"].where(U)
    q80 = sue.quantile(PCT, axis=1)
    cnt = sue.notna().sum(axis=1)
    pe = {}
    elig_p = {}
    ev = st["ev"] & U & (st["sue"] > 0) & st["sue"].ge(q80, axis=0)
    ev.loc[~(cnt >= MIN_KNOWN).values] = False
    for k in np.where(ev.values.any(axis=1))[0]:
        r = st["sue"].iloc[k][ev.iloc[k]]
        pe[k] = [(s, float(r[s])) for s in sorted(r.index, key=lambda s: (-r[s], s))]
    for k in np.where((cnt >= 1).values)[0]:
        elig_p[k] = list(sue.columns[sue.iloc[k].notna().values])
    return tg, pe, elig_v, elig_p, pd.DataFrame(cov)


# ---------------------------------------------------------------- chiến lược
class ValueStrat(Strategy):
    def __init__(self, name, targets, elig):
        self.name, self.n_params, self.tg, self.elig = name, 3, targets, elig
        self.cfg = name

    def prepare(self, panel):
        return None

    def eligible(self, k, d):
        return self.elig.get((self.cfg, k), [])

    def meta(self, k, s):
        return {}

    def candidates(self, k, d, held):
        return [(s, {}) for s in self.tg.get(k, []) if s not in held]

    def weight(self, sym, meta):
        return None

    def exit_signal(self, k, d, sym, pos):
        return "rebal" if k in self.tg and sym not in self.tg[k] else None


class PeadStrat(Strategy):
    def __init__(self, name, events, elig, hold):
        self.name, self.n_params, self.ev, self.elig, self.hold = name, 3, events, elig, hold

    def prepare(self, panel):
        return None

    def eligible(self, k, d):
        return self.elig.get(k, [])

    def meta(self, k, s):
        return {}

    def candidates(self, k, d, held):
        return [(s, {}) for s, _ in self.ev.get(k, []) if s not in held]

    def weight(self, sym, meta):
        return None

    def exit_signal(self, k, d, sym, pos):
        return "hold" if k - pos["k"] >= self.hold else None


def make_strat(name, S):
    tg, pe, elig_v, elig_p, _ = S
    kind, par = CFGS[name]
    return ValueStrat(name, tg[name], elig_v) if kind == "value" else PeadStrat(name, pe, elig_p, par)


# ---------------------------------------------------------------- tiện ích
def sharpe_of(r):
    return float(r.mean() / r.std() * np.sqrt(252)) if len(r) > 2 and r.std() > 0 else 0.0


def causal(P, F, rb_days, S, dates):
    tg, pe = S[0], S[1]
    idx = P.idx
    bad = tot = 0
    for t in dates:
        k = idx.get_loc(t)
        Pt = P.truncate(t)
        St = signals(Pt, F[F.usage <= t], rb_days)
        for c in IDEAS["value"]:
            tot += 1
            bad += St[0][c].get(k, []) != tg[c].get(k, [])
        tot += 1
        bad += St[1].get(k, []) != pe.get(k, [])
    return bad, tot


def main(mode):
    t0 = time.time()
    P = Panel(universe_all(True) + ["VN30"], start="2016-01-01")
    stocks = [s for s in P.symbols if s not in NON_STOCK]
    fsyms = [s for s in stocks if os.path.exists(FUND + s + ".income.parquet") or os.path.exists(FUND + s + ".ratio.parquet")]
    F = load_fund(P.idx, fsyms)
    idx = P.idx
    nxt_month = pd.Series(idx[1:].month.tolist() + [-1], index=idx)
    rb_days = idx[(idx.month.isin([3, 6, 9, 12])) & (nxt_month.values != idx.month) & (idx >= "2018-01-01")]
    S = signals(P, F, rb_days)
    tg, pe, elig_v, elig_p, cov = S
    win = (F.year >= 2018)
    info = dict(
        n_stocks_panel=len(stocks), n_stocks_with_fund=len(fsyms), fund_rows=int(len(F)),
        rows_no_income_pubdate=int(F.no_pub.sum()), rows_pubdate_bad_lag=int(F.bad_lag.sum()),
        rows_bad_lag_neg=int((F.lag < 0).sum()), rows_bad_lag_gt100=int((F.lag > MAX_LAG).sum()),
        rows_total_with_pubdate=int((~F.no_pub).sum()),
        rows_A_after_pub_median_days=float(((F.A - F.pub).dt.days[~(F.no_pub | F.bad_lag)]).median()),
        rows_delayed_by_floor_pct=float(((F.A > F.pub)[~(F.no_pub | F.bad_lag)]).mean() * 100),
        min_usage_minus_pub_days=int(((F.usage - F.pub).dt.days[~(F.no_pub | F.bad_lag)]).min()),
        rebalance_days=len(cov), cov_mean_universe=float(cov.universe.mean()),
        cov_mean_valid={c: float(cov[c].mean()) for c in IDEAS["value"]},
        first_rebalance_with_10=str(cov.date[cov.V1_EP >= TOP_N].min().date()) if (cov.V1_EP >= TOP_N).any() else None,
        pead_event_days=len(pe), pead_events_total=int(sum(len(v) for v in pe.values())),
        pead_first_event=str(idx[min(pe)].date()) if pe else None,
        pead_events_by_year={int(y): int(sum(len(v) for k, v in pe.items() if idx[k].year == y)) for y in range(2018, 2024)})
    print(json.dumps(info, indent=1, ensure_ascii=False))
    ev_days = list(pe)[::max(1, len(pe) // 25)]
    dates = sorted(set(list(rb_days[rb_days >= "2018-06-01"]) + [idx[k] for k in ev_days]))
    bad, tot = causal(P, F, rb_days, S, dates)
    info["causal_violations"], info["causal_checks"] = int(bad), int(tot)
    print("causal: %d/%d vi phạm (%d ngày kiểm)" % (bad, tot, len(dates)))
    json.dump(info, open(OUT + "coverage_causal.json", "w"), indent=1, ensure_ascii=False)
    cov.to_csv(OUT + "coverage_rebalance.csv", index=False)
    if mode != "run":
        print("(prep %.0fs)" % (time.time() - t0))
        return
    if os.path.exists(OUT + "summary.csv"):
        print("T51 đã chạy -> không chạy lại")
        return
    json.dump({k: time.strftime("%Y-%m-%d %H:%M") for k in CFGS}, open(OUT + "runs.json", "w"), indent=1)   # khóa trước
    cfg = protocol_config(max_pos=10, pos_pct=0.10, max_new=10, limits=True, delist_haircut=0.30)
    e1 = pd.read_csv(T47 + "summary.csv").set_index("config").loc["E1_ma10m"]
    etf = dict(cagr=float(e1.cagr_FULL), sharpe=float(e1.sharpe_FULL))
    Es, navs, trs, extras = {}, {}, {}, {}
    for k, (kind, par) in CFGS.items():
        st = make_strat(k, S)
        E, _ = evaluate(P, st, cfg, seeds=50)
        Es[k] = E
        navs[k], trs[k], extras[k] = run(P, make_strat(k, S), "2018-01-01", "2023-12-31", cfg)
        print(k, "xong (%.0fs)" % (time.time() - t0), flush=True)
    # DSR
    all_old = [s for f in ("chosen.json", "chosen2.json") for v in json.load(open(T38 + f)).values() if v
               for s in v["grid_sharpes_A"]]
    t47s = pd.read_csv(T47 + "summary.csv").sharpe_FULL.dropna().tolist()[:6]
    new_sr = [float(Es[k].loc["FULL"].sharpe) for k in CFGS]
    var_old, var_all = np.var(all_old, ddof=1), np.var(all_old + t47s + new_sr, ddof=1)
    sr_list = all_old if var_old >= var_all else all_old + t47s + new_sr
    ret = pd.DataFrame({k: navs[k].pct_change() for k in CFGS}).iloc[1:]
    ret.to_csv(OUT + "returns_full.csv")
    e1r = pd.read_csv(T47 + "returns_full.csv", index_col=0, parse_dates=True)["E1_ma10m"]
    rows, pbo, wf, gates = [], {}, {}, {}
    for idea, names in IDEAS.items():
        M = ret[names].assign(E1_ref=e1r.reindex(ret.index))
        pb = cscv_pbo(M.fillna(0.0), 16)
        pbo[idea] = dict(pbo=pb["pbo"], n_splits=pb["n_splits"], is_sr=float(pb["is_best_is_sr"].mean()),
                         oos_sr=float(pb["is_best_oos_sr"].mean()), p_oos_loss=pb["p_oos_loss"])
        # walk-forward mở rộng (cấu hình cố định; NAV FULL cắt đoạn)
        oos, is_sh, picks = [], [], []
        for a, b, c, d in WF:
            tr_sh = {k: sharpe_of(navs[k].loc[a:b].pct_change().dropna()) for k in names}
            best = max(tr_sh, key=tr_sh.get)
            picks.append(best)
            is_sh.append(tr_sh[best])
            oos.append(navs[best].loc[c:d].pct_change().dropna())
        o = pd.concat(oos)
        etf_oos = e1r.loc["2021-01-01":"2023-12-31"]
        wf[idea] = dict(picks=picks, is_sharpe_mean=float(np.mean(is_sh)), oos_sharpe=sharpe_of(o),
                        ratio=sharpe_of(o) / float(np.mean(is_sh)) if np.mean(is_sh) > 0 else None,
                        oos_ok=bool(np.mean(is_sh) > 0 and sharpe_of(o) >= 0.5 * np.mean(is_sh)),
                        oos_cagr=float((1 + o).prod() ** (252 / len(o)) - 1), etf_oos_sharpe=sharpe_of(etf_oos))
        for k in names:
            dsr, sr0 = deflated_sharpe(navs[k], N_TOTAL, sr_list)
            g = gate_a(Es[k], CFGS_PARAMS(k), etf_core=etf, dsr=dsr, pbo=pbo[idea]["pbo"])
            gates[k] = {n: dict(value=str(v[0]), ok=bool(v[1])) for n, v in g.items()}
            row = dict(config=k, idea=idea, dsr=dsr, sr0=sr0, gate_pass=sum(v[1] for v in g.values()), gate_total=len(g))
            for nm in ("A", "B", "FULL"):
                for x in ("cagr", "sharpe", "maxdd", "n"):
                    row["%s_%s" % (x, nm)] = float(Es[k].loc[nm][x])
            F_ = Es[k].loc["FULL"]
            row.update(pf=float(F_.pf), exp_pct=float(F_.exp_pct), win=float(F_.win), hold=float(F_.hold),
                       z_sharpe=float(F_.z_sharpe), rand_sharpe=float(F_.rand_sharpe),
                       VNINDEX_cagr=float(F_.VNINDEX_cagr), VNINDEX_sharpe=float(F_.VNINDEX_sharpe), VNINDEX_maxdd=float(F_.VNINDEX_maxdd),
                       E1VFVN30_cagr=float(F_.E1VFVN30_cagr), E1VFVN30_maxdd=float(F_.E1VFVN30_maxdd),
                       blocked_buy=int(F_.blocked_buy), blocked_sell=int(F_.blocked_sell), open_pos=int(F_.open_pos),
                       worst_trade=float(trs[k].net_ret.min()) if len(trs[k]) else np.nan,
                       n_delist=int((trs[k].why == "delist").sum()) if len(trs[k]) else 0)
            rows.append(row)
    Sm = pd.DataFrame(rows).set_index("config")
    Sm.to_csv(OUT + "summary.csv")
    json.dump(pbo, open(OUT + "pbo.json", "w"), indent=1)
    json.dump(wf, open(OUT + "walk_forward.json", "w"), indent=1)
    json.dump(gates, open(OUT + "gate_a.json", "w"), indent=1, ensure_ascii=False)
    yr = pd.DataFrame({k: trs[k].groupby(trs[k].buy.dt.year).size() for k in CFGS}).fillna(0).astype(int)
    yr.to_csv(OUT + "entries_per_year.csv")
    for k in CFGS:
        trs[k].to_csv(OUT + "trades_%s.csv" % k, index=False)
    pd.set_option("display.width", 250, "display.max_columns", 60)
    print(Sm[["idea", "cagr_FULL", "sharpe_FULL", "maxdd_FULL", "n_FULL", "cagr_A", "cagr_B", "pf", "z_sharpe", "dsr", "gate_pass",
              "blocked_sell", "n_delist", "worst_trade"]].round(3).to_string())
    print(yr.to_string())
    print(json.dumps(pbo)); print(json.dumps(wf))
    print("var_old %.4f var_all %.4f | E1 %s | (%.0fs)" % (var_old, var_all, etf, time.time() - t0))


def CFGS_PARAMS(k):
    return 3


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "prep")
