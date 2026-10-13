import httpx, json, re, xml.etree.ElementTree as ET
H={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36","Referer":"https://m.stock.naver.com/"}
c=httpx.Client(headers=H,timeout=15,follow_redirects=True)
b=c.get("https://m.stock.naver.com/api/stock/005930/basic").json()
print("BASIC", json.dumps(b,ensure_ascii=False)[:1500])
for u in ["https://m.stock.naver.com/api/stock/005930/integration"]:
    t=c.get(u).text; i=t.find("overMarket"); print("INTEG", t[max(0,i-100):i+600] if i>=0 else t[:300])
r=c.get("https://news.google.com/rss/search",params={"q":"반도체 when:1d","hl":"ko","gl":"KR","ceid":"KR:ko"})
print("GN",r.status_code)
root=ET.fromstring(r.text)
for it in root.iter("item"):
    print("GN", it.findtext("pubDate"), "|", it.findtext("title"), "|", (it.find("source").text if it.find("source") is not None else ""))
    break
n=sum(1 for _ in root.iter("item")); print("GN items",n)
for u in ["https://m.stock.naver.com/api/news/list?category=mainnews&page=1&pageSize=20","https://m.stock.naver.com/front-api/news/category?category=mainnews&page=1&pageSize=20"]:
    try:
        r=c.get(u); print("NV",u,r.status_code,r.text[:600])
    except Exception as e: print("NV fail",u,e)
