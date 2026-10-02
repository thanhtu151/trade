import json, subprocess
from datetime import datetime
from collections import defaultdict, Counter

REPO = "/Users/drone/Downloads/tradeclone"


def show(f):
    return json.loads(subprocess.check_output(["git", "show", "origin/state:" + f], cwd=REPO))


T = show("paper_trades.json")
P = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def off(dt):
    if dt.weekday() >= 5:
        return "weekend"
    m = dt.hour * 60 + dt.minute
    return None if any(a <= m <= b for a, b in [(540, 690), (780, 885)]) else "outside"


print("records", len(T), "types", Counter(t.get("type", t.get("side")) for t in T))
T = [t for t in T if "side" in t] if False else T
print("== 1. out-of-session (9:00-11:30, 13:00-14:45)")
bad = [(i, t, off(P(t["time"]))) for i, t in enumerate(T) if off(P(t["time"]))]
print("count", len(bad), "of", len(T))
print(Counter((t["side"], t["epoch_id"], r) for _, t, r in bad))
for i, t, r in bad:
    print(i, t["time"], P(t["time"]).strftime("%a"), t["side"], t["symbol"], t["qty"], t["price"], t["reason"][:28], r)
print("by reason", Counter(t["reason"][:20] for _, t, _ in bad))

print("\n== 2. stop_loss slippage vs plan stop of matching BUY")
opn = defaultdict(list)
rows = []
for i, t in enumerate(T):
    k = t.get("symbol")
    if k is None:
        print("NO SYMBOL:", i, json.dumps(t)[:300])
        continue
    if t["side"] == "BUY":
        opn[k].append(t)
    elif t["reason"].startswith("stop_loss"):
        rows.append((i, t, opn[k][-1] if opn[k] else None))
print("stop_loss sells", len(rows))
for i, t, b in rows:
    if b is None:
        print(i, t["symbol"], "no matching buy")
        continue
    st = b.get("plan", {}).get("stop_loss")
    sl = (st - t["price"]) / st * 100 if st else None
    flag = "  <<< >2%" if sl is not None and sl > 2 else ""
    print(i, t["time"], t["symbol"], t["reason"], "buy", b["price"], "stop", st, "sell", t["price"],
          "slip%", None if sl is None else round(sl, 2), "pnl", t["pnl"], "ep", t["epoch_id"], flag)

print("\n== 3. realized pnl by epoch")
s = defaultdict(float)
n = defaultdict(int)
T = [t for t in T if "side" in t]
print("real trades", len(T))
for t in T:
    if t["side"] == "SELL" and t["pnl"] is not None:
        s[t["epoch_id"]] += t["pnl"]
        n[t["epoch_id"]] += 1
print(dict(s), dict(n), "sells with pnl None", sum(1 for t in T if t["side"] == "SELL" and t["pnl"] is None))
for e in (0, 1):
    c = sum((-t["value"] if t["side"] == "BUY" else t["value"]) for t in T if t["epoch_id"] == e)
    print("epoch", e, "net cash flow from trades", round(c))

print("\n== epoch1 BUY unit check")
for t in T:
    if t["epoch_id"] == 1 and t["side"] == "BUY":
        print(t["time"], t["symbol"], t["qty"], t["price"], "value", t["value"], "qty*price", round(t["qty"] * t["price"]))
print("\nportfolio", json.dumps(show("paper_portfolio.json"))[:1800])
