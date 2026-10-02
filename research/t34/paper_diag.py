"""T34: chẩn đoán sổ paper epoch 1 (reset 100tr ngày 2026-07-09).

Đầu vào: /tmp/t34_trades.json, /tmp/t34_portfolio.json (gh api ... ?ref=state).
Giá tham chiếu: research/data/2026-09-29/prices (vnstock VCI, dữ liệu đến 2026-09-28).
Đơn vị trong sổ: price = nghìn VND, qty = số cp * 1000 -> value = price*qty (VND).
Phí giả định cho bản "sau phí": 0.15% mỗi chiều + 0.1% thuế bán (mức phí môi giới phổ biến);
sổ paper trên main hiện KHÔNG trừ phí (pnl = (sell-buy)*qty đúng tuyệt đối).
"""
import json
import numpy as np
import pandas as pd

D = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/prices/"
FEE, TAX = 0.0015, 0.001
T = json.load(open("/tmp/t34_trades.json"))
P = json.load(open("/tmp/t34_portfolio.json"))
E = [t for t in T if t.get("epoch_id") == 1 and t.get("side")]
_px = {}


def px(s):
    if s not in _px:
        d = pd.read_parquet(D + s + ".parquet")
        d["time"] = pd.to_datetime(d["time"]).dt.normalize()
        _px[s] = d.set_index("time")
    return _px[s]


def bar(s, ts):
    d = px(s)
    day = pd.Timestamp(ts[:10])
    if day in d.index:
        return d.loc[day]
    return None


# --- 1) khớp từng lệnh với nến ngày ---
rows = []
for t in E:
    b = bar(t["symbol"], t["time"])
    hh = int(t["time"][11:13])
    r = dict(time=t["time"], side=t["side"], sym=t["symbol"], price=t["price"], qty=t["qty"],
             value=t["value"], pnl=t.get("pnl"), reason=t["reason"][:30],
             in_session=(9 <= hh < 15), lo=None, hi=None, close=None, in_range=None)
    if b is not None:
        r.update(lo=b.low, hi=b.high, close=b.close,
                 in_range=bool(b.low * 0.995 <= t["price"] <= b.high * 1.005))
    rows.append(r)
L = pd.DataFrame(rows)

# --- 2) ghép round-trip (FIFO theo mã, trong sổ mỗi mã tối đa 1 vị thế) ---
open_, trips = {}, []
for t in E:
    s = t["symbol"]
    if t["side"] == "BUY":
        open_[s] = t
    else:
        b = open_.pop(s)
        q = t["qty"]
        gross = (t["price"] - b["price"]) * q
        cost = FEE * (b["price"] * q + t["price"] * q) + TAX * t["price"] * q
        trips.append(dict(sym=s, buy=b["time"][:10], sell=t["time"][:10], bp=b["price"], sp=t["price"],
                          plan_stop=b["plan"]["stop_loss"], plan_tgt=b["plan"]["target_price"],
                          ret=t["price"] / b["price"] - 1, gross=gross, ledger_pnl=t["pnl"],
                          cost=cost, net=gross - cost, reason=t["reason"],
                          stop_dist=1 - b["plan"]["stop_loss"] / b["price"],
                          tgt_dist=b["plan"]["target_price"] / b["price"] - 1))
R = pd.DataFrame(trips)
bad_buy = set()
for t in E:
    if t["side"] == "BUY":
        b = bar(t["symbol"], t["time"])
        if b is not None and not (b.low * 0.995 <= t["price"] <= b.high * 1.005):
            bad_buy.add((t["symbol"], t["time"][:10]))
R["bad_price"] = [(r.sym, r.buy) in bad_buy for r in R.itertuples()]


def stats(df, col):
    w, l = df[df[col] > 0][col], df[df[col] <= 0][col]
    pf = w.sum() / -l.sum() if l.sum() < 0 else np.inf
    return dict(n=len(df), win_rate=round(len(w) / max(len(df), 1), 3),
                avg=round(df[col].mean()), avg_win=round(w.mean()) if len(w) else 0,
                avg_loss=round(l.mean()) if len(l) else 0, total=round(df[col].sum()), pf=round(pf, 2))


# --- 3) NAV ngày (mark-to-market bằng giá đóng cửa thật) ---
days = px("VNINDEX").loc["2026-07-09":].index
cash, pos, nav = 100_000_000.0, {}, []
ev = sorted(E, key=lambda t: t["time"])
i = 0
for d in days:
    while i < len(ev) and pd.Timestamp(ev[i]["time"][:10]) <= d:
        t = ev[i]
        v = t["price"] * t["qty"]
        if t["side"] == "BUY":
            cash -= v
            pos[t["symbol"]] = pos.get(t["symbol"], 0) + t["qty"]
        else:
            cash += v
            pos[t["symbol"]] -= t["qty"]
        i += 1
    mv = 0.0
    for s, q in pos.items():
        if q:
            c = px(s)["close"]
            mv += q * c.loc[:d].iloc[-1]
    nav.append((d, cash + mv))
