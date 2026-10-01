import pandas as pd
B = "/Users/drone/Downloads/tradeclone/research/data/2026-09-29/prices/"
for s, a, b in [("VPB", "2026-09-16", "2026-09-25"), ("PVD", "2026-07-08", "2026-07-16"), ("PVD", "2026-09-15", "2026-09-19")]:
    d = pd.read_parquet(B + s + ".parquet")
    d = d.reset_index() if "date" not in [c.lower() for c in d.columns] else d
    c = [x for x in d.columns if "date" in x.lower() or "time" in x.lower()][0]
    d[c] = d[c].astype(str)
    print(s, list(d.columns))
    print(d[(d[c] >= a) & (d[c] <= b)].to_string())
