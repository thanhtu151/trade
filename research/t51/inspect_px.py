"""T51: giá cache là giá điều chỉnh hay danh nghĩa? So marketCap/shares (ratio) với close cùng ngày cuối quý."""
import sys
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import pandas as pd
from engine.data import load

B = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/fundamentals/"
for s in ("VNM", "FPT", "VCB", "HPG"):
    c = load(s).close
    r = pd.read_parquet(B + s + ".ratio.parquet")
    r = r[r.ratioType == "RATIO_TTM"].sort_values(["yearReport", "quarter"])
    r = r[(r.yearReport >= 2018) & (r.yearReport <= 2023)]
    out = []
    for _, x in r.iterrows():
        qe = pd.Timestamp(year=int(x.yearReport), month=int(x.quarter) * 3, day=1) + pd.offsets.MonthEnd(0)
        px = c[:qe].iloc[-1]
        out.append((int(x.yearReport), int(x.quarter), round(px, 1), round(x.marketCap / x.numberOfSharesMktCap / 1000, 1)))
    print(s, "(năm,quý,close_cache,mktcap/shares nghìn VND):", out[::3])
    print(s, "shares đầu/cuối:", int(r.numberOfSharesMktCap.iloc[0]), int(r.numberOfSharesMktCap.iloc[-1]))
