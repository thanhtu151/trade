"""Nạp dữ liệu giá cho engine. Cắt cứng ở END (holdout 2024-2026 không được đọc vào bộ nhớ).
Chỉ đọc sau END khi allow_holdout=True VÀ sếp đã tạo file duyệt HOLDOUT_APPROVAL (T42)."""
import json
import os
import numpy as np
import pandas as pd

ROOT = "/Users/drone/Downloads/tradeclone/"
PRICES = ROOT + "research/data/2026-09-29/prices/"
END = "2023-12-31"          # KHÔNG nâng mốc này nếu sếp chưa duyệt holdout
CALENDAR = "VNINDEX"
HOLDOUT_APPROVAL = ROOT + "research/t42/HOLDOUT_APPROVED.txt"   # chỉ sếp tạo file này
NON_STOCK = ("VNINDEX", "VN30", "HNXINDEX", "E1VFVN30")


def holdout_approved():
    return os.path.exists(HOLDOUT_APPROVAL) and os.path.getsize(HOLDOUT_APPROVAL) > 0


def load(sym, end=END, allow_holdout=False):
    if pd.Timestamp(end) > pd.Timestamp(END) and not (allow_holdout and holdout_approved()):
        raise ValueError("holdout 2024-2026 bị khoá: end=%s > %s" % (end, END))
    lim = pd.Timestamp(end) + pd.Timedelta(days=1)
    d = pd.read_parquet(PRICES + sym + ".parquet", filters=[("time", "<", lim)])   # không nạp dòng sau end
    d["time"] = pd.to_datetime(d["time"]).dt.normalize()
    d = d.drop_duplicates("time").set_index("time").sort_index()
    return d.loc[:end, ["open", "high", "low", "close", "volume"]].astype(float)


LIMIT = {"HSX": 0.07, "HNX": 0.10, "UPCOM": 0.15}


def listing():
    return pd.read_csv(PRICES + "../listing.csv").drop_duplicates("symbol").set_index("symbol").exchange


def price_limits(symbols):
    """Biên độ trần/sàn theo sàn niêm yết HIỆN TẠI (listing.csv). Mã từng chuyển sàn trong 2018-2023
    (vd ACB, VIB, GVR) sẽ bị gán sai biên độ ở giai đoạn trước khi chuyển — xấp xỉ.
    Mã DELISTED -> NaN -> engine dùng 7% (bảo thủ: dễ bị coi là trần/sàn hơn)."""
    ls = listing()
    return {s: LIMIT.get(ls.get(s), np.nan) for s in symbols}


def universe_backtest_config():
    """50 mã trong backtest_config.json (danh sách chọn năm 2026 => survivorship bias)."""
    cfg = json.load(open(ROOT + "backtest_config.json"))
    return cfg["positive_ev_tickers"] + cfg["negative_ev_tickers"]


def universe_all(include_delisted=True):
    """T42: mọi mã cổ phiếu trong listing.csv có file giá, kể cả mã đã hủy niêm yết (DELISTED)."""
    ls = listing()
    return [s for s, ex in ls.items() if os.path.exists(PRICES + s + ".parquet")
            and (include_delisted or ex != "DELISTED")]


def pit_universe(panel, symbols, top=50, window=60, min_hist=200, recent=20):
    """T42: universe theo thời điểm. Cuối tháng m (chỉ dữ liệu <= m): mã có >= min_hist phiên có giá
    trong panel và có giao dịch trong `recent` phiên cuối, xếp theo TB giá trị GD `window` phiên
    (phiên không GD = 0), lấy `top`; áp cho các phiên của tháng m+1. Trả DataFrame bool (ngày x mã)."""
    C, V = panel.close[symbols], panel.volume[symbols]
    traded = C.notna()
    tv = (C * V).fillna(0).rolling(window, min_periods=window).mean()
    ok = (traded.cumsum() >= min_hist) & (traded.astype(int).rolling(recent).sum() > 0) & (tv > 0)
    score = tv.where(ok)
    per = panel.idx.to_period("M")
    last_day = pd.Series(panel.idx, index=panel.idx).groupby(per).max()
    me = score.loc[last_day.values]                     # đúng hàng phiên cuối tháng (không lấy giá trị cũ hơn)
    me.index = last_day.index
    top_m = me.rank(axis=1, ascending=False, method="first") <= top
    U = top_m.reindex(per - 1, fill_value=False)
    U.index = panel.idx
    return U.astype(bool)


class Panel:
    """Bảng OHLCV theo lịch VNINDEX; mỗi trường là DataFrame (ngày x mã)."""

    def __init__(self, symbols, end=END, start=None, allow_holdout=False):
        self.end = end
        self.idx = load(CALENDAR, end, allow_holdout).index
        if start is not None:
            self.idx = self.idx[self.idx >= pd.Timestamp(start)]
        raw = {}
        for s in symbols:
            try:
                raw[s] = load(s, end, allow_holdout).reindex(self.idx)
            except FileNotFoundError:
                continue
        self.symbols = list(raw)
        for f in ("open", "high", "low", "close", "volume"):
            setattr(self, f, pd.DataFrame({s: raw[s][f] for s in self.symbols}, index=self.idx))
        self.close_ff = self.close.ffill()
        self.limit = pd.Series(price_limits(self.symbols), dtype=float)

    def truncate(self, t):
        """Bản sao chỉ chứa dữ liệu <= t (dùng cho test chống look-ahead)."""
        p = object.__new__(Panel)
        p.end, p.symbols = t, self.symbols
        p.idx = self.idx[self.idx <= t]
        p.limit = self.limit
        for f in ("open", "high", "low", "close", "volume", "close_ff"):
            setattr(p, f, getattr(self, f).loc[:t])
        return p
