"""Engine backtest danh mục (research, T37).

Quy ước khớp lệnh (mặc định, Config()):
- Tín hiệu tính trên nến đã đóng cửa ngày t -> lệnh khớp ở giá MỞ CỬA phiên t+1 ± trượt giá `slip`
  (mua: open*(1+slip), bán: open*(1-slip)). Mở cửa t+1 không có giao dịch (NaN): lệnh mua huỷ,
  lệnh bán chờ phiên kế tiếp có giá.
- Phí môi giới `fee` mỗi chiều, thuế bán `tax` trên giá trị bán. Lô `lot` cổ phiếu. Giá dữ liệu
  đơn vị nghìn VND -> nhân `unit`.
- T+2: cổ phiếu mua phiên k chỉ được bán từ phiên k+`settle` (cả lệnh mở cửa lẫn stop/target trong phiên).
- Stop/target xét TRONG PHIÊN theo low/high (stop_mode="intraday"): open<=stop -> khớp open (gap),
  low<=stop -> khớp stop; open>=target -> khớp open, high>=target -> khớp target; cùng phiên chạm cả
  hai -> tính stop (bảo thủ). Mọi giá bán trừ thêm trượt giá.
- stop_mode="close" (chỉ để đối chiếu T34): so sánh giá đóng cửa với stop/target, bán mở cửa phiên sau,
  điều kiện T+2 xét tại ngày quyết định như research/t34/wf_stage1.py.
- Tiền bán được dùng mua ngay trong cùng phiên (như ứng trước tiền bán của CTCK, không tính phí ứng).
- Cuối kỳ không thanh lý: vị thế mở được định giá theo giá đóng cửa, không vào danh sách lệnh.
- limits=True (T38): khóa trần/sàn theo biên độ sàn (HSX 7%, HNX 10%, UPCoM 15%) so với giá đóng cửa
  phiên trước. Mở cửa >= trần - lim_tol -> lệnh mua KHÔNG khớp (hủy, như xếp hàng mua trần không tới lượt).
  Cả phiên nằm ở sàn (high <= sàn + lim_tol) -> KHÔNG bán được (stop/lệnh bán dời sang mở cửa phiên sau).
  Mặc định False để tái tạo đúng T34/T37.
- delist_haircut=h (T42): mã có phiên giao dịch cuối cách cuối panel > delist_gap phiên (hủy niêm yết/
  ngừng GD) -> vị thế đang giữ bị thanh lý tại đóng cửa phiên cuối x (1-h), lý do "delist". None = tắt.
Không mô phỏng: giới hạn khối lượng, lãi tiền mặt.
"""
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class Config:
    cash: float = 1e8
    fee: float = 0.0015
    tax: float = 0.001
    slip: float = 0.001
    lot: int = 100
    unit: float = 1000.0
    settle: int = 2
    max_pos: int = 5
    pos_pct: float = 0.20
    max_new: int = 3
    stop_mode: str = "intraday"      # "intraday" | "close" (tương thích T34)
    limits: bool = False             # mô phỏng khóa trần/sàn (T38)
    lim_tol: float = 0.005           # dung sai bước giá / làm tròn giá điều chỉnh
    delist_haircut: float = None     # T42: chiết khấu thanh lý mã hủy niêm yết (None = tắt)
    delist_gap: int = 20


class Strategy:
    """Giao diện chiến lược. Mọi tín hiệu phải nhân quả: giá trị tại ngày t chỉ dùng dữ liệu <= t."""
    name = "base"
    n_params = 0

    def prepare(self, panel):
        """Tính trước tín hiệu; trả DataFrame (ngày x mã) dùng cho test look-ahead."""
        raise NotImplementedError

    def eligible(self, k, d):
        """Các mã có dữ liệu tín hiệu hợp lệ tại ngày d (vũ trụ cho baseline ngẫu nhiên)."""
        raise NotImplementedError

    def candidates(self, k, d, held):
        """Danh sách (mã, meta) muốn mua ở mở cửa phiên sau, theo thứ tự ưu tiên."""
        raise NotImplementedError

    def weight(self, sym, meta):
        return None                      # None -> Config.pos_pct

    def levels(self, sym, fill_px, meta):
        return None, None                # (stop, target) theo giá khớp

    def exit_signal(self, k, d, sym, pos):
        return None                      # lý do bán ở mở cửa phiên sau, hoặc None


