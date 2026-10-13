import json, urllib.request, urllib.parse
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
def get(url, data=None, extra=None):
    h = dict(UA, **(extra or {}))
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=20)
        b = r.read().decode("utf-8", "replace")
        print("==", url, r.status, len(b)); return b
    except Exception as e:
        print("==", url, "ERR", type(e).__name__, getattr(e, "code", ""), str(e)[:100]); return ""
b = get("https://www.investing.com/economic-calendar/"); print(b[:200].replace("\n"," "))
b = get("https://kr.investing.com/economic-calendar/"); print(b[:200].replace("\n"," "))
form = urllib.parse.urlencode({"country[]": "5", "importance[]": "3", "dateFrom": "2026-10-13", "dateTo": "2026-10-16",
    "timeZone": "88", "timeFilter": "timeRemain", "currentTab": "custom", "limit_from": "0"}).encode()
b = get("https://www.investing.com/economic-calendar/Service/getCalendarFilteredData", form,
        {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded", "Referer": "https://www.investing.com/economic-calendar/"})
print(b[:1500])
b = get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
try:
    for e in json.loads(b):
        if e.get("country") == "USD" and e.get("impact") == "High": print(e)
except Exception as e: print("parse", e)
b = get("https://api.nasdaq.com/api/calendar/earnings?date=2026-10-15", extra={"Accept": "application/json"})
try:
    rows = json.loads(b)["data"]["rows"] or []
    print([r for r in rows if r.get("symbol") in ("TSM", "NFLX")])
except Exception as e: print("parse", e, b[:300])
b = get("https://api.nasdaq.com/api/calendar/economicevents?date=2026-10-14", extra={"Accept": "application/json"})
print(b[:1500])
