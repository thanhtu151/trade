"""T42: khảo sát dữ liệu cho universe theo thời điểm (chỉ đọc dữ liệu <= 2023-12-31).
  .venv/bin/python research/t42/survey_listing.py"""
import os
import sys
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
import pandas as pd
from engine.data import load, PRICES, END

L = pd.read_csv(PRICES + "../listing.csv").drop_duplicates("symbol").set_index("symbol").exchange
rows = []
for s in L.index:
    if not os.path.exists(PRICES + s + ".parquet"):
        rows.append(dict(s=s, ex=L[s], has_file=False))
        continue
    d = load(s)
    d = d[d.close.notna()]
    w = d.loc["2018-01-01":]
    tv = (w.close * w.volume)
    rows.append(dict(s=s, ex=L[s], has_file=True, first=d.index.min(), last=d.index.max(), n_win=len(w),
                     medtv=tv.median() if len(w) else 0.0))
R = pd.DataFrame(rows)
R["in_win"] = R.n_win.fillna(0) > 0
R["ended"] = R.in_win & (R["last"] < pd.Timestamp(END) - pd.Timedelta(days=30))
print(R.groupby("ex").agg(n=("s", "size"), file=("has_file", "sum"), in_win=("in_win", "sum"), ended=("ended", "sum")))
print("\nKết thúc dữ liệu trong 2018-2023 nhưng KHÔNG ghi DELISTED:")
print(R[R.ended & (R.ex != "DELISTED")][["s", "ex", "first", "last", "n_win"]].to_string())
print("\nDELISTED có dữ liệu trong cửa sổ, thanh khoản cao nhất (medtv = trung vị giá trị GD, nghìn đ x cp):")
print(R[(R.ex == "DELISTED") & R.in_win].sort_values("medtv", ascending=False).head(20)[["s", "first", "last", "n_win", "medtv"]].to_string())
print("\nDELISTED trong cửa sổ nhưng dữ liệu kéo tới cuối 2023:", int((R.in_win & (R.ex == "DELISTED") & ~R.ended).sum()))
R.to_csv("/Users/drone/Downloads/tradeclone/research/t42/listing_survey.csv", index=False)
