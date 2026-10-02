"""Test engine T37. Chạy: .venv/bin/python -m pytest research/engine/test_engine.py -q"""
import sys
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
import pytest
from engine.data import Panel, load, universe_backtest_config, END
from engine.core import run, metrics, Config
from engine.strategies import Stage1, TrendETF
from engine.bench import causal_check

CFG = Config()


@pytest.fixture(scope="module")
def P():
    return Panel(universe_backtest_config())


@pytest.fixture(scope="module")
def full_run(P):
    return run(P, Stage1(), "2018-01-01", "2023-12-31", CFG)


def sample_dates(P, n=8):
    days = P.idx[(P.idx >= "2018-01-01")]
    return list(days[np.linspace(0, len(days) - 1, n).astype(int)])


def test_holdout_locked(P):
    assert P.idx.max() <= pd.Timestamp(END)
    with pytest.raises(ValueError):
        load("VNINDEX", end="2024-01-02")


def test_stage1_signal_causal(P):
    assert causal_check(Stage1, P, sample_dates(P)) == []


def test_trend_signal_causal():
    Q = Panel(["E1VFVN30", "VN30"])
    assert causal_check(lambda: TrendETF(calendar=Q.idx), Q, sample_dates(Q)) == []


def test_detector_catches_lookahead(P):
    class Cheat(Stage1):
        def prepare(self, panel):
            self.S = (panel.close.shift(-1) > panel.close).astype(float)  # dùng giá ngày mai
            return self.S
    assert len(causal_check(Cheat, P, sample_dates(P))) > 0


def test_signal_t_fills_t_plus_1_open(P, full_run):
    _, tr, _ = full_run
    idx = P.idx
    nxt = [idx[idx.get_loc(t) + 1] for t in tr.sig_buy]
    assert (tr.buy.values == np.array(nxt, dtype="datetime64[ns]")).all()
    o = np.array([P.open.at[d, s] for d, s in zip(tr.buy, tr.sym)])
    assert np.allclose(tr.buy_px, o * (1 + CFG.slip))
    # bán theo tín hiệu (timeout) khớp mở cửa phiên có giá đầu tiên sau ngày tín hiệu
    to = tr[tr.why == "timeout"]
    assert len(to) and (to.sell > to.sig_sell).all()
    o = np.array([P.open.at[d, s] for d, s in zip(to.sell, to.sym)])
    assert np.allclose(to.sell_px, o * (1 - CFG.slip))


def test_intraday_exit_inside_bar(P, full_run):
    _, tr, _ = full_run
    it = tr[tr.why.isin(["stop", "stop_gap", "target", "target_gap"])]
    raw = it.sell_px / (1 - CFG.slip)
    lo = np.array([P.low.at[d, s] for d, s in zip(it.sell, it.sym)])
    hi = np.array([P.high.at[d, s] for d, s in zip(it.sell, it.sym)])
    assert ((raw >= lo - 1e-9) & (raw <= hi + 1e-9)).all()


def test_t_plus_2_and_lots(P, full_run):
    _, tr, _ = full_run
    assert (tr.k_sell - tr.k_buy >= 2).all()
    assert (tr.sh % 100 == 0).all() and (tr.sh > 0).all()


def test_fees_and_tax(full_run):
    _, tr, _ = full_run
    exp = tr.sh * tr.sell_px * 1000 * (1 - CFG.fee - CFG.tax) - tr.sh * tr.buy_px * 1000 * (1 + CFG.fee)
    assert np.allclose(tr.net, exp)


def test_reproduces_t34(P):
    m = metrics(*run(P, Stage1(), "2018-01-01", "2023-12-31", Config(slip=0.0, stop_mode="close"))[:2])
    assert m["n"] == 990 and abs(m["sharpe"] - 0.09) < 0.01 and abs(m["cagr"] + 0.012) < 0.002


# ---------------- T38 ----------------
from engine.candidates import Breakout, Momentum, Reversion, month_gate


@pytest.fixture(scope="module")
def PG():
    return Panel(universe_backtest_config() + ["VN30"])


@pytest.mark.parametrize("make", [lambda: Breakout(50, 3.0), lambda: Momentum(120, 3.0), lambda: Reversion(10, 3.0)])
def test_candidates_causal(PG, make):
    assert causal_check(make, PG, sample_dates(PG)) == []


def test_gate_uses_previous_month(PG):
    g = month_gate(PG)
    t = pd.Timestamp("2020-04-15")
    g2 = month_gate(PG.truncate(pd.Timestamp("2020-04-01")))
    assert g2.iloc[-1] == g.loc[:"2020-04-01"].iloc[-1] == g.loc[t]      # cả tháng 4 dùng tín hiệu cuối tháng 3


