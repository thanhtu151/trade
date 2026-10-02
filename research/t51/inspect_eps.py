"""T51: tìm cột EPS trong income cache (giá trị nhỏ, cỡ nghìn VND/cp) + đối chiếu 3 mã."""
import sys
import pandas as pd

B = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/fundamentals/"
pd.set_option("display.width", 220, "display.max_rows", 300, "display.float_format", "{:,.2f}".format)
for sym, yr in (("VNM", 2019), ("FPT", 2019), ("VCB", 2019)):
    d = pd.read_parquet(B + sym + ".income.parquet").sort_values(["yearReport", "lengthReport"])
    d = d[d.yearReport == yr].set_index("lengthReport")
    isc = [c for c in d.columns if str(c)[:2] == "is"]
    small = [c for c in isc if d[c].abs().max() < 1e5 and d[c].abs().max() > 0]
    print(sym, yr, "cột có |giá trị|<1e5:", small)
    print(d[small].T.to_string() if small else "-")
    # tổng LNST cột nghi vấn (đơn vị tỷ)
    for c in ("isa20", "isa22", "isa16"):
        if c in d:
            print(" ", c, "4 quý:", (d[c] / 1e9).round(1).tolist(), "tổng năm (tỷ):", round(d[c].sum() / 1e9, 1))
