"""T51: cột ratio cache + đối chiếu ROE/pe/pb/EPS/BVPS với số thật."""
import pandas as pd

B = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/fundamentals/"
pd.set_option("display.width", 250, "display.max_rows", 300, "display.max_columns", 80, "display.float_format", "{:,.3f}".format)
for s in ("FPT", "VNM"):
    r = pd.read_parquet(B + s + ".ratio.parquet")
    print(s, r.year.dtype, r.quarter.dtype, r.yearReport.dtype)
    x = r[r.ratioType == "RATIO_TTM"].sort_values(["yearReport", "quarter"])
    x = x[(x.yearReport >= 2018) & (x.yearReport <= 2020)]
    print(x[["year", "quarter", "yearReport", "numberOfSharesMktCap", "marketCap", "pe", "pb", "roe", "ownersEquity", "equity", "grossMargin"]].to_string())