N = pd.Series(dict(nav))
dd = (N / N.cummax() - 1).min()
vni = px("VNINDEX")["close"].loc["2026-07-09":]
vn30 = px("VN30")["close"].loc["2026-07-09":]
e1 = px("E1VFVN30")["close"].loc["2026-07-09":]
ret_d = N.pct_change().dropna()


def rep():
    out = []
    out.append("== Lệnh epoch 1: %d BUY, %d SELL; ngoài phiên: %d; giá ngoài biên độ ngày: %d" % (
        (L.side == "BUY").sum(), (L.side == "SELL").sum(), (~L.in_session).sum(), (L.in_range == False).sum()))
    out.append(L[L.in_range == False][["time", "side", "sym", "price", "lo", "hi", "reason"]].to_string())
    out.append("\n== Round-trips (%d) ==" % len(R))
    out.append(R[["sym", "buy", "sell", "bp", "sp", "ret", "gross", "cost", "reason", "stop_dist", "tgt_dist", "bad_price"]]
               .round(4).to_string())
    out.append("\nGROSS (như sổ): %s" % stats(R, "gross"))
    out.append("NET (phí 0.15%%x2 + thuế 0.1%%): %s" % stats(R, "net"))
    ok = R[~R.bad_price]
    out.append("NET bỏ lệnh giá sai: %s" % stats(ok, "net"))
    out.append("Lỗ do lệnh giá sai (gross): %d" % R[R.bad_price].gross.sum())
    out.append("Tổng phí giả định: %d" % R.cost.sum())
    out.append("Theo lý do thoát: \n%s" % R.groupby("reason").gross.agg(["count", "sum", "mean"]).round(0).to_string())
    out.append("stop_dist TB %.2f%%, tgt_dist TB %.2f%%" % (R.stop_dist.mean() * 100, R.tgt_dist.mean() * 100))
    sl = R[R.reason.str.startswith("stop_loss")]
    out.append("Stop: slip TB so với mức stop kế hoạch = %.2f%% (sp/plan_stop-1)" % ((sl.sp / sl.plan_stop - 1).mean() * 100))
    out.append("\n== NAV (MTM giá đóng cửa VCI) 2026-07-09 -> %s ==" % N.index[-1].date())
    out.append("NAV cuối %.0f, lợi nhuận %.2f%%, maxDD %.2f%%, Sharpe năm hoá %.2f (n=%d ngày)" % (
        N.iloc[-1], (N.iloc[-1] / 1e8 - 1) * 100, dd * 100, ret_d.mean() / ret_d.std() * np.sqrt(252), len(ret_d)))
    out.append("NAV sổ state (updated %s): cash %.0f + MV %.0f = %.0f" % (
        P["updated_at"], P["cash"], sum(p["market_value"] for p in P["positions"].values()),
        P["cash"] + sum(p["market_value"] for p in P["positions"].values())))
    for nm, s in [("VNINDEX", vni), ("VN30", vn30), ("E1VFVN30", e1)]:
        out.append("%s cùng kỳ: %.2f%% (%s -> %s), maxDD %.2f%%" % (
            nm, (s.iloc[-1] / s.iloc[0] - 1) * 100, s.index[0].date(), s.index[-1].date(), (s / s.cummax() - 1).min() * 100))
    out.append("Beta NAV vs VNINDEX: %.2f" % (ret_d.cov(vni.pct_change().reindex(ret_d.index)) / vni.pct_change().var()))
    exp = R.gross.sum() / R.shape[0]
    out.append("Sổ PnL thực hiện theo ledger: %d; phần chưa thực hiện: %d" % (
        R.ledger_pnl.sum(), sum(p["unrealized_pnl"] for p in P["positions"].values())))
    return "\n".join(out)


if __name__ == "__main__":
    print(rep())


def extra():
    sl = R[R.reason.str.startswith("stop_loss") & ~R.bad_price]
    print("Stop (bỏ giá sai) n=%d slip TB %.2f%%, median %.2f%%" % (
        len(sl), (sl.sp / sl.plan_stop - 1).mean() * 100, (sl.sp / sl.plan_stop - 1).median() * 100))
    print("VNINDEX", vni.iloc[0], vni.iloc[-1], "VN30", vn30.iloc[0], vn30.iloc[-1])
    ok = R[~R.bad_price]
    print("Round-trip ret TB (bỏ giá sai) %.2f%%; ngày giữ TB %.1f" % (
        ok.ret.mean() * 100, (pd.to_datetime(ok.sell) - pd.to_datetime(ok.buy)).dt.days.mean()))
