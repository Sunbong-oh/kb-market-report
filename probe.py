import json, urllib.request, functools
print = functools.partial(print, flush=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
def get(u, n=900):
    try:
        b = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=15).read().decode("utf-8", "replace")
        print("==", u, len(b)); print(b[:n].replace("\n", " ")); return b
    except Exception as e:
        print("==", u, "ERR", e)
for u in ["https://api.nasdaq.com/api/calendar/earnings?date=2026-10-14",
          "https://api.nasdaq.com/api/company/ASML/earnings-surprise",
          "https://api.nasdaq.com/api/company/ASML/revenue?limit=1",
          "https://api.nasdaq.com/api/analyst/ASML/earnings-forecast",
          "https://query1.finance.yahoo.com/v10/finance/quoteSummary/ASML?modules=earnings",
          "https://query2.finance.yahoo.com/v1/finance/search?q=ASML&newsCount=5&quotesCount=0",
          "https://news.google.com/rss/search?q=ASML+results+when:1d&hl=en-US&gl=US&ceid=US:en",
          "https://news.google.com/rss/search?q=ASML+%EC%8B%A4%EC%A0%81+when:1d&hl=ko&gl=KR&ceid=KR:ko",
          "https://m.stock.naver.com/api/stock/000660/finance/quarter",
          "https://m.stock.naver.com/api/stock/005930/disclosure?pageSize=5&page=1",
          "https://m.stock.naver.com/api/news/stock/005930?pageSize=5&page=1",
          "https://m.stock.naver.com/api/stock/005930/integration"]:
    b = get(u)
    if b and "calendar/earnings" in u:
        rows = json.loads(b)["data"]["rows"] or []
        print([r for r in rows if r["symbol"] in ("ASML", "TSM", "BAC", "MS")])
