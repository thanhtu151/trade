"""T192 phần A: walk-forward bộ lọc trên prediction_log (origin/state e45635b, snapshot cùng thư mục).
Hợp lệ/đúng/pnl theo learning_engine.py (_is_valid_outcome, _is_correct, _signed_pnl_pct).
Chỉ dùng trường có trong log (không nạp giá 2024-2026: holdout chưa được duyệt cho T192).
Lãi sau phí chỉ tính cho lệnh MUA (VN không bán khống): pnl - 0,5% khứ hồi."""
import itertools
import json
import math
import os
import re

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.5          # % khứ hồi, protocol_config
MIN_N = 30
TARGET = 0.80


def wilson(w, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = w / n
    c = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    d = 1 + z * z / n
    return ((p + z * z / (2 * n) - c) / d, (p + z * z / (2 * n) + c) / d)


def load():
    L = list(json.load(open(os.path.join(HERE, "prediction_log_state_e45635b.json"))).values())
    rows = []
    for r in L:
        if r.get("invalid") or r.get("correct") is None:
            continue
        raw = float(r["price_change_pct"]) if "price_change_pct" in r else float(r.get("pnl_pct") or 0)
        if abs(raw) > 30:
            continue
        d = int(r["predicted_direction"])
        correct = bool(r["correct"]) and not (raw == 0.0 and d != 0)
        pnl = float(r.get("pnl_pct") or 0) if "price_change_pct" in r else raw * d
        m = re.search(r"stage1_score=([\d.]+)", r["notes"])
        rows.append(dict(date=pd.Timestamp(r["date"]), ticker=r["ticker"], dir=d, conf=float(r["confidence"]),
                         s1=float(m.group(1)) if m else float("nan"), debate=r["source"] == "two_stage_debate",
                         llm=r["notes"].split("llm=")[-1], correct=correct, pnl=pnl))
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


GRID = dict(dir=("all", "buy"), conf=(0, 30, 40, 50, 60), s1=(0, 2, 3, 4), src=("all", "debate"), llm=("all", "MUA"))


def apply(df, f):
    m = pd.Series(True, index=df.index)
    if f["dir"] == "buy":
        m &= df.dir == 1
    m &= df.conf >= f["conf"]
    m &= df.s1 >= f["s1"]
    if f["src"] == "debate":
        m &= df.debate
    if f["llm"] == "MUA":
        m &= df.llm == "MUA"
    return df[m]


def stats(x):
    n, w = len(x), int(x.correct.sum())
    lo, hi = wilson(w, n)
    buy = x[x.dir == 1]
    return dict(n=n, prec=w / n if n else float("nan"), lo=lo, hi=hi, n_buy=len(buy),
                exp_buy_net=(buy.pnl - FEE).mean() if len(buy) else float("nan"))


def main():
    df = load()
    keys = list(GRID)
    filters = [dict(zip(keys, v)) for v in itertools.product(*GRID.values())]
    out = ["# T192 phần A — prediction_log walk-forward", "",
           "Dòng hợp lệ đã có kết quả: %d (%s → %s); MUA %d, BÁN %d" % (
               len(df), df.date.min().date(), df.date.max().date(), (df.dir == 1).sum(), (df.dir == -1).sum()),
           "Số bộ lọc trong lưới: %d" % len(filters), ""]
    s = stats(df)
    out.append("Toàn bộ: n=%d precision %.1f%% (Wilson %.1f–%.1f%%), kỳ vọng MUA sau phí %.2f%%/lệnh" % (
        s["n"], 100 * s["prec"], 100 * s["lo"], 100 * s["hi"], s["exp_buy_net"]))
    for name, sub in (("MUA", df[df.dir == 1]), ("BÁN", df[df.dir == -1])):
        t = stats(sub)
        out.append("  %s: n=%d precision %.1f%% (Wilson %.1f–%.1f%%)" % (name, t["n"], 100 * t["prec"], 100 * t["lo"], 100 * t["hi"]))
    out.append("")
    # walk-forward mở rộng theo tháng: train mọi tháng < tháng test
    months = sorted(df.date.dt.to_period("M").unique())
    out.append("| Tháng test | n train | Bộ lọc chọn trên train | train n / precision / Wilson lo | test n / precision (Wilson) | test kỳ vọng MUA sau phí |")
    out.append("|---|---|---|---|---|---|")
    for tm in months[1:]:
        tr = df[df.date.dt.to_period("M") < tm]
        te = df[df.date.dt.to_period("M") == tm]
        cand = []
        for f in filters:
            s = stats(apply(tr, f))
            if s["n"] >= MIN_N:
                cand.append((f, s))
        ok = [c for c in cand if c[1]["prec"] >= TARGET and not (c[1]["exp_buy_net"] <= 0)]
        if ok:
            f, s = max(ok, key=lambda c: c[1]["lo"])
            tag = "ĐẠT trên train"
        elif cand:
            f, s = max(cand, key=lambda c: c[1]["lo"])
            tag = "không đạt 80% trên train → mô tả, Wilson lo cao nhất"
        else:
            out.append("| %s | %d | không bộ lọc nào n≥%d | – | – | – |" % (tm, len(tr), MIN_N))
            continue
        t = stats(apply(te, f))
        warn = " ⚠ n<30" if t["n"] < MIN_N else ""
        out.append("| %s | %d | %s (%s) | %d / %.1f%% / %.1f%% | %d / %.1f%% (%.1f–%.1f%%)%s | %s |" % (
            tm, len(tr), f, tag, s["n"], 100 * s["prec"], 100 * s["lo"], t["n"], 100 * t["prec"] if t["n"] else float("nan"),
            100 * t["lo"], 100 * t["hi"], warn, "%.2f%% (n MUA %d)" % (t["exp_buy_net"], t["n_buy"])))
    out.append("")
    # mô tả: bộ lọc nào đạt precision ≥80% trên TOÀN BỘ log (in-sample, chỉ để thấy có tồn tại không)
    allc = [(f, stats(apply(df, f))) for f in filters]
    big = sorted([c for c in allc if c[1]["n"] >= MIN_N], key=lambda c: -c[1]["prec"])[:5]
    out.append("Top 5 precision in-sample (n≥30, CHỈ MÔ TẢ, có chọn lọc hậu nghiệm):")
    for f, s in big:
        out.append("- %s: n=%d %.1f%% (Wilson %.1f–%.1f%%), exp MUA sau phí %.2f%%" % (f, s["n"], 100 * s["prec"], 100 * s["lo"], 100 * s["hi"], s["exp_buy_net"]))
    hi80 = [c for c in allc if c[1]["prec"] >= TARGET and c[1]["n"] >= 5]
    out.append("Bộ lọc precision ≥80%% với n≥5 (in-sample): %d; lớn nhất n=%s" % (
        len(hi80), max([c[1]["n"] for c in hi80]) if hi80 else 0))
    # theo khoảng confidence
    out.append("")
    out.append("Precision theo khoảng confidence (toàn bộ):")
    for a, b in ((0, 30), (30, 40), (40, 50), (50, 60), (60, 101)):
        x = df[(df.conf >= a) & (df.conf < b)]
        s = stats(x)
        out.append("- [%d,%d): n=%d %.1f%% (Wilson %.1f–%.1f%%)" % (a, b, s["n"], 100 * s["prec"] if s["n"] else float("nan"), 100 * s["lo"], 100 * s["hi"]))
    txt = "\n".join(out)
    open(os.path.join(HERE, "part_a.md"), "w").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
