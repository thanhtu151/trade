"""T34: xem nhanh cấu trúc parquet giá (VNINDEX, PVD, VPB, VHM) quanh kỳ paper epoch 1."""
import pandas as pd

D = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/prices/"
for s in ["VNINDEX", "PVD", "VPB", "VHM"]:
    d = pd.read_parquet(D + s + ".parquet")
    print(s, d.columns.tolist(), len(d))
    c = d.columns[0]
    print(d[d[c].astype(str) >= "2026-07-08"].head(3).to_string())
    print(d.tail(2).to_string())
