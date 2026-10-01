"""Chất lượng cột publicDate (income cache): null, độ trễ so với cuối quý, khoảng thời gian, EPS."""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1] / "data" / "2026-09-29" / "fundamentals"
SAMPLE = ["FPT", "HPG", "VCB", "MWG", "VNM"]

rows = []
for f in sorted(BASE.glob("*.income.parquet")):
    df = pd.read_parquet(f, columns=["ticker", "yearReport", "lengthReport", "publicDate", "createDate", "updateDate"])
    rows.append(df)
a = pd.concat(rows, ignore_index=True)
a["pub"] = pd.to_datetime(a["publicDate"], errors="coerce")
a["qend"] = pd.to_datetime(dict(year=a.yearReport, month=a.lengthReport * 3, day=1)) + pd.offsets.MonthEnd(0)
a["lag"] = (a["pub"] - a["qend"]).dt.days
print("hàng:", len(a), "mã:", a.ticker.nunique(), "yearReport:", a.yearReport.min(), "-", a.yearReport.max())
print("publicDate null:", int(a.pub.isna().sum()), "| dtype gốc:", a.publicDate.dtype)
print("publicDate mẫu:", a.publicDate.dropna().head(3).tolist())
print("lag (ngày) mô tả:\n", a.lag.describe().round(1).to_string())
print("lag<0:", int((a.lag < 0).sum()), "| lag==0:", int((a.lag == 0).sum()),
      "| lag>100 (nghi ngày sau/restate):", int((a.lag > 100).sum()))
print("lag theo quý (median):", a.groupby("lengthReport").lag.median().to_dict())
print("createDate/updateDate mẫu:", a[["createDate", "updateDate"]].dropna().head(2).values.tolist())
print("số giá trị publicDate khác nhau / hàng:", a.pub.nunique(), "/", len(a))
for t in SAMPLE:
    s = a[(a.ticker == t) & (a.yearReport <= 2020)].sort_values(["yearReport", "lengthReport"])
    print(t, s[["yearReport", "lengthReport", "publicDate", "lag"]].head(6).values.tolist())

# EPS / lợi nhuận: liệt kê cột isa nào có tên? cache dùng mã isaN, không có nhãn
i = pd.read_parquet(BASE / "FPT.income.parquet")
print("cột chứa 'eps' (không phân biệt hoa thường):", [c for c in i.columns if "eps" in str(c).lower()] or "không có (chỉ mã isaN)")
r = pd.read_parquet(BASE / "FPT.ratio.parquet")
print("ratio cột eps/roe/pb/pe/gross:", [c for c in r.columns if any(k in str(c).lower() for k in ("eps", "roe", "pb", "pe", "gross", "margin"))])
print("ratio FPT 2 dòng đầu:", r[["year", "quarter", "ratioType", "pe", "pb", "roe"]].head(2).values.tolist())
print("ratioType giá trị:", r.ratioType.unique().tolist())
