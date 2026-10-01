"""Benchmark, baseline ngẫu nhiên nhiều seed, walk-forward 2 nửa và kiểm cổng (a)."""
import numpy as np
import pandas as pd
from scipy.stats import norm
from engine.core import run, metrics, RandomEntry, Config
from engine.data import load

PERIODS = [("A", "2018-01-01", "2020-12-31"), ("B", "2021-01-01", "2023-12-31"),
           ("FULL", "2018-01-01", "2023-12-31")]


def index_bench(start, end, syms=("VNINDEX", "VN30", "E1VFVN30")):
    """Chuỗi giá đóng cửa (không phí) — mốc so sánh thô như T34."""
    res = {}
    for s in syms:
        c = load(s).close.loc[start:end]
        r = c.pct_change().dropna()
        res[s] = dict(cagr=(c.iloc[-1] / c.iloc[0]) ** (252 / len(r)) - 1,
                      sharpe=r.mean() / r.std() * np.sqrt(252), maxdd=(c / c.cummax() - 1).min())
    return res


def random_baseline(panel, strat, start, end, cfg=None, seeds=50):
    strat.prepare(panel)
    ms = []
    for i in range(seeds):
        nav, tr, _ = run(panel, RandomEntry(strat, i), start, end, cfg, prepared=True)
        ms.append(metrics(nav, tr))
    return pd.DataFrame(ms)


def zgap(value, dist):
    sd = dist.std(ddof=1)
    return (value - dist.mean()) / sd if sd > 0 else np.nan


def evaluate(panel, strat, cfg=None, seeds=50, periods=PERIODS):
    """Chạy chiến lược + baseline ngẫu nhiên trên từng giai đoạn (mỗi giai đoạn bắt đầu lại từ vốn đầu)."""
    rows, trades = [], {}
    for nm, a, b in periods:
        nav, tr, extra = run(panel, strat, a, b, cfg)
        m = metrics(nav, tr)
        m.update(period=nm, open_pos=extra["open_positions"], blocked_buy=extra.get("blocked_buy", 0),
                 blocked_sell=extra.get("blocked_sell", 0))
        R = random_baseline(panel, strat, a, b, cfg, seeds) if seeds else None
        if R is not None:
            m.update(rand_sharpe=R.sharpe.mean(), rand_sharpe_sd=R.sharpe.std(ddof=1),
                     rand_cagr=R.cagr.mean(), z_sharpe=zgap(m["sharpe"], R.sharpe),
                     z_cagr=zgap(m["cagr"], R.cagr))
        bm = index_bench(a, b)
        for s in ("VNINDEX", "E1VFVN30"):
            m["%s_cagr" % s], m["%s_sharpe" % s], m["%s_maxdd" % s] = (bm[s][x] for x in ("cagr", "sharpe", "maxdd"))
        rows.append(m)
        trades[nm] = tr
    return pd.DataFrame(rows).set_index("period"), trades


DSR_MIN, PBO_MAX = 0.95, 0.2    # PROTOCOL.md (2026-09-30)
PROTOCOL_ROUND_TRIP = 0.005     # 0,35% phí+thuế + 0,15% trượt giá, khứ hồi


def protocol_config(**kw):
    """Config theo PROTOCOL.md: khứ hồi 0,5% = phí 0,125%x2 + thuế 0,1% (0,35%) + trượt 0,075%x2 (0,15%).
    Config() mặc định cũ (0,15%x2 + 0,1% + 0,1%x2 = 0,6%) giữ nguyên để kết quả T34-T46 tái tạo được."""
    return Config(**{**dict(fee=0.00125, tax=0.001, slip=0.00075), **kw})