class RandomEntry(Strategy):
    """Baseline: mỗi ngày chọn ngẫu nhiên đúng số ứng viên như chiến lược gốc, cùng luật thoát/khớp."""

    def __init__(self, base, seed):
        self.base, self.rng = base, np.random.default_rng(seed)
        self.name, self.n_params = "random(%s)" % base.name, 0

    def prepare(self, panel):
        return self.base.prepare(panel)

    def candidates(self, k, d, held):
        n = len(self.base.candidates(k, d, held))
        pool = [s for s in self.base.eligible(k, d) if s not in held]
        if not n or not pool:
            return []
        pick = self.rng.choice(len(pool), size=min(n, len(pool)), replace=False)
        return [(pool[i], self.base.meta(k, pool[i])) for i in pick]

    def weight(self, sym, meta):
        return self.base.weight(sym, meta)

    def levels(self, sym, fill_px, meta):
        return self.base.levels(sym, fill_px, meta)

    def exit_signal(self, k, d, sym, pos):
        return self.base.exit_signal(k, d, sym, pos)


def run(panel, strat, start, end, cfg=None, prepared=False):
    cfg = cfg or Config()
    if not prepared:
        strat.prepare(panel)
    idx = panel.idx
    ks = np.where((idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end)))[0]
    col = {s: i for i, s in enumerate(panel.symbols)}
    O, H, L, C = (getattr(panel, f).values for f in ("open", "high", "low", "close_ff"))
    if cfg.limits:
        ref = panel.close_ff.shift(1).values
        lim = panel.limit.reindex(panel.symbols).fillna(0.07).values
        CEIL, FLOOR = ref * (1 + lim - cfg.lim_tol), ref * (1 - lim + cfg.lim_tol)
    blocked = dict(buy=0, sell=0)
    if cfg.delist_haircut is not None:
        has = panel.close.notna().values
        lastk = np.where(has.any(0), len(idx) - 1 - np.argmax(has[::-1], axis=0), -1)
        dead = (lastk >= 0) & (lastk < len(idx) - 1 - cfg.delist_gap)

    def at_ceiling(k, j):
        return cfg.limits and O[k, j] >= CEIL[k, j]

    def floor_locked(k, j):
        return cfg.limits and H[k, j] <= FLOOR[k, j]
    cash, pos, nav, trades = cfg.cash, {}, [], []
    pend_buy, pend_sell = [], {}
    buy_c = 1 + cfg.fee
    sell_c = 1 - cfg.fee - cfg.tax

    def close_pos(s, k, px, why, sig_k):
        nonlocal cash
        p = pos.pop(s)
        proceeds = p["sh"] * px * cfg.unit * sell_c
        cash += proceeds
        trades.append(dict(sym=s, sig_buy=idx[p["sig_k"]], buy=idx[p["k"]], buy_px=p["px"],
                           sig_sell=idx[sig_k], sell=idx[k], sell_px=px, sh=p["sh"], k_buy=p["k"],
                           k_sell=k, cost=p["cost"], proceeds=proceeds, net=proceeds - p["cost"],
                           net_ret=proceeds / p["cost"] - 1, why=why))

    for n_day, k in enumerate(ks):
        d = idx[k]
        # 1) khớp lệnh mở cửa (quyết định từ phiên trước)
        for s, (why, sig_k) in list(pend_sell.items()):
            o = O[k, col[s]]
            if np.isnan(o):
                continue
            if floor_locked(k, col[s]):
                blocked["sell"] += 1
                continue
            del pend_sell[s]
            close_pos(s, k, o * (1 - cfg.slip), why, sig_k)
        for s, tv, meta, sig_k in pend_buy:
            o = O[k, col[s]]
            if np.isnan(o) or len(pos) >= cfg.max_pos or s in pos:
                continue
            if at_ceiling(k, col[s]):
                blocked["buy"] += 1
                continue
            px = o * (1 + cfg.slip)
            sh = int(min(tv, cash / buy_c) / (px * cfg.unit) / cfg.lot) * cfg.lot
            if sh <= 0:
                continue
            cost = sh * px * cfg.unit * buy_c
            cash -= cost
            stop, tgt = strat.levels(s, px, meta)
            pos[s] = dict(sh=sh, px=px, cost=cost, k=k, sig_k=sig_k, stop=stop, tgt=tgt, meta=meta)
        pend_buy = []
        # 2) stop/target trong phiên
        if cfg.stop_mode == "intraday":
            for s in list(pos):
                p = pos[s]
                if s in pend_sell or k - p["k"] < cfg.settle:
                    continue
                j = col[s]
                o, h, l = O[k, j], H[k, j], L[k, j]
                if np.isnan(o) or np.isnan(l):
                    continue
                if floor_locked(k, j):
                    if p["stop"] is not None and l <= p["stop"]:
                        blocked["sell"] += 1
                        pend_sell[s] = ("stop_locked", k)   # bán mở cửa phiên sau (nếu hết khóa sàn)
                    continue
                st, tg = p["stop"], p["tgt"]
                px = why = None
                if st is not None and o <= st:
                    px, why = o, "stop_gap"
                elif st is not None and l <= st:
                    px, why = st, "stop"
                elif tg is not None and o >= tg:
                    px, why = o, "target_gap"
                elif tg is not None and h >= tg:
                    px, why = tg, "target"
                if why:
                    close_pos(s, k, px * (1 - cfg.slip), why, k)
        if cfg.delist_haircut is not None:
            for s in list(pos):
                j = col[s]
                if dead[j] and k >= lastk[j]:
                    pend_sell.pop(s, None)
                    close_pos(s, k, C[k, j] * (1 - cfg.delist_haircut), "delist", k)
        # 3) NAV đóng cửa
        mv = sum(p["sh"] * C[k, col[s]] * cfg.unit for s, p in pos.items())
        navd = cash + mv
        nav.append((d, navd))
        if n_day == len(ks) - 1:
            break
        # 4) quyết định bán ở mở cửa phiên sau
        for s, p in pos.items():
            if s in pend_sell:
                continue
            age = k - p["k"] if cfg.stop_mode == "close" else k + 1 - p["k"]
            if age < cfg.settle:
                continue
            why = None
            if cfg.stop_mode == "close":
                c = C[k, col[s]]
                why = "stop" if p["stop"] is not None and c <= p["stop"] else \
                    "target" if p["tgt"] is not None and c >= p["tgt"] else None
            why = why or strat.exit_signal(k, d, s, p)
            if why:
                pend_sell[s] = (why, k)
        # 5) quyết định mua ở mở cửa phiên sau
        slots = cfg.max_pos - (len(pos) - len(pend_sell))
        if slots <= 0:
            continue
        for s, meta in strat.candidates(k, d, set(pos))[:min(slots, cfg.max_new)]:
            w = strat.weight(s, meta)
            pend_buy.append((s, (cfg.pos_pct if w is None else w) * navd, meta, k))
    tr = pd.DataFrame(trades)
    return pd.Series(dict(nav)), tr, dict(open_positions=len(pos), blocked_buy=blocked["buy"],
                                          blocked_sell=blocked["sell"])


def metrics(nav, tr):
    r = nav.pct_change().dropna()
    yrs = len(r) / 252
    out = dict(cagr=(nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1,
               sharpe=r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0,
               maxdd=(nav / nav.cummax() - 1).min(), n=len(tr))
    if len(tr):
        w, l = tr[tr.net > 0].net, tr[tr.net <= 0].net
        out.update(win=len(w) / len(tr), pf=w.sum() / -l.sum() if l.sum() < 0 else np.inf,
                   exp_pct=tr.net_ret.mean(), exp_vnd=tr.net.mean(),
                   hold=(tr.k_sell - tr.k_buy).mean())
    else:
        out.update(win=np.nan, pf=np.nan, exp_pct=np.nan, exp_vnd=np.nan, hold=np.nan)
    return out


def fmt(m):
    return " ".join("%s=%s" % (k, ("%.3f" % v) if isinstance(v, (float, np.floating)) else v)
                    for k, v in m.items())
