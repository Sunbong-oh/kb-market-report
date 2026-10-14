import json, urllib.request, functools
print = functools.partial(print, flush=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
def get(u, n=1200):
    try:
        b = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=15).read().decode("utf-8", "replace")
        print("==", u, len(b)); print(b[:n]); return b
    except Exception as e:
        print("==", u, "ERR", e)
get("https://api.nasdaq.com/api/quote/ASML/info?assetclass=stocks")
get("https://api.nasdaq.com/api/quote/TSM/info?assetclass=stocks")
b = get("https://query1.finance.yahoo.com/v8/finance/chart/ASML?range=1d&interval=5m&includePrePost=true", 200)
if b:
    m = json.loads(b)["chart"]["result"][0]["meta"]; print({k: m.get(k) for k in ("regularMarketPrice","chartPreviousClose","previousClose","currentTradingPeriod","regularMarketTime","exchangeName")})
    q = json.loads(b)["chart"]["result"][0]
    ts, cl = q["timestamp"], q["indicators"]["quote"][0]["close"]; print(ts[-3:], cl[-3:])
for s in ("ASML.AS", "2330.TW", "8035.T"):
    b = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=1d&interval=5m", 100)
    if b:
        m = json.loads(b)["chart"]["result"][0]["meta"]; print(s, {k: m.get(k) for k in ("regularMarketPrice","chartPreviousClose","previousClose","currency","regularMarketTime")})
get("https://api.stock.naver.com/stock/ASML.O/basic")
