import json, urllib.request, functools
from datetime import datetime, timezone, timedelta
print = functools.partial(print, flush=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36", "Accept": "application/json"}
K = timezone(timedelta(hours=9))
for s in ("ASML", "ASML.AS", "005930.KS", "MU"):
    j = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=5d&interval=5m&includePrePost=true", headers=H), timeout=15).read())
    r = j["chart"]["result"][0]; m = r["meta"]
    tp = m.get("tradingPeriods")
    print(s, type(tp).__name__, (list(tp.keys()) if isinstance(tp, dict) else len(tp)))
    if isinstance(tp, dict):
        for k, v in tp.items(): print(" ", k, [(datetime.fromtimestamp(x[0]["start"], K).strftime("%m/%d %H:%M"), datetime.fromtimestamp(x[0]["end"], K).strftime("%H:%M")) for x in v])
    else:
        print(" ", [(datetime.fromtimestamp(x[0]["start"], K).strftime("%m/%d %H:%M"), datetime.fromtimestamp(x[0]["end"], K).strftime("%H:%M")) for x in tp])
    ts = r["timestamp"]; c = r["indicators"]["quote"][0]["close"]
    pts = [(t, v) for t, v in zip(ts, c) if v is not None]
    print("  last", datetime.fromtimestamp(pts[-1][0], K), pts[-1][1], "regPrice", m.get("regularMarketPrice"), "prevClose", m.get("previousClose"), m.get("chartPreviousClose"))
