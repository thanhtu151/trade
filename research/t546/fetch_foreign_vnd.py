"""T546 Phần B — mua/bán khối ngoại theo mã/ngày, nguồn DUY NHẤT VNDirect api-finfo (công khai), 2018-01-01 → 2023-12-31.

Không ghép với CafeF: trên ngày chồng lấp (VCB, FLC) hai nguồn lệch > 1% ở 5–17% số ngày → khác định nghĩa,
ghép sẽ là dữ liệu xấp xỉ. CafeF (t546/fetch_foreign.py) chỉ giữ làm bằng chứng thiếu dữ liệu.
Universe = mọi mã từng có trong research/t42/universe_pit.csv. Dòng > 2023-12-31 bị loại (holdout không đọc).
Ghi research/data/foreign_vnd/foreign_2018_2023.csv + coverage.json (so lịch phiên của file giá snapshot 2026-09-29).

    python t546/fetch_foreign_vnd.py VCB      # chạy thử
    python t546/fetch_foreign_vnd.py ALL
"""
import json
import os
import sys
import time

import pandas as pd
import requests

ROOT = "/Users/drone/Downloads/tradeclone/research/"
OUT = ROOT + "data/foreign_vnd/"
PRICES = ROOT + "data/2026-09-29/prices/"
URL = "https://api-finfo.vndirect.com.vn/v4/foreigns"
A, B = "2018-01-01", "2023-12-31"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"


def fetch(sym):
    for k in range(4):
        try:
            j = S.get(URL, params=dict(q="code:%s~tradingDate:gte:%s~tradingDate:lte:%s" % (sym, A, B), size=3000),
                      timeout=60).json()
            d = pd.DataFrame(j.get("data", []))
            assert len(d) == (j.get("totalElements") or 0), "thiếu trang"
            return d
        except Exception:
            time.sleep(3 * (k + 1))
    raise RuntimeError(sym + ": giving up")


def sessions(sym):
    f = PRICES + sym + ".parquet"
    if not os.path.exists(f):
        return None
    t = pd.to_datetime(pd.read_parquet(f, columns=["time"]).time)
    return t[(t >= A) & (t <= B)]


def main(arg):
    os.makedirs(OUT, exist_ok=True)
    u = pd.read_csv(ROOT + "t42/universe_pit.csv")
    syms = sorted({x for v in u.symbols for x in v.split()}) if arg == "ALL" else arg.split(",")
    frames, cov = [], {}
    for i, s in enumerate(syms):
        d = fetch(s)
        time.sleep(0.5)
        t = sessions(s)
        if d.empty:
            cov[s] = dict(rows=0, sessions=None if t is None else len(t))
            continue
        d["date"] = pd.to_datetime(d.tradingDate)
        d = d[(d.date >= A) & (d.date <= B)].drop_duplicates()
        assert d.date.max() <= pd.Timestamp(B)
        dup = int(d.duplicated(["code", "date"]).sum())
        frames.append(d)
        c = dict(rows=len(d), dup_dates=dup, first=str(d.date.min().date()), last=str(d.date.max().date()))
        if t is not None and len(t):
            c.update(sessions=len(t), matched=int(t.isin(d.date).sum()), extra=int((~d.date.isin(t)).sum()),
                     cov_from_2018_09=round(float(t[t >= "2018-09-01"].isin(d.date).mean()), 4),
                     cov_by_year={int(y): round(float(g.isin(d.date).mean()), 3) for y, g in t.groupby(t.dt.year)})
        cov[s] = c
        print(i + 1, len(syms), s, {k: c[k] for k in ("rows", "first", "last") if k in c}, flush=True)
    if frames:
        df = pd.concat(frames)[["code", "date", "floor", "buyVol", "sellVol", "netVol", "buyVal", "sellVal", "netVal",
                                "totalRoom", "currentRoom"]].sort_values(["code", "date"])
        name = "foreign_2018_2023.csv" if arg == "ALL" else "trial_%s.csv" % arg.replace(",", "_")
        df.to_csv(OUT + name, index=False)
        json.dump(cov, open(OUT + ("coverage.json" if arg == "ALL" else "trial_coverage.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
