import json, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept": "application/json"}
def get(url):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())
    except Exception as e: print("ERR", url, e); return {}
for d in ("2026-10-14", "2026-09-10", "2026-09-11", "2026-09-16", "2026-09-17", "2026-10-02", "2026-10-07", "2026-10-08"):
    rows = ((get(f"https://api.nasdaq.com/api/calendar/economicevents?date={d}").get("data") or {}).get("rows")) or []
    print("##", d)
    for r in rows:
        if r.get("country") == "United States": print({k: r.get(k) for k in ("gmt", "eventName", "actual", "consensus", "previous")})
for d in ("2026-09-23", "2026-09-24", "2026-10-01", "2026-10-15", "2026-10-22", "2026-10-29"):
    data = get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}").get("data") or {}
    print("## E", d, data.get("headers"))
    for r in data.get("rows") or []:
        if r.get("symbol") in ("MU", "TSM", "TSLA", "GOOGL", "MSFT", "META", "AAPL", "AMZN", "NKE", "FDX"): print(r)
ff = urllib.request.urlopen(urllib.request.Request("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=UA), timeout=20).read()
print([e for e in json.loads(ff) if e["country"] == "USD" and e["impact"] == "High"][:2])
