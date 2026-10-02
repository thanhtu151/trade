"""Chiến lược dùng với engine: Stage1 (tái tạo T34), xu hướng ETF 10 tháng (etf_core.py), mua-giữ."""
import numpy as np
import pandas as pd
from engine.core import Strategy


def rsi(close, n):
    delta = close.diff()
    g = delta.clip(lower=0).rolling(n).mean()
    l = (-delta.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))


def stage1_score(d):
    """Điểm Stage1 như auto_trader.py:299-343 (news=0) — bản sao research/t34/wf_stage1.py."""
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


def per_symbol(panel, fn):
    """Áp fn lên chuỗi từng mã chỉ gồm phiên có giao dịch (như T34), rồi đưa về lịch chung."""
    outs = {}
    for s in panel.symbols:
        d = pd.DataFrame({f: getattr(panel, f)[s] for f in ("open", "high", "low", "close", "volume")})
        d = d.dropna(subset=["close"])
        outs[s] = [x.reindex(panel.idx) for x in fn(d)]
    n_out = len(next(iter(outs.values())))
    return [pd.DataFrame({s: o[i] for s, o in outs.items()}, index=panel.idx) for i in range(n_out)]


class Stage1(Strategy):
    """Luật Stage1 + cổng score>=thr + stop m_stop*ATR, target m_tgt*ATR, tối đa max_hold phiên."""

    def __init__(self, thr=3.0, m_stop=1.0, m_tgt=2.0, max_hold=15):
        self.thr, self.m_stop, self.m_tgt, self.max_hold = thr, m_stop, m_tgt, max_hold
        self.name = "stage1(thr=%g,stop=%gATR,tgt=%gATR,hold=%d)" % (thr, m_stop, m_tgt, max_hold)
        self.n_params = 4 + 7   # 4 tham số giao dịch + 7 trọng số điểm Stage1 (đặt tay)

    def prepare(self, panel):
        self.S, self.A = per_symbol(panel, stage1_score)
        return self.S

    def eligible(self, k, d):
        return list(self.S.iloc[k].dropna().index)

    def meta(self, k, s):
        return dict(atr=self.A.iat[k, self.A.columns.get_loc(s)])

    def candidates(self, k, d, held):
        row = self.S.iloc[k].dropna()
        row = row[[s for s in row.index if s not in held]]
        return [(s, self.meta(k, s)) for s in row[row >= self.thr].sort_values(ascending=False).index]

    def levels(self, sym, fill_px, meta):
        return fill_px - self.m_stop * meta["atr"], fill_px + self.m_tgt * meta["atr"]

    def exit_signal(self, k, d, sym, pos):
        return "timeout" if k - pos["k"] >= self.max_hold else None


class TrendETF(Strategy):
    """etf_core.py: phiên cuối tháng, giữ ETF nếu VN30 đóng cửa >= TB 10 giá đóng cửa cuối tháng
    gần nhất (tính cả tháng này), ngược lại giữ tiền. Khớp mở cửa phiên sau.
    Lịch giao dịch (ngày nghỉ) coi là thông tin biết trước; giá thì không."""

    def __init__(self, etf="E1VFVN30", signal="VN30", months=10, calendar=None):
        self.etf, self.signal, self.months, self.calendar = etf, signal, months, calendar
        self.name, self.n_params = "trend_ma%dm(%s|%s)" % (months, etf, signal), 1

    def prepare(self, panel):
        cal = self.calendar if self.calendar is not None else panel.idx
        nxt = pd.Series(cal[1:].append(pd.DatetimeIndex([pd.NaT])), index=cal)
        c = panel.close[self.signal].dropna()
        me = c[[nxt[t] is pd.NaT or nxt[t].month != t.month for t in c.index]]
        ma = me.rolling(self.months).mean()
        on = (me >= ma).where(ma.notna())
        self.sig = pd.DataFrame({self.etf: on.reindex(panel.idx)}).astype(float)  # chỉ có giá trị ở cuối tháng
        self.want = self.sig[self.etf].ffill().fillna(0).values > 0
        return self.sig

    def eligible(self, k, d):
        return [self.etf]

    def meta(self, k, s):
        return {}

    def candidates(self, k, d, held):
        return [(self.etf, {})] if self.want[k] and self.etf not in held else []

    def weight(self, sym, meta):
        return 1.0

    def exit_signal(self, k, d, sym, pos):
        return "trend_off" if not self.want[k] else None


class BuyHold(Strategy):
    def __init__(self, sym="E1VFVN30"):
        self.sym, self.name = sym, "buyhold(%s)" % sym

    def prepare(self, panel):
        return None

    def eligible(self, k, d):
        return [self.sym]

    def candidates(self, k, d, held):
        return [(self.sym, {})] if self.sym not in held else []

    def weight(self, sym, meta):
        return 1.0
