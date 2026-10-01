"""T51: kiểm hợp lý trạng thái E/P, B/P, ROE, SUE (không chạy engine, không có chỉ số hiệu suất)."""
import sys
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research")
sys.path.insert(0, "/Users/drone/Downloads/tradeclone/research/t51")
import numpy as np
import pandas as pd
import run_t51 as R
from engine.data import Panel, universe_all, NON_STOCK

P = Panel(universe_all(True) + ["VN30"], start="2016-01-01")
stocks = [s for s in P.symbols if s not in NON_STOCK]
F = R.load_fund(P.idx, [s for s in stocks if __import__("os").path.exists(R.FUND + s + ".ratio.parquet")])
st = R.build_state(F, P)
pd.set_option("display.width", 200, "display.float_format", "{:,.3f}".format)
for d in ("2019-06-28", "2021-06-30", "2023-06-30"):
    print(d)
    print(pd.DataFrame({k: st[k].loc[d, ["VNM", "FPT", "VCB", "HPG"]] for k in ("ep", "bp", "roe", "sue")}).to_string())
print("SUE phân bố (mọi ngày/mã có giá trị) mô tả:", st["sue"].stack().describe().round(2).to_dict())
print("ep phân bố:", st["ep"].stack().describe().round(3).to_dict())
g = F[(F.sym == "FPT") & (F.year.isin([2019, 2020]))][["year", "q", "pub", "A", "usage", "eps", "pe", "pb", "roe"]]
print(g.to_string())
