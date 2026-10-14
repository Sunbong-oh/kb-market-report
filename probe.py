import json, urllib.request, functools
print = functools.partial(print, flush=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36", "Accept": "application/json, text/xml, */*", "Accept-Language": "en-US,en;q=0.9"}
def get(u):
    try: return urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=15).read().decode("utf-8", "replace")
    except Exception as e: print("ERR", u, e); return ""
for d in ("2026-10-13", "2026-10-14", "2026-10-15"):
    b = get(f"https://api.nasdaq.com/api/calendar/economicevents?date={d}")
    try:
        rows = json.loads(b)["data"]["rows"] or []
        print(d, len(rows), [ (r["country"], r["gmt"], r["eventName"], r["actual"], r["consensus"]) for r in rows if "CPI" in r["eventName"] or "Price" in r["eventName"]][:12])
    except Exception as e: print(d, "parse", e, b[:200])
b = get("https://api.tradingeconomics.com/calendar/country/united%20states?c=guest:guest&f=json")
print("TE", b[:300])
try:
    j = json.loads(b); print([ (x.get("Date"), x.get("Event"), x.get("Actual"), x.get("Forecast"), x.get("TEForecast"), x.get("Previous")) for x in j if "CPI" in (x.get("Event") or "") or "Inflation" in (x.get("Event") or "")][:8])
except Exception as e: print("TE parse", e)
b = get("https://nfs.faireconomy.media/ff_calendar_thisweek.xml"); i = b.find("CPI"); print("FFXML", b[max(0,i-300):i+500])
b = get("https://www.forexfactory.com/calendar?day=oct14.2026"); print("FFweb", len(b), b[:150])
b = get("https://query1.finance.yahoo.com/v1/finance/search?q=CPI%20September&newsCount=8&quotesCount=0")
try: print([n["title"] for n in json.loads(b)["news"]])
except Exception as e: print("y", e)
