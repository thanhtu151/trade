"""T128: H1 (bản đồ tỉ lệ thắng <-> kỳ vọng theo luật thoát) và H2 (mua nhịp chỉnh khi VN30 ON, lọc chặt).
CHỈ dữ liệu <= 2023-12-31 (engine.data.END khóa holdout). Đăng ký trước: research/trial_log.md mục T128.
  .venv/bin/python research/t128/run_t128.py time      # đo tốc độ, không tính là lần thử
  .venv/bin/python research/t128/run_t128.py h1        # lưới 3 tín hiệu vào x 48 luật thoát, mỗi cấu hình 1 lần
  .venv/bin/python research/t128/run_t128.py h2        # 12 cấu hình pullback
  .venv/bin/python research/t128/run_t128.py causal    # kiểm look-ahead tín hiệu
Mọi cấu hình: universe PIT top-50 (gồm mã hủy niêm yết, haircut 30%), 10 vị thế x 10%, phí khứ hồi 0,5%
(protocol_config), T+2, khóa trần/sàn, tín hiệu đóng cửa t -> khớp mở cửa t+1."""
import itertools
import json
import os
import sys
import time
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import numpy as np
import pandas as pd
from engine.data import Panel, universe_all, pit_universe
from engine.core import run, metrics, Strategy, RandomEntry
from engine.strategies import Stage1, rsi
from engine.candidates import Breakout, Reversion, Gated, NON_TRADABLE
from engine.bench import protocol_config, causal_check, index_bench, zgap

OUT = "/Users/drone/Downloads/tradeclone/research/t128/"
START = "2016-01-01"
PERIODS = [("A", "2018-01-01", "2020-12-31"), ("B", "2021-01-01", "2023-12-31"), ("FULL", "2018-01-01", "2023-12-31")]
YEARS = dict(A=3.0, B=3.0, FULL=6.0)
TGT, STOP, HOLD = (0.5, 1.0, 1.5, 2.0), (1.0, 2.0, 3.0, 4.0), (5, 10, 15)
MIN_N_A = 100                      # cấu hình có < 100 lệnh ở nửa A không được chọn
PASS = dict(win=0.80, pf=1.3, maxdd=-0.15)
pd.set_option("display.width", 250)


def cfg():
    return protocol_config(limits=True, max_pos=10, pos_pct=0.10, delist_haircut=0.30)


def panel():
    return Panel(universe_all(True) + ["VN30"], start=START)


class Stage1PIT(Stage1):
    """Stage1 (score>=3) chỉ vào lệnh trong universe PIT top-50; không có cổng thị trường (như T34/T37)."""

    def prepare(self, panel):
        super().prepare(panel)
        syms = [s for s in panel.symbols if s not in NON_TRADABLE]
        self.S = self.S.where(pit_universe(panel, syms).reindex(columns=self.S.columns, fill_value=False))
        return self.S


class FixedExit(Strategy):
    """Giữ nguyên tín hiệu vào của `base`; thoát: stop = giá khớp - s*ATR14, target = giá khớp + t*ATR14
    (xét trong phiên, từ T+2), quá `hold` phiên -> bán mở cửa phiên sau. ATR lấy tại phiên tín hiệu."""

    def __init__(self, base, t, s, hold):
        self.base, self.t, self.s, self.hold = base, t, s, hold
        self.name = "%s|tgt%gATR,stop%gATR,hold%d" % (base.name, t, s, hold)
        self.n_params = base.n_params + 3

    def prepare(self, panel):
        return self.base.prepare(panel)

    def eligible(self, k, d):
        return self.base.eligible(k, d)

    def meta(self, k, s):
        return self.base.meta(k, s)

    def candidates(self, k, d, held):
        return self.base.candidates(k, d, held)

    def levels(self, sym, fill_px, meta):
        return fill_px - self.s * meta["atr"], fill_px + self.t * meta["atr"]

    def exit_signal(self, k, d, sym, pos):
        return "timeout" if k - pos["k"] >= self.hold else None


class Pullback(Gated):
    """H2: RSI(2) < x VÀ đóng cửa > SMA200 của chính mã, cổng VN30 >= MA10 tháng (Gated), PIT top-50.
    Thoát: mode 'sma5' = đóng cửa > SMA5; 'up' = đóng cửa > đóng cửa phiên trước; hoặc giữ đủ 10 phiên;
    stop cứng m_stop x ATR14 trong phiên."""
    school = "đảo chiều"

    def __init__(self, x=10, m_stop=3.0, mode="sma5", max_hold=10):
        super().__init__(m_stop)
        self.x, self.mode, self.max_hold = x, mode, max_hold
        self.name = "pullback(RSI2<%g,>SMA200,stop=%gATR,exit=%s,hold%d)" % (x, m_stop, mode, max_hold)
        self.n_params = 7          # RSI len, X, SMA200, m_stop, luật thoát, max_hold, cổng

    def signals(self, d):
        c = d.close
        r2, ma = rsi(c, 2), c.rolling(200).mean()
        s = (-r2).where((r2 < self.x) & (c > ma), -np.inf).where(r2.notna() & ma.notna())
        if self.mode == "sma5":
            m5 = c.rolling(5).mean()
            out = (c > m5).astype(float).where(m5.notna())
        else:
            out = (c > c.shift(1)).astype(float).where(c.shift(1).notna())
        return s, out

    def post_prepare(self):
        self.Up = self.X[0].values

    def exit_signal(self, k, d, sym, pos):
        if self.Up[k, self.col[sym]] == 1:
            return "revert"
        return "timeout" if k - pos["k"] >= self.max_hold else None