def gate_a(E, n_params, etf_core=None, dsr=None, pbo=None):
    """Cổng (a) — trade_roadmap.md mục 4. E = kết quả evaluate(). Trả dict tiêu chí -> (giá trị, đạt?).
    Giao thức 2026-09-30: nếu truyền BẤT KỲ của etf_core / dsr / pbo thì thêm 3 tiêu chí (thiếu cái nào = trượt):
      etf_core = dict(cagr=, sharpe=) của lõi ETF VN30>MA10 cùng kỳ FULL -> phải vượt CẢ CAGR lẫn Sharpe;
      dsr = DSR với N tổng (>= 0,95); pbo = PBO/CSCV (<= 0,2). Không truyền gì -> 8 tiêu chí cũ như trước."""
    F, A, B = E.loc["FULL"], E.loc["A"], E.loc["B"]
    g = {
        "lệnh FULL >= 200": (int(F.n), F.n >= 200),
        "CAGR dương cả 2 nửa": ("A %.1f%% / B %.1f%%" % (A.cagr * 100, B.cagr * 100), A.cagr > 0 and B.cagr > 0),
        "Sharpe FULL >= 0,5": (round(F.sharpe, 2), F.sharpe >= 0.5),
        "MaxDD <= MaxDD VN-Index": ("%.1f%% vs %.1f%%" % (F.maxdd * 100, F.VNINDEX_maxdd * 100), F.maxdd >= F.VNINDEX_maxdd),
        "PF >= 1,2": (round(F.pf, 2), F.pf >= 1.2),
        "CAGR > VN-Index và E1VFVN30": ("%.1f%% vs %.1f%% / %.1f%%" % (F.cagr * 100, F.VNINDEX_cagr * 100, F.E1VFVN30_cagr * 100),
                                       F.cagr > F.VNINDEX_cagr and F.cagr > F.E1VFVN30_cagr),
        "Sharpe hơn ngẫu nhiên >= 2 SD": (round(F.get("z_sharpe", np.nan), 2), F.get("z_sharpe", -np.inf) >= 2),
        "số tham số <= lệnh/50": ("%d vs %.1f" % (n_params, F.n / 50), n_params <= F.n / 50),
    }
    if etf_core is not None or dsr is not None or pbo is not None:
        if etf_core is None:
            g["vượt lõi ETF cả CAGR lẫn Sharpe"] = ("thiếu etf_core", False)
        else:
            g["vượt lõi ETF cả CAGR lẫn Sharpe"] = (
                "CAGR %.1f%% vs %.1f%% / Sharpe %.2f vs %.2f" % (F.cagr * 100, etf_core["cagr"] * 100, F.sharpe, etf_core["sharpe"]),
                bool(F.cagr > etf_core["cagr"] and F.sharpe > etf_core["sharpe"]))
        g["DSR >= 0,95"] = (None, False) if dsr is None else (round(float(dsr), 3), bool(dsr >= DSR_MIN))
        g["PBO <= 0,2"] = (None, False) if pbo is None else (round(float(pbo), 3), bool(pbo <= PBO_MAX))
    return g


def causal_check(make, panel, dates):
    """Chống look-ahead cho tín hiệu: tín hiệu ngày t tính trên dữ liệu cắt tại t phải bằng tín hiệu
    ngày t tính trên toàn bộ dữ liệu. Trả danh sách ngày vi phạm (rỗng = đạt)."""
    full = make().prepare(panel)
    bad = []
    for t in dates:
        cut = make().prepare(panel.truncate(t))
        a, b = full.loc[t], cut.loc[t].reindex(full.columns)
        if not np.allclose(a.values.astype(float), b.values.astype(float), equal_nan=True, rtol=1e-9, atol=1e-9):
            bad.append(t)
    return bad


def walk_forward(panel, make, grid, cfg=None, key="sharpe"):
    """Chọn tham số tốt nhất trên nửa A, kiểm trên B. make(params)->Strategy."""
    res = []
    for p in grid:
        mA = metrics(*run(panel, make(p), "2018-01-01", "2020-12-31", cfg)[:2])
        mB = metrics(*run(panel, make(p), "2021-01-01", "2023-12-31", cfg)[:2])
        res.append(dict(params=p, A=mA[key], B=mB[key], nA=mA["n"], nB=mB["n"]))
    R = pd.DataFrame(res)
    best = R.loc[R.A.idxmax()]
    return R, best, R.A.rank().corr(R.B.rank())


def deflated_sharpe(nav, n_trials, sr_trials_annual):
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014). nav: chuỗi NAV ngày của chiến lược được chọn;
    n_trials: số cấu hình đã thử; sr_trials_annual: Sharpe năm hoá của các lần thử (ước lượng phương sai).
    Trả (DSR = P[SR thật > SR kỳ vọng tối đa do may mắn], SR0 năm hoá)."""
    r = nav.pct_change().dropna()
    T = len(r)
    sr = r.mean() / r.std()
    var = np.var(np.asarray(sr_trials_annual, float) / np.sqrt(252), ddof=1)
    g = 0.5772156649
    sr0 = np.sqrt(var) * ((1 - g) * norm.ppf(1 - 1 / n_trials) + g * norm.ppf(1 - 1 / (n_trials * np.e))) \
        if n_trials > 1 else 0.0
    sk, ku = r.skew(), r.kurt() + 3
    z = (sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    return norm.cdf(z), sr0 * np.sqrt(252)
