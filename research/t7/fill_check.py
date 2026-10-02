import json, subprocess, os
import pandas as pd

REPO = "/Users/drone/Downloads/tradeclone"
B = REPO + "/research/data/2026-09-29/prices/"
T = json.loads(subprocess.check_output(["git", "show", "origin/state:paper_trades.json"], cwd=REPO))
cache = {}
bad = []
n = 0
for i, t in enumerate(T):
    if "side" not in t:
        continue
    s = t["symbol"]
    f = B + s + ".parquet"
    if not os.path.exists(f):
        continue
    if s not in cache:
        d = pd.read_parquet(f)
        d["time"] = d["time"].astype(str).str[:10]
        cache[s] = d.set_index("time")
    day = t["time"][:10]
    d = cache[s]
    if day not in d.index:
        # weekend/holiday: compare with the last prior session close
        prior = d[d.index < day]
        if prior.empty:
            continue
        lo = hi = float(prior.iloc[-1]["close"])
        note = "non-session-day(prev close)"
    else:
        lo, hi = float(d.loc[day, "low"]), float(d.loc[day, "high"])
        note = ""
    p = t["price"]
    if p > 1000:
        p = p / 1000  # ledger rows already in VND (e.g. epoch 0 ACB/TCB)
    n += 1
    dev = (p - hi) / hi * 100 if p > hi else (p - lo) / lo * 100 if p < lo else 0.0
    if abs(dev) > 1.0:
        bad.append((i, t["epoch_id"], t["time"], t["side"], s, t["price"], lo, hi, round(dev, 1), t.get("pnl"), note))
print("checked", n, "fills; outside [low,high] by >1%:", len(bad))
for b in bad:
    print(b)