BASES = {"stage1": lambda: Stage1PIT(thr=3.0),
         "breakout": lambda: Breakout(n=50, m_stop=2.0).with_pit(),          # tham số chốt T38 chosen2
         "reversion": lambda: Reversion(x=20, m_stop=3.0).with_pit()}         # tham số chốt T38 chosen
H2_GRID = [dict(x=x, mode=m, m_stop=s) for x, m, s in itertools.product((15, 10, 5), ("sma5", "up"), (3.0, 5.0))]


def wilson_lo(w, n, z=1.96):
    if not n:
        return np.nan
    p = w / n
    return (p + z * z / (2 * n) - z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def row(nav, tr, per):
    m = metrics(nav, tr)
    n = m["n"]
    w = int((tr.net > 0).sum()) if n else 0
    m.update(period=per, wins=w, win_lo95=wilson_lo(w, n), per_year=n / YEARS[per],
             avg_win=tr[tr.net > 0].net_ret.mean() if n else np.nan,
             avg_loss=tr[tr.net <= 0].net_ret.mean() if n else np.nan)
    return m


def passes(m):
    return bool(m["win"] >= PASS["win"] and m["exp_pct"] > 0 and m["pf"] > PASS["pf"] and m["maxdd"] >= PASS["maxdd"])


def choose(R):
    """Luật chọn đăng ký trước, CHỈ dùng nửa A: trong các cấu hình có exp_pct > 0 và n >= MIN_N_A,
    nếu có cấu hình win >= 80% -> lấy exp_pct cao nhất trong số đó; nếu không -> lấy win cao nhất."""
    a = R[(R.period == "A") & (R.exp_pct > 0) & (R.n >= MIN_N_A)]
    if a.empty:
        return None
    hi = a[a.win >= PASS["win"]]
    return (hi.sort_values("exp_pct", ascending=False) if len(hi) else a.sort_values("win", ascending=False)).iloc[0].key


def lock(key):
    f = OUT + "runs.json"
    done = json.load(open(f)) if os.path.exists(f) else {}
    if key in done:
        return False
    done[key] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(done, open(f, "w"), indent=1)
    return True


def sweep(P, tag, items):
    """items: [(key, fam, strat, prepared_base)] -> chạy A/B/FULL mỗi cấu hình đúng 1 lần, lưu summary."""
    rows, c = [], cfg()
    for key, fam, s in items:
        if not lock(key):
            print("ĐÃ chạy", key, "-> bỏ qua")
            continue
        for per, a, b in PERIODS:
            nav, tr, _ = run(P, s, a, b, c, prepared=True)
            m = row(nav, tr, per)
            m.update(key=key, fam=fam, name=s.name)
            rows.append(m)
    R = pd.DataFrame(rows)
    f = OUT + "summary_%s.csv" % tag
    if os.path.exists(f):
        R = pd.concat([pd.read_csv(f), R])
    R.to_csv(f, index=False)
    return R


def confirm(P, tag, key, fam, s, seeds=50):
    """Cấu hình được chọn trên A: lưu lệnh, lý do thoát, và baseline ngẫu nhiên 50 seed cùng luật thoát."""
    c, out = cfg(), {}
    for per, a, b in PERIODS:
        nav, tr, extra = run(P, s, a, b, c, prepared=True)
        m = row(nav, tr, per)
        rnd = []
        for i in range(seeds):
            rn, rt, _ = run(P, RandomEntry(s, i), a, b, c, prepared=True)
            rnd.append(row(rn, rt, per))
        Rn = pd.DataFrame(rnd)
        m.update(rand_win=Rn.win.mean(), rand_exp=Rn.exp_pct.mean(), rand_sharpe=Rn.sharpe.mean(),
                 z_win=zgap(m["win"], Rn.win), z_exp=zgap(m["exp_pct"], Rn.exp_pct),
                 z_sharpe=zgap(m["sharpe"], Rn.sharpe), blocked_buy=extra["blocked_buy"],
                 blocked_sell=extra["blocked_sell"], passes=passes(m))
        if per == "FULL":
            tr.to_csv(OUT + "trades_%s_%s.csv" % (tag, fam), index=False)
            m["why"] = tr.groupby("why").net_ret.agg(["count", "mean"]).round(4).to_dict("index")
            m["worst"] = float(tr.net_ret.min())
        out[per] = {k: (v if isinstance(v, (dict, str, bool)) else float(v)) for k, v in m.items()}
    json.dump(dict(key=key, name=s.name, res=out), open(OUT + "chosen_%s_%s.json" % (tag, fam), "w"), indent=1,
              ensure_ascii=False, default=str)
    return out


COLS = ["key", "n", "per_year", "win", "win_lo95", "exp_pct", "pf", "maxdd", "cagr", "sharpe", "avg_win", "avg_loss", "hold"]


def show(R, out):
    for per in ("A", "B", "FULL"):
        r = R[R.period == per]
        ok = r[r.exp_pct > 0]
        print("\n[%s] cấu hình: %d | exp>0: %d | win>=80%%: %d | win>=80%% & exp>0: %d | đạt đủ 4 tiêu chí: %d" % (
            per, len(r), len(ok), (r.win >= 0.8).sum(), ((r.win >= 0.8) & (r.exp_pct > 0)).sum(),
            sum(passes(x) for _, x in r.iterrows())))
        print("  top win có exp>0:\n", ok.sort_values("win", ascending=False)[COLS].head(6).round(4).to_string(index=False))
        print("  top win bất kể exp:\n", r.sort_values("win", ascending=False)[COLS].head(4).round(4).to_string(index=False))
    for fam, o in out.items():
        print("\n== chọn trên A — %s: %s" % (fam, o and o["name"]))
        if o:
            for per in ("A", "B", "FULL"):
                m = o["res"][per]
                print("  %s n=%d (%.0f/năm) win=%.1f%% [cận dưới %.1f%%] exp=%.2f%% pf=%.2f dd=%.1f%% cagr=%.1f%% sharpe=%.2f | "
                      "ngẫu nhiên win=%.1f%% exp=%.2f%% z_win=%.1f z_exp=%.1f | đạt=%s" % (
                          per, m["n"], m["per_year"], m["win"] * 100, m["win_lo95"] * 100, m["exp_pct"] * 100, m["pf"],
                          m["maxdd"] * 100, m["cagr"] * 100, m["sharpe"], m["rand_win"] * 100, m["rand_exp"] * 100,
                          m["z_win"], m["z_exp"], m["passes"]))
            print("  thoát FULL:", o["res"]["FULL"]["why"], "| lệnh tệ nhất %.1f%%" % (o["res"]["FULL"]["worst"] * 100))


def h1(P):
    items, base = [], {}
    for fam, mk in BASES.items():
        base[fam] = mk()
        base[fam].prepare(P)
        for t, s, h in itertools.product(TGT, STOP, HOLD):
            items.append(("h1_%s_t%g_s%g_h%d" % (fam, t, s, h), fam, FixedExit(base[fam], t, s, h)))
    R = sweep(P, "h1", items)
    by = {k: s for k, _, s in items}
    out = {}
    for fam in BASES:
        key = choose(R[R.fam == fam])
        out[fam] = key and dict(name=by[key].name, res=confirm(P, "h1", key, fam, by[key]))
    show(R, out)


def h2(P):
    items = []
    for g in H2_GRID:
        s = Pullback(**g).with_pit()
        s.prepare(P)
        items.append(("h2_x%g_%s_s%g" % (g["x"], g["mode"], g["m_stop"]), "pullback", s))
    R = sweep(P, "h2", items)
    by = {k: s for k, _, s in items}
    key = choose(R)
    out = {"pullback": key and dict(name=by[key].name, res=confirm(P, "h2", key, "pullback", by[key]))}
    show(R, out)


def causal(P):
    dates = list(P.idx[P.idx >= "2018-01-01"][100::300])
    mk = dict(BASES, pullback_sma5=lambda: Pullback(x=10, mode="sma5").with_pit(),
              pullback_up=lambda: Pullback(x=10, mode="up").with_pit())
    res = {k: [str(t.date()) for t in causal_check(f, P, dates)] for k, f in mk.items()}
    json.dump(dict(dates=[str(t.date()) for t in dates], violations=res), open(OUT + "causal.json", "w"), indent=1)
    print("ngày kiểm:", len(dates), "| vi phạm:", {k: len(v) for k, v in res.items()})


if __name__ == "__main__":
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    P = panel()
    print("panel %d mã; %s -> %s (%.0fs)" % (len(P.symbols), P.idx[0].date(), P.idx[-1].date(), time.time() - t0))
    assert P.idx[-1] <= pd.Timestamp("2023-12-31")
    mode = sys.argv[1]
    if mode == "time":
        b = BASES["reversion"]()
        t1 = time.time(); b.prepare(P); t2 = time.time()
        run(P, FixedExit(b, 1, 2, 10), "2018-01-01", "2020-12-31", cfg(), prepared=True)
        print("prepare %.0fs, 1 run A %.1fs" % (t2 - t1, time.time() - t2))
    elif mode == "h1":
        h1(P)
        print(json.dumps(index_bench("2018-01-01", "2023-12-31"), default=float))
    elif mode == "h2":
        h2(P)
    elif mode == "causal":
        causal(P)
    print("(%.0fs)" % (time.time() - t0))