def test_price_limits(PG):
    cfg = Config(limits=True)
    nav, tr, ex = run(PG, Reversion(20, 2.0), "2018-01-01", "2020-12-31", cfg)
    ref = PG.close_ff.shift(1)
    lim = PG.limit
    # không lệnh mua nào khớp khi mở cửa ở giá trần
    o = np.array([PG.open.at[d, s] / ref.at[d, s] - 1 for d, s in zip(tr.buy, tr.sym)])
    assert (o < np.array([lim[s] for s in tr.sym]) - cfg.lim_tol).all()
    # không lệnh bán nào khớp trong phiên nằm sàn cả phiên
    h = np.array([PG.high.at[d, s] / ref.at[d, s] - 1 for d, s in zip(tr.sell, tr.sym)])
    assert (h > -np.array([lim[s] for s in tr.sym]) + cfg.lim_tol).all()
    assert (tr.k_sell - tr.k_buy >= 2).all()


def test_ceiling_open_blocks_buy(PG):
    """Chiến lược ép mua đúng mã mở cửa trần phiên sau -> lệnh phải bị hủy."""
    from engine.core import Strategy
    ref = PG.close_ff.shift(1)
    o = (PG.open / ref - 1).loc["2018-01-01":"2020-12-31"]
    hit = o.ge(PG.limit - 0.005, axis=1).stack()
    d, s = hit[hit].index[0]
    k = PG.idx.get_loc(d)

    class Force(Strategy):
        def prepare(self, panel):
            return None

        def candidates(self, kk, dd, held):
            return [(s, {})] if kk == k - 1 else []

    _, tr, ex = run(PG, Force(), PG.idx[k - 5], PG.idx[k + 5], Config(limits=True))
    assert len(tr) == 0 and ex["blocked_buy"] == 1 and ex["open_positions"] == 0
    _, tr, ex = run(PG, Force(), PG.idx[k - 5], PG.idx[k + 5], Config(limits=False))
    assert ex["open_positions"] == 1


# ---- T42: universe theo thời điểm, hủy niêm yết, cổng holdout ----
from engine.data import universe_all, pit_universe, holdout_approved, NON_STOCK
from engine.candidates import Momentum5


@pytest.fixture(scope="module")
def PP():
    return Panel(universe_all() + ["VN30"], start="2016-01-01")


def test_pit_universe_causal(PP):
    syms = [s for s in PP.symbols if s not in NON_STOCK]
    U = pit_universe(PP, syms)
    assert (U.loc["2018-01-01":].sum(axis=1) == 50).all()
    for t in ("2018-03-14", "2020-06-30", "2022-11-01"):
        Ut = pit_universe(PP.truncate(pd.Timestamp(t)), syms)
        assert Ut.equals(U.loc[:t])


@pytest.mark.parametrize("make", [lambda: Momentum5(60, 2.0).with_pit(), lambda: Breakout(50, 2.0).with_pit()])
def test_pit_candidates_causal(PP, make):
    assert causal_check(make, PP, list(PP.idx[PP.idx >= "2018-01-01"][::250])) == []


def test_pit_trades_only_members(PP):
    s = Momentum5(60, 2.0).with_pit()
    _, tr, _ = run(PP, s, "2018-01-01", "2019-12-31", Config(limits=True, max_pos=10, pos_pct=0.1))
    assert len(tr) > 0 and all(s.U.at[d, x] for d, x in zip(tr.sig_buy, tr.sym))


def test_delist_haircut(PP):
    """Giữ ROS (ngừng GD 2022-08) tới phiên cuối -> thanh lý ở đóng cửa phiên cuối x (1-h)."""
    from engine.core import Strategy
    last = PP.close["ROS"].last_valid_index()
    k = PP.idx.get_loc(last)

    class Force(Strategy):
        def prepare(self, panel):
            return None

        def candidates(self, kk, dd, held):
            return [("ROS", {})] if kk == k - 30 else []

    _, tr, ex = run(PP, Force(), PP.idx[k - 40], PP.idx[k + 40], Config(delist_haircut=0.3))
    assert len(tr) == 1 and tr.why[0] == "delist" and tr.sell[0] == last
    assert np.isclose(tr.sell_px[0], PP.close.at[last, "ROS"] * 0.7)
    _, tr, ex = run(PP, Force(), PP.idx[k - 40], PP.idx[k + 40], Config())
    assert len(tr) == 0 and ex["open_positions"] == 1          # mặc định: không đổi hành vi cũ


def test_holdout_needs_approval():
    assert not holdout_approved()
    with pytest.raises(ValueError):
        load("VNINDEX", end="2024-06-30", allow_holdout=True)
    assert load("VNINDEX").index.max() <= pd.Timestamp(END)


