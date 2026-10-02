"""T51: khảo sát ánh xạ isaN (income) -> lợi nhuận sau thuế / EPS bằng đối chiếu số thật."""
import sys
import pandas as pd

B = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/fundamentals/"
sym, yr = sys.argv[1], int(sys.argv[2])
d = pd.read_parquet(B + sym + ".income.parquet").sort_values(["yearReport", "lengthReport"])
isa = [c for c in d.columns if str(c).startswith("isa")]
print(sym, d.shape, "isa cols:", len(isa), "| cột khác:", [c for c in d.columns if c not in isa])
x = d[d.yearReport == yr][["lengthReport"] + isa].set_index("lengthReport").T
pd.set_option("display.width", 200, "display.max_rows", 300, "display.float_format", "{:,.1f}".format)
print((x / 1e9).dropna(how="all").to_string())
