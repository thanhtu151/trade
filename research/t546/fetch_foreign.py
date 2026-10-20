"""T546 Phần B — tải lịch sử mua/bán khối ngoại theo mã/ngày từ CafeF (công khai), CHỈ 2018-01-01 → 2023-12-31.

Nguồn: https://cafef.vn/du-lieu/ajax/pagenew/datahistory/gdkhoingoai.ashx (StartDate/EndDate dạng MM/DD/YYYY,
trang tối đa 30 dòng, server trả dòng trùng y hệt → khử trùng theo ngày, đếm xung đột).
Universe = mọi mã từng có trong research/t42/universe_pit.csv (PIT top-50, gồm mã hủy niêm yết).
Mọi dòng có ngày > 2023-12-31 bị bỏ (không đọc holdout). Chạy lại được (bỏ qua mã đã có file).

    python t546/fetch_foreign.py VCB          # chạy thử 1 mã
    python t546/fetch_foreign.py ALL          # toàn bộ universe
"""
import json
import os
import sys
import time

import pandas as pd
import requests

ROOT = "/Users/drone/Downloads/tradeclone/research/"
OUT = ROOT + "data/foreign_cafef/"
URL = "https://cafef.vn/du-lieu/ajax/pagenew/datahistory/gdkhoingoai.ashx"
END = pd.Timestamp("2023-12-31")
DELAY = 0.35
COLS = ["Ngay", "KLMua", "GtMua", "KLBan", "GtBan", "KLGDRong", "GTDGRong", "RoomConLai", "DangSoHuu"]
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"


def get(sym, a, b, page):
    for k in range(4):
        try:
            r = S.get(URL, params=dict(Symbol=sym, StartDate=a.strftime("%m/%d/%Y"), EndDate=b.strftime("%m/%d/%Y"),
                                       PageIndex=page, PageSize=30), timeout=30)
            j = r.json()
            d = j.get("Data") or {}
            return d.get("TotalCount") or 0, d.get("Data") or []
        except Exception:
            time.sleep(2 * (k + 1))
    raise RuntimeError("%s %s page %d: giving up" % (sym, a.date(), page))


def fetch(sym):
    rows = []
    for a in pd.date_range("2018-01-01", "2023-12-01", freq="MS"):
        b = a + pd.offsets.MonthEnd(0)
        page, got = 1, 0
        while True:
            total, data = get(sym, a, b, page)
            time.sleep(DELAY)
            rows += data
            got += len(data)
            if not data or got >= total or page >= 20:
                break
            page += 1
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df[[c for c in COLS if c in df.columns]].copy()
    df["date"] = pd.to_datetime(df.pop("Ngay"), format="%d/%m/%Y")
    df = df[(df.date >= "2018-01-01") & (df.date <= END)]
    n_raw = len(df)
    df = df.drop_duplicates()
    conflict = int(df.date.duplicated().sum())          # cùng ngày nhưng số khác nhau
    df = df.drop_duplicates("date", keep="first").sort_values("date")
    df.insert(0, "symbol", sym)
    return df, n_raw, conflict


def main(arg):
    os.makedirs(OUT, exist_ok=True)
    u = pd.read_csv(ROOT + "t42/universe_pit.csv")
    syms = sorted({x for v in u.symbols for x in v.split()}) if arg == "ALL" else arg.split(",")
    log_p = OUT + "fetch_log.json"
    log = json.load(open(log_p)) if os.path.exists(log_p) else {}
    for i, s in enumerate(syms):
        f = OUT + s + ".csv"
        if os.path.exists(f) or log.get(s, {}).get("status") == "empty":
            continue
        t0 = time.time()
        try:
            res = fetch(s)
        except Exception as e:
            log[s] = dict(status="failed", err=str(e)[:200])
        else:
            if res is None:
                log[s] = dict(status="empty")
            else:
                df, n_raw, conflict = res
                assert df.date.max() <= END
                df.to_csv(f, index=False)
                log[s] = dict(status="ok", rows=len(df), raw=n_raw, conflict=conflict,
                              first=str(df.date.min().date()), last=str(df.date.max().date()))
        json.dump(log, open(log_p, "w"), indent=1)
        print(i + 1, len(syms), s, log[s], "%.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
