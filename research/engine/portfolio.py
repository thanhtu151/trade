"""T46: phép toán trên chuỗi NAV — ghép 2 túi (cân bằng lại theo quý), phủ vol-scaling, PBO/CSCV.
Mọi hàm chỉ dùng lợi suất đã biết tới phiên quyết định (nhân quả); lịch giao dịch coi là biết trước."""
from itertools import combinations
import numpy as np
import pandas as pd

REBAL_COST = 0.006        # một vòng bán + mua trên giá trị chuyển giữa 2 túi (bảo thủ)
VS_COST = 0.0035          # phí + thuế + trượt một chiều trên |thay đổi độ phơi nhiễm|


def _last_of(idx, key):
    """Mảng bool: phiên cuối của mỗi kỳ (key = 'M' hoặc 'Q') trong lịch idx."""
    p = idx.to_period(key)
    return np.r_[p[1:] != p[:-1], True]


def combine(nav_a, nav_b, w, cost=REBAL_COST):
    """Túi a tỷ trọng w, túi b (1-w); cân bằng lại về (w, 1-w) ở phiên cuối mỗi quý (trừ phiên cuối chuỗi).
    Trả (NAV ghép cùng vốn đầu với nav_a, chuỗi tỷ trọng thực của túi a đầu mỗi phiên)."""
    assert nav_a.index.equals(nav_b.index)
    ra, rb = nav_a.values[1:] / nav_a.values[:-1], nav_b.values[1:] / nav_b.values[:-1]
    qe = _last_of(nav_a.index, "Q")
    va, vb = w, 1 - w
    out, wa = [1.0], [w]
    for i in range(1, len(nav_a)):
        va, vb = va * ra[i - 1], vb * rb[i - 1]
        if qe[i] and i < len(nav_a) - 1:
            t = va + vb
            t -= cost * abs(va - w * t)
            va, vb = w * t, (1 - w) * t
        out.append(va + vb)
        wa.append(va / (va + vb))
    return pd.Series(out, index=nav_a.index) * nav_a.iloc[0], pd.Series(wa, index=nav_a.index)


def vol_overlay(nav, target=0.15, lookback=60, cost=VS_COST):
    """Phiên cuối tháng i: vol = std(lợi suất ngày chưa scale i-lookback+1..i) x căn 252;
    độ phơi nhiễm từ phiên i+1: e = min(1, target/vol). Chưa đủ lookback lợi suất -> e = 1.
    Trả (NAV đã scale, chuỗi e áp cho lợi suất của từng phiên)."""
    r = nav.pct_change().fillna(0.0).values
    me = _last_of(nav.index, "M")
    v, e = 1.0, 1.0
    out, es = [1.0], [1.0]
    for i in range(1, len(r)):
        v *= 1 + e * r[i]
        es.append(e)
        if me[i] and i >= lookback and i < len(r) - 1:
            sd = r[i - lookback + 1:i + 1].std(ddof=1) * np.sqrt(252)
            e_new = min(1.0, target / sd) if sd > 0 else 1.0
            v -= v * abs(e_new - e) * cost
            e = e_new
        out.append(v)
    return pd.Series(out, index=nav.index) * nav.iloc[0], pd.Series(es, index=nav.index)


def sharpe(r):
    sd = r.std(axis=0, ddof=1)
    return np.where(sd > 0, r.mean(axis=0) / np.where(sd > 0, sd, 1), 0.0) * np.sqrt(252)


def cscv_pbo(R, S=16):
    """PBO theo CSCV (Bailey, Borwein, López de Prado, Zhu 2016). R: DataFrame lợi suất ngày (T x N cấu hình).
    Chia S khối liên tiếp; mỗi cách chọn S/2 khối làm IS (phần còn lại OOS): lấy cấu hình Sharpe IS cao nhất,
    hạng OOS tương đối w = hạng/(N+1), logit = ln(w/(1-w)). PBO = P[logit <= 0].
    Trả dict(pbo, n_splits, logits, is_best_oos_sr (SR OOS của cấu hình chọn), p_oos_loss)."""
    X = R.values
    blocks = np.array_split(np.arange(len(X)), S)
    N = X.shape[1]
    logits, oos_sr, is_sr = [], [], []
    for c in combinations(range(S), S // 2):
        ins = np.concatenate([blocks[j] for j in c])
        oos = np.concatenate([blocks[j] for j in range(S) if j not in c])
        s_in, s_out = sharpe(X[ins]), sharpe(X[oos])
        best = int(np.argmax(s_in))
        rank = (s_out < s_out[best]).sum() + 1 + 0.5 * ((s_out == s_out[best]).sum() - 1)   # hạng tăng dần 1..N
        w = rank / (N + 1)
        logits.append(np.log(w / (1 - w)))
        oos_sr.append(s_out[best])
        is_sr.append(s_in[best])
    logits, oos_sr = np.array(logits), np.array(oos_sr)
    return dict(pbo=float((logits <= 0).mean()), n_splits=len(logits), logits=logits,
                is_best_oos_sr=oos_sr, is_best_is_sr=np.array(is_sr), p_oos_loss=float((oos_sr < 0).mean()))