# ---- T46: ghép túi, vol-scaling, PBO/CSCV, lọc trần ----
from engine.portfolio import combine, vol_overlay, cscv_pbo
from engine.candidates import BreakoutNoLimitUp


def _nav(seed, n=600, mu=0.0004, sd=0.012):
    idx = pd.bdate_range("2018-01-01", periods=n)
    r = np.random.default_rng(seed).normal(mu, sd, n)
    r[0] = 0
    return pd.Series(1e8 * np.cumprod(1 + r), index=idx)


def test_combine_limits():
    a, b = _nav(1), _nav(2)
    assert np.allclose(combine(a, b, 1.0)[0], a)                  # w=1 -> đúng túi a
    assert np.allclose(combine(a, a, 0.5)[0], a)                  # 2 túi giống nhau -> không phát sinh chuyển
    c, wa = combine(a, b, 0.7)
    q = a.index.to_period("Q")
    qe = [i for i in range(len(a) - 2) if q[i] != q[i + 1]]       # phiên cuối quý (trừ cuối chuỗi)
    assert qe and all(np.isclose(wa.iloc[i], 0.7) for i in qe)    # sau cân bằng = 0,7
    assert not np.isclose(wa.iloc[qe[0] - 1], 0.7)                # giữa quý thì trôi


def test_vol_overlay_causal_and_capped():
    a = _nav(3, sd=0.03)                                          # vol ~48%/năm -> e ~ 0,31
    v, e = vol_overlay(a)
    assert (e <= 1).all() and (e.iloc[:61] == 1).all() and e.iloc[-1] < 0.5
    for t in (200, 350, 480):
        vt, et = vol_overlay(a.iloc[:t])
        assert np.allclose(vt, v.iloc[:t]) and np.allclose(et, e.iloc[:t])   # không dùng dữ liệu sau t
    low = _nav(4, sd=0.002)
    assert np.allclose(vol_overlay(low)[0], low)                  # vol thấp -> e = 1, không phí


def test_cscv_pbo():
    rng = np.random.default_rng(0)
    noise = pd.DataFrame(rng.normal(0, 0.01, (1600, 8)))
    assert 0.3 < cscv_pbo(noise)["pbo"] < 0.7                     # toàn nhiễu -> PBO ~ 0,5
    strong = noise.copy()
    strong[0] += 0.003                                            # 1 cấu hình trội thật
    assert cscv_pbo(strong)["pbo"] < 0.05


def test_nolimitup_causal_and_filters(PP):
    make = lambda: BreakoutNoLimitUp(50, 2.0).with_pit()
    assert causal_check(make, PP, list(PP.idx[PP.idx >= "2018-01-01"][::250])) == []
    s, b = make(), Breakout(50, 2.0).with_pit()
    s.prepare(PP)
    b.prepare(PP)
    jumped = np.isneginf(s.S.values) & np.isfinite(b.S.values)
    assert jumped.sum() > 0 and (np.isnan(s.S.values) == np.isnan(b.S.values)).all()


def _fake_E(cagr=0.10, sharpe=0.8):
    row = dict(n=300, cagr=cagr, sharpe=sharpe, maxdd=-0.2, pf=1.5, VNINDEX_cagr=0.02, VNINDEX_maxdd=-0.45,
               E1VFVN30_cagr=0.03, z_sharpe=3.0)
    return pd.DataFrame([row, dict(row), dict(row)], index=["A", "B", "FULL"])


def test_gate_a_protocol_criteria():
    from engine.bench import gate_a, protocol_config
    E = _fake_E()
    assert len(gate_a(E, 5)) == 8                                     # không truyền gì = 8 tiêu chí cũ
    etf = dict(cagr=0.074, sharpe=0.56)
    g = gate_a(E, 5, etf_core=etf, dsr=0.96, pbo=0.1)
    assert len(g) == 11 and all(ok for _, ok in g.values())
    assert not gate_a(E, 5, etf_core=etf, dsr=0.94, pbo=0.1)["DSR >= 0,95"][1]
    assert not gate_a(E, 5, etf_core=etf, dsr=0.96, pbo=0.21)["PBO <= 0,2"][1]
    assert not gate_a(_fake_E(sharpe=0.5), 5, etf_core=etf, dsr=1, pbo=0)["vượt lõi ETF cả CAGR lẫn Sharpe"][1]
    assert not gate_a(_fake_E(cagr=0.07), 5, etf_core=etf, dsr=1, pbo=0)["vượt lõi ETF cả CAGR lẫn Sharpe"][1]
    assert not gate_a(E, 5, dsr=0.99)["PBO <= 0,2"][1]                # thiếu = trượt
    c = protocol_config()
    assert abs(2 * c.fee + c.tax + 2 * c.slip - 0.005) < 1e-12
