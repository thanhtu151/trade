"""Khảo sát cache fundamentals (chỉ đọc) cho PEAD / value+profitability."""
import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1] / "data" / "2026-09-29" / "fundamentals"
SAMPLE = ["FPT", "HPG", "VCB", "MWG", "VNM"]
DATE_PAT = re.compile(r"(public|publish|announce|report.?date|ngay|disclos|release|filing)", re.I)


def load(t, kind):
    p = BASE / f"{t}.{kind}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def period_info(df):
    """Trả về (cột năm, cột quý) nếu tìm thấy."""
    cols = {c.lower(): c for c in map(str, df.columns)}
    y = next((cols[c] for c in cols if c in ("year", "yearreport", "year_report", "nam")), None)
    q = next((cols[c] for c in cols if c in ("quarter", "lengthreport", "length_report", "quy")), None)
    return y, q


def periods(df):
    y, q = period_info(df)
    if y is None:
        return None
    d = df[[y] + ([q] if q else [])].copy()
    d.columns = ["y"] + (["q"] if q else [])
    if "q" not in d:
        d["q"] = 0
    return d.dropna().astype(int).drop_duplicates()


print("BASE:", BASE, "exists:", BASE.exists())
for t in SAMPLE:
    for kind in ("income", "ratio"):
        df = load(t, kind)
        if df is None:
            print(f"[{t}.{kind}] KHÔNG CÓ FILE")
            continue
        cols = [str(c) for c in df.columns]
        datecols = [c for c in cols if DATE_PAT.search(c)]
        pr = periods(df)
        print(f"\n[{t}.{kind}] shape={df.shape} index={type(df.index).__name__}")
        print("  cột(%d): %s" % (len(cols), cols[:25]))
        print("  cột nghi là ngày công bố:", datecols or "KHÔNG CÓ")
        if pr is not None:
            pr = pr.sort_values(["y", "q"])
            print("  số kỳ:", len(pr), "đầu:", tuple(pr.iloc[0]), "cuối:", tuple(pr.iloc[-1]),
                  "quý(q>0):", int((pr.q > 0).sum()), "năm(q=0/5):", int((pr.q <= 0).sum()))
        else:
            print("  không nhận ra cột năm/quý; dtypes:", dict(df.dtypes.astype(str).head(8)))

# Độ phủ toàn bộ cache
print("\n=== ĐỘ PHỦ 2016–2023 (chỉ kỳ quý q in 1..4) ===")
for kind in ("income", "ratio"):
    files = sorted(BASE.glob(f"*.{kind}.parquet"))
    cov, empty, noparse = {}, 0, 0
    for f in files:
        t = f.name.split(".")[0]
        try:
            df = pd.read_parquet(f)
        except Exception:
            noparse += 1
            continue
        if df.empty:
            empty += 1
            continue
        pr = periods(df)
        if pr is None:
            noparse += 1
            continue
        pr = pr[(pr.y >= 2016) & (pr.y <= 2023) & (pr.q.between(1, 4))]
        cov[t] = len(pr)
    s = pd.Series(cov)
    print(f"{kind}: files={len(files)} rỗng={empty} không-parse={noparse} có-dữ-liệu={len(s)}")
    if len(s):
        print("  quý/mã (tối đa 32): median=%d mean=%.1f" % (s.median(), s.mean()))
        for k in (32, 28, 24, 16):
            print(f"  mã có >= {k} quý 2016-2023: {int((s >= k).sum())}")
        print("  5 mẫu:", {t: int(s.get(t, 0)) for t in SAMPLE})

try:
    import vnstock  # noqa: F401
    print("\nvnstock có trong venv:", vnstock.__file__)
except ImportError:
    print("\nvnstock: KHÔNG có trong venv -> không kiểm được endpoint ngày công bố")
