"""T38: chiến lược một-trường-phái thay Stage1. Mọi chiến lược:
- cổng thị trường: chỉ mở vị thế mới khi VN30 (đóng cửa cuối tháng TRƯỚC) >= TB 10 giá đóng cửa cuối tháng;
  cổng tắt không đóng vị thế đang có (vị thế thoát theo luật riêng);
- stop cứng trong phiên = giá khớp - m_stop x ATR14 (m_stop >= 2).
Trường phái:
- Breakout (xu hướng): đóng cửa lập đỉnh N phiên; thoát khi đóng cửa < đỉnh đóng cửa từ lúc mua - m_stop x ATR.
- Momentum (xu hướng): top 5 theo lợi suất L phiên (bỏ 5 phiên gần nhất), > 0; thoát khi rơi khỏi top 10.
- Reversion (đảo chiều): RSI(2) < X; thoát khi đóng cửa > SMA5 hoặc giữ đủ 10 phiên.
Panel phải chứa VN30 (chỉ dùng làm cổng, không giao dịch)."""
import numpy as np
import pandas as pd
from engine.core import Strategy
from engine.strategies import per_symbol, rsi
from engine.data import pit_universe

NON_TRADABLE = ("VN30", "VNINDEX", "E1VFVN30")


def atr14(d):
    c = d.close
    tr = pd.concat([d.high - d.low, (d.high - c.shift()).abs(), (d.low - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def month_gate(panel, sym="VN30", months=10):
    """Cổng ngày t = [đóng cửa cuối tháng trước >= TB `months` đóng cửa cuối tháng tính đến tháng trước].
    Chỉ dùng tháng đã hoàn tất -> nhân quả, không cần biết trước lịch."""
    c = panel.close[sym].dropna()
    per = c.index.to_period("M")
    me = c.groupby(per).last()
    on = (me >= me.rolling(months).mean()).where(me.rolling(months).mean().notna())
    g = pd.Series((per - 1).map(on), index=c.index).astype(float)
    return g.reindex(panel.idx).ffill()


class Gated(Strategy):
    school = ""

    pit = False                    # T42: True -> chỉ vào lệnh/xếp hạng trong universe theo thời điểm

    def __init__(self, m_stop=2.0):
        assert m_stop >= 2
        self.m_stop = m_stop

    def with_pit(self):
        self.pit = True
        self.name += "[PIT top50]"
        return self

    def signals(self, d):
        """-> (entry_score, *phụ) theo từng mã; entry_score NaN = không đủ dữ liệu, -inf = không vào."""
        raise NotImplementedError

    def prepare(self, panel):
        self.syms = [s for s in panel.symbols if s not in NON_TRADABLE]
        sub = object.__new__(type(panel))
        sub.__dict__.update(panel.__dict__)
        sub.symbols = self.syms
        outs = per_symbol(sub, lambda d: (atr14(d),) + tuple(self.signals(d)))
        self.A, self.S, self.X = outs[0], outs[1], outs[2:]
        if self.pit:
            self.U = pit_universe(panel, self.syms)
            self.S = self.S.where(self.U)
        self.C = panel.close_ff[self.syms]
        self.Cv, self.col = self.C.values, {s: i for i, s in enumerate(self.syms)}
        self.post_prepare()
        self.gate = month_gate(panel)
        self.g = self.gate.fillna(0).values > 0
        out = self.S.copy()
        out["_gate"] = self.gate
        for i, x in enumerate(self.X):
            out = out.join(x.add_suffix("_x%d" % i))
        return out

    def post_prepare(self):
        pass

    def eligible(self, k, d):
        a = self.A.iloc[k]
        return [s for s in self.S.iloc[k].dropna().index if a[s] > 0]

    def meta(self, k, s):
        return dict(atr=self.A.iat[k, self.col[s]])

    def entries(self, k):
        row = self.S.iloc[k]
        return row[np.isfinite(row.values)].sort_values(ascending=False)

    def candidates(self, k, d, held):
        if not self.g[k]:
            return []
        return [(s, self.meta(k, s)) for s in self.entries(k).index if s not in held and self.A.iat[k, self.col[s]] > 0]

    def levels(self, sym, fill_px, meta):
        return fill_px - self.m_stop * meta["atr"], None


class Breakout(Gated):
    school = "xu hướng"

    def __init__(self, n=50, m_stop=3.0):
        super().__init__(m_stop)
        self.n = n
        self.name = "breakout(N=%d,stop=%gATR)" % (n, m_stop)
        self.n_params = 4          # N, m_stop, ATR14, cổng MA10

    def signals(self, d):
        c = d.close
        hi = c.shift(1).rolling(self.n).max()
        s = (c / c.shift(self.n) - 1).where(c >= hi, -np.inf)        # xếp hạng theo lợi suất N phiên
        return (s.where(hi.notna()),)

    def exit_signal(self, k, d, sym, pos):
        j = self.col[sym]
        peak = np.nanmax(self.Cv[pos["k"]:k + 1, j])
        return "trail" if self.Cv[k, j] < peak - self.m_stop * pos["meta"]["atr"] else None


class BreakoutNoLimitUp(Breakout):
    """T46 (ý #3): như Breakout nhưng bỏ tín hiệu khi lợi suất đóng cửa phiên tín hiệu >= `jump`
    so với phiên giao dịch trước (xấp xỉ chạm trần HSX 7%)."""

    def __init__(self, n=50, m_stop=2.0, jump=0.065):
        super().__init__(n, m_stop)
        self.jump = jump
        self.name = "breakout_nolu(N=%d,stop=%gATR,jump<%g)" % (n, m_stop, jump)
        self.n_params = 5          # + ngưỡng jump

    def signals(self, d):
        s, = super().signals(d)
        c = d.close
        jumped = (c / c.shift(1) - 1 >= self.jump) & s.notna()
        return (s.where(~jumped, -np.inf),)


class Momentum(Gated):
    school = "xu hướng"

    def __init__(self, lookback=120, m_stop=3.0, top=5, keep=10):
        super().__init__(m_stop)
        self.L, self.top, self.keep = lookback, top, keep
        self.name = "momentum(L=%d,stop=%gATR,top%d/keep%d)" % (lookback, m_stop, top, keep)
        self.n_params = 6          # L, bỏ 5 phiên, m_stop, top, keep, cổng

    def signals(self, d):
        c = d.close
        return (c.shift(5) / c.shift(self.L) - 1,)

    def post_prepare(self):
        self.R = self.S.rank(axis=1, ascending=False).values     # hạng cắt ngang trong ngày

    def entries(self, k):
        row = self.S.iloc[k].dropna()
        row = row[row > 0].sort_values(ascending=False)
        return row.iloc[:self.top]

    def exit_signal(self, k, d, sym, pos):
        r = self.R[k, self.col[sym]]
        return "rank_out" if not (r <= self.keep) else None


class Momentum5(Momentum):
    """T42: momentum rút gọn — cố định bỏ 5 phiên, top 5, giữ trong top 10 (hằng số cấu trúc, không tinh chỉnh)."""

    def __init__(self, lookback=60, m_stop=2.0):
        super().__init__(lookback, m_stop, top=5, keep=10)
        self.name = "momentum5p(L=%d,stop=%gATR)" % (lookback, m_stop)
        self.n_params = 5          # L, m_stop, ATR14, top, cổng


class Reversion(Gated):
    school = "đảo chiều"

    def __init__(self, x=10, m_stop=3.0, sma=5, max_hold=10):
        super().__init__(m_stop)
        self.x, self.sma, self.max_hold = x, sma, max_hold
        self.name = "reversion(RSI2<%g,stop=%gATR,exit>SMA%d,hold%d)" % (x, m_stop, sma, max_hold)
        self.n_params = 6          # RSI len 2, X, m_stop, SMA exit, max_hold, cổng

    def signals(self, d):
        c = d.close
        r2 = rsi(c, 2)
        s = (-r2).where(r2 < self.x, -np.inf).where(r2.notna())      # RSI càng thấp càng ưu tiên
        above = (c > c.rolling(self.sma).mean()).astype(float).where(c.rolling(self.sma).mean().notna())
        return s, above

    def post_prepare(self):
        self.Up = self.X[0].values

    def exit_signal(self, k, d, sym, pos):
        if self.Up[k, self.col[sym]] == 1:
            return "revert"
        return "timeout" if k - pos["k"] >= self.max_hold else None
